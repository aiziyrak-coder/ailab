#!/usr/bin/env python3
"""Gistologiya / dermatopatologiya kitoblarini vektor indeksiga o'qitish.

Xususiyatlar:
  * Har bir kitob alohida keshlanadi (sha256) — qayta ishga tushirishda faqat
    yangi kitoblar embed qilinadi (pul va vaqt tejaladi).
  * Skanerlangan (matn qatlami yo'q) PDF uchun OCR: OpenAI vision bilan
    sahifama-sahifa transkripsiya, natija diskda keshlanadi.
  * Manba bo'yicha kvota — bitta yo'g'on kitob indeksni bosib ketmaydi.

Misollar:
  python scripts/ingest_histology_kb.py --pdf-dir "C:\\Users\\me\\Desktop\\Gistalogiya Kitoblar"
  python scripts/ingest_histology_kb.py --pdf-dir "..." --ocr          # skanerlangan kitoblar ham
  python scripts/ingest_histology_kb.py --pdf-dir "..." --ocr-only     # faqat OCR keshini to'ldirish
  python scripts/ingest_histology_kb.py --status                       # indeks holati
"""
from __future__ import annotations

import argparse
import base64
import hashlib

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Windows konsoli cp1251 bo'lishi mumkin — kitob nomlarida kirill/strelka bor
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

_ENV_CANDIDATES = [
    Path(os.environ.get("MEDLAB_ENV_FILE") or "") if os.environ.get("MEDLAB_ENV_FILE") else None,
    BACKEND / ".env",
    ROOT / ".env",
]
for _p in _ENV_CANDIDATES:
    if _p and _p.is_file():
        load_dotenv(_p, override=True)
        break

from lab_core.histology_kb import (  # noqa: E402
    SOURCES,
    chunk_pages,
    detect_source,
    embed_texts,
    extract_pdf_pages,
    index_stats,
    kb_dir,
    save_index,
    source_prefix,
)

# Manba bo'yicha maksimal chunk (indeks muvozanati)
DEFAULT_SOURCE_CAP = {
    "weedon": 9000,
    "weedon_essentials": 5000,
    "first_impression": 4000,
    "atlas_biopsy_ru": 5000,
    "dermatoonko_ru": 4000,
    "color_atlas": 4000,
    "vademecum": 3000,
    "vascular_skin": 3000,
    "derm_course": 3000,
    "derm_basics": 2000,
    "melanoma_genetics": 2000,
    "tsvetkova_ru": 3000,
    "derma_misc": 2000,
    "junqueira": 2500,
    "langman": 1200,
    "mboc": 2500,
    "histology": 2000,
}

OCR_MIN_WORDS_PER_PAGE = 25  # shundan past bo'lsa sahifa "skan" deb hisoblanadi


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()[:16]


