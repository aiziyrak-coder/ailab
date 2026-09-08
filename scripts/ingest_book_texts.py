#!/usr/bin/env python3
"""Klinika kutubxonasi (.doc / .docx / .pdf → matn) ni vektor indeksiga qo'shish.

Kirish tuzilishi:
    <text-dir>/<manba_kodi>/**/*.txt

Har bir manba kodi `lab_core.histology_kb.SOURCES` da ro'yxatdan o'tgan bo'lishi
kerak (masalan derm_guide_ru, atlas_book_ru, ...).

Xususiyatlari:
  * Takroriy parchalar tashlanadi (bir kitob bir necha nusxada uchraydi).
  * Har manba alohida keshlanadi (sha256) — qayta ishga tushirish bepul.
  * Mavjud indeks saqlanadi: faqat shu manbalar almashtiriladi, qolgani tegilmaydi.

Misollar:
    python scripts/ingest_book_texts.py --text-dir D:/AILab/_kb_text
    python scripts/ingest_book_texts.py --text-dir D:/AILab/_kb_text --plan
    python scripts/ingest_book_texts.py --text-dir D:/AILab/_kb_text --only derm_guide_ru
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

for _p in (
    Path(os.environ["MEDLAB_ENV_FILE"]) if os.environ.get("MEDLAB_ENV_FILE") else None,
    BACKEND / ".env",
    ROOT / ".env",
):
    if _p and _p.is_file():
        load_dotenv(_p, override=True)
        break

from lab_core.histology_kb import (  # noqa: E402
    SOURCES,
    chunk_pages,
    embed_texts,
    index_stats,
    kb_dir,
    save_index,
    source_label,
    source_prefix,
)

MIN_CHUNK_CHARS = 280

# Takrorni tashlash tartibi: bir xil matn bir necha arxivda uchraydi
# (masalan "ВНУТРЕННИЕ БОЛЕЗНИ" monografiyasi "Дерматология — руководство"
# ichida ham bor). Parcha BIRINCHI ko'rilgan manbaga yoziladi, shuning uchun
# alohida monografiyalar yig'ma to'plamlardan oldin turadi — aks holda kitob
# o'z nomi ostida deyarli bo'sh ko'rinadi.
PRIORITY_ORDER = [
    "internal_skin_ru",
    "eczema_mono_ru",
    "anogenital_ru",
    "dermatoscopy_ru",
    "nash_atlas_ru",
    "atlas_book_ru",
    "derm_guide_ru",
]


def _order_key(name):
    return (PRIORITY_ORDER.index(name) if name in PRIORITY_ORDER else len(PRIORITY_ORDER), name)


def cache_dir() -> Path:
    d = Path(kb_dir()) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm_key(text: str) -> str:
    t = re.sub(r"[^0-9a-zа-яё]+", "", (text or "").lower())
    return hashlib.sha1(t[:600].encode("utf-8")).hexdigest()[:20]


MIN_FILE_CHARS = 120  # butun fayl bitta parcha bo'lib qolishi uchun eng kam hajm


def collect_source_files(src_dir: Path) -> list[Path]:
    """Word vaqtinchalik fayllari (~$...) tashlanadi, qolgani tartib bilan."""
    return sorted(
        p
        for p in src_dir.rglob("*.txt")
        if p.is_file() and p.stat().st_size > 200 and not p.name.startswith("~$")
    )


def source_sha(files: list[Path]) -> str:
    h = hashlib.sha256()
    for p in files:
        st = p.stat()
        h.update(p.name.encode("utf-8", "replace"))
        h.update(str(st.st_size).encode())
    return h.hexdigest()[:16]


def build_chunks(source: str, files: list[Path], seen: set[str], stats: dict) -> list[dict]:
    """Har fayl alohida hujjat — parchalar fayllar orasida aralashmaydi."""
    out: list[dict] = []
    dup = 0
    for p in files:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"    o'qib bo'lmadi {p.name}: {e}")
            continue
        title = p.name
        for suf in (".txt", ".doc", ".docx", ".pdf"):
            if title.lower().endswith(suf):
                title = title[: -len(suf)]
        # Sahifa raqami o'rniga hujjat ichidagi ketma-ketlik
        pages = [(i + 1, part) for i, part in enumerate(_split_pages(raw))]
        made = [c for c in chunk_pages(pages, source) if len(c["text"]) >= MIN_CHUNK_CHARS]
        if not made:
            # "Липоидный некробиоз", "Узловатая эритема" kabi qisqa izoh fayllari:
            # chunk_pages ularni tashlaydi, lekin kasallik nomi va tavsifi qimmatli.
            body = " ".join(raw.split()).strip()
            if len(body) >= MIN_FILE_CHARS:
                made = [{"source": source, "page": 1, "text": body}]
        for ch in made:
            key = _norm_key(ch["text"])
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            ch["title"] = title
            out.append(ch)
    stats[source] = {"files": len(files), "chunks": len(out), "duplicates": dup}
    return out


def _split_pages(raw: str, size: int = 3000) -> list[str]:
    """Uzun matnni sahifa-o'lchamli bo'laklarga bo'lish (chunk_pages uchun)."""
    raw = raw.strip()
    if len(raw) <= size:
        return [raw]
    parts, buf = [], []
    n = 0
    for para in raw.split("\n"):
        buf.append(para)
        n += len(para) + 1
        if n >= size:
            parts.append("\n".join(buf))
            buf, n = [], 0
    if buf:
        parts.append("\n".join(buf))
    return parts


