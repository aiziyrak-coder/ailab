#!/usr/bin/env python3
"""Skanerlangan PDF kitoblarni matnga aylantirish (vision OCR) va _kb_text ga yozish.

Matn qatlami bor PDF to'g'ridan-to'g'ri o'qiladi, yo'q bo'lsa sahifama-sahifa
OCR qilinadi. OCR natijasi diskda keshlanadi — qayta ishga tushirish bepul.

Misol:
    python scripts/ocr_books_to_text.py --map "C:/.../kitob.pdf=roeken_atlas_ru"
    python scripts/ocr_books_to_text.py --plan --map "..."
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402

for _p in (
    Path(os.environ["MEDLAB_ENV_FILE"]) if os.environ.get("MEDLAB_ENV_FILE") else None,
    BACKEND / ".env",
    ROOT / ".env",
):
    if _p and _p.is_file():
        load_dotenv(_p, override=True)
        break

from lab_core.histology_kb import SOURCES, extract_pdf_pages  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ingest_histology_kb", ROOT / "scripts" / "ingest_histology_kb.py"
)
_ing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ing)

OUT_BASE = Path(os.environ.get("KB_TEXT_DIR") or "D:/AILab/_kb_text")
MIN_WORDS_PER_PAGE = 25


def _page_has_text(text: str) -> bool:
    return len(re.findall(r"[A-Za-zА-Яа-я]{3,}", text or "")) >= MIN_WORDS_PER_PAGE


def scanned_ratio(pages) -> float:
    if not pages:
        return 1.0
    good = sum(1 for _, t in pages if _page_has_text(t))
    return 1.0 - good / len(pages)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", action="append", required=True,
                    help="PDF yo'li=manba_kodi (bir necha marta berilishi mumkin)")
    ap.add_argument("--plan", action="store_true", help="faqat hisob, OCR qilinmaydi")
    ap.add_argument("--ocr-model", default=os.environ.get("KB_OCR_MODEL", "gpt-4o-mini"))
    ap.add_argument("--ocr-dpi", type=int, default=int(os.environ.get("KB_OCR_DPI", "170")))
    ap.add_argument("--ocr-workers", type=int, default=int(os.environ.get("KB_OCR_WORKERS", "6")))
    ap.add_argument("--limit", type=int, default=0, help="sinov uchun sahifa chegarasi")
    args = ap.parse_args()

    jobs = []
    for item in args.map:
        if "=" not in item:
            print(f"Noto'g'ri --map: {item}")
            return 2
        path, source = item.rsplit("=", 1)
        pdf = Path(path.strip().strip('"'))
        source = source.strip()
        if not pdf.is_file():
            print(f"Fayl yo'q: {pdf}")
            return 2
        if source not in SOURCES:
            print(f"SOURCES da yo'q manba: {source}")
            return 2
        jobs.append((pdf, source))

    total_ocr_pages = 0
    plans = []
    for pdf, source in jobs:
        pages = extract_pdf_pages(str(pdf))
        ratio = scanned_ratio(pages)
        need_ocr = ratio > 0.7
        n_ocr = len(pages) if need_ocr else 0
        total_ocr_pages += n_ocr
        plans.append((pdf, source, pages, need_ocr))
        print(
            f"{source:20s} {len(pages):4d} sahifa  "
            f"{'OCR kerak' if need_ocr else 'matn qatlami bor'}  {pdf.name}"
        )

    if total_ocr_pages:
        print(f"\nOCR qilinadigan sahifa: {total_ocr_pages} ({args.ocr_model}, {args.ocr_dpi} dpi)")
    if args.plan:
        return 0

    for pdf, source, pages, need_ocr in plans:
        out_dir = OUT_BASE / source
        out_dir.mkdir(parents=True, exist_ok=True)
        out_f = out_dir / (pdf.stem + ".pdf.txt")

        if need_ocr:
            print(f"\n{source}: OCR — {pdf.name}")
            pages = _ing.ocr_pdf_pages(
                pdf, source, args.ocr_dpi, args.ocr_workers, args.ocr_model, args.limit
            )
        body = []
        for _, text in pages:
            text = (text or "").strip()
            if text:
                body.append(text)
        joined = re.sub(r"\n{3,}", "\n\n", "\n\n".join(body)).strip()
        out_f.write_text(joined, encoding="utf-8")
        print(f"{source}: {len(joined)/1e6:.2f} mln belgi → {out_f}")

    print("\nTayyor. Endi: python scripts/ingest_book_texts.py --text-dir", OUT_BASE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