def cache_dir() -> Path:
    d = Path(kb_dir()) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def collect_pdfs(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix.lower() == ".pdf":
            out.append(p)
        elif p.is_dir():
            out.extend(sorted(p.rglob("*.pdf")))
    seen = set()
    uniq = []
    for p in out:
        r = p.resolve()
        if r in seen:
            continue
        seen.add(r)
        uniq.append(p)
    return uniq


# ─── OCR (skanerlangan kitoblar) ──────────────────────────────────────────────
def _ocr_client():
    from openai import OpenAI

    key = (os.environ.get("OPENAI_API_KEY") or "").strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("OPENAI_API_KEY yo'q — OCR ishlamaydi")
    return OpenAI(api_key=key, timeout=180.0)


_OCR_SYSTEM = (
    "You transcribe scanned pages of medical (dermatopathology / histology) textbooks. "
    "Return ONLY the readable body text of the page, preserving paragraph order and "
    "figure captions. Keep the original language (Russian or English). "
    "Do not translate, do not summarize, do not add commentary. "
    "If the page is only a photograph with no text, return an empty string."
)


def _page_png_b64(doc, page_no: int, dpi: int) -> str:
    page = doc[page_no]
    pix = page.get_pixmap(dpi=dpi)
    data = pix.tobytes("jpeg") if hasattr(pix, "tobytes") else pix.getPNGData()
    return base64.b64encode(data).decode("ascii")


def ocr_pdf_pages(pdf: Path, source: str, dpi: int, workers: int, model: str,
                  limit: int = 0) -> list[tuple[int, str]]:
    """Skanerlangan PDF → [(page_no, text)], natija keshda saqlanadi."""
    import fitz

    sha = _sha256(pdf)
    cache_f = cache_dir() / f"ocr_{source}_{sha}.jsonl"
    done: dict[int, str] = {}
    if cache_f.is_file():
        with cache_f.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    done[int(row["page"])] = row.get("text") or ""
                except Exception:
                    continue

    doc = fitz.open(str(pdf))
    total = doc.page_count
    todo = [i for i in range(total) if (i + 1) not in done]
    if limit:
        todo = todo[:limit]
    if todo:
        client = _ocr_client()
        print(f"  OCR: {len(todo)}/{total} sahifa (kesh: {len(done)}) model={model} dpi={dpi}")

        lock = __import__("threading").Lock()
        out_f = cache_f.open("a", encoding="utf-8")

        def work(i: int):
            page_no = i + 1
            try:
                b64 = _page_png_b64(doc, i, dpi)
            except Exception as e:
                return page_no, "", f"render xato: {e}"
            for attempt in range(3):
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": _OCR_SYSTEM},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Transcribe this page."},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": "data:image/jpeg;base64," + b64,
                                            "detail": "high",
                                        },
                                    },
                                ],
                            },
                        ],
                        max_tokens=2400,
                        temperature=0.0,
                    )
                    txt = (resp.choices[0].message.content or "").strip()
                    return page_no, txt, ""
                except Exception as e:
                    if attempt == 2:
                        return page_no, "", str(e)[:120]
                    time.sleep(2 * (attempt + 1))
            return page_no, "", "urinishlar tugadi"

        n_ok = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for k, (page_no, txt, err) in enumerate(ex.map(work, todo), start=1):
                if err:
                    print(f"    p{page_no}: {err}")
                done[page_no] = txt
                with lock:
                    out_f.write(
                        json.dumps({"page": page_no, "text": txt}, ensure_ascii=False) + "\n"
                    )
                    out_f.flush()
                if txt:
                    n_ok += 1
                if k % 25 == 0:
                    print(f"    OCR {k}/{len(todo)} (matnli: {n_ok})")
        out_f.close()
    doc.close()
    return [(p, done.get(p, "")) for p in sorted(done)]


