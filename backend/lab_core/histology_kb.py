"""Gistologiya vektor bazasi: Junqueira / Langman / Alberts (MBOC).

Kitob matni gitga yozilmaydi. Indeks: backend/data/histology_kb/
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


MAX_PROMPT_CHARS = 7200
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 220
TOP_K = 8

_ORGAN_EN = {
    "teri": (
        "skin epidermis dermis hypodermis keratinocyte melanocyte collagen "
        "papillary dermis reticular dermis hair follicle sweat gland histology H&E"
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

_SOURCE_LABEL = {
    "junqueira": "Junqueira Basic Histology (uslub/mezon)",
    "langman": "Langman Medical Embryology (rivojlanish konteksti)",
    "mboc": "Molecular Biology of the Cell / Alberts (hujayra mezonlari)",
}


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
            log.warning("histology_kb: indeks mos emas emb=%s chunks=%s", getattr(emb, "shape", None), len(chunks))
            return None, None
        _cache["emb"] = emb
        _cache["chunks"] = chunks
        _cache["mtime"] = mtime
        log.info("histology_kb: yuklandi n=%s dim=%s", emb.shape[0], emb.shape[1])
        return emb, chunks


def _openai_client():
    from openai import OpenAI

    key = (os.environ.get("OPENAI_API_KEY") or "").strip().strip('"').strip("'")
    if not key:
        return None
    return OpenAI(api_key=key, timeout=120.0)


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
    t = t.replace("\u00ad", "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def chunk_pages(pages, source):
    """pages: list[(page_no, text)] → chunk dicts."""
    chunks = []
    buf = ""
    buf_page = 1
    for page_no, text in pages:
        text = _clean_text(text)
        if len(re.findall(r"[A-Za-z]{3,}", text)) < 12:
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
                chunks.append(
                    {
                        "source": source,
                        "page": buf_page,
                        "text": piece,
                    }
                )
            buf = buf[max(0, cut - CHUNK_OVERLAP) :].strip()
            buf_page = page_no
    if len(buf) >= 280:
        chunks.append({"source": source, "page": buf_page, "text": buf[:CHUNK_CHARS].strip()})
    return chunks


def detect_source(filename):
    n = (filename or "").lower()
    if "junqueira" in n or "mescher" in n:
        return "junqueira"
    if "langman" in n or "embryology" in n or "sadler" in n:
        return "langman"
    if "molecular" in n or "alberts" in n or "mboc" in n or "cell_seventh" in n:
        return "mboc"
    return "histology"


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


def _query_parts(organ_lock, patient_context=None, draft=None):
    organ = "noaniq"
    reason = ""
    if organ_lock:
        organ = (organ_lock.get("organ") or "noaniq").strip().lower()
        reason = organ_lock.get("reason_uz") or ""
    base = _ORGAN_EN.get(organ) or _ORGAN_EN["noaniq"]
    parts = [
        f"{base} tissue architecture nucleus chromatin basement membrane H&E histology diagnosis criteria",
        f"{base} {reason} epithelium connective tissue invasion vs reactive",
    ]
    p = patient_context or {}
    site = (p.get("specimen_site") or "") + " " + (p.get("clinical_note") or "")
    if site.strip():
        parts.append(f"{base} {site} histology differential diagnosis")
    if draft:
        low = re.sub(r"\s+", " ", (draft or "")[:2500])
        # faqat tashxis bo'limidan kalit so'zlar — butun hisobotni qayta yubormaslik
        m = re.search(r"aniq\s+tashxis(.{0,1200})", low, flags=re.I)
        hint = m.group(0) if m else low[:600]
        parts.append(f"{base} {hint} WHO criteria differential")
    return parts[:4]


def retrieve(queries, k=TOP_K):
    if not kb_enabled() or not index_ready():
        return []
    emb, chunks = _load_index()
    if emb is None:
        return []
    try:
        qv = embed_texts(list(queries))
    except Exception as e:
        log.warning("histology_kb: embed xato: %s", e)
        return []
    scores = emb @ qv.T
    best = scores.max(axis=1)
    k = max(1, min(int(k), best.shape[0]))
    idx = np.argpartition(-best, k - 1)[:k]
    idx = idx[np.argsort(-best[idx])]
    out = []
    seen = set()
    for i in idx:
        ch = chunks[int(i)]
        key = (ch.get("source"), ch.get("page"), (ch.get("text") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        item = dict(ch)
        item["score"] = float(best[int(i)])
        out.append(item)
    return out


def format_prompt_block(hits):
    if not hits:
        return ""
    lines = [
        "#### ICHKI GISTOLOGIYA VEKTOR-KANON (har tahlilda majburiy qo'llaniladi)",
        "Quyidagi parchalar jamoa LIS o'quv indeksi (Junqueira, Langman, Alberts/MBOC).",
        "ULARDAN METOD va MEZONNI ol: to'qima tipi, hujayra/yadro, rivojlanish konteksti.",
        "Kitob sahifasini so'zma-so'z KO'CHIRMA. Hisobot o'zbek tilida, o'z so'zing bilan.",
        "Parcha va rasm MOS KELMASA — rasm ustun; parchani e'tiborsiz qoldir.",
        "",
    ]
    used = 0
    for n, h in enumerate(hits, start=1):
        src = _SOURCE_LABEL.get(h.get("source") or "", h.get("source") or "kanon")
        page = h.get("page") or "?"
        body = re.sub(r"\s+", " ", (h.get("text") or "")).strip()
        if len(body) > 720:
            body = body[:720].rsplit(" ", 1)[0] + "…"
        block = f"[{n}] {src}, sahifa {page}: {body}"
        if used + len(block) > MAX_PROMPT_CHARS:
            break
        lines.append(block)
        used += len(block) + 1
    if len(lines) <= 6:
        return ""
    return "\n".join(lines) + "\n"


def histology_kb_prompt_block(organ_lock=None, patient_context=None, draft=None):
    if not kb_enabled():
        return ""
    if not index_ready():
        log.info("histology_kb: indeks yo'q — %s", kb_dir())
        return ""
    queries = _query_parts(organ_lock, patient_context, draft)
    hits = retrieve(queries, k=TOP_K)
    if not hits:
        return ""
    log.info(
        "histology_kb: retrieved=%s organ=%s sources=%s",
        len(hits),
        (organ_lock or {}).get("organ"),
        ",".join(sorted({h.get("source") or "?" for h in hits})),
    )
    return format_prompt_block(hits)
