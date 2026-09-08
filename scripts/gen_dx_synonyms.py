#!/usr/bin/env python3
"""dx_list.js dan backend uchun sinonimlar jadvalini yaratish.

Ro'yxat frontendda yozilgan (foydalanuvchi ko'radigan joy) — backend esa
o'sha nomlarni ruscha yorliqlar bilan solishtirishi kerak (atlas papkalari
rus tilida). Ikki nusxa saqlamaslik uchun jadval shu fayldan generatsiya
qilinadi.

    python scripts/gen_dx_synonyms.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "static" / "js" / "dx_list.js"
DST = ROOT / "backend" / "lab_core" / "dx_synonyms.py"

ROW = re.compile(r'\[\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\]')


def main() -> int:
    text = SRC.read_text(encoding="utf-8")
    rows = []
    for m in ROW.finditer(text):
        uz, ru, group = (ast.literal_eval('"%s"' % g) for g in m.groups())
        rows.append((uz, ru, group))
    if not rows:
        print("dx_list.js dan yozuv topilmadi")
        return 1

    lines = [
        '"""Klinik tashxis sinonimlari — dx_list.js dan generatsiya qilingan.',
        "",
        "Qo'lda tahrirlamang: scripts/gen_dx_synonyms.py ni qayta ishga tushiring.",
        'Har yozuv: (o\'zbekcha/lotincha nom, ruscha sinonim, guruh).',
        '"""',
        "",
        "DX_SYNONYMS = (",
    ]
    for uz, ru, group in rows:
        lines.append(f"    ({uz!r}, {ru!r}, {group!r}),")
    lines.append(")")
    lines.append("")
    DST.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(rows)} ta yozuv → {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
