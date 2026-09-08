"""Klinika kitoblaridagi ma'lumotnoma rasmlar (atlas).

Kitob arxivlarida rasmlar kasallik papkalari ichida yotadi
("АТЛАС/Базалиома/*.jpg") — ya'ni ular allaqachon tashxis bo'yicha
belgilangan. `scripts/build_atlas_images.py` shu yorliqlardan to'plam
quradi; bu modul tahlil paytida mos rasmlarni topib beradi.

Nima uchun: matn mezoni "periferik palisad" deydi, lekin uni KO'RISH boshqa
narsa. Shifokor kesmani atlasdagi rasm bilan solishtiradi — dastur ham shuni
qila olishi kerak.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading

log = logging.getLogger("medlab")

_lock = threading.Lock()
_cache = {"mtime": None, "rows": None}


def atlas_dir():
    from .histology_kb import kb_dir

    return os.environ.get("HISTOLOGY_ATLAS_DIR") or os.path.join(kb_dir(), "atlas")


def _index_path():
    return os.path.join(atlas_dir(), "atlas.json")


def atlas_ready():
    return os.path.isfile(_index_path())


def _load():
    p = _index_path()
    if not os.path.isfile(p):
        return []
    mtime = os.path.getmtime(p)
    with _lock:
        if _cache["rows"] is not None and _cache["mtime"] == mtime:
            return _cache["rows"]
        try:
            with open(p, "r", encoding="utf-8") as f:
                rows = (json.load(f) or {}).get("images") or []
        except Exception as e:
            log.warning("atlas: indeks o'qilmadi: %s", e)
            return []
        _cache["rows"] = rows
        _cache["mtime"] = mtime
        log.info(
            "atlas: %s rasm, %s yorliq",
            len(rows),
            len({r.get("slug") for r in rows}),
        )
        return rows


def atlas_stats():
    rows = _load()
    return {
        "ready": bool(rows),
        "images": len(rows),
        "labels": len({r.get("slug") for r in rows}),
        "slides": sum(1 for r in rows if r.get("kind") == "slide"),
    }


# ─── Nomlarni solishtirish ────────────────────────────────────────────────────
_WORD = re.compile(r"[a-zа-яё]{4,}", re.I)
_STOP = {
    "kasallik", "teri", "kozhi", "skin", "болезнь", "кожи", "кожа", "форма",
    "вариант", "тип", "фото", "подписи", "рисункам", "рисунки",
}


def _tokens(text):
    return {w.lower() for w in _WORD.findall(str(text or "").lower())} - _STOP


def _synonym_terms(name):
    """Nom uchun qidiruv atamalari: o'zi + ruscha sinonimi."""
    out = {str(name or "").strip()}
    try:
        from .dx_synonyms import DX_SYNONYMS
    except Exception:
        return {t for t in out if t}
    low = str(name or "").strip().lower()
    for uz, ru, _g in DX_SYNONYMS:
        u, r = uz.lower(), ru.lower()
        if low and (low in u or u in low or low in r or r in low):
            out.add(uz)
            out.add(ru)
    return {t for t in out if t}


def _score_label(query_tokens, label):
    lt = _tokens(label)
    if not lt or not query_tokens:
        return 0.0
    inter = query_tokens & lt
    if not inter:
        # qisman moslik: "базалиом" ⊂ "базалиома"
        for q in query_tokens:
            for l in lt:
                if len(q) >= 6 and (q in l or l in q):
                    return 0.55
        return 0.0
    return len(inter) / max(1, min(len(query_tokens), len(lt)))


def find_reference_images(names, max_slides=2, max_clinical=1, min_score=0.5):
    """Berilgan tashxis nomlariga mos ma'lumotnoma rasmlar.

    names: ro'yxat (klinik gipoteza, qoralamadagi tashxis va h.k.)
    Natija: [{"label", "kind", "path"}] — eng mos yorliqdan.
    """
    rows = _load()
    if not rows or not names:
        return []
    terms = set()
    for n in names:
        terms |= _synonym_terms(n)
    q = set()
    for t in terms:
        q |= _tokens(t)
    if not q:
        return []

    best_label, best_score = None, 0.0
    labels = {}
    for r in rows:
        labels.setdefault(r.get("label") or "", []).append(r)
    for label in labels:
        sc = _score_label(q, label)
        if sc > best_score:
            best_label, best_score = label, sc
    if not best_label or best_score < min_score:
        return []

    picked = []
    group = labels[best_label]
    for kind, cap in (("slide", max_slides), ("clinical", max_clinical)):
        for r in [x for x in group if x.get("kind") == kind][:cap]:
            path = os.path.join(atlas_dir(), (r.get("file") or "").replace("/", os.sep))
            if os.path.isfile(path):
                picked.append({"label": best_label, "kind": kind, "path": path})
    if picked:
        log.info(
            "atlas: «%s» uchun %s ta ma'lumotnoma rasm (moslik %.2f)",
            best_label, len(picked), best_score,
        )
    return picked


def image_parts(refs, detail="low"):
    """Ma'lumotnoma rasmlarni vision so'roviga tayyorlash."""
    parts = []
    for r in refs:
        try:
            with open(r["path"], "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except Exception:
            continue
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + b64, "detail": detail},
            }
        )
    return parts


def reference_block(refs):
    """Promptga qo'shiladigan izoh matni."""
    if not refs:
        return ""
    label = refs[0]["label"]
    kinds = ", ".join(
        f"{sum(1 for r in refs if r['kind'] == k)} ta {'kesma' if k == 'slide' else 'klinik'}"
        for k in ("slide", "clinical")
        if any(r["kind"] == k for r in refs)
    )
    return (
        "### MA'LUMOTNOMA RASMLAR — klinika atlasidan\n"
        f"Oxirgi {kinds} rasm «{label}» yorlig'i bilan klinika kitobidan olingan "
        "(bemor kesmasi EMAS).\n"
        "Ular bilan bemor kesmasini SOLISHTIR: qaysi belgilar mos keladi, "
        "qaysilari kelmaydi — buni asosda ayt.\n"
        "MUHIM: bu tasdiq emas. Bemor kesmasi ma'lumotnomaga o'xshamasa, "
        "shuni ochiq yoz va o'z xulosangni bemor kesmasidan chiqar.\n"
    )
