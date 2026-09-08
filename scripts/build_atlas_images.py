#!/usr/bin/env python3
"""Kitoblardagi rasmlardan ma'lumotnoma atlas yig'ish.

Arxivlardagi rasmlar kasallik papkalari ichida yotadi (masalan
"АТЛАС/Базалиома/*.jpg") — ya'ni ular allaqachon tashxis bo'yicha belgilangan.
Shu bepul yorliqdan foydalanib, tahlil paytida solishtirish uchun ma'lumotnoma
to'plami quriladi.

Har rasm:
  * kesmami yoki klinik suratmi — rang statistikasi bo'yicha aniqlanadi
  * 768px gacha kichraytiriladi (server va so'rov hajmi uchun)
  * kasallik yorlig'i bilan atlas.json ga yoziladi

Misol:
    python scripts/build_atlas_images.py --plan
    python scripts/build_atlas_images.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from PIL import Image  # noqa: E402

from lab_core.engine import SLIDE_SCORE_MIN, _slide_score  # noqa: E402
from lab_core.histology_kb import kb_dir  # noqa: E402

SEVEN = r"C:\Program Files\7-Zip\7z.exe"
BOOKS = Path("C:/Users/alocomputers/Desktop/Kitoblar")
RAW = Path("D:/AILab/_kb_img_raw")
IMG_EXT = (".jpg", ".jpeg", ".png")

MAX_SIDE = 768
MAX_SLIDES_PER_LABEL = 3
MAX_CLINICAL_PER_LABEL = 2

# Yorliq bo'lolmaydigan papkalar
SKIP_DIR = re.compile(
    r"^(фото|photo|рисун|подписи|images?|pict|new|новая|книга|атлас|"
    r"глава|раздел|\d+$)",
    re.I,
)


def slug(text):
    t = unicodedata.normalize("NFKD", str(text or "").strip().lower())
    t = re.sub(r"[^0-9a-zA-Zа-яё\s-]", "", t)
    t = re.sub(r"\s+", "-", t).strip("-")
    return t[:60] or "boshqa"


def clean_label(name):
    """Papka nomidan kasallik yorlig'i."""
    t = re.sub(r"\s*\(\d+\)\s*$", "", str(name or "").strip())
    t = re.sub(r"^\d+[.\s]*", "", t)
    t = re.sub(r"\s+(фото|photo)$", "", t, flags=re.I)
    return " ".join(t.split())[:70]


def extract(plan=False):
    RAW.mkdir(parents=True, exist_ok=True)
    for arc in sorted(BOOKS.iterdir()):
        if arc.suffix.lower() not in (".zip", ".rar"):
            continue
        dest = RAW / arc.stem
        if dest.exists() and any(dest.rglob("*.jpg")):
            print(f"  {arc.name}: allaqachon chiqarilgan")
            continue
        if plan:
            print(f"  {arc.name}: chiqariladi")
            continue
        print(f"  {arc.name}: rasm chiqarilmoqda…")
        subprocess.run(
            [SEVEN, "x", "-y", f"-o{dest}", str(arc), "*.jpg", "*.jpeg", "*.png", "-r"],
            capture_output=True,
        )


def collect():
    """[(yorliq, yo'l)] — papka nomi kasallik yorlig'i bo'ladiganlari."""
    rows = []
    for p in RAW.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in IMG_EXT:
            continue
        parent = p.parent.name
        if SKIP_DIR.match(parent) or len(parent) < 4:
            # bir pog'ona yuqoriga qaraymiz
            parent = p.parent.parent.name if p.parent.parent != RAW else ""
            if not parent or SKIP_DIR.match(parent) or len(parent) < 4:
                continue
        label = clean_label(parent)
        if len(label) < 4:
            continue
        rows.append((label, p))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--out", default=str(Path(kb_dir()) / "atlas"))
    args = ap.parse_args()

    print("1) Arxivlardan rasm chiqarish")
    extract(plan=args.plan)
    if args.plan and not RAW.exists():
        print("   (--plan: chiqarilmadi)")
        return 0

    print("2) Yorliqlarni yig'ish")
    rows = collect()
    by_label = {}
    for label, path in rows:
        by_label.setdefault(label, []).append(path)
    print(f"   {len(rows)} rasm, {len(by_label)} yorliq")
    if args.plan:
        for lab in sorted(by_label, key=lambda k: -len(by_label[k]))[:25]:
            print(f"     {len(by_label[lab]):4d}  {lab}")
        return 0

    out_dir = Path(args.out)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("3) Saralash va kichraytirish")
    index = []
    for i, (label, paths) in enumerate(sorted(by_label.items()), 1):
        scored = []
        for p in paths[:40]:  # bir yorliqdan ko'pi bilan 40 tasini baholaymiz
            try:
                with Image.open(p) as im:
                    im.load()
                    sc = _slide_score(im)
                    scored.append((sc, p))
            except Exception:
                continue
        slides = sorted([x for x in scored if x[0] >= SLIDE_SCORE_MIN], reverse=True)
        clinical = sorted([x for x in scored if x[0] < SLIDE_SCORE_MIN], reverse=True)
        picked = (
            [(p, "slide") for _, p in slides[:MAX_SLIDES_PER_LABEL]]
            + [(p, "clinical") for _, p in clinical[:MAX_CLINICAL_PER_LABEL]]
        )
        if not picked:
            continue
        sub = out_dir / slug(label)
        sub.mkdir(parents=True, exist_ok=True)
        for n, (src, kind) in enumerate(picked, 1):
            dst = sub / f"{kind}_{n}.jpg"
            try:
                with Image.open(src) as im:
                    im = im.convert("RGB")
                    im.thumbnail((MAX_SIDE, MAX_SIDE))
                    im.save(dst, "JPEG", quality=82, optimize=True)
            except Exception:
                continue
            index.append(
                {
                    "label": label,
                    "slug": slug(label),
                    "kind": kind,
                    "file": str(dst.relative_to(out_dir)).replace("\\", "/"),
                }
            )
        if i % 25 == 0:
            print(f"   {i}/{len(by_label)} yorliq")

    (out_dir / "atlas.json").write_text(
        json.dumps({"images": index}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    size = sum(f.stat().st_size for f in out_dir.rglob("*.jpg"))
    n_slide = sum(1 for r in index if r["kind"] == "slide")
    print(
        f"\nTayyor: {len(index)} rasm ({n_slide} kesma), "
        f"{len(by_label)} yorliq, {size/1e6:.1f} MB → {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
