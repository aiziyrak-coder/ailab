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


# Yorliqlar rus tilida, so'rov esa lotinchada keladi («базалиома» va
# «Bazalioma» — bir xil kasallik). Ilgari ular faqat sinonim jadvalida
# yozilgan bo'lsa uchrashardi, ya'ni jadvalga kirmagan 200 dan ortiq yorliq
# hech qachon topilmasdi. Endi ikkala yozuv bir o'zakka keltiriladi.
_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# Lotin imlosidagi tibbiy atamalar ham bir o'zakka: "carcinoma"/"karsinoma"
_LAT_FOLD = (
    ("sch", "sh"), ("ch", "x"), ("kh", "x"), ("ph", "f"), ("th", "t"),
    ("ck", "k"), ("cz", "z"), ("c", "k"), ("w", "v"), ("y", "i"), ("j", "i"),
    ("ee", "i"), ("oo", "u"), ("ss", "s"), ("ll", "l"), ("nn", "n"),
)


def _fold(word):
    w = "".join(_CYR2LAT.get(ch, ch) for ch in word.lower())
    for a, b in _LAT_FOLD:
        w = w.replace(a, b)
    return w


def _tokens(text):
    raw = {w.lower() for w in _WORD.findall(str(text or "").lower())} - _STOP
    return {_fold(w) for w in raw if _fold(w)}


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


# Papka nomlari kitobdan olingani uchun ular tashxis emas, bob sarlavhasi
# bo'lishi mumkin («XV. Псориаз»), yoki umuman kasallik nomi emas (kitob
# nomi, «Новая папка», sana). Raqamli prefiks kesiladi, kasallik bo'lmagan
# yorliqlar esa moslashtirishdan chiqariladi — aks holda ma'lumotnoma rasm
# tasodifiy bobdan olinadi.
_LABEL_NUM = re.compile(r"^\s*(?:[IVXLC]+|\d+)\s*[.)]\s*", re.I)
_LABEL_DATE = re.compile(r"\d{2}-\d{2}-\d{4}")
_NOT_DIAGNOSIS = (
    "норма и патология", "гистопатология кожи", "принципы диагностики",
    "внутренние болезни", "руководство до и после", "монография",
    "новая папка", "пороки развития",
)


def clean_label(raw):
    """Papka nomidan tashxis nomi; tashxis bo'lmasa bo'sh satr."""
    s = _LABEL_NUM.sub("", str(raw or "").strip())
    s = re.sub(r"\s+", " ", s).strip()
    low = s.lower()
    if len(s) < 4 or _LABEL_DATE.search(s) or any(b in low for b in _NOT_DIAGNOSIS):
        return ""
    return s


def _pair_score(a, b):
    """Ikki o'zakning yaqinligi 0..1.

    Qisman moslik faqat BOSHIDAN mos kelganda hisobga olinadi. Ilgari oddiy
    ichida-borlik yetardi va «keratoz» ⊂ «porokeratoz» deb hisoblanardi —
    natijada modelga BOSHQA kasallikning rasmi ko'rsatilardi. Noto'g'ri
    ma'lumotnoma rasm rasmsizdan yomonroq.
    """
    if a == b:
        return 1.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 5 and long.startswith(short):
        return len(short) / len(long)
    return 0.0


def _score_label(query_tokens, label):
    lt = _tokens(label)
    if not lt or not query_tokens:
        return 0.0
    total = 0.0
    for q in query_tokens:
        best = max((_pair_score(q, l) for l in lt), default=0.0)
        total += best
    return total / max(1, min(len(query_tokens), len(lt)))


def find_reference_images(names, max_slides=2, max_clinical=1, min_score=0.6):
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
        # Moslashtirish TOZALANGAN nom bo'yicha ketadi, lekin rasmlar asl
        # yorliq ostida guruhlanadi — indeks o'zgarmaydi.
        clean = clean_label(r.get("label"))
        if clean:
            labels.setdefault(clean, []).append(r)
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
