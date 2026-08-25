"""Gistologiya / dermatopatologiya vektor bazasi (RAG).

Manbalar (kitob matni gitga yozilmaydi — faqat indeks):
  Umumiy gistologiya : Junqueira, Langman, Alberts/MBOC
  Dermatopatologiya  : Weedon (3rd ed + Essentials), Diagnosis by First Impression,
                       Vademecum, The Basics, Color Atlas, "8 dermatopathology",
                       Pathology of Vascular Skin Lesions, Genetics of Melanoma,
                       Атлас диагностических биопсий кожи, Дерматология (Цветкова),
                       Дерматоонкопатология

Indeks: backend/data/histology_kb/  (embeddings.npy, chunks.jsonl, meta.json)
Tahlilda faqat qisqa mezon parchalari promptga qo'shiladi (nusxa ko'chirish emas).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

import numpy as np

log = logging.getLogger("medlab")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_lock = threading.Lock()
_cache = {"mtime": None, "emb": None, "chunks": None}


def kb_dir():
    return os.environ.get("HISTOLOGY_KB_DIR") or os.path.join(_BASE, "data", "histology_kb")


def embed_model():
    return (os.environ.get("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small").strip()


def _env_int(name, default, lo, hi):
    try:
        v = int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(v, hi))


MAX_PROMPT_CHARS = _env_int("HISTOLOGY_KB_PROMPT_CHARS", 11000, 2000, 24000)
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 220
TOP_K = _env_int("HISTOLOGY_KB_TOP_K", 14, 3, 40)
PER_SOURCE_MAX = _env_int("HISTOLOGY_KB_PER_SOURCE", 3, 1, 10)

# ─── Manba registri ───────────────────────────────────────────────────────────
# tier: 1 = tashxis uchun asosiy (dermatopatologiya), 2 = umumiy gistologiya kanoni
# domain: skin | general | melanoma | vascular
SOURCES = {
    "weedon": {
        "label": "Weedon's Skin Pathology, 3rd ed (dermatopatologiya etaloni)",
        "domain": "skin",
        "tier": 1,
        "weight": 0.10,
        "prefix": "Weedon skin pathology. ",
    },
    "weedon_essentials": {
        "label": "Weedon's Skin Pathology Essentials (Johnston)",
        "domain": "skin",
        "tier": 1,
        "weight": 0.08,
        "prefix": "Weedon essentials skin pathology. ",
    },
    "first_impression": {
        "label": "Dermatopathology: Diagnosis by First Impression (pattern-tanish)",
        "domain": "skin",
        "tier": 1,
        "weight": 0.07,
        "prefix": "Dermatopathology pattern first impression. ",
    },
    "vademecum": {
        "label": "Dermatopathology Vademecum (Sanchez & Raimer)",
        "domain": "skin",
        "tier": 1,
        "weight": 0.06,
        "prefix": "Dermatopathology vademecum. ",
    },
    "derm_basics": {
        "label": "Dermatopathology: The Basics",
        "domain": "skin",
        "tier": 1,
        "weight": 0.05,
        "prefix": "Dermatopathology basics. ",
    },
    "color_atlas": {
        "label": "Color Atlas of Dermatopathology",
        "domain": "skin",
        "tier": 1,
        "weight": 0.06,
        "prefix": "Color atlas of dermatopathology. ",
    },
    "derm_course": {
        "label": "Dermatopathology (kurs materiali)",
        "domain": "skin",
        "tier": 1,
        "weight": 0.04,
        "prefix": "Dermatopathology course. ",
    },
    "derma_misc": {
        "label": "Dermatologiya (qo'shimcha manba)",
        "domain": "skin",
        "tier": 1,
        "weight": 0.03,
        "prefix": "Dermatology notes. ",
    },
    "vascular_skin": {
        "label": "Pathology of Vascular Skin Lesions (tomir lezyonlari)",
        "domain": "vascular",
        "tier": 1,
        "weight": 0.05,
        "prefix": "Vascular skin lesions pathology. ",
    },
    "melanoma_genetics": {
        "label": "Genetics of Melanoma (melanotsitar molekulyar kontekst)",
        "domain": "melanoma",
        "tier": 1,
        "weight": 0.04,
        "prefix": "Melanoma genetics. ",
    },
    "atlas_biopsy_ru": {
        "label": "Атлас диагностических биопсий кожи (rus)",
        "domain": "skin",
        "tier": 1,
        "weight": 0.06,
        "prefix": "Атлас диагностических биопсий кожи. ",
    },
    "dermatoonko_ru": {
        "label": "Дерматоонкопатология (rus)",
        "domain": "skin",
        "tier": 1,
        "weight": 0.06,
        "prefix": "Дерматоонкопатология. ",
    },
    "tsvetkova_ru": {
        "label": "Дерматология, Цветкова (rus)",
        "domain": "skin",
        "tier": 1,
        "weight": 0.04,
        "prefix": "Дерматология клинико-морфологическая. ",
    },
    "junqueira": {
        "label": "Junqueira Basic Histology (to'qima tipi mezoni)",
        "domain": "general",
        "tier": 2,
        "weight": 0.04,
        "prefix": "Junqueira histology. ",
    },
    "langman": {
        "label": "Langman Medical Embryology (rivojlanish konteksti)",
        "domain": "general",
        "tier": 2,
        "weight": 0.01,
        "prefix": "Langman embryology. ",
    },
    "mboc": {
        "label": "Molecular Biology of the Cell / Alberts (hujayra mezonlari)",
        "domain": "general",
        "tier": 2,
        "weight": 0.0,
        "prefix": "Alberts cell biology. ",
    },
    "histology": {
        "label": "Gistologiya manbasi",
        "domain": "general",
        "tier": 2,
        "weight": 0.0,
        "prefix": "",
    },
}

# Eski indekslar bilan moslik
_SOURCE_LABEL = {k: v["label"] for k, v in SOURCES.items()}

SKIN_SOURCES = frozenset(
    k for k, v in SOURCES.items() if v["domain"] in ("skin", "vascular", "melanoma")
)
GENERAL_SOURCES = frozenset(k for k, v in SOURCES.items() if v["domain"] == "general")


def source_meta(code):
    return SOURCES.get(code) or SOURCES["histology"]


def source_prefix(code):
    return source_meta(code)["prefix"]


def source_label(code):
    return source_meta(code)["label"]


# ─── Fayl nomidan manba aniqlash ──────────────────────────────────────────────
_FILENAME_RULES = (
    ("weedon_essentials", ("essentials",)),
    ("weedon", ("weedon",)),
    ("first_impression", ("first_impression", "first impression")),
    ("vademecum", ("vademecum",)),
    ("derm_basics", ("the basics", "the_basics")),
    ("color_atlas", ("color_atlas", "color atlas")),
    ("vascular_skin", ("vascular",)),
    ("melanoma_genetics", ("genetics of melanoma", "genetics_of_melanoma")),
    ("atlas_biopsy_ru", ("атлас", "биопси")),
    ("dermatoonko_ru", ("дерматоонко", "онкопатолог")),
    ("tsvetkova_ru", ("цветков",)),
    ("junqueira", ("junqueira", "mescher")),
    ("langman", ("langman", "embryology", "sadler")),
    ("mboc", ("molecular", "alberts", "mboc", "cell_seventh")),
    ("derm_course", ("dermatopathology",)),
    ("derma_misc", ("derma",)),
)


def detect_source(filename):
    n = (filename or "").lower()
    for code, keys in _FILENAME_RULES:
        if any(k in n for k in keys):
            return code
    return "histology"


# ─── Organ → qidiruv konteksti ────────────────────────────────────────────────
_ORGAN_EN = {
    "teri": (
        "skin biopsy epidermis dermoepidermal junction papillary reticular dermis subcutis "
        "keratinocyte melanocyte adnexa hair follicle sebaceous eccrine collagen elastic fibers "
        "acanthosis hyperkeratosis parakeratosis spongiosis lichenoid interface granulomatous "
        "panniculitis vasculitis dermatopathology H&E diagnostic criteria differential"
    ),
    "sut_bezi": (
        "mammary gland breast duct lobule myoepithelium intraductal papilloma "
        "terminal duct lobular unit histology H&E"
    ),
    "qovuq": "urinary bladder urothelium umbrella cell transitional epithelium papillary histology",
    "prostata": "prostate gland acinar epithelium corpora amylacea basal cell histology",
    "qalqonsimon": "thyroid follicle colloid papillary nuclear grooves histology",
    "ichak": "gastrointestinal mucosa goblet cell villus crypt gland histology",
    "yumurtalik": "ovary ovarian follicle stroma surface epithelium serous mucinous histology",
    "buyrak": "kidney glomerulus renal tubule cortex medulla histology",
    "endometrium": "endometrium uterine glands stroma proliferative secretory histology",
    "opka": "lung alveolus bronchus respiratory epithelium histology",
    "noaniq": (
        "basic histology tissue type epithelium connective tissue muscle nerve "
        "nucleus chromatin cytoplasm basement membrane H&E"
    ),
}

# Qoralamadagi kalit atamalar → maqsadli qidiruv (dermatopatologiya diqqat markazi)
_DX_TERM_HINTS = (
    ("melanom", "melanoma melanocytic atypia pagetoid spread Breslow Clark mitoses dermal nests"),
    ("melanocyt", "melanocytic nevus junctional compound intradermal maturation atypia"),
    ("nevus", "melanocytic nevus junctional compound intradermal Spitz dysplastic maturation"),
    ("dermatofibrom", "dermatofibroma benign fibrous histiocytoma collagen trapping grenz zone storiform"),
    ("dfsp", "dermatofibrosarcoma protuberans storiform CD34 honeycomb subcutaneous infiltration"),
    ("basal cell", "basal cell carcinoma nodular superficial infiltrative peripheral palisading retraction"),
    ("bcc", "basal cell carcinoma palisading clefting mucinous stroma"),
    ("squamous", "squamous cell carcinoma keratinocyte atypia keratin pearls invasion actinic keratosis"),
    ("scc", "squamous cell carcinoma in situ Bowen disease full thickness atypia"),
    ("keratosis", "seborrheic keratosis actinic keratosis horn cysts basaloid acanthosis solar elastosis"),
    ("verruca", "verruca vulgaris koilocytes papillomatosis hypergranulosis HPV"),
    ("papillom", "squamous papilloma papillomatosis fibrovascular core"),
    ("psoriaz", "psoriasis parakeratosis Munro microabscess regular acanthosis suprapapillary thinning"),
    ("lichen", "lichen planus lichenoid interface band-like infiltrate Civatte bodies sawtooth"),
    ("granulom", "granuloma annulare sarcoidosis necrobiosis palisading granulomatous dermatitis"),
    ("vaskulit", "vasculitis leukocytoclastic fibrinoid necrosis neutrophils vessel wall"),
    ("vasculit", "vasculitis leukocytoclastic fibrinoid necrosis vessel wall damage"),
    ("gemangiom", "hemangioma vascular proliferation lobular capillary pyogenic granuloma"),
    ("hemangio", "hemangioma vascular lesion lobular capillary endothelium"),
    ("angio", "angiosarcoma Kaposi sarcoma vascular proliferation dissecting collagen"),
    ("kaposi", "Kaposi sarcoma spindle cells slit-like vessels promontory sign HHV8"),
    ("limfom", "cutaneous lymphoma mycosis fungoides epidermotropism Pautrier microabscess"),
    ("lymphom", "cutaneous T cell lymphoma epidermotropism atypical lymphocytes"),
    ("adneks", "adnexal tumor trichoepithelioma pilomatricoma sebaceous hidradenoma"),
    ("cyst", "epidermal inclusion cyst pilar cyst trichilemmal keratin"),
    ("kist", "epidermoid cyst pilar cyst keratin granular layer"),
)


def kb_enabled():
    v = (os.environ.get("HISTOLOGY_KB_ENABLED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _paths():
    d = kb_dir()
    return (
        os.path.join(d, "embeddings.npy"),
        os.path.join(d, "chunks.jsonl"),
        os.path.join(d, "meta.json"),
    )


def index_ready():
    emb, chunks, _ = _paths()
    return os.path.isfile(emb) and os.path.isfile(chunks)


def _load_index():
    emb_p, chunks_p, _ = _paths()
    if not index_ready():
        return None, None
    mtime = max(os.path.getmtime(emb_p), os.path.getmtime(chunks_p))
    with _lock:
        if _cache["emb"] is not None and _cache["mtime"] == mtime:
            return _cache["emb"], _cache["chunks"]
        emb = np.load(emb_p)
        chunks = []
        with open(chunks_p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
        if emb.ndim != 2 or len(chunks) != emb.shape[0]:
            log.warning(
                "histology_kb: indeks mos emas emb=%s chunks=%s",
                getattr(emb, "shape", None),
                len(chunks),
            )
            return None, None
        _cache["emb"] = emb
        _cache["chunks"] = chunks
        _cache["mtime"] = mtime
        srcs = {}
        for c in chunks:
            srcs[c.get("source") or "?"] = srcs.get(c.get("source") or "?", 0) + 1
        log.info(
            "histology_kb: yuklandi n=%s dim=%s manbalar=%s",
            emb.shape[0],
            emb.shape[1],
            ",".join(f"{k}:{v}" for k, v in sorted(srcs.items())),
        )
        return emb, chunks


def index_stats():
    """Health uchun: indeks hajmi va manbalar."""
    if not index_ready():
        return {"ready": False, "chunks": 0, "sources": {}}
    emb, chunks = _load_index()
    if emb is None:
        return {"ready": False, "chunks": 0, "sources": {}}
    srcs = {}
    for c in chunks:
        k = c.get("source") or "?"
        srcs[k] = srcs.get(k, 0) + 1
    return {
        "ready": True,
        "chunks": int(emb.shape[0]),
        "dim": int(emb.shape[1]),
        "sources": srcs,
        "skin_chunks": sum(v for k, v in srcs.items() if k in SKIN_SOURCES),
    }


def _openai_client():
    from openai import OpenAI

    key = (os.environ.get("OPENAI_API_KEY") or "").strip().strip('"').strip("'")
    if not key:
        return None
    return OpenAI(api_key=key, timeout=120.0)


_QUERY_CACHE = {}
_QUERY_CACHE_MAX = 512


def embed_queries(queries):
    """Qidiruv so'rovlari uchun embedding — takroriy so'rovlar keshdan (tezlik)."""
    queries = list(queries)
    missing = [q for q in queries if q not in _QUERY_CACHE]
    if missing:
        vecs = embed_texts(missing)
        with _lock:
            for q, v in zip(missing, vecs):
                _QUERY_CACHE[q] = v
            while len(_QUERY_CACHE) > _QUERY_CACHE_MAX:
                _QUERY_CACHE.pop(next(iter(_QUERY_CACHE)), None)
    return np.vstack([_QUERY_CACHE[q] for q in queries])


def embed_texts(texts):
    client = _openai_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY yo'q — vektor o'qitish ishlamaydi")
    out = []
    batch = 64
    for i in range(0, len(texts), batch):
        part = texts[i : i + batch]
        resp = client.embeddings.create(model=embed_model(), input=part)
        rows = sorted(resp.data, key=lambda x: x.index)
        out.extend([r.embedding for r in rows])
    arr = np.asarray(out, dtype=np.float32)
    nrm = np.linalg.norm(arr, axis=1, keepdims=True)
    nrm = np.clip(nrm, 1e-12, None)
    return arr / nrm


def _clean_text(raw):
    t = (raw or "").replace("\x00", " ")
    t = t.replace("­", "")
    # OCR javoblari ba'zan ``` bloklariga o'raladi
    t = re.sub(r"^\s*```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    t = t.replace("```", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _word_count(text):
    return len(re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", text or ""))


def chunk_pages(pages, source):
    """pages: list[(page_no, text)] → chunk dicts."""
    chunks = []
    buf = ""
    buf_page = 1
    for page_no, text in pages:
        text = _clean_text(text)
        if _word_count(text) < 12:
            continue
        if not buf:
            buf_page = page_no
        buf = (buf + "\n" + text).strip() if buf else text
        while len(buf) >= CHUNK_CHARS:
            piece = buf[:CHUNK_CHARS]
            cut = piece.rfind(". ")
            if cut < CHUNK_CHARS * 0.55:
                cut = CHUNK_CHARS
            else:
                cut = cut + 1
            piece = buf[:cut].strip()
            if len(piece) >= 280:
                chunks.append({"source": source, "page": buf_page, "text": piece})
            buf = buf[max(0, cut - CHUNK_OVERLAP) :].strip()
            buf_page = page_no
    if len(buf) >= 280:
        chunks.append({"source": source, "page": buf_page, "text": buf[:CHUNK_CHARS].strip()})
    return chunks


def extract_pdf_pages(pdf_path):
    try:
        import fitz  # PyMuPDF — katta atlaslar uchun tezroq

        doc = fitz.open(pdf_path)
        pages = []
        for i, page in enumerate(doc, start=1):
            try:
                txt = page.get_text("text") or ""
            except Exception:
                txt = ""
            pages.append((i, txt))
        doc.close()
        return pages
    except ImportError:
        pass
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        pages.append((i, txt))
    return pages


def save_index(chunks, embeddings, meta):
    os.makedirs(kb_dir(), exist_ok=True)
    emb_p, chunks_p, meta_p = _paths()
    np.save(emb_p, embeddings.astype(np.float32))
    with open(chunks_p, "w", encoding="utf-8") as f:
        for i, ch in enumerate(chunks):
            row = dict(ch)
            row["id"] = i
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(meta_p, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with _lock:
        _cache["emb"] = None
        _cache["chunks"] = None
        _cache["mtime"] = None


# ─── Qidiruv ──────────────────────────────────────────────────────────────────
def _dx_terms_from_draft(draft):
    """Qoralamadagi tashxis atamalari → maqsadli mezon qidiruvi."""
    if not draft:
        return []
    low = re.sub(r"\s+", " ", draft.lower())
    m = re.search(r"aniq\s+tashxis(.{0,1500})", low, flags=re.I)
    focus = m.group(1) if m else low[:1200]
    hits = []
    for key, expand in _DX_TERM_HINTS:
        if key in focus or key in low[:3000]:
            hits.append(expand)
        if len(hits) >= 3:
            break
    return hits


def _query_parts(organ_lock, patient_context=None, draft=None):
    organ = "noaniq"
    reason = ""
    if organ_lock:
        organ = (organ_lock.get("organ") or "noaniq").strip().lower()
        reason = organ_lock.get("reason_uz") or ""
    base = _ORGAN_EN.get(organ) or _ORGAN_EN["noaniq"]
    is_skin = organ == "teri"

    parts = []
    if is_skin:
        parts.append(
            f"{base} histopathologic diagnostic criteria pattern recognition "
            "epidermal dermal changes differential diagnosis"
        )
        parts.append(
            f"skin biopsy {reason} architectural pattern cellular atypia invasion "
            "benign versus malignant immunohistochemistry CD34 S100 SOX10 Melan-A p63"
        )
    else:
        parts.append(
            f"{base} tissue architecture nucleus chromatin basement membrane "
            "H&E histology diagnosis criteria"
        )
        parts.append(f"{base} {reason} epithelium connective tissue invasion vs reactive")

    p = patient_context or {}
    site = ((p.get("specimen_site") or "") + " " + (p.get("clinical_note") or "")).strip()
    if site:
        parts.append(f"{base} {site} histopathology differential diagnosis criteria")

    for term in _dx_terms_from_draft(draft):
        parts.append(f"{term} histopathology diagnostic criteria differential")

    if draft and len(parts) < 6:
        low = re.sub(r"\s+", " ", draft[:2500])
        m = re.search(r"aniq\s+tashxis(.{0,1000})", low, flags=re.I)
        hint = m.group(0) if m else low[:600]
        parts.append(f"{base} {hint} WHO criteria differential")

    return parts[:6]


def _source_bonus(code, organ):
    """Organ mos manbaga kichik ustunlik (cosine ustiga qo'shiladi)."""
    meta = source_meta(code)
    w = float(meta.get("weight") or 0.0)
    if organ == "teri":
        if meta["domain"] in ("skin", "vascular", "melanoma"):
            return w
        return -0.05 if code == "mboc" else -0.02
    # Teri bo'lmagan organ: umumiy gistologiya kanoni ustun
    if meta["domain"] == "general":
        return 0.05 if code == "junqueira" else 0.02
    return -0.06


def retrieve(queries, k=None, organ=None, per_source_max=None):
    if not kb_enabled() or not index_ready():
        return []
    emb, chunks = _load_index()
    if emb is None:
        return []
    try:
        qv = embed_queries(queries)
    except Exception as e:
        log.warning("histology_kb: embed xato: %s", e)
        return []

    k = TOP_K if k is None else max(1, int(k))
    per_source_max = PER_SOURCE_MAX if per_source_max is None else max(1, int(per_source_max))

    scores = emb @ qv.T
    best = scores.max(axis=1)

    # Manba bonusi (organga qarab)
    bonus = np.zeros_like(best)
    cache = {}
    for i, ch in enumerate(chunks):
        code = ch.get("source") or "histology"
        if code not in cache:
            cache[code] = _source_bonus(code, organ)
        bonus[i] = cache[code]
    ranked = best + bonus

    # Nomzodlar (k dan ko'proq — kvota uchun)
    cand = max(k * 6, 60)
    cand = min(cand, ranked.shape[0])
    idx = np.argpartition(-ranked, cand - 1)[:cand]
    idx = idx[np.argsort(-ranked[idx])]

    out = []
    seen = set()
    per_source = {}
    def _limit_for(code):
        """Organga mos manbadan ko'proq parcha olinadi (teri emas → umumiy kanon)."""
        dom = source_meta(code)["domain"]
        if organ == "teri":
            return per_source_max + 2 if dom in ("skin", "vascular", "melanoma") else per_source_max
        return per_source_max + 2 if dom == "general" else per_source_max

    for i in idx:
        ch = chunks[int(i)]
        code = ch.get("source") or "histology"
        if per_source.get(code, 0) >= _limit_for(code):
            continue
        key = (code, ch.get("page"), (ch.get("text") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        per_source[code] = per_source.get(code, 0) + 1
        item = dict(ch)
        item["score"] = float(best[int(i)])
        item["ranked"] = float(ranked[int(i)])
        out.append(item)
        if len(out) >= k:
            break
    return out


def format_prompt_block(hits, organ=None):
    if not hits:
        return ""
    skin = organ == "teri"
    lines = [
        "#### ICHKI KANON — KITOB MEZONLARI (har tahlilda majburiy qo'llaniladi)",
        (
            "Quyidagi parchalar LIS vektor indeksidan: Weedon Skin Pathology, Weedon Essentials, "
            "Diagnosis by First Impression, Dermatopathology Vademecum, The Basics, Color Atlas, "
            "Pathology of Vascular Skin Lesions, Genetics of Melanoma, Атлас диагностических "
            "биопсий кожи, Дерматоонкопатология, Цветкова + Junqueira/Langman/Alberts."
            if skin
            else "Quyidagi parchalar LIS o'quv indeksidan (Junqueira, Langman, Alberts/MBOC "
            "va dermatopatologiya kitoblari)."
        ),
        "ULARDAN METOD va MEZONNI ol: pattern nomi, Essential belgilar, differensial ajratish.",
        "Kitob sahifasini so'zma-so'z KO'CHIRMA. Hisobot o'zbek tilida, o'z so'zing bilan.",
        "Parcha va rasm MOS KELMASA — rasm ustun; parchani e'tiborsiz qoldir.",
        (
            "TASHXIS QO'YISHDA: mezonni shu manbalardan tekshir; #### WHO MEZONLARI bo'limida "
            "har bir Essential belgi uchun KO'RINADI/KO'RINMAYDI yoz."
            if skin
            else "Mezonni shu manbalardan tekshirib, Essential belgilarni bor/yo'q qilib yoz."
        ),
        "",
    ]
    # Teri — asosiy yo'nalish: kitob mezonlariga ko'proq joy ajratiladi
    budget = int(MAX_PROMPT_CHARS * 1.4) if skin else MAX_PROMPT_CHARS
    used = 0
    for n, h in enumerate(hits, start=1):
        src = source_label(h.get("source") or "")
        page = h.get("page") or "?"
        body = re.sub(r"\s+", " ", (h.get("text") or "")).strip()
        if len(body) > 780:
            body = body[:780].rsplit(" ", 1)[0] + "…"
        block = f"[{n}] {src}, sahifa {page}: {body}"
        if used + len(block) > budget:
            break
        lines.append(block)
        used += len(block) + 1
    if len(lines) <= 8:
        return ""
    return "\n".join(lines) + "\n"


def histology_kb_prompt_block(organ_lock=None, patient_context=None, draft=None):
    if not kb_enabled():
        return ""
    if not index_ready():
        log.info("histology_kb: indeks yo'q — %s", kb_dir())
        return ""
    organ = ((organ_lock or {}).get("organ") or "noaniq").strip().lower()
    queries = _query_parts(organ_lock, patient_context, draft)
    # Teri — asosiy yo'nalish: kitob mezonlaridan ko'proq parcha olinadi
    k = TOP_K + 4 if organ == "teri" else TOP_K
    hits = retrieve(queries, k=k, organ=organ)
    if not hits:
        return ""
    log.info(
        "histology_kb: retrieved=%s organ=%s manbalar=%s top=%.3f",
        len(hits),
        organ,
        ",".join(sorted({h.get("source") or "?" for h in hits})),
        hits[0].get("score") or 0.0,
    )
    return format_prompt_block(hits, organ)


def warm_index(background=True):
    """Indeksni oldindan xotiraga yuklash — birinchi tahlil kutib qolmasin."""
    if not kb_enabled() or not index_ready():
        return None

    def _load():
        try:
            _load_index()
        except Exception as e:
            log.warning("histology_kb: warmup xato: %s", e)

    if not background:
        _load()
        return None
    t = threading.Thread(target=_load, name="kb-warmup", daemon=True)
    t.start()
    return t