def load_cache(source: str, sha: str):
    emb_p = cache_dir() / f"txt_{source}_{sha}.npy"
    ch_p = cache_dir() / f"txt_{source}_{sha}.jsonl"
    if not (emb_p.is_file() and ch_p.is_file()):
        return None, None
    emb = np.load(emb_p)
    chunks = [json.loads(l) for l in ch_p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if emb.ndim != 2 or emb.shape[0] != len(chunks):
        return None, None
    return emb, chunks


def save_cache(source: str, sha: str, chunks, emb):
    emb_p = cache_dir() / f"txt_{source}_{sha}.npy"
    ch_p = cache_dir() / f"txt_{source}_{sha}.jsonl"
    np.save(emb_p, emb.astype(np.float32))
    with ch_p.open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")


def known_vectors(source: str) -> dict[str, np.ndarray]:
    """Shu manbaning oldingi kesh fayllaridan matn -> vektor lug'ati.

    Kesh manba bo'yicha emas, PARCHA bo'yicha ishlaydi: kitobga bitta yangi
    bo'lim qo'shilsa ham qolgan 11 ming parcha qayta embed qilinmaydi.
    """
    store: dict[str, np.ndarray] = {}
    for ch_p in sorted(cache_dir().glob(f"txt_{source}_*.jsonl")):
        emb_p = ch_p.with_suffix(".npy")
        if not emb_p.is_file():
            continue
        try:
            emb = np.load(emb_p)
            rows = [
                json.loads(line)
                for line in ch_p.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except Exception:
            continue
        if emb.ndim != 2 or emb.shape[0] != len(rows):
            continue
        for i, row in enumerate(rows):
            store.setdefault(_text_key(row.get("text") or ""), emb[i])
    return store


def _text_key(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:24]


def embed_chunks(chunks, source: str):
    store = known_vectors(source)
    keys = [_text_key(ch["text"]) for ch in chunks]
    missing = [i for i, k in enumerate(keys) if k not in store]
    if store:
        print(f"      keshdan {len(chunks) - len(missing)}/{len(chunks)} parcha")
    if missing:
        texts = [source_prefix(source) + chunks[i]["text"] for i in missing]
        step = 256
        done = 0
        for i in range(0, len(texts), step):
            vecs = embed_texts(texts[i : i + step])
            for j, v in enumerate(vecs):
                store[keys[missing[i + j]]] = np.asarray(v, dtype=np.float32)
            done = min(i + step, len(texts))
            if done % 2048 < step or done == len(texts):
                print(f"      embed {done}/{len(texts)}")
    if not chunks:
        return np.zeros((0, 1536), dtype=np.float32)
    return np.vstack([store[k] for k in keys]).astype(np.float32)


def load_index():
    d = Path(kb_dir())
    emb_p, ch_p = d / "embeddings.npy", d / "chunks.jsonl"
    if not (emb_p.is_file() and ch_p.is_file()):
        return [], None
    emb = np.load(emb_p)
    chunks = [json.loads(l) for l in ch_p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if emb.ndim != 2 or emb.shape[0] != len(chunks):
        print("DIQQAT: mavjud indeks nomuvofiq — qaytadan quriladi")
        return [], None
    return chunks, emb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-dir", default="D:/AILab/_kb_text")
    ap.add_argument("--only", action="append", default=[], help="faqat shu manba(lar)")
    ap.add_argument("--plan", action="store_true", help="hisoblab ko'rsatadi, embed qilmaydi")
    args = ap.parse_args()

    base = Path(args.text_dir)
    if not base.is_dir():
        print(f"Papka yo'q: {base}")
        return 2

    dirs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: _order_key(p.name))
    if args.only:
        dirs = [p for p in dirs if p.name in set(args.only)]

    unknown = [p.name for p in dirs if p.name not in SOURCES]
    if unknown:
        print("DIQQAT: SOURCES da yo'q manbalar tashlab ketildi:", ", ".join(unknown))
        dirs = [p for p in dirs if p.name in SOURCES]
    if not dirs:
        print("Ishlov beriladigan manba topilmadi.")
        return 2

    seen: set[str] = set()
    stats: dict = {}
    plan = []
    for d in dirs:
        files = collect_source_files(d)
        chunks = build_chunks(d.name, files, seen, stats)
        n_chars = sum(len(c["text"]) for c in chunks)
        plan.append((d.name, files, chunks))
        print(
            f"{d.name}: {len(files)} fayl → {len(chunks)} parcha "
            f"(takror {stats[d.name]['duplicates']}), "
            f"{n_chars/1e6:.2f} mln belgi  [{source_label(d.name)}]"
        )

    total_chunks = sum(len(c) for _, _, c in plan)
    total_chars = sum(len(ch['text']) for _, _, c in plan for ch in c)
    print(f"\nJAMI yangi: {total_chunks} parcha, {total_chars/1e6:.2f} mln belgi")
    print(f"Taxminiy embedding narxi: ~${total_chars/3/1e6*0.02:.2f}")
    if args.plan:
        return 0

    embedded = []
    for source, files, chunks in plan:
        if not chunks:
            continue
        sha = source_sha(files)
        emb, cached = load_cache(source, sha)
        if emb is not None and len(cached) == len(chunks):
            print(f"  {source}: keshdan ({len(cached)} parcha)")
            embedded.append((source, cached, emb))
            continue
        print(f"  {source}: embedding ({len(chunks)} parcha)…")
        emb = embed_chunks(chunks, source)
        save_cache(source, sha, chunks, emb)
        embedded.append((source, chunks, emb))

    old_chunks, old_emb = load_index()
    replace = {s for s, _, _ in embedded}
    keep_idx = [i for i, c in enumerate(old_chunks) if (c.get("source") or "") not in replace]
    out_chunks = []
    for i in keep_idx:
        row = dict(old_chunks[i])
        row.pop("id", None)
        out_chunks.append(row)
    mats = [old_emb[keep_idx]] if (old_emb is not None and keep_idx) else []
    for source, chunks, emb in embedded:
        out_chunks.extend(chunks)
        mats.append(emb)
    if not mats:
        print("Hech narsa yozilmadi.")
        return 1
    all_emb = np.vstack(mats)
    assert all_emb.shape[0] == len(out_chunks), (all_emb.shape, len(out_chunks))

    by_source: dict[str, int] = {}
    for c in out_chunks:
        k = c.get("source") or "?"
        by_source[k] = by_source.get(k, 0) + 1

    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "embedding_model": os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        "n_chunks": len(out_chunks),
        "by_source": by_source,
        "clinic_library_added": sorted(replace),
        "clinic_library": stats,
    }
    save_index(out_chunks, all_emb, meta)
    print("\nIndeks yangilandi:", kb_dir())
    st = index_stats()
    print(
        f"  jami {st['chunks']} parcha, teri {st.get('skin_chunks')}, "
        f"klinika {st.get('clinic_chunks')}"
    )
    for k, v in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"    {k:20s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
