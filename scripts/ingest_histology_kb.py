#!/usr/bin/env python3
"""Gistologiya kitoblarini vektor indeksiga o'qitish (jamoa LIS, gitga PDF yozilmaydi).

Misol:
  python scripts/ingest_histology_kb.py --pdf-dir "C:\\Users\\...\\4. Gistologiya va biologiya"
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env", override=True)

from lab_core.histology_kb import kb_dir, detect_source, embed_texts, extract_pdf_pages, chunk_pages, save_index


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()[:16]


def collect_pdfs(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix.lower() == ".pdf":
            out.append(p)
        elif p.is_dir():
            out.extend(sorted(p.rglob("*.pdf")))
    # unique by resolved path
    seen = set()
    uniq = []
    for p in out:
        r = p.resolve()
        if r in seen:
            continue
        seen.add(r)
        uniq.append(p)
    return uniq


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="append", default=[], help="PDF yo'li (takrorlash mumkin)")
    parser.add_argument("--pdf-dir", action="append", default=[], help="PDF papka")
    args = parser.parse_args()

    defaults = [
        Path(
            r"C:\Users\alocomputers\Downloads\Telegram Desktop"
            r"\4. Gistologiya va biologiya\4. Gistologiya va biologiya"
        )
    ]
    raw = [Path(x) for x in (args.pdf + args.pdf_dir)] or defaults
    pdfs = collect_pdfs(raw)
    if not pdfs:
        print("PDF topilmadi", file=sys.stderr)
        return 2

    print(f"PDF: {len(pdfs)} ta")
    all_chunks = []
    books = []
    t0 = time.time()
    for pdf in pdfs:
        src = detect_source(pdf.name)
        print(f"extract {src}: {pdf.name}")
        pages = extract_pdf_pages(str(pdf))
        chunks = chunk_pages(pages, src)
        print(f"  pages={len(pages)} chunks={len(chunks)}")
        all_chunks.extend(chunks)
        books.append(
            {
                "file": pdf.name,
                "source": src,
                "pages": len(pages),
                "chunks": len(chunks),
                "sha256_16": _sha256(pdf),
            }
        )

    if not all_chunks:
        print("Matn chiqmadi", file=sys.stderr)
        return 3

    max_chunks = int(os.environ.get("HISTOLOGY_KB_MAX_CHUNKS") or "10000")
    if len(all_chunks) > max_chunks:
        # Avvalo Junqueira/Langman, keyin MBOC ning uzunroq parchalari
        pri = {"junqueira": 0, "langman": 1, "mboc": 2}
        all_chunks.sort(key=lambda c: (pri.get(c["source"], 9), -len(c.get("text") or "")))
        all_chunks = all_chunks[:max_chunks]
        print(f"cap chunks={max_chunks}")

    print(f"embed n={len(all_chunks)} …")
    texts = []
    for ch in all_chunks:
        prefix = {
            "junqueira": "Junqueira histology. ",
            "langman": "Langman embryology. ",
            "mboc": "Alberts cell biology. ",
        }.get(ch["source"], "")
        texts.append(prefix + ch["text"])
    emb = embed_texts(texts)
    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "embedding_model": os.environ.get("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small",
        "n_chunks": len(all_chunks),
        "books": books,
        "kb_dir": str(kb_dir()),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    save_index(all_chunks, emb, meta)
    print(f"saved {kb_dir()} chunks={len(all_chunks)} dim={emb.shape[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
