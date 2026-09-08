"""Tahlil qilingan keyslarni saqlash — xatolarni keyin ko'rib chiqish uchun.

MUAMMO (audit topgan). Rasmlar xotiraga olinadi, tahlil qilinadi va
tashlab yuboriladi. Ya'ni:
  — noto'g'ri tashxis qo'yilgan keysni qayta ochib bo'lmaydi,
  — algoritm yaxshilangach eski keyslarni qayta ishga tushirib bo'lmaydi,
  — «shu 20 ta keysda adashdik» deb patologga ko'rsatib bo'lmaydi.
O'lchanmagan narsani yaxshilab bo'lmaydi, saqlanmagan narsani esa
o'lchab bo'lmaydi.

MAXFIYLIK. Bu bemor materiali. Shuning uchun:
  — arxiv IXTIYORIY: CASE_ARCHIVE=1 qo'yilmasa, hech narsa yozilmaydi;
  — hammasi klinikaning O'Z serverida qoladi, tashqariga chiqmaydi;
  — bemor ismi va palata yozilmaydi — faqat namuna raqami va morfologiya;
  — eski keyslar CASE_ARCHIVE_DAYS (default 180) dan keyin o'chiriladi.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta

log = logging.getLogger("medlab")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Arxivga TUSHMAYDIGAN maydonlar: bemorni shaxsan aniqlaydiganlari
_PRIVATE_FIELDS = ("patient_name", "ward", "region", "locality")

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def enabled():
    return (os.environ.get("CASE_ARCHIVE") or "0").strip().lower() in ("1", "true", "yes", "on")


def archive_dir():
    return os.environ.get("CASE_ARCHIVE_DIR") or os.path.join(_BASE, "data", "cases")


def _retention_days():
    try:
        return max(1, int(os.environ.get("CASE_ARCHIVE_DAYS") or 180))
    except (TypeError, ValueError):
        return 180


def _case_name(patient_context):
    sid = _SAFE.sub("", str((patient_context or {}).get("sample_id") or "")).strip("-_.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{sid}" if sid else stamp


def _public_context(patient_context):
    """Bemorni aniqlaydigan maydonlarsiz klinik kontekst."""
    ctx = dict(patient_context or {})
    for k in _PRIVATE_FIELDS:
        ctx.pop(k, None)
    return ctx


def save(images, report, features=None, record=None, patient_context=None,
         clinical_images=None, lab_type="histology"):
    """Bitta keysni diskka yozish. Xato bo'lsa tahlilga xalaqit bermaydi."""
    if not enabled():
        return ""
    try:
        root = os.path.join(archive_dir(), time.strftime("%Y-%m"), _case_name(patient_context))
        os.makedirs(root, exist_ok=True)

        for i, img in enumerate(images or [], 1):
            try:
                img.save(os.path.join(root, f"kesma_{i:02d}.jpg"), "JPEG", quality=88)
            except Exception:
                pass
        for i, img in enumerate(clinical_images or [], 1):
            try:
                img.save(os.path.join(root, f"klinik_{i:02d}.jpg"), "JPEG", quality=85)
            except Exception:
                pass

        meta = {
            "saqlandi": datetime.now().isoformat(timespec="seconds"),
            "lab_type": lab_type,
            "kontekst": _public_context(patient_context),
            "kesma_soni": len(images or []),
            "klinik_soni": len(clinical_images or []),
        }
        if isinstance(record, dict):
            meta["tashxis"] = record
        _write(os.path.join(root, "keys.json"), json.dumps(meta, ensure_ascii=False, indent=1))
        if isinstance(features, dict):
            _write(os.path.join(root, "korik.json"),
                   json.dumps(features, ensure_ascii=False, indent=1))
        if report:
            _write(os.path.join(root, "hisobot.md"), report)

        log.info("keys arxivi: %s", root)
        _prune()
        return root
    except Exception as e:
        log.warning("keys arxivi yozilmadi: %s", e)
        return ""


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _prune():
    """Saqlash muddati o'tgan oylik papkalarni o'chirish."""
    root = archive_dir()
    if not os.path.isdir(root):
        return
    cutoff = datetime.now() - timedelta(days=_retention_days())
    keep_from = cutoff.strftime("%Y-%m")
    for name in os.listdir(root):
        if not re.fullmatch(r"\d{4}-\d{2}", name) or name >= keep_from:
            continue
        try:
            shutil.rmtree(os.path.join(root, name))
            log.info("keys arxivi: %s oyi muddati o'tdi — o'chirildi", name)
        except Exception as e:
            log.warning("keys arxivi: %s o'chmadi: %s", name, e)


def stats():
    root = archive_dir()
    if not enabled():
        return {"enabled": False, "cases": 0}
    n = 0
    if os.path.isdir(root):
        for month in os.listdir(root):
            p = os.path.join(root, month)
            if os.path.isdir(p):
                n += sum(1 for d in os.listdir(p) if os.path.isdir(os.path.join(p, d)))
    return {"enabled": True, "cases": n, "dir": root, "days": _retention_days()}