def _pages_are_scanned(pages: list[tuple[int, str]]) -> bool:
    import re

    if not pages:
        return True
    sample = pages[len(pages) // 5 : len(pages) // 5 + 40] or pages[:40]
    words = sum(len(re.findall(r"[A-Za-zА-Яа-я]{3,}", t or "")) for _, t in sample)
    return (words / max(1, len(sample))) < OCR_MIN_WORDS_PER_PAGE


# ─── Kitob → chunk + embedding keshi ──────────────────────────────────────────
def book_cache_paths(source: str, sha: str):
    d = cache_dir()
    return d / f"{source}_{sha}.npy", d / f"{source}_{sha}.jsonl"


def load_book_cache(source: str, sha: str):
    emb_p, ch_p = book_cache_paths(source, sha)
    if not (emb_p.is_file() and ch_p.is_file()):
        return None, None
    emb = np.load(emb_p)
    chunks = []
    with ch_p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    if emb.ndim != 2 or emb.shape[0] != len(chunks):
        return None, None
    return emb, chunks


def save_book_cache(source: str, sha: str, chunks, emb):
    emb_p, ch_p = book_cache_paths(source, sha)
    np.save(emb_p, emb.astype(np.float32))
    with ch_p.open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")


def load_existing_sources(keep: set[str]):
    """Joriy indeksdan tanlangan manbalarni (chunk + vektor) ko'chirish.

    PDF endi diskda bo'lmaganda ham eski kitoblar indeksda qoladi.
    """
    d = Path(kb_dir())
    emb_p, ch_p = d / "embeddings.npy", d / "chunks.jsonl"
    if not (emb_p.is_file() and ch_p.is_file()):
        return [], None
    emb = np.load(emb_p)
    chunks = []
    with ch_p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    if emb.ndim != 2 or emb.shape[0] != len(chunks):
        print("DIQQAT: eski indeks nomuvofiq — ko'chirilmadi")
        return [], None
    idx = [i for i, c in enumerate(chunks) if (c.get("source") or "") in keep]
    if not idx:
        return [], None
    # Manba kvotasi — eski indeksda MBOC juda ko'p joy egallagan
    by_src: dict[str, list[int]] = {}
    for i in idx:
        by_src.setdefault(chunks[i].get("source") or "histology", []).append(i)
    picked: list[int] = []
    for src, rows in by_src.items():
        cap = DEFAULT_SOURCE_CAP.get(src, 3000)
        if len(rows) > cap:
            rows = sorted(rows, key=lambda i: -len(chunks[i].get("text") or ""))[:cap]
            print(f"  cap {src}: {cap}")
        picked.extend(rows)
    picked.sort()
    out_chunks = []
    for i in picked:
        row = dict(chunks[i])
        row.pop("id", None)
        out_chunks.append(row)
    return out_chunks, emb[picked]


def embed_book(chunks, source: str, batch_log=2000):
    texts = [source_prefix(source) + ch["text"] for ch in chunks]
    parts = []
    step = 512
    for i in range(0, len(texts), step):
        parts.append(embed_texts(texts[i : i + step]))
        done = min(i + step, len(texts))
        if done % batch_log < step:
            print(f"    embed {done}/{len(texts)}")
    return np.vstack(parts) if parts else np.zeros((0, 1536), dtype=np.float32)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="append", default=[], help="PDF yo'li (takrorlash mumkin)")
    parser.add_argument("--pdf-dir", action="append", default=[], help="PDF papka")
    parser.add_argument("--ocr", action="store_true", help="Skanerlangan PDF uchun OCR yoqish")
    parser.add_argument("--ocr-only", action="store_true", help="Faqat OCR keshini to'ldirish")
    parser.add_argument("--ocr-model", default=os.environ.get("KB_OCR_MODEL", "gpt-4o-mini"))
    parser.add_argument("--ocr-dpi", type=int, default=int(os.environ.get("KB_OCR_DPI", "170")))
    parser.add_argument("--ocr-workers", type=int, default=int(os.environ.get("KB_OCR_WORKERS", "6")))
    parser.add_argument("--ocr-limit", type=int, default=0, help="Sinov uchun sahifa chegarasi")
    parser.add_argument("--max-chunks", type=int,
                        default=int(os.environ.get("HISTOLOGY_KB_MAX_CHUNKS") or "60000"))
    parser.add_argument("--status", action="store_true", help="Indeks holatini ko'rsatish")
    parser.add_argument("--rebuild", action="store_true", help="Keshni e'tiborsiz qoldirib qayta embed")
    parser.add_argument(
        "--keep-source",
        action="append",
        default=[],
        help="Mavjud indeksdan shu manba chunklarini ko'chirish (PDF endi diskda bo'lmasa), "
             "masalan --keep-source junqueira --keep-source mboc",
    )
    args = parser.parse_args()

    if args.status:
        st = index_stats()
        print(json.dumps(st, ensure_ascii=False, indent=2))
        meta_p = Path(kb_dir()) / "meta.json"
        if meta_p.is_file():
            print(meta_p.read_text(encoding="utf-8")[:4000])
        return 0

    raw = [Path(x) for x in (args.pdf + args.pdf_dir)]
    pdfs = collect_pdfs(raw) if raw else []
    if not pdfs and not args.keep_source:
        print("--pdf yoki --pdf-dir bering", file=sys.stderr)
        return 2

    print(f"PDF: {len(pdfs)} ta")
    all_chunks: list[dict] = []
    all_emb: list[np.ndarray] = []
    books = []
    t0 = time.time()

    if args.keep_source:
        keep = set(args.keep_source)
        kept_chunks, kept_emb = load_existing_sources(keep)
        if kept_chunks:
            all_chunks.extend(kept_chunks)
            all_emb.append(kept_emb)
            got: dict[str, int] = {}
            for c in kept_chunks:
                got[c["source"]] = got.get(c["source"], 0) + 1
            for s, n in sorted(got.items()):
                print(f"[{s}] mavjud indeksdan ko'chirildi: {n} chunk")
                books.append({"file": "(mavjud indeks)", "source": s, "chunks": n,
                              "carried_over": True})
        else:
            print(f"DIQQAT: mavjud indeksdan {sorted(keep)} topilmadi")

    for pdf in pdfs:
        src = detect_source(pdf.name)
        sha = _sha256(pdf)
        label = SOURCES.get(src, {}).get("label", src)
        print(f"\n[{src}] {pdf.name}\n  → {label}")

        if not args.rebuild and not args.ocr_only:
            emb, chunks = load_book_cache(src, sha)
            if emb is not None:
                print(f"  kesh: chunks={len(chunks)} dim={emb.shape[1]}")
                all_chunks.extend(chunks)
                all_emb.append(emb)
                books.append({"file": pdf.name, "source": src, "chunks": len(chunks),
                              "sha256_16": sha, "cached": True})
                continue

        pages = extract_pdf_pages(str(pdf))
        scanned = _pages_are_scanned(pages)
        if scanned:
            if args.ocr or args.ocr_only:
                print(f"  matn qatlami yo'q ({len(pages)} sahifa) — OCR")
                pages = ocr_pdf_pages(pdf, src, args.ocr_dpi, args.ocr_workers,
                                      args.ocr_model, args.ocr_limit)
            else:
                print(f"  DIQQAT: skanerlangan, matn yo'q — o'tkazib yuborildi (--ocr bering)")
                books.append({"file": pdf.name, "source": src, "chunks": 0,
                              "sha256_16": sha, "skipped": "scanned_no_ocr"})
                continue
        if args.ocr_only:
            continue

        chunks = chunk_pages(pages, src)
        for ch in chunks:
            ch["book"] = pdf.name[:80]
        cap = DEFAULT_SOURCE_CAP.get(src, 3000)
        if len(chunks) > cap:
            chunks.sort(key=lambda c: -len(c.get("text") or ""))
            chunks = chunks[:cap]
            chunks.sort(key=lambda c: c.get("page") or 0)
            print(f"  cap {src}: {cap}")
        print(f"  pages={len(pages)} chunks={len(chunks)} — embed…")
        if not chunks:
            books.append({"file": pdf.name, "source": src, "chunks": 0, "sha256_16": sha})
            continue
        emb = embed_book(chunks, src)
        save_book_cache(src, sha, chunks, emb)
        all_chunks.extend(chunks)
        all_emb.append(emb)
        books.append({"file": pdf.name, "source": src, "pages": len(pages),
                      "chunks": len(chunks), "sha256_16": sha, "ocr": bool(scanned)})

    if args.ocr_only:
        print("\nOCR keshi tayyor.")
        return 0

    if not all_chunks:
        print("Matn chiqmadi", file=sys.stderr)
        return 3

    emb = np.vstack(all_emb)
    if len(all_chunks) > args.max_chunks:
        order = sorted(
            range(len(all_chunks)),
            key=lambda i: (
                SOURCES.get(all_chunks[i]["source"], {}).get("tier", 9),
                -len(all_chunks[i].get("text") or ""),
            ),
        )[: args.max_chunks]
        order.sort()
        all_chunks = [all_chunks[i] for i in order]
        emb = emb[order]
        print(f"cap total={args.max_chunks}")

    by_src: dict[str, int] = {}
    for c in all_chunks:
        by_src[c["source"]] = by_src.get(c["source"], 0) + 1

    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "embedding_model": os.environ.get("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small",
        "n_chunks": len(all_chunks),
        "by_source": by_src,
        "books": books,
        "kb_dir": str(kb_dir()),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    save_index(all_chunks, emb, meta)
    print(f"\nsaqlandi: {kb_dir()} chunks={len(all_chunks)} dim={emb.shape[1]}")
    for k, v in sorted(by_src.items(), key=lambda x: -x[1]):
        print(f"  {k:20s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
