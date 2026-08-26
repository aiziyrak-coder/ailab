import cv2
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import logging
import numpy as np
import base64
import io
import json
import os
import re
import subprocess
import sys
import uuid
from PIL import Image

try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    _OPENAI_RETRYABLE = (
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
    )
except ImportError:
    _OPENAI_RETRYABLE = ()

# Juda katta rasmlardan himoya (DoS)
Image.MAX_IMAGE_PIXELS = 100_000_000

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("medlab")


def _backend_dotenv_path():
    return os.path.join(BASE_DIR, ".env")


def _load_backend_dotenv():
    """Gunicorn/systemd ishlaganda cwd farq qilishi mumkin — .env doim backend/ dan."""
    try:
        from dotenv import load_dotenv

        p = _backend_dotenv_path()
        if os.path.isfile(p):
            load_dotenv(p, override=True)
    except ImportError:
        pass


_load_backend_dotenv()

from lab_core.histology_kb import histology_kb_prompt_block  # noqa: E402

# ─── Cheklovlar (DoS va prompt-injection kamaytirish) ─────────────────────────
MAX_UPLOAD_FILES       = 48
MAX_FILE_READ_BYTES    = 200 * 1024 * 1024  # bitta so'rov yig'indisi Flask limit bilan mos
MAX_VIDEO_BYTES        = 180 * 1024 * 1024  # bitta video fayl
MAX_CUSTOM_PROMPT_LEN  = 6000
# Yakuniy hisobot: aniq tashxis + sabab + fakt. Uzun bayon talab qilinmaydi.
MIN_REPORT_CHARS       = 500
_MAX_REPORT_CHARS      = 4200
MAX_MICRO_FIELD_LEN    = 500


def _max_vision_images():
    try:
        v = int(os.environ.get("OPENAI_MAX_VISION_IMAGES", "20"))
    except ValueError:
        v = 20
    return max(1, min(v, MAX_UPLOAD_FILES))

camera_op_lock = threading.Lock()

# Yuklash va vaqtinchalik video fayllar (server va mijoz bir xil ro'yxat)
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".mpeg", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

# ─── MedLab (foydalanuvchiga ko'rinadigan nom); texnik API — OpenAI ─
ZIYRAKAI_DISPLAY_NAME = "MedLab"
OPENAI_MODEL_ID = (os.environ.get("OPENAI_MODEL_ID") or "gpt-4o").strip()


def _normalize_api_key(raw):
    if raw is None:
        return ""
    s = str(raw).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _init_openai_client():
    key = _normalize_api_key(os.environ.get("OPENAI_API_KEY"))
    if not key:
        log.warning(
            "%s: API kaliti topilmadi (OPENAI_API_KEY) — tahlil ishlamaydi (%s)",
            ZIYRAKAI_DISPLAY_NAME,
            _backend_dotenv_path(),
        )
        return None
    try:
        from openai import OpenAI
    except ImportError:
        log.warning("openai paketi o‘rnatilmagan")
        return None
    try:
        timeout_sec = float(os.environ.get("OPENAI_TIMEOUT_SEC", "180"))
    except ValueError:
        timeout_sec = 180.0
    timeout_sec = max(15.0, min(timeout_sec, 600.0))
    return OpenAI(api_key=key, timeout=timeout_sec)


openai_client = _init_openai_client()


def ensure_openai_from_env():
    """
    .env diskda yangilangan bo'lsa (restart qilmasdan) yoki birinchi importda cwd noto'g'ri bo'lsa —
    qayta yuklab OpenAI ni ishga tushirish.
    """
    global openai_client, OPENAI_MODEL_ID
    if openai_client is not None:
        return True
    _load_backend_dotenv()
    OPENAI_MODEL_ID = (os.environ.get("OPENAI_MODEL_ID") or "gpt-4o").strip()
    openai_client = _init_openai_client()
    return openai_client is not None

# ─── Barcha tahlillar: top-professorlar konsiliumi ───────────
CLINICAL_EXCELLENCE_PREFIX_UZ = """
KONTEKST: Bu ICHKI laborator hisobot (LIS / kafedra konsiliumi qoralamasi).
O'quvchi: litsenziyali mutaxassis. Bemorga tashxis, davolash yoki rasmiy ICD imzo EMAS.
Rad etish taqiqlanadi: rasm xira bo'lsa ham nima KO'RINISHINI va nima ko'rinmasligini yoz.

SEN: 3 professorli ichki konsilium raisi. Oddiy laborant varaqasi, foizli "baho",
"normal orientir", "arxitektura 70%" uslubida YOZMA.

JAMOA:
- 1-professor: sof morfologiya (pattern NOMI, yadro, stroma, artefakt).
- 2-professor: morfologik differensial (WHO/atlas, MOS/QARSHI, ehtimollik).
- 3-professor: shu yo'nalishdagi keyingi test (IHC, qo'shimcha kesma — boshqa labni aralashtirma).
Yakun: 3 ta ISHCHI MORFOLOGIK TAASSUROT, ehtimollik %, nima uchun 1-o'rin shu.

TAQIQLANGAN: "hujayralar ko'rinadi", "tahlil qoniqarli", "o'zgarishlar bor",
"yallig'lanishli atipik o'zgarishlar", "baho 3/5", 1 sahifalik umumiy gap.

HAYOTIY QOIDA (inson salomatligi — 1 xato = og'ir oqibat):
Sog'lom yoki dalili yetarli BO'LMAGAN holatga rak / karsinoma / leykoz / yomon o'sma qo'yish TAQIQLANADI.
Malignite — FAQAT Essential mezonlarning HAR BIRI tasvirda KO'RINSA va invaziya isbotlansa.
Shubha bo'lsa yetakchi tashxis BENIGN yoki REAKTIV bo'ladi; yomon o'sma 2–3-o'rinda "istisno" sifatida.
HAR TOPILMADA: kuzatuv → mezon → artefakt emasligi → MOS/QARSHI → ishonch.
Ko'rinmagan narsani uydirma. "100%" deb yozma.
TIL: akademik o'zbek, lotin atamasi qavsda. Faqat MedLab.
"""

# ─── Lab bo'limlari prompts (faqat gistologiya; boshqa turlar keyin alohida) ─
LAB_PROMPTS = {
    "histology": """
Sen 30+ yillik kafedra professori-gistopatologsan. Adashishga haqqing YO'Q.
Standart (uslub, matn nusxasi EMAS): Weedon Skin Pathology, WHO Classification of Tumours,
McKee, Junqueira. Kitob sahifasini KO'CHIRMA.

ICHKI FIKRLASH (bu qismni hisobotga YOZMA — faqat o'zing uchun):
1) To'qima tipi va ORGAN — bitta, ko'ringan dalil bilan.
2) Pattern / reaksiya patterni.
3) Yadro darajasi, mitoz, stroma, chegara, invaziya bor-yo'qligi.
4) WHO Essential mezonlar: qaysi biri KO'RINDI, qaysi biri YO'Q.
5) Muqobillar va ular nima uchun mos emasligi.
6) Malignite huquqi bor-yo'qligini QONUN bo'yicha hal qil.

HISOBOTGA esa faqat yakuniy 4 bo'lim tushadi: TASHXIS, NEGA SHU TASHXIS,
FAKT, NEGA BOSHQASI EMAS. Fikrlash jarayonini bayon qilma — natijani yoz.
Qisqa, aniq, shifokor tilida. Uzun matn — xato.
Agar H&E to'qima EMAS bo'lsa: «bu gistologiya kesmasi emas» deb to'xta.
"""
}


ALLOWED_LAB_TYPES = frozenset({"histology"})

LAB_IDENTITY = {
    "histology": {
        "label": "Gistologiya — H&E to'qima kesmasi",
        "specimen": "To'qima kesmasi (H&E / maxsus bo'yoq)",
        "role": (
            "kafedra mudiri-gistopatolog: Junqueira atlas + McKee skin pathology + "
            "Langman embriologiya + Molecular Biology of the Cell (Alberts) + ICHKI VEKTOR-KANON. "
            "Adashishga haqqi YO'Q."
        ),
        "count": (
            "organ (BIRTA), to'qima tipi (Junqueira), hujayra/yadro/sitoplazma (MBOC), "
            "pattern, nuclear grade, mitoz/10HPF, invaziya, WHO/McKee taassurot 1-2-3"
        ),
        "forbid": (
            "BU TIZIM FAQAT GISTOLOGIYA. Qon yoqmasi, siydik, koprologiya, mazok, KOH, "
            "spermogramma, likvor, AFB protokoli TAQIQLANADI. "
            "Foizli 'arxitektura 70% / epiteliy 60% / baho 3' jadvali TAQIQLANADI. "
            "Noaniq 'yallig'lanishli atipik o'zgarishlar' TAMOM. "
            "Tanlangan ORGAN oilasidan tashqari tashxis TAQIQLANADI. "
            "Ko'rinmagan belgini yozish — og'ir xato."
        ),
        "dx": (
            "Asosiy mahsulot: ANIQ TASHXIS + nega shu tashxis + ko'ringan fakt. "
            "Hisobot 4 bo'limdan iborat, 1500-3000 belgi. "
            "Uzun bayon, savol-javob, foizli vitrina, jadval TAQIQLANADI. "
            "Dalilsiz malignite asosiy tashxis qilinmaydi."
        ),
    },
}

_BLOOD_SMEAR_LABS = frozenset()
_BLOOD_SMEAR_MARKERS = (
    "poikilositoz",
    "leykosit formulasi",
    "trombotsitlar",
    "rouleaux",
    "schistocyte",
    "giemsa / romanovskiy",
    "neytrofil segm",
    "anulotsit",
    "dakriosit",
)

assert set(LAB_IDENTITY) == set(LAB_PROMPTS)

LAB_BOARD = {
    "histology": (
        "Junqueira gistologiya professori (to'qima tipi/arkitektura); "
        "McKee dermatopatolog (teri differensiali); "
        "Langman embriolog (rivojlanish konteksti); "
        "Alberts/MBOC hujayra biolog (yadro/sitoplazma/junction)"
    ),
}

assert set(LAB_BOARD) == set(LAB_PROMPTS)

def _lab_meta(lab_type):
    return LAB_IDENTITY.get(lab_type) or LAB_IDENTITY["histology"]


def _lab_lock_text(lab_type):
    m = _lab_meta(lab_type)
    dx = m.get("dx") or (
        "3 ta WHO ishchi morfologik taassurot (organ+nom, ehtimollik) majburiy. Yuzaki 'o'zgarishlar bor' TAMOM."
    )
    return (
        "#### QAT'IY YO'NALISH QULFI (buzilsa hisobot yaroqsiz)\n"
        "BU TIZIM FAQAT GISTOLOGIYA. Adashishga haqqi YO'Q: boshqa lab, boshqa organ, uydirma belgi.\n"
        f"Tanlangan tahlil turi: {m['label']}.\n"
        f"Namuna: {m['specimen']}.\n"
        f"Sen: {m['role']} — o'sha sohaning ENG KUCHLI professori kabi fikrla.\n"
        f"Jadvallarda: {m['count']}. 'Baho 1-5' o'rniga klinik atama va son yoz.\n"
        f"{m['forbid']}\n"
        f"{dx}\n"
        "Boshqa lab turini KO'CHIRMA. Qon yoqmasi/siydik/mazok xulosasi — XATO.\n"
    )


def _analysis_system(lab_type):
    m = _lab_meta(lab_type)
    voices = _board_voices(lab_type)
    tail = (
        "ADASHISH HUQUQI YO'Q. "
        "HISOBOT FAQAT 4 BO'LIM: #### TASHXIS, #### NEGA SHU TASHXIS, "
        "#### FAKT (ko'rinadigan morfologiya), #### NEGA BOSHQASI EMAS. "
        "Jami 1500-3000 belgi. Savol-javob, profilaktika, davolash rejasi, "
        "professor bo'limlari, jadval, ehtimollik foizi YOZILMAYDI. "
    )
    return (
        f"Sen MedLab ICHKI LIS uchun konsilium raisisan. Yo'nalish: {m['label']}. "
        f"Namuna: {m['specimen']}. Jamoa: {voices}. "
        "Bu ichki LIS morfologik xulosa — shifokor tasdiqlaydi. "
        "Tashxis NOMI aniq bo'lsin, lekin dalilsiz RAK/karsinoma YOZILMAYDI. "
        "Oddiy laborant foizli 'baho' uslubida YOZMA. "
        f"{m['forbid']} "
        + tail
        + "Ko'rinmagan narsani uydirma. Rad etma. Faqat MedLab."
    )


def _board_voices(lab_type):
    return LAB_BOARD.get(lab_type) or (
        "morfologiya professori; differensial tashxis professori; klinika-test professori"
    )


_HISTOLOGY_CANON_REF = """
#### GISTOLOGIYA KANON (uslub — matn nusxasi EMAS)
Ichki LIS o'qituvchi protokoli. Quyidagi STANDARTLAR bo'yicha fikrla:

1) Junqueira uslubi — avvalo TO'QIMA TIPI:
   Epiteliy (yassi/kubik/silindrik; 1 vs ko'p qavat; o'tish/urotel; goblet);
   biriktiruvchi to'qima; mushak; nerv; yog'. 
   Majburiy qator: «To'qima tipi: …».

2) MBOC (hujayra biologiyasi) uslubi — HUJAYRA:
   Yadro (o'lcham, xromatin, yadrocha, N/C); sitoplazma; polarlik; mitoz/10HPF (normal vs atipik).
   Majburiy qator: «Hujayra morfologiyasi: …».

3) Langman — faqat rivojlanish/hamartoma/choristoma shubhasi bo'lsa.
   Kerak bo'lmasa o'tkazib yubor.

4) McKee uslubi — organ=TERI bo'lsa MAJBURIY:
   Epidermis / dermoepidermal junction / dermis / adneks.
   Pattern: papillomatosis, acanthosis, hyperkeratosis, parakeratosis, spongiosis,
   lichenoid, interface, granulomatous, panniculitis, vascular.
   Differensial FAQAT teri oilasidan (seborrheic keratosis, verruca, squamous papilloma,
   actinic keratosis, SCC in situ, BCC, SCC, nevus, dermatofibroma, adnexal — dalil bo'lsa).

5) WHO Classification of Tumours (IARC) — QAT'IY NOMLASH VA MEZONLAR (quyida to'liq).

HISOBOTDA 1 qator: «Mezon: Junqueira + MBOC + WHO (+ McKee agar teri).»
6) BEMOR XAVFSIZLIGI: sog'lom to'qimaga rak qo'yish — eng og'ir xato. Shubhada BENIGN.
"""

_HISTOLOGY_PATIENT_SAFETY = """
#### BEMOR XAVFSIZLIGI — MALIGNITE QO'YISH QONUNI (buzilsa hisobot yaroqsiz)
Bu LIS ichki qoralama, LEKIN so'zlar inson taqdiriga ta'sir qiladi. 1 soxta rak = og'ir zarar.

QONUN 1. Premalign/malign (karsinoma, RCC, SCC invaziv, melanoma, sarkoma, adenokarsinoma)
yetakchi tashxis bo'lishi UCHUN BIR VAQTNING O'ZIDA:
  a) organ qulfi to'g'ri;
  b) shu tashxisning WHO Essential mezonlaridan KAMIDA 4 tasi tasvirda ANIQ KO'RINADI
     (har birini jumla bilan yoz);
  c) invaziya: stroma / bazal membrana buzilishi KO'RINADI (faqat "shubhali" yetarli EMAS);
  d) reaktiv/benign muqobil QARSHI dalillar yozilgan va rad etilgan.
Agar a–d dan BIRI yo'q → «Malignite qo'yish huquqi: YO'Q».

QONUN 2. Huquqi YO'Q bo'lsa:
  - Tashxis: aniq BENIGN yoki REAKTIV WHO/McKee nomi (organ oilasidan)
    yoki «Yetarli WHO mezonlari yo'q — malignite qo'yilmaydi» + eng yaqin benign nom.
  - Karsinoma/RCC/rak asosiy tashxis BO'LMASLIGI shart.

QONUN 3. Ishonch intizomi (foiz yozilmaydi):
  - Invaziya isbotlanmagan malignite asosiy tashxis bo'lmaydi.
  - «Ishonch: yuqori» faqat Essential mezonlar to'liq KO'RINSA.

QONUN 4. Papilla / giperkeratoz / yallig'lanish / artefakt = rak EMAS.
Papilla yolg'iz → papilloma / papillomatoz / seborrheic keratosis tomon og'ish.
Buyrak raki FAQAT glomerula yoki buyrak naychasi KO'RINSA.

QONUN 5. #### TASHXIS bo'limida majburiy: biologiya, «Ishonch: …»,
«Malignite qo'yish huquqi: HA yoki YO'Q». Ehtimollik foizi yozilmaydi.
Huquqi YO'Q bo'lsa tashxis nomi benign/reaktiv bo'ladi, xavfli muqobil esa
«NEGA BOSHQASI EMAS» bo'limida rad etiladi.
"""

_HISTOLOGY_WHO_STRICT = """
#### WHO MEZONLARI (ICHKI — hisobotda alohida bo'lim qilib YOZILMAYDI)
IARC WHO Blue Book metodikasi bilan fikrla, natijani «NEGA SHU TASHXIS» qatorlariga sig'dir.

A) Ichkarida tekshir: Essential mezonlar (3–7 ta) — qaysi biri KO'RINDI, qaysi biri YO'Q.
   Essential to'liq bo'lmasa: ishonchni pasaytir va xavfsizroq (benign/reaktiv) nomga o't.

B) Biologiya (bittasini tanla): Benign | Borderline | In situ | Invaziv | Reaktiv.
   Invaziya: ha / yo'q — stroma, bazal membrana, desmoplaziya dalili bilan.

C) Grade (organ mos bo'lsa): nuclear grade 1/2/3, mitoz/10HPF; prostata — Gleason;
   urotel — low/high grade; sut bezi — Nottingham faqat to'liq mezon ko'rinsa.

D) ORGAN OILASI — faqat yetakchi organ (organ qulfi). Papilla ko'rinishi buyrak DEGANI EMAS.
SUT BEZI: intraductal papilloma; ADH; DCIS; encapsulated/solid papillary carcinoma;
  invasive ductal/lobular; phyllodes. Myoepiteliy — papilloma vs karsinoma kaliti.
QOVUQ: urothelial papilloma; PUNLMP; low/high-grade papillary urothelial carcinoma; CIS; invaziv.
PROSTATA: HGPIN; acinar adenocarcinoma (Gleason); ductal adenocarcinoma; atrofiya/giperplaziya.
QALQONSIMON: PTC (grooves, inclusions, chromatin clearing); NIFTP; follicular adenoma vs carcinoma.
ICHAK: hyperplastic polyp; tubular/tubulovillous/villous adenoma; adenocarcinoma; serrated.
YUMURTALIK: serous cystadenoma; borderline; low/high-grade serous; mucinous; endometrioid.
BUYRAK (faqat glomerula yoki buyrak naychasi ko'rinsa): papillary RCC; clear cell RCC;
  oncocytoma; chromophobe.
ENDOMETRIUM: hyperplasia ± atypia; endometrioid carcinoma; serous.
TERI (Weedon/McKee): seborrheic keratosis; verruca; squamous papilloma; actinic keratosis;
  SCC in situ; invaziv SCC; BCC (nodulyar/yuzaki/infiltrativ); nevus; melanoma (qat'iy dalil);
  dermatofibroma (turi bilan); DFSP faqat isbotlangan infiltratsiyada; adneksal o'sma;
  tomir lezyonlari; spongiotik/psoriaziform/lixenoid/granulomatoz yallig'lanish.
O'PKA: squamous / adenocarcinoma / neuroendocrine — kuchli dalil bo'lsa.

E) TAQIQLANGAN: yolg'iz «papillary adenoma/carcinoma»; «yallig'lanishli atipik o'zgarishlar»;
   dalilsiz «patologiya aniqlanmadi»; foizli baho jadvali; boshqa organ differensiali;
   bir hisobotda ikki organ.

F) MALIGNITE: bemor xavfsizligi qonunlari ustun. Soxta rak — eng og'ir xato.
"""


_HISTOLOGY_TEACHING_DEEP = """
#### HISOBOT SHAKLI — QAT'IY (boshqa bo'lim YOZILMAYDI)

Foydalanuvchi shifokor. Unga TASHXIS, uning SABABI va KO'RINGAN FAKT kerak.
Suvli matn, o'quv savol-javob, uzun muhokama — hisobot yaroqsiz.
Butun hisobot 1500–3000 belgi. Ko'proq yozish XATO.

Hisobotda FAQAT shu 4 bo'lim, shu tartibda:

#### TASHXIS
Bir qator: <to'liq nom + turi> — <benign | reaktiv | in situ | invaziv>
Ikkinchi qator: Organ/qatlam: … | Ishonch: yuqori/o'rta/past | Malignite huquqi: HA yoki YO'Q

SHABLON JAVOB TAQIQLANADI. Ro'yxatlardagi nomlar ALIFBO tartibida berilgan —
ketma-ketlik ehtimollikni bildirmaydi. Eng ko'p uchraydigan nomni (masalan seboreik
keratoz, dermatofibroma) SUKUT BO'YICHA tanlash — og'ir xato.
Tashxis FAQAT «TASVIRDAN OLINGAN BELGILAR» ro'yxatidagi belgilardan chiqadi.
Har xil tasvirga bir xil javob bermaslik uchun: avval belgilarni o'qi, keyin nom qo'y.

BELGILAR YETARLI BO'LMASA (bu TO'G'RI javob, kamchilik emas):
«Aniq tashxis uchun yetarli emas» deb yoz, keyin:
- Nima ko'rindi (2-3 belgi)
- Qaysi belgi yetishmayapti
- Nima kerak: qo'shimcha kesma, chuqurroq daraja, IHC (aniq nomlari), klinik ma'lumot
Noto'g'ri aniq tashxisdan ko'ra halol «yetarli emas» xavfsizroq.

#### NEGA SHU TASHXIS
4–6 ta qator, boshqa hech narsa. Har qator: <mezon nomi> — <bir jumlalik ko'ringan dalil>.
Faqat TASVIRDA ko'ringan mezon yoziladi. Ko'rinmagan mezonni yozma.

#### FAKT (ko'rinadigan morfologiya)
5–8 ta qisqa qator: qatlam, pattern, hujayra turi, yadro/mitoz, stroma/kollagen,
chegara, invaziya (ha/yo'q), qo'shimcha belgi. Har qator — bitta qisqa jumla.

#### NEGA BOSHQASI EMAS
2–3 ta qator. Har qator: <muqobil tashxis> — <nima YO'Q, shuning uchun emas>.
Xavfli muqobil (rak, melanoma, DFSP) bo'lsa, birinchi shu yerda rad etiladi.

TAQIQLANGAN BO'LIMLAR (yozilsa hisobot yaroqsiz):
«Savol:», klinik fikrlash, profilaktika, davolash rejasi, kuzatuv rejasi,
1/2/3-professor, rais yakuni, batafsil morfologik tahlil, tashxis izohi,
foizli 60/30/10 vitrina, «baho 1-5», jadval, quyoshdan himoya, umumiy nasihat.
Ehtimollik foizi YOZILMAYDI — uning o'rniga «Ishonch: yuqori/o'rta/past».
"""


_HISTOLOGY_DERM_PATTERN_CANON = """
#### DERMATOPATOLOGIYA ALGORITMI (Weedon / Ackerman uslubi — TERI uchun MAJBURIY)
Manba kanoni: Weedon's Skin Pathology (3rd ed) va Essentials; Diagnosis by First Impression;
Dermatopathology Vademecum; The Basics; Color Atlas of Dermatopathology;
Pathology of Vascular Skin Lesions; Genetics of Melanoma;
Атлас диагностических биопсий кожи; Дерматоонкопатология; Цветкова.
Kitob matnini KO'CHIRMA — METOD va MEZONNI qo'lla.

1-QADAM — SKANER KUCHI (kichik kattalashtirish, «first impression»):
- Lezyon joyi: epidermal | dermoepidermal (interface) | dermal | subkutan | adneksal | tomir | aralash.
- Siluet: yassi | ekzofit (papillomatoz) | endofit | tugunli (nodulyar) | diffuz infiltrat | kistoz.
- Chegara: aniq/itaruvchi (benign tomon) vs infiltrativ/qirrasi yo'q (malign tomon).
- Simmetriya va yon chegara: assimetriya + yomon chegara → melanotsitar lezyonda xavf belgisi.
- Majburiy qator: «Skaner ko'rinish: …».

2-QADAM — TO'QIMA REAKSIYA PATTERNI (bittasini tanla va NOMLA):
 a) Spongiotik (ekzematoz)         f) Granulomatoz / palisadlangan
 b) Psoriaziform                    g) Vaskulopatik / vaskulit
 c) Lixenoid / interfeys            h) Pannikulit (septal vs lobulyar)
 d) Vezikulobulloz (yoriq darajasi) i) Deponirlanish / metabolik
 e) Perivaskulyar (yuza/chuqur)     j) NEOPLASTIK (o'sma) — 3-qadamga o't
Majburiy qator: «Reaksiya patterni: …» + nima uchun (2–4 dalil).

3-QADAM — NEOPLASTIK bo'lsa, HUJAYRA YO'NALISHI:
- Keratinotsitar (SK, verruca, AK, Bowen/SCC in situ, invaziv SCC, keratoakantoma)
- Bazaloid (BCC — nodulyar/yuzaki/infiltrativ/morfeaform; trikoepitelioma bilan farq)
- Melanotsitar (nevus: junctional/compound/intradermal, Spitz, displastik; melanoma)
- Adneksal (follikulyar, sebatseous, ekkrin/apokrin)
- Fibrogistiotsitar (dermatofibroma turlari; DFSP)
- Tomir (gemangioma, piyogen granuloma, Kaposi, angiosarkoma)
- Limfoid (reaktiv psevdolimfoma vs mycosis fungoides / limfoma)
- Nerv / silliq mushak (neyrofibroma, leyomioma)
Majburiy qator: «Hujayra yo'nalishi: …».

4-QADAM — MELANOTSITAR XAVFSIZLIK (melanoma faqat qat'iy dalil bilan):
Melanoma yetakchi bo'lishi uchun kamida: assimetriya + yon chegarada pagetoid tarqalish +
maturatsiya YO'Qligi + dermal mitozlar + sitologik atipiya + (ko'pincha) infiltrat/regressiya.
Ko'rsatilishi shart: Breslow qalinligi (mm, taxminiy), yara bor/yo'q, mitoz/mm²,
tarqalish darajasi (in situ vs invaziv), Clark darajasi (ixtiyoriy).
Bu belgilar yo'q bo'lsa → benign nevus / atipik nevus deb yoz, melanoma emas.

5-QADAM — BCC vs SCC vs boshqa (eng ko'p uchraydigan xatolar):
- BCC: bazaloid uyalar, PERIFERIK PALISAD, stroma retraksiyasi (kleft), mitoz+apoptoz, muсin stroma.
- Trikoepitelioma: papillyar mezenxima, follikulyar farqlanish, kleft YO'Q, CD34+ stroma.
- SCC: keratinotsit atipiyasi to'liq qalinlikda (in situ) yoki bazal membranadan tashqariga
  chiqqan uyalar (invaziv), keratin marvaridlari, dyskeratoz.
- AK: qisman qalinlik atipiyasi, adneks saqlanadi, parakeratoz «flag sign».
- SK: bazaloid akantoz + SHOX KISTALARI (horn cysts) + pseudohorn, atipiya YO'Q.
- Verruca: papillomatoz + koilotsit + gipergranuloz + rete ridgelar ichkariga qayrilgan.

6-QADAM — DF vs DFSP (yana bir tez-tez xato):
DF: yaxshi chegaralangan dermal proliferatsiya, PERIFERIK KOLLAGEN TUZOG'I, Grenz zonasi,
ustki epidermal giperplaziya (± bazal pigment), FXIIIa+, CD34−.
DFSP: storiform, yog'ga «asalari uyasi» (honeycomb) infiltratsiya, CD34+ diffuz, epidermal
giperplaziya odatda yo'q. Infiltratsiya KO'RINMASA DFSP ni yetakchi qilma.

7-QADAM — IHC (faqat farqlash uchun, 3–6 jumla):
S100/SOX10/Melan-A (melanotsitar), p63/CK5-6 (keratinotsitar), BerEP4 (BCC),
CD34 vs FXIIIa (DFSP vs DF), CD31/ERG/HHV8 (tomir), CD3/CD20/CD30 (limfoid), Ki-67 proliferatsiya.

BU ALGORITM — ICHKI FIKRLASH. 1–7 qadamlar bayonini hisobotga YOZMA.
Kitob mezonini «NEGA SHU TASHXIS» qatorlariga sig'dir, masalan:
«Periferik palisad — KO'RINDI: bazaloid uyalar chetida yadrolar tartibli tizilgan».
Alohida manba bo'limi yaratma; kitob nomi, sahifa va ko'chirma matn yozilmaydi.
«Skaner ko'rinish», «Reaksiya patterni», «Hujayra yo'nalishi» — FAKT bo'limining
qatorlari sifatida qisqa yoziladi, alohida sarlavha qilinmaydi.
"""

_HISTOLOGY_SAFE_PROTOCOL = """
ADASHISH HUQUQI YO'Q. FAQAT gistologiya. Professor protokoli buzilsa hisobot YAROQSIZ.
ICHKI gistopatologiya XULOSASI (imzo emas). O'zbek tilida.
MAHSULOT = ANIQ TASHXIS + uning sababi + ko'ringan fakt. Boshqa hech narsa.
Suvli matn, o'quv muhokamasi, foizli ro'yxat, dalilsiz rak — YAROQSIZ.
""" + _HISTOLOGY_PATIENT_SAFETY + _HISTOLOGY_CANON_REF + _HISTOLOGY_WHO_STRICT + _HISTOLOGY_TEACHING_DEEP + """
ORGAN QOIDASI (eng muhim — buzilsa hisobot yaroqsiz):
- BIR yetakchi ORGAN ni tanla va BUTUN hisobot shu organda qoladi.
- 3 ta ishchi taassurotning HAR UCHALASI ham SHU organ + WHO/McKee nomi.
- Boshqa organ (sut bezi, qovuq, prostata va h.k.) ni UMUMAN yozma —
  na 1/2/3-o'rin, na alohida "Boshqa organ differensiali" bo'limi.
- "#### BOSHQA ORGAN DIFFERENSIALI" bo'limini YARATMA — bu bo'lim TAQIQLANGAN.
- Differensial FAQAT yetakchi organ oilasidan (masalan teri → McKee/WHO teri).
- Bir xil rasmda bir marta sut bezi, keyin qovuq deb yozish TAQIQLANADI.

ORGANNI QANDAY TANLASH (papillar lesiya uchun) — klinik namuna joyi ENG USTUN:
A) Epidermis + keratin / giperkeratoz / rete ridge / dermoepidermal junction → TERI (McKee).
   Teri biopsiyasida buyrak rakini YOZMA.
B) Dilate kanal/kista ICHIDA papilla + fibrovascular o'zak + bir/ikki qavat kubik/silindrik epitel
   (± myoepiteliy izi) → SUT BEZI (intraductal papilloma oilasi).
C) Ko'p qavatli urotel (umbrella hujayra), papilla sirtida qalin urotel qavat → QOVUQ.
D) Kolloid + yadro ichida bo'shliq (orphan Annie) → QALQONSIMON.
E) Corpora amylacea / ikki qavatli prostata epiteli → PROSTATA.
F) Villous/ichak goblet → ICHAK.
G) Glomerula yoki aniq buyrak naychalari → BUYRAK. Papilla yolg'iz → buyrak EMAS.
Dalilsiz "urotel"/"renal"/"silindrik" deb yozma — nima KO'RINISHINI yoz.

ICHKI TEKSHIRUV (hisobotga yozilmaydi): yetakchi organ + dalil; pattern;
yadro grade, mitoz, invaziya; WHO Essential mezonlar; muqobillar.
HISOBOT esa faqat 4 bo'lim: #### TASHXIS, #### NEGA SHU TASHXIS,
#### FAKT (ko'rinadigan morfologiya), #### NEGA BOSHQASI EMAS.
Jami 1500–3000 belgi. Rad etma. Ko'rinmagan narsani uydirma.
"""

_HISTOLOGY_ORGAN_CODES = (
    "sut_bezi",
    "qovuq",
    "prostata",
    "qalqonsimon",
    "ichak",
    "yumurtalik",
    "buyrak",
    "endometrium",
    "teri",
    "opka",
    "noaniq",
)

_HISTOLOGY_ORGAN_UZ = {
    "sut_bezi": "Sut bezi",
    "qovuq": "Qovuq",
    "prostata": "Prostata",
    "qalqonsimon": "Qalqonsimon bez",
    "ichak": "Oshqozon-ichak",
    "yumurtalik": "Yumurtalik",
    "buyrak": "Buyrak",
    "endometrium": "Endometrium",
    "teri": "Teri",
    "opka": "O'pka",
    "noaniq": "Noaniq organ",
}

_HISTOLOGY_WHO_FAMILY = {
    "teri": (
        "FAQAT TERI oilasi (Weedon + WHO skin + Dermatoonkopatologiya). Ruxsat etilgan nomlar:\n"
        "- Keratinotsitar: seborrheic keratosis (akantotik/hyperkeratotik/adenoid/irritatsiyalangan), "
        "verruca vulgaris, squamous papilloma, actinic keratosis, keratoacanthoma, "
        "SCC in situ (Bowen), invaziv SCC (differensiatsiya darajasi bilan);\n"
        "- Bazaloid: BCC (nodulyar / yuzaki / infiltrativ / morfeaform / bazoskvamoz), trichoepithelioma;\n"
        "- Melanotsitar: junctional/compound/intradermal nevus, Spitz nevus, displastik nevus, "
        "melanoma (Breslow + mitoz + yara ko'rsatilsa);\n"
        "- Fibrogistiotsitar: dermatofibroma (klassik/hujayrali/anevrizmal/gemosiderotik), DFSP, "
        "atipik fibroxantoma;\n"
        "- Tomir: gemangioma (kapillyar/kavernoz/lobulyar), pyogenic granuloma, angiokeratoma, "
        "limfangioma, Kaposi sarkomasi, angiosarkoma;\n"
        "- Adneksal: trichofolliculoma, pilomatricoma, syringoma, hidradenoma, sebaceous adenoma;\n"
        "- Limfoid: reaktiv psevdolimfoma, mycosis fungoides;\n"
        "- Yallig'lanish: psoriasis, spongiotik dermatit, lichen planus, interface/lupus, "
        "granuloma annulare, sarkoidoz, leykositoklastik vaskulit, erythema nodosum;\n"
        "- Kista: epidermal inklyuzion kista, pilar (trichilemmal) kista.\n"
        "TAQIQLANGAN: buyrak RCC, urotel, sut bezi tashxislari; qisqa 60/30/10 foiz vitrinasi; "
        "dalilsiz melanoma yoki DFSP."
    ),
    "sut_bezi": (
        "FAQAT SUT BEZI: intraductal papilloma; ADH/DCIS; encapsulated/solid papillary carcinoma; "
        "invasive ductal/lobular; phyllodes. TAQIQLANGAN: RCC, urotel, teri SCC ni asosiy qilish."
    ),
    "qovuq": (
        "FAQAT QOVUQ/UROTEL: papilloma; PUNLMP; low/high-grade papillary urothelial neoplasm; CIS; "
        "invasive urothelial carcinoma. TAQIQLANGAN: RCC, teri, sut bezi."
    ),
    "prostata": (
        "FAQAT PROSTATA: HGPIN; acinar adenocarcinoma Gleason; ductal adenocarcinoma; polyp/atrophy. "
        "TAQIQLANGAN: RCC, teri, sut bezi."
    ),
    "qalqonsimon": (
        "FAQAT QALQONSIMON: PTC; NIFTP; follicular adenoma vs carcinoma; papillary hyperplasia."
    ),
    "ichak": (
        "FAQAT GI: hyperplastic polyp; adenoma; adenocarcinoma; serrated (dalil bo'lsa)."
    ),
    "yumurtalik": (
        "FAQAT YUMURTALIK: serous/mucinous/endometrioid oilasi — faqat dalil."
    ),
    "buyrak": (
        "FAQAT BUYRAK: papillary RCC; clear cell RCC; oncocytoma; chromophobe — "
        "glomerula yoki buyrak naychasi ko'rinsa. Teri/sut bezi tashxisini yozma."
    ),
    "endometrium": (
        "FAQAT ENDOMETRIUM: hyperplasia ± atypia; endometrioid carcinoma; serous endometrial."
    ),
    "opka": (
        "FAQAT O'PKA: squamous / adenocarcinoma / neuroendocrine — kuchli dalil; aks holda IHC."
    ),
    "noaniq": (
        "Avval to'qima tipini (epidermis vs bez vs urotel vs glomerula) yoz. "
        "Noaniq bo'lsa RCC ni SUKUTAN tanlama."
    ),
}

_HISTOLOGY_FOREIGN_MARKERS = {
    "teri": (
        "papillary renal", "renal cell", "buyrak rak", "buyrak karsinom", "buyrak adenokarsinom",
        "clear cell rcc", "ccrcc", "prcc", "oncocytoma", "onkotsitom", "chromophobe",
        "xromofob", "punlmp", "urothelial carcinoma", "intraductal papilloma",
        "gleason", "nottingham",
    ),
    "sut_bezi": (
        "papillary renal", "renal cell", "buyrak rak", "punlmp", "urothelial",
        "actinic keratosis", "seborrheic keratosis", "basal cell",
    ),
    "qovuq": (
        "papillary renal", "renal cell", "buyrak rak", "seborrheic", "intraductal papilloma",
    ),
    "buyrak": (
        "seborrheic keratosis", "actinic keratosis", "basal cell carcinoma",
        "intraductal papilloma", "punlmp",
    ),
}

_HISTOLOGY_ORGAN_GATE_SYSTEM = (
    "You are an internal pathology router. Look at H&E photomicrograph(s) of ONE case. "
    "Return ONE JSON object only, no markdown. Never refuse. "
    "Keys: organ (code), confidence (high|medium|low), reason_uz (short Uzbek). "
    "organ codes: sut_bezi, qovuq, prostata, qalqonsimon, ichak, yumurtalik, buyrak, "
    "endometrium, teri, opka, noaniq. "
    "PRIORITY morphology: "
    "epidermis / stratum corneum / rete ridges / keratin / hair follicle / dermoepidermal junction → teri; "
    "intraductal papillae with fibrovascular cores in a dilated duct → sut_bezi; "
    "stratified urothelium with umbrella cells → qovuq; "
    "colloid/orphan Annie → qalqonsimon; corpora amylacea → prostata; "
    "glomerulus or definite renal tubules → buyrak. "
    "CRITICAL: papillary architecture ALONE is NOT kidney. "
    "Do NOT choose buyrak for skin papillomatosis, hyperkeratosis, or seborrheic-like lesions. "
    "If the clinical site/note says skin/teri/koja, organ MUST be teri. "
    "If truly ambiguous, pick the single most likely organ; default papillary-in-duct to sut_bezi, "
    "papillomatosis+keratin to teri — never default to buyrak."
)


def _parse_histology_organ(raw):
    if not raw:
        return None
    t = raw.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
        t = re.sub(r"\s*```$", "", t)
    try:
        start = t.find("{")
        end = t.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(t[start : end + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    organ = str(data.get("organ") or "").strip().lower().replace(" ", "_")
    aliases = {
        "breast": "sut_bezi",
        "sut": "sut_bezi",
        "mammary": "sut_bezi",
        "bladder": "qovuq",
        "urothelial": "qovuq",
        "prostate": "prostata",
        "thyroid": "qalqonsimon",
        "gi": "ichak",
        "ovary": "yumurtalik",
        "kidney": "buyrak",
        "lung": "opka",
        "skin": "teri",
        "unknown": "noaniq",
    }
    organ = aliases.get(organ, organ)
    if organ not in _HISTOLOGY_ORGAN_CODES:
        organ = "noaniq"
    conf = str(data.get("confidence") or "low").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"
    reason = _truncate_field(data.get("reason_uz"), 240)
    return {"organ": organ, "confidence": conf, "reason_uz": reason}


def _lock_histology_organ(image_parts, patient_context=None):
    """Klinik namuna joyi + klinik izoh + morfologiya — bitta organ qulfi."""
    p = _normalize_patient_context(patient_context)
    site_forced = _organ_from_text(p.get("specimen_site"))
    note_forced = _organ_from_text(p.get("clinical_note"))
    sex = _patient_sex_norm(p.get("sex"))
    forced = site_forced or note_forced
    if forced:
        src = p.get("specimen_site") if site_forced else p.get("clinical_note")
        log.info(
            "%s: histology organ FROM CLINIC=%s src=%r",
            ZIYRAKAI_DISPLAY_NAME,
            forced,
            src,
        )
        return {
            "organ": forced,
            "confidence": "high",
            "reason_uz": f"Klinik yo'nalish: {src}",
        }

    if not image_parts:
        return None
    try:
        low_parts = []
        for part in image_parts[: min(2, len(image_parts))]:
            url = (part.get("image_url") or {}).get("url") or ""
            low_parts.append(
                {"type": "image_url", "image_url": {"url": url, "detail": "high"}}
            )
        sex_line = f"Patient sex={p.get('sex') or 'unknown'}; age={p.get('age') or 'unknown'}."
        n_img = len(low_parts)
        organ_q = (
            f"Classify the most likely ORGAN using ALL {n_img} H&E field(s) of the SAME case. JSON only. "
            if n_img > 1
            else "Classify the most likely ORGAN for this H&E field. JSON only. "
        )
        site_hint = (p.get("specimen_site") or "").strip()
        note = p.get("clinical_note") or ""
        hint_line = (
            f" Clinical specimen site: {site_hint or '—'}. Clinical note: {note or '—'}. "
            "If site/note indicates skin/teri/koja/epidermis, organ MUST be teri. "
            "Do not output buyrak unless glomeruli/renal tubules are visible."
        )
        raw = _chat_complete(
            [
                {"role": "system", "content": _HISTOLOGY_ORGAN_GATE_SYSTEM},
                {
                    "role": "user",
                    "content": _vision_user(
                        organ_q
                        + sex_line
                        + hint_line
                        + " Respect sex: male → avoid ovary/endometrium as primary; "
                        "female → avoid prostate. Male breast is allowed only if ducts are clear. "
                        "Never remap skin to kidney or bladder.",
                        low_parts,
                    ),
                },
            ],
            {"max_tokens": 180, "temperature": 0.0, "top_p": 0.1},
            model=_router_model(),
        )
        parsed = _parse_histology_organ(raw)
        if not parsed:
            log.warning("%s: organ lock parse fail: %r", ZIYRAKAI_DISPLAY_NAME, _preview(raw))
            return None
        # Sex hard filter — ayol organlari; teri/buyrakka o'zgartirma
        if sex == "erkak" and parsed["organ"] in ("yumurtalik", "endometrium"):
            parsed = {
                "organ": "noaniq",
                "confidence": "medium",
                "reason_uz": (
                    (parsed.get("reason_uz") or "")
                    + " (erkak jinsi: ayol organi asosiy qilib olinmadi)"
                ).strip(),
            }
        if sex == "ayol" and parsed["organ"] == "prostata":
            parsed = {
                "organ": "noaniq",
                "confidence": "low",
                "reason_uz": "Ayol bemorda prostata asosiy organ qilib olinmadi.",
            }
        log.info(
            "%s: histology organ lock=%s conf=%s",
            ZIYRAKAI_DISPLAY_NAME,
            parsed["organ"],
            parsed["confidence"],
        )
        return parsed
    except Exception as e:
        log.warning("%s: organ lock xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return None


def _histology_organ_lock_text(organ_info):
    if not organ_info:
        return ""
    code = organ_info.get("organ") or "noaniq"
    name = _HISTOLOGY_ORGAN_UZ.get(code, code)
    reason = organ_info.get("reason_uz") or ""
    family = _HISTOLOGY_WHO_FAMILY.get(code) or _HISTOLOGY_WHO_FAMILY["noaniq"]
    return (
        f"#### ORGAN QULFI (o'zgartirma — buzilsa hisobot yaroqsiz)\n"
        f"Yetakchi organ: {name} ({code}).\n"
        f"Asos: {reason}\n"
        f"Oila: {family}\n"
        f"3 ta ishchi taassurot VA barcha differensial FAQAT shu organ oilasidan.\n"
        f"Boshqa organ nomini (ayniqsa buyrak/RCC, qovuq, sut bezi — agar qulf {name} bo'lmasa) yozma.\n"
        f"'BOSHQA ORGAN DIFFERENSIALI' bo'limini YARATMA.\n"
        f"Dalilsiz malignite YO'Q: malignite huquqi YO'Q bo'lsa yetakchi benign/reaktiv.\n"
    )


def _is_skin_case(organ_lock=None, patient_context=None):
    """Organ qulfi yoki klinik namuna joyi teri ekanligini aniqlash."""
    code = ((organ_lock or {}).get("organ") or "").strip().lower()
    if code == "teri":
        return True
    if code and code != "noaniq":
        return False
    p = _normalize_patient_context(patient_context)
    site = " ".join(x for x in (p.get("specimen_site"), p.get("clinical_note")) if x)
    return _organ_from_text(site) == "teri"


def _histology_protocol(organ_lock=None, patient_context=None):
    """Gistologiya protokoli; teri holatida dermatopatologiya algoritmi ham qo'shiladi."""
    if _is_skin_case(organ_lock, patient_context):
        return _HISTOLOGY_SAFE_PROTOCOL + _HISTOLOGY_DERM_PATTERN_CANON
    return _HISTOLOGY_SAFE_PROTOCOL


def _histology_report_wrong_organ(text, organ_lock):
    """Qulfdagi organga zid tashxis oilasi (teri → buyrak rak)."""
    if not text or not organ_lock:
        return False
    code = (organ_lock.get("organ") or "").strip().lower()
    markers = _HISTOLOGY_FOREIGN_MARKERS.get(code) or ()
    if not markers:
        return False
    low = (text or "").lower()
    return any(m in low for m in markers)


def _histology_report_organs_conflict(text):
    """Bir hisobotda ikki yetakchi organ oilasi aralashsa — qayta yozish kerak."""
    if not text:
        return False
    low = _strip_other_organ_differential(text).lower()
    strong_breast = ("intraductal papilloma" in low) or (
        "sut bezi" in low and ("papilloma" in low or "dcis" in low or "encapsulated papillary" in low)
    )
    strong_bladder = ("punlmp" in low) or ("urothelial" in low) or ("urotel" in low) or (
        "qovuq" in low and ("papillar" in low or "papilloma" in low)
    )
    return strong_breast and strong_bladder


def _strip_other_organ_differential(text):
    """Model ba'zan 'Boshqa organ differensiali' yozadi — olib tashlash."""
    if not text:
        return text
    cleaned = re.sub(
        r"(?im)^(?:#{1,6}\s*|\*\*|__)?\s*boshqa\s+organ\s+differensial[^\n]*\*?\*?\n"
        r"(?:(?!^#{1,6}\s)(?!^\*\*[A-ZА-ЯЁ])(?!^[A-ZА-ЯЁ][^\n]{0,40}$).*\n)*",
        "",
        text,
    )
    # Oddiy sarlavha: "Boshqa organ differensiali" keyin 1-8 qator
    cleaned = re.sub(
        r"(?im)^boshqa\s+organ\s+differensial[iı]?\s*\n"
        r"(?:.*\n){0,8}",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?im)^\s*(?:[-*•]\s*)?(?:sut\s*bezi|qovuq|prostata|qalqonsimon)\s*:\s*.*?"
        r"(?:mos\s*emas|tegishli\s*emas|u\s*uchun\s*emas).*\n?",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ─── Majburiy morfologik ko'rik (tashxisdan OLDIN) ───────────────────────────
# Model tashxis nomini o'ylashdan oldin tasvirdagi belgilarni sanab chiqadi.
# Nomlar bu bosqichda TAQIQLANGAN — aks holda model eng ko'p uchraydigan
# tashxisga (masalan seboreik keratoz) yopishib qoladi va har xil keyslarga
# bir xil javob beradi.
_HISTOLOGY_OBSERVE_SYSTEM = (
    "You are a histopathology image reader. Report ONLY what is visible in this H&E "
    "photomicrograph. Return ONE JSON object, no markdown, no commentary. "
    "CRITICAL: do NOT name any disease, tumour, or diagnosis anywhere in the output. "
    "No entity names (no 'keratosis', 'carcinoma', 'nevus', 'dermatofibroma', ...). "
    "Only descriptive morphology. If a feature is not visible, use false. "
    "Never refuse; if the field is blurry, mark sample_quality low and describe what is discernible."
)

_OBSERVE_SCHEMA = (
    '{"not_tissue": false, "sample_quality": "yaxshi|o\'rtacha|past", '
    '"magnification": "kichik|o\'rta|yuqori", '
    '"layers_present": {"epidermis": false, "dermis": false, "subcutis": false, "adnexa": false}, '
    '"epidermis": {"acanthosis": false, "hyperkeratosis": false, "parakeratosis": false, '
    '"papillomatosis": false, "horn_cysts": false, "basaloid_proliferation": false, '
    '"spongiosis": false, "koilocytes": false, "full_thickness_atypia": false, "ulceration": false, '
    '"basal_pigment": false}, '
    '"junction": {"interface_damage": false, "band_like_infiltrate": false, '
    '"melanocyte_nests": false, "single_melanocyte_proliferation": false, "pagetoid_spread": false, '
    '"clefting_retraction": false, "peripheral_palisading": false}, '
    '"dermis": {"tumour_nodule": false, "spindle_cells": false, "storiform_pattern": false, '
    '"collagen_trapping": false, "grenz_zone": false, "granuloma": false, "vasculitis": false, '
    '"vascular_proliferation": false, "dense_lymphoid_infiltrate": false, "plasma_cells": false, '
    '"eosinophils": false, "neutrophils": false, "mucin": false, "desmoplasia": false, '
    '"necrosis": false, "solar_elastosis": false, "hemosiderin": false, "fibrosis": false}, '
    '"glandular": {"glands_present": false, "cribriform": false, "papillary_fronds": false, '
    '"fibrovascular_cores": false, "goblet_cells": false, "colloid": false, "myoepithelial_layer": false}, '
    '"cytology": {"pleomorphism": "yo\'q|yengil|o\'rta|kuchli", "nuclear_grade": "1|2|3|noaniq", '
    '"mitoses_10hpf": "0|1-2|3-10|>10|noaniq", "atypical_mitoses": false, "prominent_nucleoli": false, '
    '"clear_cytoplasm": false, "keratin_pearls": false, "maturation_with_depth": false}, '
    '"invasion": "yo\'q|shubhali|bor", '
    '"dominant_pattern": "one short English phrase for the architectural pattern", '
    '"observations_uz": ["3-6 ta qisqa o\'zbekcha jumla — faqat KO\'RINGAN narsa, tashxis nomisiz"]}'
)


def _observe_enabled():
    v = (os.environ.get("HISTOLOGY_OBSERVE_PASS") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _parse_observation(raw):
    if not raw:
        return None
    t = raw.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
        t = re.sub(r"\s*```$", "", t)
    try:
        start = t.find("{")
        end = t.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(t[start : end + 1])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _observe_histology(image_parts, patient_context=None):
    """Tasvirdagi belgilarni tashxis nomisiz yig'ish — har keys uchun o'ziga xos."""
    if not image_parts or not _observe_enabled():
        return None
    p = _normalize_patient_context(patient_context)
    site = (p.get("specimen_site") or "").strip() or "—"
    try:
        user = (
            f"Clinical specimen site: {site}. "
            "Read EVERY field provided. Fill this JSON exactly, same keys, no extra keys:\n"
            + _OBSERVE_SCHEMA
            + "\nRemember: NO diagnosis names anywhere."
        )
        raw = _chat_complete(
            [
                {"role": "system", "content": _HISTOLOGY_OBSERVE_SYSTEM},
                {"role": "user", "content": _vision_user(user, image_parts)},
            ],
            {"max_tokens": 1200, "temperature": 0.0, "top_p": 0.1},
        )
        data = _parse_observation(raw)
        if not data:
            log.warning("%s: ko'rik JSON o'qilmadi: %r", ZIYRAKAI_DISPLAY_NAME, _preview(raw))
            return None
        log.info(
            "%s: ko'rik pattern=%r invaziya=%s sifat=%s belgilar=%s",
            ZIYRAKAI_DISPLAY_NAME,
            str(data.get("dominant_pattern"))[:60],
            data.get("invasion"),
            data.get("sample_quality"),
            len(_true_features(data)),
        )
        return data
    except Exception as e:
        log.warning("%s: ko'rik xato (tahlil davom etadi): %s", ZIYRAKAI_DISPLAY_NAME, e)
        return None


_FEATURE_UZ = {
    "acanthosis": "akantoz",
    "hyperkeratosis": "giperkeratoz",
    "parakeratosis": "parakeratoz",
    "papillomatosis": "papillomatoz",
    "horn_cysts": "shox kistalari",
    "basaloid_proliferation": "bazaloid proliferatsiya",
    "spongiosis": "spongioz",
    "koilocytes": "koilotsitlar",
    "full_thickness_atypia": "to'liq qalinlikdagi atipiya",
    "ulceration": "yara",
    "basal_pigment": "bazal pigment",
    "interface_damage": "interfeys shikasti",
    "band_like_infiltrate": "lentasimon infiltrat",
    "melanocyte_nests": "melanotsitar uyalar",
    "single_melanocyte_proliferation": "yakka melanotsit proliferatsiyasi",
    "pagetoid_spread": "pagetoid tarqalish",
    "clefting_retraction": "stroma retraksiyasi (kleft)",
    "peripheral_palisading": "periferik palisad",
    "tumour_nodule": "dermal tugun",
    "spindle_cells": "duksimon hujayralar",
    "storiform_pattern": "storiform pattern",
    "collagen_trapping": "kollagen tuzog'i",
    "grenz_zone": "Grenz zonasi",
    "granuloma": "granuloma",
    "vasculitis": "vaskulit",
    "vascular_proliferation": "tomir proliferatsiyasi",
    "dense_lymphoid_infiltrate": "zich limfoid infiltrat",
    "plasma_cells": "plazmatik hujayralar",
    "eosinophils": "eozinofillar",
    "neutrophils": "neytrofillar",
    "mucin": "musin",
    "desmoplasia": "desmoplaziya",
    "necrosis": "nekroz",
    "solar_elastosis": "solar elastoz",
    "hemosiderin": "gemosiderin",
    "fibrosis": "fibroz",
    "glands_present": "bezlar",
    "cribriform": "cribriform",
    "papillary_fronds": "papillyar shoxlar",
    "fibrovascular_cores": "fibrovaskulyar o'zak",
    "goblet_cells": "goblet hujayralar",
    "colloid": "kolloid",
    "myoepithelial_layer": "myoepitelial qavat",
    "atypical_mitoses": "atipik mitozlar",
    "prominent_nucleoli": "yirik yadrocha",
    "clear_cytoplasm": "tiniq sitoplazma",
    "keratin_pearls": "keratin marvaridlari",
    "maturation_with_depth": "chuqurlik bo'yicha maturatsiya",
}

# Tashxis "langari": nom qo'yilsa, quyidagi SPETSIFIK belgilardan KAMIDA BITTASI
# ko'rikda topilgan bo'lishi shart. Nospetsifik belgilar (akantoz, giperkeratoz)
# ataylab kiritilmagan — ular deyarli har qanday teri kesmasida uchraydi va
# noto'g'ri tashxisni "oqlab" yuboradi.
_DX_REQUIRED_FEATURES = {
    "seborrheic keratosis": ("horn_cysts", "basaloid_proliferation"),
    "seboreik keratoz": ("horn_cysts", "basaloid_proliferation"),
    "verruca": ("koilocytes", "papillomatosis"),
    "verruka": ("koilocytes", "papillomatosis"),
    "basal cell carcinoma": ("peripheral_palisading", "clefting_retraction", "basaloid_proliferation"),
    "bazal hujayrali": ("peripheral_palisading", "clefting_retraction", "basaloid_proliferation"),
    "squamous cell carcinoma": ("full_thickness_atypia", "keratin_pearls"),
    "actinic keratosis": ("parakeratosis", "solar_elastosis"),
    "aktinik keratoz": ("parakeratosis", "solar_elastosis"),
    "dermatofibroma": ("collagen_trapping", "spindle_cells", "tumour_nodule"),
    "dermatofibrosarcoma": ("storiform_pattern",),
    "dfsp": ("storiform_pattern",),
    "melanoma": ("pagetoid_spread", "single_melanocyte_proliferation", "melanocyte_nests"),
    "melanom": ("pagetoid_spread", "single_melanocyte_proliferation", "melanocyte_nests"),
    "nevus": ("melanocyte_nests", "single_melanocyte_proliferation"),
    "psoriaz": ("parakeratosis",),
    "psoriasis": ("parakeratosis",),
    "lichen planus": ("band_like_infiltrate", "interface_damage"),
    "granuloma annulare": ("granuloma",),
    "sarkoidoz": ("granuloma",),
    "vaskulit": ("vasculitis",),
    "hemangioma": ("vascular_proliferation",),
    "gemangiom": ("vascular_proliferation",),
    "kaposi": ("vascular_proliferation", "spindle_cells"),
    "spongiotik": ("spongiosis",),
    "mycosis fungoides": ("dense_lymphoid_infiltrate", "pagetoid_spread"),
}


def _true_features(features):
    """Ko'rikda TRUE bo'lgan belgilar ro'yxati (kalit nomlari)."""
    out = []
    if not isinstance(features, dict):
        return out
    for group in ("epidermis", "junction", "dermis", "glandular", "cytology"):
        sub = features.get(group)
        if isinstance(sub, dict):
            for k, v in sub.items():
                if v is True:
                    out.append(k)
    return out


def _features_prompt_block(features):
    """Ko'rik natijasi — tashxis shu belgilardan kelib chiqishi shart."""
    if not isinstance(features, dict):
        return ""
    present = [_FEATURE_UZ.get(k, k) for k in _true_features(features)]
    absent = [
        _FEATURE_UZ.get(k, k)
        for k in (
            "horn_cysts", "koilocytes", "peripheral_palisading", "pagetoid_spread",
            "storiform_pattern", "collagen_trapping", "granuloma", "vascular_proliferation",
            "full_thickness_atypia", "keratin_pearls", "melanocyte_nests",
        )
        if not _feature_true(features, k)
    ]
    cyt = features.get("cytology") if isinstance(features.get("cytology"), dict) else {}
    lines = [
        "#### TASVIRDAN OLINGAN BELGILAR (avtomatik ko'rik — tashxis SHU ro'yxatdan chiqadi)",
        f"Pattern: {_truncate_field(features.get('dominant_pattern'), 120) or '—'}",
        f"Invaziya: {features.get('invasion') or 'noaniq'} | "
        f"Pleomorfizm: {cyt.get('pleomorphism') or 'noaniq'} | "
        f"Mitoz/10HPF: {cyt.get('mitoses_10hpf') or 'noaniq'} | "
        f"Namuna sifati: {features.get('sample_quality') or 'noaniq'}",
        "KO'RINGAN: " + (", ".join(present[:26]) if present else "—"),
        "KO'RINMAGAN (muhim): " + (", ".join(absent[:14]) if absent else "—"),
    ]
    obs = features.get("observations_uz")
    if isinstance(obs, list) and obs:
        lines.append("Ko'rik izohi:")
        for o in obs[:6]:
            t = _truncate_field(o, 200)
            if t:
                lines.append(f"- {t}")
    lines.append(
        "QOIDA: tashxis FAQAT «KO'RINGAN» belgilarga tayanadi. «KO'RINMAGAN» belgini "
        "talab qiladigan tashxisni QO'YMA. Agar ko'ringan belgilar biror aniq nozologiyaga "
        "yetarli bo'lmasa — «Aniq tashxis uchun yetarli emas» deb yoz va nima kerakligini ayt."
    )
    return "\n".join(lines) + "\n"


def _feature_true(features, key):
    if not isinstance(features, dict):
        return False
    for group in ("epidermis", "junction", "dermis", "glandular", "cytology", "layers_present"):
        sub = features.get(group)
        if isinstance(sub, dict) and key in sub:
            return sub[key] is True
    return False


def _report_contradicts_features(text, features):
    """Hisobotdagi tashxis ko'rikda topilmagan belgiga tayanmayaptimi."""
    if not text or not isinstance(features, dict):
        return ""
    dx = _histology_dx_block(text).lower()
    # Rad etilgan nomlar («… EMAS», «… YO'Q») qo'yilgan tashxis emas
    dx = re.sub(r"[^\n]*\b(emas|yo'q|yoq)\b[^\n]*", " ", dx)
    for name, required in _DX_REQUIRED_FEATURES.items():
        if name not in dx:
            continue
        missing = [k for k in required if not _feature_true(features, k)]
        if len(missing) == len(required):
            return (
                f"«{name}» qo'yilgan, lekin ko'rikda uning birorta asosiy belgisi topilmadi: "
                + ", ".join(_FEATURE_UZ.get(m, m) for m in missing)
            )
    return ""


def _features_query_text(features):
    """Kitob qidiruvi uchun — har tasvirga o'ziga xos so'rov."""
    if not isinstance(features, dict):
        return ""
    keys = _true_features(features)[:14]
    pattern = str(features.get("dominant_pattern") or "").strip()
    inv = features.get("invasion") or ""
    parts = [k.replace("_", " ") for k in keys]
    if pattern:
        parts.insert(0, pattern)
    if inv and inv != "yo'q":
        parts.append("stromal invasion")
    return " ".join(parts)[:400]


def _worksheet_user(lab_type, organ_lock=None, kb_block=""):
    m = _lab_meta(lab_type)
    extra = _histology_protocol(organ_lock) if lab_type == "histology" else (
        "Qisqa: pattern/tuzilma NOMLARI, keyin bitta ishchi taassurot va uning asosi."
    )
    lock = _histology_organ_lock_text(organ_lock) if lab_type == "histology" else ""
    kb = ("\n" + kb_block + "\n") if (lab_type == "histology" and kb_block) else ""
    return (
        f"Bu {m['specimen']} maydoni. {m['label']}. Ichki LIS qoralama. Imzo emas.\n"
        f"{m['forbid']}\n\n"
        + lock
        + kb
        + "Jadval YOZMA. Baho 1-5 ISHLATMA. Faqat 4 bo'lim: TASHXIS, NEGA SHU TASHXIS, "
        "FAKT, NEGA BOSHQASI EMAS.\n"
        f"Ichkarida tekshiriladigan maydonlar: {m['count']}.\n"
        + extra
        + "\nYulduzcha ** yo'q. Rad etma."
    )


def _describe_user(lab_type, organ_lock=None, kb_block=""):
    m = _lab_meta(lab_type)
    if lab_type == "histology":
        kb = ("\n" + kb_block + "\n") if kb_block else ""
        return (
            f"H&E tissue photomicrograph. You are a histopathology chair. NO RIGHT TO ERR: "
            f"wrong organ, invented findings, other lab protocols = INVALID. "
            f"Write a SHORT pathology report in Uzbek: the diagnosis, why it is that "
            f"diagnosis, and the visible facts. Nothing else. 1500-3000 characters total. "
            f"NO Q&A, NO prevention, NO treatment plan, NO teaching text, NO tables, NO percentages. "
            f"Do NOT lead with cancer unless invasion+Essential are VISIBLE. "
            f"Required line: Malignite qo'yish huquqi HA/YO'Q. Never refuse a real H&E field.\n"
            f"{m['forbid']}\n\n"
            + _histology_organ_lock_text(organ_lock)
            + kb
            + _histology_protocol(organ_lock)
        )
    return (
        f"Microscope field of {m['specimen']}. Internal LIS note in Uzbek as {m['role']}. "
        f"ONLY {m['label']}. {m['forbid']}\n"
        "Unsigned draft. Named patterns, then 3 working impressions with %.\n"
        f"Describe: {m['count']}. If absent, write 0 and why. This lab is histology-only — no blood-smear CBC."
    )


def _histology_dx_block(text):
    """FAQAT «#### TASHXIS» bo'limi.

    «NEGA BOSHQASI EMAS» bo'limida rad etilgan nomlar (masalan «DFSP — storiform
    YO'Q») qo'yilgan tashxis deb hisoblanmasligi kerak.
    """
    m = re.search(
        r"#+\s*(?:aniq\s+)?tashxis\b[^\n]*\n(.{0,900}?)(?=\n#+\s|\Z)",
        text or "",
        flags=re.I | re.S,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"#+\s*(?:aniq\s+)?tashxis\b(.{0,900}?)(?=\n#+\s|\Z)",
        text or "",
        flags=re.I | re.S,
    )
    return m.group(1) if m else (text or "")[:300]


_MALIGN_LEAD_RE = re.compile(
    r"carcinom|karsinom|adenokarsinom|sarkom|sarcom|melanom|"
    r"\brcc\b|renal cell|buyrak\s+rak|yomon\s+o'sma|yomon\s+osma|"
    r"invaziv\s+scc|invasive\s+squamous|\bmalignant\b",
    re.I,
)


def _histology_cancer_overcall(text):
    """Yetakchi tashxisda dalilsiz rak — qayta yozish."""
    if not text:
        return False
    block = _histology_dx_block(text)
    lead = ""
    for line in block.splitlines():
        if re.search(r"yetakchi|1-o.?rin", line, re.I):
            lead = line
            break
    if not lead:
        lead = block[:600]
    if not _MALIGN_LEAD_RE.search(lead):
        return False
    low = text.lower().replace("‘", "'").replace("’", "'")
    ha = bool(re.search(r"malignite\s+qo'?yish\s+huquqi\s*:\s*ha\b", low))
    inv = bool(
        re.search(
            r"invaziy[aeis]\s*:\s*ha\b|stromal\s+invaziya\s*:\s*ha|"
            r"invaziya\s+aniq\s+ko'?rin|bazal\s+membrana\s+buzil",
            low,
        )
    )
    pcts = [int(x) for x in re.findall(r"(\d{1,3})\s*%", lead)]
    if any(p >= 55 for p in pcts) and not inv:
        return True
    if not ha:
        return True
    if ha and not inv:
        return True
    return False


_MELANOMA_LEAD_RE = re.compile(r"melanom", re.I)


def _histology_melanoma_overcall(text):
    """Melanoma yetakchi bo'lsa — Breslow / mitoz / pagetoid dalili majburiy."""
    if not text:
        return False
    block = _histology_dx_block(text)
    lead = ""
    for line in block.splitlines():
        if re.search(r"yetakchi|1-o.?rin|tashxis\s*\(", line, re.I):
            lead = line
            break
    if not lead:
        lead = block[:600]
    if not _MELANOMA_LEAD_RE.search(lead):
        return False
    low = text.lower().replace("‘", "'").replace("’", "'")
    if "nevus" in lead.lower() and "melanoma" not in lead.lower():
        return False
    has_breslow = bool(re.search(r"breslow|qalinlig[i']?\s*[:=]?\s*\d|\d[\.,]?\d*\s*mm", low))
    has_mitosis = bool(re.search(r"mitoz", low))
    has_pattern = bool(re.search(r"pagetoid|maturatsiya|atipik melanotsit|assimetri", low))
    return not (has_breslow and has_mitosis and has_pattern)


def _looks_like_weak_generic(text, lab_type, organ_lock=None):
    if not text:
        return True
    low = text.lower()
    if lab_type == "histology":
        if "savol:" in low or "savol :" in low:
            return True
        if "quyoshdan himoya" in low:
            return True
        if len(text) < 400:
            return True
        if _missing_diagnosis_sections(text, lab_type):
            return True
        organ = any(x in low for x in (
            "prostata", "sut bezi", "qovuq", "siydik pufak", "qalqon",
            "endometrium", "ichak", "yumurtalik", "buyrak", "o'pka",
            "teri", "urotel", "intraductal", "ductal",
        ))
        if not organ:
            return True
        named = any(x in low for x in (
            "papilloma", "carcinoma", "karsinom", "adenom", "dcis", "punlmp",
            "keratosis", "keratoz", "bowen", "bcc", "gleason", "niftp", "pin", "verruca",
            "malignite qo'yilmaydi", "malignite quyilmaydi", "yetarli mezon",
            "dermatofibroma", "dfsp", "fibroxantom", "fibrous histiocytoma",
            "nevus", "melanom", "psoriaz", "lichen", "granulom", "vaskulit",
            "gemangiom", "spongiotik", "dermatit", "kista", "yetarli emas",
        ))
        if not named:
            return True
        generic_only = (
            ("papillary adenoma" in low or "papillar adenoma" in low)
            and "intraductal" not in low
            and "ductal adenokarsinom" not in low
            and "urotel" not in low
            and "encapsulated" not in low
        )
        if generic_only:
            return True
        if _histology_report_organs_conflict(text):
            return True
        if _histology_report_wrong_organ(text, organ_lock):
            return True
        if _histology_cancer_overcall(text):
            return True
        if _histology_melanoma_overcall(text):
            return True
        if low.count("%") >= 3:
            return True
        if _too_verbose(text, lab_type):
            return True
        if (organ_lock or {}).get("organ") == "teri":
            has_pattern = any(x in low for x in (
                "reaksiya patterni", "pattern", "skaner ko'rin", "skaner korin",
                "hujayra yo'nalishi", "hujayra yonalishi",
            ))
            if not has_pattern:
                return True
            has_layers = any(x in low for x in ("epidermis", "dermis", "epiderm"))
            if not has_layers:
                return True
    return False


def _looks_like_wrong_blood_smear(text, lab_type):
    if not text or lab_type in _BLOOD_SMEAR_LABS:
        return False
    low = text.lower()
    hits = sum(1 for m in _BLOOD_SMEAR_MARKERS if m in low)
    return hits >= 3


OUTPUT_FORMAT_HISTOLOGY_UZ = """
---
CHIQISH (qat'iy): faqat 4 bo'lim, jami 1500–3000 belgi.
#### TASHXIS
#### NEGA SHU TASHXIS
#### FAKT (ko'rinadigan morfologiya)
#### NEGA BOSHQASI EMAS
Yulduzcha ** yo'q. Jadval yo'q. Foiz yo'q. Boshqa sarlavha yo'q.
Uzun muhokama, o'quv matni, profilaktika, davolash — YOZILMAYDI.
"""


def _append_output_format(prompt, lab_type=None):
    return (prompt or "").rstrip() + "\n\n" + OUTPUT_FORMAT_HISTOLOGY_UZ

def _full_analysis_prompt(base, microscope_prefix, lab_type=None, patient_context=None):
    """Bemor konteksti + yo'nalish protokoli."""
    merged = _merge_prompt_with_microscope(base, microscope_prefix)
    lock = _lab_lock_text(lab_type or "histology")
    patient = _patient_prompt_prefix(patient_context, lab_type or "histology")
    parts = [lock]
    if patient:
        parts.append(patient)
    parts.append(CLINICAL_EXCELLENCE_PREFIX_UZ.strip())
    if (lab_type or "") == "histology":
        parts.append(_HISTOLOGY_PATIENT_SAFETY.strip())
        parts.append(_HISTOLOGY_CANON_REF.strip())
        parts.append(_HISTOLOGY_WHO_STRICT.strip())
        parts.append(_HISTOLOGY_TEACHING_DEEP.strip())
        if _is_skin_case(None, patient_context):
            parts.append(_HISTOLOGY_DERM_PATTERN_CANON.strip())
    parts.append(merged)
    return _append_output_format("\n\n".join(parts), lab_type)


_SITE_ORGAN_HINTS = (
    ("kojniy rog", "teri"),
    ("kozhnyy rog", "teri"),
    ("cutaneous horn", "teri"),
    ("seborrheic", "teri"),
    ("seborrey", "teri"),
    ("actinic keratosis", "teri"),
    ("aktinichesk", "teri"),
    ("keratoakantom", "teri"),
    ("keratoacanthoma", "teri"),
    ("squamous papilloma", "teri"),
    ("epidermis", "teri"),
    ("epiderm", "teri"),
    ("dermoepidermal", "teri"),
    ("cutaneous", "teri"),
    ("dermatopat", "teri"),
    ("keratoz", "teri"),
    ("keratosis", "teri"),
    ("kojniy", "teri"),
    ("kozhnyy", "teri"),
    ("кожный", "teri"),
    ("кожн", "teri"),
    ("teri", "teri"),
    ("skin", "teri"),
    ("koja", "teri"),
    ("kozha", "teri"),
    ("кожа", "teri"),
    ("sut bezi", "sut_bezi"),
    ("ko'krakdan", "sut_bezi"),
    ("kokrakdan", "sut_bezi"),
    ("ko'krak", "sut_bezi"),
    ("kokrak", "sut_bezi"),
    ("breast", "sut_bezi"),
    ("mamma", "sut_bezi"),
    ("siydik pufak", "qovuq"),
    ("urothelial", "qovuq"),
    ("qovuq", "qovuq"),
    ("bladder", "qovuq"),
    ("urotel", "qovuq"),
    ("prostata", "prostata"),
    ("prostate", "prostata"),
    ("qalqonsimon", "qalqonsimon"),
    ("qalqon", "qalqonsimon"),
    ("thyroid", "qalqonsimon"),
    ("endometr", "endometrium"),
    ("bachadon", "endometrium"),
    ("yumurtalik", "yumurtalik"),
    ("ovary", "yumurtalik"),
    ("oshqozon", "ichak"),
    ("colon", "ichak"),
    ("ichak", "ichak"),
    ("renal cell", "buyrak"),
    ("buyrak", "buyrak"),
    ("kidney", "buyrak"),
    ("glomerul", "buyrak"),
    ("o'pka", "opka"),
    ("opka", "opka"),
    ("lung", "opka"),
)


def _organ_from_text(text):
    low = (text or "").strip().lower().replace("ё", "е")
    if not low:
        return None
    # Uzunroq kalit avval (masalan "sut bezi" > tasodifiy qism)
    for hint, code in sorted(_SITE_ORGAN_HINTS, key=lambda x: len(x[0]), reverse=True):
        if hint in low:
            return code
    return None


def _organ_from_specimen_site(site):
    return _organ_from_text(site)


def _normalize_patient_context(patient_context):
    if not patient_context or not isinstance(patient_context, dict):
        return {}
    out = {}
    for k, maxlen in (
        ("patient_name", 120),
        ("sample_id", 40),
        ("age", 8),
        ("sex", 16),
        ("ward", 80),
        ("specimen_site", 80),
        ("clinical_note", 200),
        ("region", 40),
        ("locality", 80),
        ("clinic", 8),
        ("facility_type", 8),
        ("priority", 16),
    ):
        out[k] = _truncate_field(patient_context.get(k), maxlen)
    return out


def _patient_sex_norm(sex):
    s = (sex or "").strip().lower()
    if s.startswith("ayol") or s in ("f", "female", "woman"):
        return "ayol"
    if s.startswith("erkak") or s in ("m", "male", "man"):
        return "erkak"
    return ""


def _patient_lab_mismatch_message(lab_type, patient_context):
    """Jins / lab turi ziddiyati — tahlilni to'xtatish."""
    p = _normalize_patient_context(patient_context)
    sex = _patient_sex_norm(p.get("sex"))
    if not sex:
        return None
    if lab_type == "spermogram" and sex == "ayol":
        return (
            "#### BEMOR MA'LUMOTI VA TAHLIL TURI MOS EMAS\n\n"
            "Jins: **Ayol**, tanlangan tahlil: **Spermogramma**.\n"
            "Bu kombinatsiya klinik jihatdan noto'g'ri. Jinsni yoki tahlil turini tuzating."
        )
    if lab_type == "prostata_sok" and sex == "ayol":
        return (
            "#### BEMOR MA'LUMOTI VA TAHLIL TURI MOS EMAS\n\n"
            "Jins: **Ayol**, tanlangan tahlil: **Prostata SOK**.\n"
            "Jinsni yoki tahlil turini tuzating."
        )
    if lab_type == "smear" and sex == "erkak":
        return (
            "#### BEMOR MA'LUMOTI VA TAHLIL TURI MOS EMAS\n\n"
            "Jins: **Erkak**, tanlangan tahlil: **Ginekologik mazok**.\n"
            "Jinsni yoki tahlil turini tuzating."
        )
    if lab_type == "histology":
        site = (p.get("specimen_site") or "").strip()
        if not site:
            return (
                "#### NAMUNA JOYI KERAK\n\n"
                "Gistologiya uchun **Namuna joyi (organ)** majburiy "
                "(masalan: Teri, sut bezi, qovuq, prostata).\n"
                "Chapdagi bemor formasida namuna joyini to'ldirib, qayta tahlil qiling.\n"
                "Kliniksiz organ taxmin qilish — xato xavfi yuqori, shuning uchun to'xtatildi."
            )
        site_organ = _organ_from_specimen_site(site)
        if site_organ == "sut_bezi" and sex == "erkak":
            # male breast exists but rare — allow with note, don't block
            return None
        if site_organ == "prostata" and sex == "ayol":
            return (
                "#### BEMOR MA'LUMOTI VA NAMUNA JOYI MOS EMAS\n\n"
                f"Jins: **Ayol**, namuna joyi: **{site}** (prostata).\n"
                "Jins yoki namuna joyini tuzating."
            )
    return None


def _patient_prompt_prefix(patient_context, lab_type="histology"):
    p = _normalize_patient_context(patient_context)
    if not any(p.values()):
        return ""
    lines = [
        "### BEMOR VA NAMUNA KONTEKSTI (majburiy — e'tiborsiz qoldirma)",
        "Quyidagi ma'lumotlar LIS kartasidan. Tasvirga zid bo'lsa — ziddiyatni YOZ, "
        "lekin bemor jinsi/yoshi/namuna joyini IGNORE QILMA. Random organ tanlama.",
    ]
    if p.get("patient_name"):
        lines.append(f"- F.I.Sh.: {p['patient_name']}")
    if p.get("sample_id"):
        lines.append(f"- Namuna №: {p['sample_id']}")
    if p.get("age"):
        lines.append(f"- Yosh: {p['age']}")
    if p.get("sex"):
        lines.append(f"- Jins: {p['sex']}")
    if p.get("ward"):
        lines.append(f"- Bo'lim: {p['ward']}")
    if p.get("specimen_site"):
        lines.append(f"- Namuna joyi (klinik organ): {p['specimen_site']}")
    if p.get("clinical_note"):
        lines.append(f"- Klinik izoh: {p['clinical_note']}")
    if p.get("priority"):
        lines.append(f"- Ustuvorlik: {p['priority']}")
    loc = " / ".join(x for x in (p.get("region"), p.get("locality"), p.get("clinic")) if x)
    if loc:
        lines.append(f"- Muassasa: {loc} ({p.get('facility_type') or '—'})")

    sex = _patient_sex_norm(p.get("sex"))
    site_organ = _organ_from_text(
        " ".join(x for x in (p.get("specimen_site"), p.get("clinical_note")) if x)
    )
    lines.append("")
    lines.append("QAT'IY QOIDALAR:")
    if site_organ:
        name = _HISTOLOGY_ORGAN_UZ.get(site_organ, site_organ)
        lines.append(
            f"- Namuna joyi → yetakchi organ QULFI: {name}. "
            "3 ta ishchi taassurot VA differensial FAQAT shu organ. "
            "Boshqa organ (sut bezi/qovuq va h.k.) ni umuman yozma."
        )
    if sex == "erkak":
        lines.append(
            "- Bemor ERKAK: sut bezi (ayol) asosiy tashxisini qo'yma, "
            "agar namuna joyi aniq 'sut bezi/breast' deb yozilmagan bo'lsa."
        )
        lines.append("- Yumurtalik / endometrium / ginekologik organ — asosiy qilma.")
    if sex == "ayol":
        lines.append(
            "- Bemor AYOL: prostata asosiy tashxisini qo'yma "
            "(namuna joyi aniq prostata bo'lmasa)."
        )
    if lab_type == "histology":
        lines.append(
            "- Gistologiyada klinik namuna joyi morfologik 'taxmin'dan ustun. "
            "Bir xil rasmda bir marta sut bezi, keyin qovuq deb sakrama."
        )
    lines.append(
        "- Hisobot boshida qisqa 'Bemor: yosh, jins, namuna joyi' qatorini yoz."
    )
    return "\n".join(lines)

# ─── Global state ─────────────────────────────────────────────────────────────
camera        = None
camera_index  = 0
stream_active = False
frame_lock    = threading.Lock()
latest_frame  = None
preview_jpeg  = None  # jonli oqim uchun oldindan JPEG (tezlik)

def _preview_fps():
    try:
        return max(12.0, min(float(os.environ.get("PREVIEW_FPS", "25")), 30.0))
    except ValueError:
        return 25.0


def _preview_max_edge():
    try:
        return max(640, min(int(os.environ.get("PREVIEW_MAX_EDGE", "1280")), 1920))
    except ValueError:
        return 1280


def _preview_jpeg_quality():
    try:
        return max(40, min(int(os.environ.get("PREVIEW_JPEG_QUALITY", "62")), 85))
    except ValueError:
        return 62


def _encode_preview_jpeg(frame):
    """Jonli ko'rsatish: kichikroq JPEG — tahlil uchun latest_frame to'liq qoladi."""
    if frame is None:
        return None
    img = frame
    h, w = img.shape[:2]
    edge = _preview_max_edge()
    m = max(w, h)
    if m > edge:
        scale = edge / float(m)
        img = cv2.resize(
            img,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_LINEAR,
        )
    ok, buf = cv2.imencode(
        ".jpg",
        img,
        [int(cv2.IMWRITE_JPEG_QUALITY), _preview_jpeg_quality()],
    )
    if not ok:
        return None
    return buf.tobytes()
analysis_lock = threading.Lock()
latest_analysis = {
    "text": "", "lines": [], "timestamp": "",
    "status": "kutilmoqda", "loading": False,
    "lab_type": "",
    "job_id": "",
    "public_id": "",
    "user_id": None,
    "img_count": 0,
}
_completed_jobs = {}
_COMPLETED_JOBS_MAX = 40


def _publish_analysis(updates):
    """latest_analysis ni yangilash; tugagan ishni job_id bo'yicha saqlab qo'yish."""
    with analysis_lock:
        latest_analysis.update(updates)
        if updates.get("loading") is False:
            jid = latest_analysis.get("job_id") or ""
            if jid:
                _completed_jobs[jid] = latest_analysis.copy()
                while len(_completed_jobs) > _COMPLETED_JOBS_MAX:
                    _completed_jobs.pop(next(iter(_completed_jobs)), None)


def take_completed_job(job_id):
    """Persist uchun tugagan ish nusxasi (boshqa tahlil boshlansa ham yo'qolmaydi)."""
    if not job_id:
        return None
    with analysis_lock:
        return _completed_jobs.pop(job_id, None)


def begin_analysis_job(lab_type, status="tahlil_qilinmoqda", user_id=None):
    """Yangi tahlil ishini belgilash. Band bo'lsa None qaytaradi."""
    job_id = uuid.uuid4().hex
    with analysis_lock:
        if latest_analysis.get("loading"):
            return None
        latest_analysis.update({
            "job_id": job_id,
            "loading": True,
            "status": status,
            "lab_type": lab_type,
            "text": "",
            "lines": [],
            "timestamp": "",
            "public_id": "",
            "user_id": user_id,
            "img_count": 0,
        })
    return job_id


def _video_temp_suffix(original_name):
    ext = os.path.splitext((original_name or "").lower())[1]
    return ext if ext in VIDEO_EXT else ".mp4"


def _ensure_bgr_frame(frame):
    """Kulrang yoki alpha kadrlarni BGR 3-kanalli qilish (imencode / tahlil)."""
    if frame is None:
        return None
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim == 3:
        ch = frame.shape[2]
        if ch == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        if ch == 1:
            return cv2.cvtColor(frame[:, :, 0], cv2.COLOR_GRAY2BGR)
    return frame


# ─── Kamera ───────────────────────────────────────────────────────────────────
_MICRO_NAME_KEYS = (
    "euromex", "bioblue", "cmex", "tucsen", "touptek", "toupview", "toupcam",
    "microscope", "mikroskop",
    "usb2.0 camera", "usb 2.0 camera", "usb2.0 cam", "imaging source",
)
_PHONE_NAME_KEYS = (
    "droidcam", "iriun", "iphone", "android", "samsung", "continuity",
    "phone", "telefon", "ip webcam", "epoccam", "ivcam",
)


def _classify_camera_name(name):
    n = (name or "").lower()
    if any(k in n for k in _MICRO_NAME_KEYS):
        return "microscope"
    if any(k in n for k in _PHONE_NAME_KEYS):
        return "phone"
    return "webcam"


def _warmup_read(cap, tries=10):
    for _ in range(tries):
        ret, frame = cap.read()
        if ret and frame is not None and getattr(frame, "size", 0):
            return True
        time.sleep(0.04)
    return False


_dshow_names_cache = []


def _dshow_device_names():
    global _dshow_names_cache
    if sys.platform != "win32":
        return list(_dshow_names_cache)
    try:
        from pygrabber.dshow_graph import FilterGraph
        graph = FilterGraph()
        names = list(graph.get_input_devices())
        del graph
        _dshow_names_cache = names
        return names
    except Exception:
        return list(_dshow_names_cache)


def _try_open_capture(index, backends):
    for backend in backends:
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            continue
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        try:
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
        except Exception:
            pass
        # MJPG + 720p — USB2 YUY2 5MP ~5 FPS; MJPG 25–30 FPS
        fourcc_mjpg = cv2.VideoWriter_fourcc(*"MJPG")
        for w, h in ((1280, 720), (800, 600), (640, 480), (1920, 1080)):
            try:
                cap.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
            except Exception:
                pass
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            try:
                cap.set(cv2.CAP_PROP_FPS, 25)
                cap.set(cv2.CAP_PROP_FOURCC, fourcc_mjpg)
            except Exception:
                pass
            if _warmup_read(cap, tries=8):
                aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or w)
                ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or h)
                log.info("Kamera %s ochildi (backend=%s, %sx%s)", index, backend, aw, ah)
                return cap
        if _warmup_read(cap, tries=6):
            log.info("Kamera %s ochildi (backend=%s, native)", index, backend)
            return cap
        cap.release()
    return None


def _open_dshow_named(substr):
    """DirectShow nomidan kamera ochish (masalan ToupcamMicro)."""
    needle = (substr or "").lower()
    if not needle:
        return None
    names = list(_dshow_names_cache) if _dshow_names_cache else _dshow_device_names()
    backends = (cv2.CAP_DSHOW, cv2.CAP_ANY)
    for i, name in enumerate(names):
        if needle in (name or "").lower():
            cap = _try_open_capture(i, backends)
            if cap is not None:
                return cap
    return None


def _open_touptek(slot=0):
    """WinUSB ToupTek/Euromex — OpenCV emas, toupcam.dll."""
    if sys.platform != "win32":
        return None
    try:
        from lab_core import toupcam_cam
        cap = toupcam_cam.open_toupcam(max(0, int(slot)))
        if cap is not None:
            log.info("Mikroskop ToupTek SDK orqali ochildi (slot=%s)", slot)
            return cap
    except Exception:
        log.exception("ToupTek SDK ochilmadi")
    return None


def open_camera(index):
    """Kamerani ochish: avval kadr olinishini tekshiradi (bo‘sh ochilishni rad etadi)."""
    idx = int(index)
    if sys.platform == "win32":
        backends = (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY)
    else:
        v4l2 = getattr(cv2, "CAP_V4L2", cv2.CAP_ANY)
        backends = (v4l2, cv2.CAP_ANY)

    names = _dshow_device_names()
    name = names[idx] if 0 <= idx < len(names) else ""
    n = (name or "").lower()
    want_sdk = (
        idx >= 16
        or "toup" in n
        or _classify_camera_name(name) == "microscope"
    )

    if want_sdk:
        slot = idx - 16 if idx >= 16 else 0
        cap = _open_touptek(slot)
        if cap is not None:
            return cap
        cap = (
            _open_dshow_named("toupcammicro")
            or _open_dshow_named("toup")
            or _open_dshow_named("usb2.0 camera")
        )
        if cap is not None:
            log.info("Mikroskop DirectShow orqali ochildi (so‘ralgan index=%s)", idx)
            return cap

    if idx < 16:
        cap = _try_open_capture(idx, backends)
        if cap is not None:
            return cap

    cap = _open_touptek(0)
    if cap is not None:
        return cap
    return (
        _open_dshow_named("toupcammicro")
        or _open_dshow_named("toup")
        or _open_dshow_named("usb2.0 camera")
    )

def capture_thread():
    global camera, latest_frame, stream_active, preview_jpeg
    interval = 1.0 / _preview_fps()
    while stream_active:
        t0 = time.perf_counter()
        if camera is None or not camera.isOpened():
            time.sleep(0.05)
            continue
        ret, frame = camera.read()
        if ret and frame is not None:
            bgr = _ensure_bgr_frame(frame).copy()
            jpeg = _encode_preview_jpeg(bgr)
            with frame_lock:
                latest_frame = bgr
                if jpeg:
                    preview_jpeg = jpeg
        elapsed = time.perf_counter() - t0
        wait = interval - elapsed
        if wait > 0.002:
            time.sleep(wait)

def generate_mjpeg():
    """Oldindan kodlangan JPEG — har mijoz qayta encode qilmaydi."""
    blank = None
    last = None
    while True:
        with frame_lock:
            buf = preview_jpeg
        if not buf:
            if blank is None:
                img = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(
                    img, "Kamera kutilmoqda...", (130, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (50, 50, 50), 2,
                )
                ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                blank = enc.tobytes() if ok else b""
            payload = blank
            time.sleep(0.08)
        else:
            payload = buf
            if payload is last:
                time.sleep(0.008)
                continue
            last = payload
        yield (
            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + payload + b"\r\n"
        )

def _probe_windows_microscope_usb():
    """BioBlue/CMEX USB (VID_0547) — WinUSB bo‘lsa OpenCV uni kamera deb ko‘rmaydi."""
    if sys.platform != "win32":
        return {"found": False, "ready": False}
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
$d = Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_0547' } | Select-Object -First 1
if (-not $d) { Write-Output '{"found":false,"ready":false}'; exit 0 }
$svc = [string]$d.Service
$ready = ($svc -match 'usbvideo')
@{
  found = $true
  name = [string]$d.FriendlyName
  instance_id = [string]$d.InstanceId
  service = $svc
  pnp_class = [string]$d.PNPClass
  ready = [bool]$ready
} | ConvertTo-Json -Compress
"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, timeout=12,
        )
        raw = (r.stdout or "").strip()
        if not raw:
            return {"found": False, "ready": False}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"found": False, "ready": False}
        found = bool(data.get("found"))
        ready = bool(data.get("ready"))
        svc = (data.get("service") or "").upper()
        hint = ""
        if found and not ready:
            if "WINUSB" in svc or (data.get("pnp_class") or "") == "USBDevice":
                hint = (
                    "Mikroskop USB da ulangan (ToupTek/Euromex). Uni MedLab ToupTek SDK orqali ochadi — "
                    "USB Video Device ni tanlamang."
                )
            else:
                hint = "Mikroskop USB da bor."
        return {
            "found": found,
            "ready": ready,
            "name": data.get("name") or "USB2.0 Camera",
            "service": data.get("service") or "",
            "pnp_class": data.get("pnp_class") or "",
            "hint": hint,
        }
    except Exception as e:
        log.warning("USB mikroskop tekshiruvi: %s", e)
        return {"found": False, "ready": False}


def scan_cameras():
    found = []
    names = _dshow_device_names()
    is_win = sys.platform == "win32"
    backends = (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY) if is_win else (cv2.CAP_ANY,)
    seen = set()

    def _add(i, name, w=0, h=0, kind=None):
        if i in seen:
            return
        seen.add(i)
        kind = kind or _classify_camera_name(name)
        found.append({
            "index": i,
            "name": name,
            "resolution": f"{w}x{h}" if w and h else "—",
            "kind": kind,
        })

    if is_win:
        try:
            from lab_core import toupcam_cam
            for slot, dev in enumerate(toupcam_cam.enum_devices()):
                label = (dev.get("name") or dev.get("model") or "ToupcamMicro").strip()
                _add(16 + slot, label, kind="microscope")
        except Exception:
            log.exception("ToupTek qurilmalar ro‘yxati olinmadi")

    for i, name in enumerate(names):
        n = (name or "").lower().strip()
        if n.startswith("usb video device"):
            continue
        if "toup" in n or _classify_camera_name(name) == "microscope":
            _add(i, name, kind="microscope")

    max_i = max(8, len(names))
    for i in range(max_i):
        if i in seen:
            continue
        opened = False
        w = h = 0
        for backend in backends:
            cap = cv2.VideoCapture(i, backend)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                opened = True
                cap.release()
                break
            cap.release()
        if not opened:
            continue
        name = names[i] if i < len(names) else f"Kamera {i}"
        if (name or "").lower().startswith("usb video device"):
            continue
        _add(i, name, w, h)

    usb = _probe_windows_microscope_usb()
    already_scope = any(c.get("kind") == "microscope" for c in found)
    if already_scope:
        usb["found"] = True
        usb["ready"] = True
        usb["sdk"] = "touptek"
    elif usb.get("found"):
        _add(16, usb.get("name") or "ToupcamMicro", kind="microscope")
        usb["ready"] = True
        usb["sdk"] = "touptek"
        usb["hint"] = ""
    usb["host"] = sys.platform
    return {"cameras": found, "microscope_usb": usb}

# ─── Mikroskop konteksti (laborant kiritadi) ──────────────────────────────────
def microscope_dict_from_input(*, json_body=None, form_get=None):
    """JSON yoki multipart form dan mikroskop parametrlari (Django/DRF uchun)."""
    ml = MAX_MICRO_FIELD_LEN

    def _g(key, default=""):
        if form_get is None:
            return default
        v = form_get(key)
        return default if v is None else v

    if json_body:
        m = json_body.get("microscope") or {}
        return {
            "ocular":       _truncate_field(m.get("ocular"), ml),
            "objective":    _truncate_field(m.get("objective"), ml),
            "total_label":  _truncate_field(m.get("total_label"), ml),
            "condenser":    _truncate_field(m.get("condenser"), ml),
            "illumination": _truncate_field(m.get("illumination"), ml),
            "notes":        _truncate_field(m.get("notes"), ml * 2),
        }
    return {
        "ocular":       _truncate_field(_g("micro_ocular"), ml),
        "objective":    _truncate_field(_g("micro_objective"), ml),
        "total_label":  _truncate_field(_g("micro_total_label"), ml),
        "condenser":    _truncate_field(_g("micro_condenser"), ml),
        "illumination": _truncate_field(_g("micro_illumination"), ml),
        "notes":        _truncate_field(_g("micro_notes"), ml * 2),
    }

def _microscope_prompt_prefix(d):
    """Tahlil uchun mikroskop holati bloklari (bo'sh bo'lsa None)."""
    if not d:
        return None
    if not any(d.values()):
        return None
    lines = [
        "### MIKROSKOP HOLATI (laborant kiritgan — tahlilni shu parametrlarga moslashtir)",
        "Quyidagi ma'lumotlar tasvir olingan paytdagi mikroskop sozlamalaridir. "
        "Hujayra o'lchamlari, ko'ruv maydoni kengligi va taxminiy sonlarni shu masshtab bilan bog'lab baholang.",
        "",
    ]
    if d.get('ocular'):
        lines.append(f"- Okulyar: {d['ocular']}")
    if d.get('objective'):
        lines.append(f"- Obyektiv: {d['objective']}")
    if d.get('total_label'):
        lines.append(f"- Umumiy kattalashtirish: {d['total_label']}")
    if d.get('condenser'):
        lines.append(f"- Kondensor / diyafragma: {d['condenser']}")
    if d.get('illumination'):
        lines.append(f"- Yoritish: {d['illumination']}")
    if d.get('notes'):
        lines.append(f"- Qo'shimcha izoh: {d['notes']}")
    lines.extend([
        "",
        "Agar parametrlar kiritilmagan bo'lsa, tasvirdan taxminiy baholash qilinishi mumkinligini natijada qisqacha yoz.",
    ])
    return "\n".join(lines)

def _merge_prompt_with_microscope(base_prompt, microscope_prefix):
    if microscope_prefix and microscope_prefix.strip():
        return microscope_prefix.strip() + "\n\n" + base_prompt
    return base_prompt

# ─── OpenAI tahlil ────────────────────────────────────────────────────────────
def _openai_image_max_px():
    try:
        v = int(os.environ.get("OPENAI_IMAGE_MAX_PX", "2048"))
    except ValueError:
        v = 2048
    return max(960, min(v, 4096))


def _openai_generation_kwargs():
    try:
        max_out = int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "16384"))
    except ValueError:
        max_out = 16384
    max_out = max(2048, min(max_out, 16384))
    try:
        temp = float(os.environ.get("OPENAI_TEMPERATURE", "0.12"))
    except ValueError:
        temp = 0.12
    try:
        top_p = float(os.environ.get("OPENAI_TOP_P", "0.85"))
    except ValueError:
        top_p = 0.85
    return {
        "max_tokens": max_out,
        "temperature": max(0.0, min(temp, 1.5)),
        "top_p": max(0.05, min(top_p, 1.0)),
    }


def _truncate_field(val, maxlen):
    if val is None:
        return ""
    s = str(val).replace("\x00", "").strip()
    if len(s) > maxlen:
        return s[:maxlen] + "…"
    return s


_LAB_ALIASES = {
    "gistologiya": "histology",
    "gistalogiya": "histology",
    "gistology": "histology",
}


def _normalize_lab_type(lab_type):
    if lab_type and isinstance(lab_type, str):
        lab_type = _LAB_ALIASES.get(lab_type.strip().lower(), lab_type.strip().lower())
        if lab_type in ALLOWED_LAB_TYPES:
            return lab_type
    return "histology"


def _pil_to_data_url(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=93)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + b64


_ANALYSIS_SYSTEM = (
    "Sen MedLab GISTOLOGIYA kafedrasi raisisan. FAQAT H&E to'qima. Adashishga haqqi YO'Q. "
    "Tashxis NOMI aniq bo'lsin va TASVIRDAGI belgilardan chiqsin. "
    "Har xil tasvirga bir xil shablon javob berish — og'ir xato. "
    "Belgilar yetarli bo'lmasa «Aniq tashxis uchun yetarli emas» deb yoz. "
    "Dalilsiz rak/karsinoma YOZMA. "
    "HISOBOT FAQAT 4 BO'LIM: #### TASHXIS, #### NEGA SHU TASHXIS, #### FAKT (ko'rinadigan morfologiya), #### NEGA BOSHQASI EMAS. Jami 1500-3000 belgi. Uzun matn, o'quv muhokamasi, savol-javob, profilaktika, davolash rejasi, professor bo'limlari, jadval, ehtimollik foizi - TAQIQLANADI. "
    "Rad etma. Faqat MedLab gistologiya."
)

_WORKSHEET_SYSTEM = (
    "Sen MedLab gistologiya hisobotini to'ldirasan (LIS). Adashishga haqqi YO'Q. "
    "WHO/Weedon NOMINI aniq yoz. Dalilsiz malignite qo'yma - avval benign/reaktiv. "
    "FAQAT gistologiya. O'zbek tili. "
    "HISOBOT FAQAT 4 BO'LIM: #### TASHXIS, #### NEGA SHU TASHXIS, #### FAKT (ko'rinadigan morfologiya), #### NEGA BOSHQASI EMAS. Jami 1500-3000 belgi. Uzun matn, o'quv muhokamasi, savol-javob, profilaktika, davolash rejasi, professor bo'limlari, jadval, ehtimollik foizi - TAQIQLANADI. "
    "Rad etma. Faqat MedLab gistologiya."
)

_SAFE_SYSTEM = (
    "You are a chair of histopathology. This LIS is HISTOLOGY ONLY. No blood smear, urine, "
    "stool, KOH, cytology smear, or another organ family. "
    "Write an INTERNAL H&E report in Uzbek for licensed staff. Unsigned draft; clinician confirms. "
    "Never refuse a histology field. If the photo is NOT an H&E tissue section, say so. "
    "PATIENT SAFETY: falsely calling cancer is the worst error. Malignancy may lead ONLY if "
    "WHO Essential criteria are VISIBLE and invasion is PROVEN; otherwise benign/reactive. "
    "Apply Weedon/WHO/Junqueira METHOD and the retrieved canon as INTERNAL reasoning; "
    "do not paste textbook text and do not narrate your reasoning. "
    "Base the diagnosis strictly on the listed observed features. Never fall back to the most "
    "common entity: if the features do not support one, say so in Uzbek and state what is needed. "
    "HISOBOT FAQAT 4 BO'LIM: #### TASHXIS, #### NEGA SHU TASHXIS, #### FAKT (ko'rinadigan morfologiya), #### NEGA BOSHQASI EMAS. Jami 1500-3000 belgi. Uzun matn, o'quv muhokamasi, savol-javob, profilaktika, davolash rejasi, professor bo'limlari, jadval, ehtimollik foizi - TAQIQLANADI. "
    "One organ only. No percentages, no tables, no teaching text. MedLab histology only."
)

_SHALLOW_MARKERS = (
    "hujayralar ko'rinadi",
    "hujayralar korinadi",
    "umuman norma",
    "patologiya aniqlanmadi",
    "o'ziga xos o'zgarish yo'q",
    "qo'shimcha izoh shart emas",
    "tahlil qoniqarli",
    "yallig'lanishli atipik",
    "to'qima o'zgarishi",
    "normal orientir",
    "baho 1-5",
    "baho 3/5",
    "arxitektura 70",
)

_EXPAND_DEEP_USER = (
    "Quyida ICHKI qoralama berilgan. Original rasmlarni qayta ko'rib, YAKUNIY qisqa "
    "hisobotni yoz: #### TASHXIS, #### NEGA SHU TASHXIS, #### FAKT (ko'rinadigan morfologiya), "
    "#### NEGA BOSHQASI EMAS. Jami 1500-3000 belgi. "
    "Muhokama, o'quv matni, foiz, jadval yozma. O'zbek tili. Yulduzcha ** yo'q.\n\n"
)

_RETRY_DEEP_USER = (
    "Oldingi matn talabga mos emas. Qayta yoz: aniq tashxis, uning sababi va ko'ringan fakt. "
    "Faqat 4 bo'lim, 1500-3000 belgi. Ortiqcha gap, savol-javob, foiz, jadval - o'chir. "
    "Rad etma.\n\n"
    "==== OLDINGI MATN ====\n"
)

_REFUSAL_MARKERS = (
    "i'm sorry, i can't assist",
    "i’m sorry, i can’t assist",
    "i cannot assist with that",
    "i can't assist with that",
    "i can’t assist with that",
    "i'm not able to assist",
    "i am not able to assist",
    "i cannot help with that",
    "i can't help with that",
    "i’m unable to assist",
    "i am unable to assist",
    "cannot provide medical",
    "can't provide medical",
    "i cannot provide a diagnosis",
    "i can't provide a diagnosis",
    "i cannot analyze medical",
    "i'm sorry, i can't help",
)

_REFUSAL_FALLBACK_UZ = (
    "Hisobotni tuzib bo'lmadi. Bir necha soniyadan keyin tahlilni qayta bosing."
)


def _looks_like_refusal(text):
    if not text:
        return True
    t = text.strip().lower()
    if any(m in t for m in _REFUSAL_MARKERS):
        return True
    if len(t) < 80 and ("can't" in t or "cannot" in t or "unable" in t):
        return True
    return False


def _router_model():
    """Tekshiruv/organ aniqlash uchun model — arzonroq/tezroqqa almashtirish mumkin."""
    return (os.environ.get("OPENAI_ROUTER_MODEL") or OPENAI_MODEL_ID).strip() or OPENAI_MODEL_ID


def _chat_complete(messages, kwargs, model=None):
    max_retries = max(1, int(os.environ.get("OPENAI_MAX_RETRIES", "3")))
    base_delay = float(os.environ.get("OPENAI_RETRY_DELAY_SEC", "2"))
    call_kwargs = dict(kwargs or {})
    model_id = (model or OPENAI_MODEL_ID).strip() or OPENAI_MODEL_ID
    for attempt in range(max_retries):
        try:
            resp = openai_client.chat.completions.create(
                model=model_id,
                messages=messages,
                **call_kwargs,
            )
            choice = (resp.choices or [None])[0]
            if choice is None:
                return "%s javobi bo‘sh." % ZIYRAKAI_DISPLAY_NAME
            text = (choice.message.content or "").strip()
            fr = getattr(choice, "finish_reason", None)
            if fr in ("content_filter",) or (not text and fr == "content_filter"):
                log.warning("%s: content_filter", ZIYRAKAI_DISPLAY_NAME)
                return "I'm sorry, I can't assist with that."
            if text:
                if len(text) < 400:
                    log.warning(
                        "%s: qisqa javob fr=%s len=%s: %r",
                        ZIYRAKAI_DISPLAY_NAME,
                        fr,
                        len(text),
                        text[:180],
                    )
                return text
            return (
                "%s javob matni bo'sh yoki to'liq emas (finish_reason=%s). "
                "Keyinroq qayta urinib ko'ring."
            ) % (ZIYRAKAI_DISPLAY_NAME, fr)
        except Exception as e:
            err_s = str(e).lower()
            if "seed" in err_s and "seed" in call_kwargs:
                call_kwargs.pop("seed", None)
                log.warning("%s: seed qo'llab-quvvatlanmadi — seedsiz qayta", ZIYRAKAI_DISPLAY_NAME)
                continue
            retry = bool(_OPENAI_RETRYABLE and isinstance(e, _OPENAI_RETRYABLE))
            if retry and attempt < max_retries - 1:
                delay = base_delay * (2**attempt)
                log.warning(
                    "%s vaqtincha xato (%s), %.1fs dan keyin qayta urinish %s/%s",
                    ZIYRAKAI_DISPLAY_NAME,
                    e,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue
            raise


def _usable(text, min_len=120):
    return bool(text) and not _looks_like_refusal(text) and len(text.strip()) >= min_len


def _table_row_count(text):
    return sum(1 for line in (text or "").splitlines() if line.count("|") >= 3)


def _looks_like_technician(text):
    """Foizli 'baho' / 'normal orientir' laborant uslubi — professor emas."""
    if not text:
        return True
    low = text.lower()
    if "normal orientir" in low:
        return True
    if "baho" in low and (low.count("%") >= 6 or "baho 1" in low or "1-5" in low):
        return True
    if low.count("%") >= 12 and "ishchi" not in low and "tashxis" not in low:
        return True
    return False


def _missing_diagnosis_sections(text, lab_type=None):
    """Qisqa hisobotning majburiy bo'limlari yo'qmi."""
    if not text:
        return True
    low = text.lower()
    if lab_type == "histology":
        has_dx = "tashxis" in low
        has_why = ("nega shu tashxis" in low) or ("nega bu tashxis" in low)
        has_fact = ("fakt" in low) or ("morfologiya" in low)
        has_diff = ("nega boshqasi emas" in low) or ("differensial" in low)
        return not (has_dx and has_why and has_fact and has_diff)
    has_dx = ("aniq tashxis" in low) or ("ishchi morfologik taassurot" in low and "yetakchi" in low)
    has_who = ("who mezon" in low) or ("essential" in low)
    has_detail = (
        "batafsil morfologik" in low
        or ("morfologik tahlil" in low and len(text) > 4500)
        or ("1-professor" in low and "morfologiya" in low)
    )
    has_think = ("klinik fikrlash" in low) or ("nega bu tashxis" in low)
    has_next = ("nima qilish kerak" in low) or ("keyingi qadam" in low)
    has_plan = (
        "profilaktika" in low
        or "davolash rejasi" in low
        or "davolash yo'nalishi" in low
        or "davolash yonalishi" in low
    )
    return not (has_dx and has_who and has_detail and has_think and has_next and has_plan)


_VERBOSE_MARKERS = (
    "klinik fikrlash",
    "profilaktika",
    "davolash rejasi",
    "1-professor",
    "2-professor",
    "3-professor",
    "rais yakuni",
    "tashxis izohi",
    "batafsil morfologik",
    "savol:",
    "quyoshdan himoya",
    "kuzatuv rejasi",
)


def _too_verbose(text, lab_type=None):
    """Ortiqcha uzun yoki taqiqlangan bo'limli hisobot — qayta yozish kerak."""
    if not text or lab_type != "histology":
        return False
    low = text.lower()
    if any(m in low for m in _VERBOSE_MARKERS):
        return True
    if len(text) > _MAX_REPORT_CHARS:
        return True
    if _table_row_count(text) >= 3:
        return True
    if len(re.findall(r"\d{1,3}\s*%", text)) >= 3:
        return True
    return False


def _too_shallow(text, lab_type=None):
    min_len = 350 if lab_type == "histology" else 1800
    if not _usable(text, min_len):
        return True
    if _looks_like_technician(text):
        return True
    low = text.strip().lower()
    if lab_type == "histology" and ("savol:" in low or "quyoshdan himoya" in low):
        return True
    if any(m in low for m in _SHALLOW_MARKERS) and len(text) < 4500:
        return True
    named_dx = any(x in low for x in (
        "karsinom", "carcinom", "adenom", "papillar", "displaziya",
        "leykoz", "blast", "glomerul", "trichomonas",
        "intraductal", "ductal", "urotel", "punlmp", "pin",
        "keratosis", "papilloma", "verruca", "dermatofibroma",
    ))
    if "taassurot" not in low and "tashxis" not in low and not named_dx:
        return True
    shallow_len = 900 if lab_type == "histology" else 5000
    if _missing_diagnosis_sections(text, lab_type) and len(text) < shallow_len:
        return True
    return False


def _multi_image_protocol(n):
    """Bir nechta rasm = bitta holatning turli rakurs/maydonlari."""
    if n <= 1:
        return ""
    return (
        f"\n\n#### KO'P RASM QOIDASI (majburiy — {n} ta tasvir)\n"
        f"Bu {n} ta rasm BIR xil bemor / BIR xil kasallik / BIR xil namuna holatiga tegishli "
        "(turli rakurs, turli maydon, turli kattalashtirish yoki turli joy).\n"
        "- HAR BIR tasvirni alohida ko'rib chiq (TASVIR 1…N). Faqat 1-rasmga tayanma.\n"
        "- Topilmalarni SINTEZ qil: BITTA yagona tashxis, bitta qisqa hisobot.\n"
        "- Alohida «rasmlar sintezi» bo‘limi YOZILMAYDI — hammasi 4 bo‘limga sig‘adi.\n"
        "- Bir rasmda ko‘rinib, boshqasida yo‘q bo‘lgan belgini yashirma.\n"
        "- Turli organ tashxislariga sakrama — bu bir holatning turli ko‘rinishlari.\n"
    )


def _limit_image_parts(image_parts):
    if not image_parts:
        return []
    cap = _max_vision_images()
    if len(image_parts) <= cap:
        return list(image_parts)
    log.warning(
        "%s: vision rasmlar %s → %s (OPENAI_MAX_VISION_IMAGES)",
        ZIYRAKAI_DISPLAY_NAME,
        len(image_parts),
        cap,
    )
    return list(image_parts[:cap])


def _vision_user(prompt, image_parts):
    """Vision so'rov: ko'p rasmda har birini raqamlab, oxirida sintez talabi."""
    parts_in = _limit_image_parts(image_parts or [])
    if not parts_in:
        return [{"type": "text", "text": prompt}]
    n = len(parts_in)
    head = (prompt or "") + _multi_image_protocol(n)
    if n == 1:
        return [{"type": "text", "text": head}, parts_in[0]]
    out = [{"type": "text", "text": head}]
    for i, img in enumerate(parts_in, 1):
        out.append({
            "type": "text",
            "text": f"==== TASVIR {i}/{n} — shu maydonni diqqat bilan ko'rib chiq ====",
        })
        out.append(img)
    out.append({
        "type": "text",
        "text": (
            f"==== SINTEZ ({n} ta tasvir) ====\n"
            f"Yuqoridagi {n} ta TASVIRNING HAMMASINI inobatga ol. "
            "Faqat birinchi yoki oxirgi rasmga tayanma. "
            "Bitta yagona TASHXIS, uning sababi va ko'ringan fakt — 4 bo'limdan iborat "
            "qisqa hisobot. Alohida «rasmlar sintezi» bo'limi yozilmaydi."
        ),
    })
    return out


def _expand_full_report(observation, full_prompt, kwargs, image_parts=None, lab_type="histology"):
    """Varaqa + original rasmlardan to'liq laborator hisobot."""
    user_text = (
        _lab_lock_text(lab_type)
        + "\n"
        + _EXPAND_DEEP_USER
        + "==== KUZATUV / JADVAL ====\n"
        + (observation or "")[:14000]
        + "\n==== TUGADI ====\n\n"
        + full_prompt
    )
    content = _vision_user(user_text, image_parts) if image_parts else user_text
    return _chat_complete(
        [
            {"role": "system", "content": _analysis_system(lab_type)},
            {"role": "user", "content": content},
        ],
        kwargs,
    )


def _deepen_report(shallow, full_prompt, kwargs, image_parts=None, lab_type="histology"):
    extra = ""
    if _looks_like_wrong_blood_smear(shallow, lab_type):
        extra = (
            "OLDINGI MATN NOTO'G'RI YO'NALISHDA: u qon yoqmasi/gematologiya kabi yozilgan. "
            "BUNI TAKRORLAMA. Faqat tanlangan tahlil turi protokolini yoz.\n\n"
        )
    user_text = (
        _lab_lock_text(lab_type)
        + "\n"
        + extra
        + _RETRY_DEEP_USER
        + (shallow or "")[:8000]
        + "\n==== TUGADI ====\n\n"
        + full_prompt
    )
    content = _vision_user(user_text, image_parts) if image_parts else user_text
    return _chat_complete(
        [
            {"role": "system", "content": _analysis_system(lab_type)},
            {"role": "user", "content": content},
        ],
        kwargs,
    )


def _preview(text):
    return (text or "").replace("\n", " ").strip()[:180]


def _needs_rewrite(text, lab_type, organ_lock=None):
    return (
        _too_shallow(text, lab_type)
        or _looks_like_technician(text)
        or _looks_like_wrong_blood_smear(text, lab_type)
        or _looks_like_weak_generic(text, lab_type, organ_lock)
        or _missing_diagnosis_sections(text, lab_type)
        or (lab_type == "histology" and _histology_cancer_overcall(text))
        or (lab_type == "histology" and _histology_melanoma_overcall(text))
        or _too_verbose(text, lab_type)
    )


def _safe_expand(draft, kwargs, image_parts=None, lab_type="histology", organ_lock=None,
                 patient_context=None, features=None):
    """Uzaytirish: tashxis so'zisiz, filtr rad etmasin."""
    protocol = _histology_protocol(organ_lock, patient_context) if lab_type == "histology" else (
        "Ichki LIS protokoli: qisqa tashxis, uning asosi va ko'ringan fakt. Rad etma."
    )
    lock = _histology_organ_lock_text(organ_lock) if lab_type == "histology" else ""
    kb = ""
    if lab_type == "histology":
        kb = histology_kb_prompt_block(organ_lock, patient_context, draft=draft)
        if kb:
            kb = "\n" + kb + "\n"
    patient = _patient_prompt_prefix(patient_context, lab_type)
    feats = _features_prompt_block(features) if lab_type == "histology" else ""
    n_img = len(image_parts or [])
    multi = _multi_image_protocol(n_img) if n_img > 1 else ""
    user_text = (
        _lab_lock_text(lab_type)
        + "\n"
        + (patient + "\n" if patient else "")
        + lock
        + (feats + "\n" if feats else "")
        + kb
        + multi
        + protocol
        + "\n\n==== ICHKI QORALAMA (shu asosda YAKUNIY QISQA hisobotni yoz) ====\n"
        + (draft or "")[:8000]
        + "\n==== TUGADI ====\n"
        "BIR organ. Hisobot FAQAT 4 bo'lim: #### TASHXIS, #### NEGA SHU TASHXIS, "
        "#### FAKT (ko'rinadigan morfologiya), #### NEGA BOSHQASI EMAS. "
        "Jami 1500-3000 belgi, 4200 dan oshmasin. "
        "Savol-javob, profilaktika, davolash, professor bo'limlari, jadval, foiz — YO'Q. "
        + (f"Barcha {n_img} ta rasmni sintez qil; faqat 1-rasmga tayanma. " if n_img > 1 else "")
        + "Har mezon qatori: <mezon> — KO'RINDI: <bir jumlalik dalil>. "
        "Ko'rinmagan mezonni yozma. Boshqa organ differensiali YO'Q. "
        "Bemor jinsi va namuna joyiga zid yozma. "
        "Agar organ qulfi TERI bo'lsa buyrak rakini / RCC ni YOZMA. "
        "Malignite qo'yish huquqi YO'Q bo'lsa asosiy tashxis benign/reaktiv bo'ladi. "
        "Sog'lom/dalilsiz holatga rak qo'yish — hisobot yaroqsiz."
    )
    content = _vision_user(user_text, image_parts) if image_parts else user_text
    expand_kwargs = dict(kwargs or {})
    if lab_type == "histology":
        expand_kwargs["temperature"] = min(float(expand_kwargs.get("temperature", 0.12) or 0.12), 0.15)
    return _chat_complete(
        [
            {"role": "system", "content": _SAFE_SYSTEM},
            {"role": "user", "content": content},
        ],
        expand_kwargs,
    )


_SPECIMEN_CODE_SET = frozenset(LAB_PROMPTS.keys()) | frozenset({"unknown", "other"})

# Bir xil preparat oilasi — tanlangan turi bilan "mos" hisoblanadi.
_SPECIMEN_COMPAT = {
    "histology": frozenset({"histology"}),
}

_SPECIMEN_GATE_SYSTEM = (
    "You classify microscope photos for a HISTOLOGY-ONLY LIS. "
    "Reply with ONE JSON object only, no markdown. Never refuse. "
    "Keys: detected (histology|other|unknown), confidence (high|medium|low), reason_uz (one short Uzbek sentence). "
    "Rules: pink-purple H&E tissue architecture, glands, papilla, dermis/epidermis, stroma = histology. "
    "Blood smear, urine sediment, stool, KOH scrape, sperm, AFB rods, cytology smear without tissue = other. "
    "If unsure use unknown with low confidence."
)


def _lab_display_name(lab_type):
    m = LAB_IDENTITY.get(lab_type) or {}
    return m.get("label") or lab_type


def _specimen_compatible(selected, detected):
    if not selected or not detected:
        return True
    if detected in ("unknown", "other"):
        return True
    if selected == detected:
        return True
    allowed = _SPECIMEN_COMPAT.get(selected) or frozenset({selected})
    return detected in allowed


def _parse_specimen_gate(raw):
    if not raw:
        return None
    t = raw.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
        t = re.sub(r"\s*```$", "", t)
    try:
        start = t.find("{")
        end = t.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(t[start : end + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    detected = str(data.get("detected") or "").strip().lower()
    detected = _LAB_ALIASES.get(detected, detected)
    if detected not in _SPECIMEN_CODE_SET:
        detected = "unknown"
    conf = str(data.get("confidence") or "low").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"
    reason = _truncate_field(data.get("reason_uz"), 240)
    return {"detected": detected, "confidence": conf, "reason_uz": reason}


def _mismatch_message(selected, detected, reason_uz=""):
    det_name = _lab_display_name(detected) if detected in LAB_IDENTITY else detected
    reason_line = f"\n- Asos: {reason_uz}" if reason_uz else ""
    return (
        "#### TASVIR GISTOLOGIYA KESMASI EMAS\n\n"
        f"- Bu tizim FAQAT gistologiya (H&E to'qima kesmasi).\n"
        f"- Tasvir ko'rinishi: **{det_name}**{reason_line}\n\n"
        "Boshqa lab protokoli (qon yoqmasi, siydik, najas va h.k.) yozilmaydi — "
        "uydirma tashxis chiqmasligi uchun tahlil TO'XTATILDI.\n\n"
        "**Nima qilish kerak:** H&E to'qima kesmasining aniq kadri (4–10× landshaft + 40× hujayra) yuklang.\n"
        "Boshqa sohalar hozircha yoqilmagan."
    )


def _gate_specimen_match(image_parts, lab_type):
    """Rasm tanlangan lab turiga mos emas bo'lsa ogohlantirish matni, aks holda None."""
    if not image_parts:
        return None
    selected = _normalize_lab_type(lab_type)
    # Gate uchun arzonroq: 1-rasm, low detail, qisqa javob
    gate_img = image_parts[:1]
    try:
        low_parts = []
        for part in gate_img:
            url = (part.get("image_url") or {}).get("url") or ""
            low_parts.append(
                {"type": "image_url", "image_url": {"url": url, "detail": "low"}}
            )
        if not low_parts:
            return None
        user = (
            "Classify this microscope photograph. "
            f"User currently selected lab_type={selected}. "
            "Return JSON only."
        )
        gate_kwargs = {
            "max_tokens": 220,
            "temperature": 0.0,
            "top_p": 0.2,
        }
        raw = _chat_complete(
            [
                {"role": "system", "content": _SPECIMEN_GATE_SYSTEM},
                {"role": "user", "content": _vision_user(user, low_parts)},
            ],
            gate_kwargs,
            model=_router_model(),
        )
        parsed = _parse_specimen_gate(raw)
        if not parsed:
            log.warning("%s: specimen gate parse fail: %r", ZIYRAKAI_DISPLAY_NAME, _preview(raw))
            return None
        detected = parsed["detected"]
        conf = parsed["confidence"]
        log.info(
            "%s: specimen gate selected=%s detected=%s conf=%s",
            ZIYRAKAI_DISPLAY_NAME,
            selected,
            detected,
            conf,
        )
        if conf == "low" or detected in ("unknown", "other"):
            return None
        if _specimen_compatible(selected, detected):
            return None
        return _mismatch_message(selected, detected, parsed.get("reason_uz") or "")
    except Exception as e:
        log.warning("%s: specimen gate xato (tahlil davom etadi): %s", ZIYRAKAI_DISPLAY_NAME, e)
        return None


def _openai_generate(content_list, lab_type="histology", patient_context=None):
    if openai_client is None:
        raise RuntimeError(
            "%s sozlanmagan: xizmat kaliti o'rnatilmagan — administrator .env faylida "
            "OPENAI_API_KEY ni belgilashi kerak."
            % ZIYRAKAI_DISPLAY_NAME
        )
    patient_context = _normalize_patient_context(patient_context)
    lab_type = _normalize_lab_type(lab_type)
    mismatch_pt = _patient_lab_mismatch_message(lab_type, patient_context)
    if mismatch_pt:
        log.warning("%s: patient/lab mismatch lab=%s", ZIYRAKAI_DISPLAY_NAME, lab_type)
        return mismatch_pt

    t_start = time.time()
    full_prompt = "\n\n".join(item for item in content_list if isinstance(item, str))
    image_parts = [
        {
            "type": "image_url",
            "image_url": {"url": _pil_to_data_url(item), "detail": "high"},
        }
        for item in content_list
        if isinstance(item, Image.Image)
    ]
    kwargs = _openai_generation_kwargs()
    if lab_type == "histology":
        kwargs["temperature"] = 0.0
        kwargs["top_p"] = min(float(kwargs.get("top_p", 0.85) or 0.85), 0.5)
        # Qayta tahlilda barqarorroq (model qo'llab-quvvatlasa)
        kwargs.setdefault("seed", 42)

    n_img = len(image_parts)
    organ_lock = None
    features = None
    if image_parts:
        # Namuna turi tekshiruvi va organ qulfi bir-biriga bog'liq emas — parallel bajariladi
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=3) as pool:
            gate_f = pool.submit(_gate_specimen_match, image_parts, lab_type)
            lock_f = (
                pool.submit(_lock_histology_organ, image_parts, patient_context)
                if lab_type == "histology"
                else None
            )
            obs_f = (
                pool.submit(_observe_histology, image_parts, patient_context)
                if lab_type == "histology"
                else None
            )
            mismatch = gate_f.result()
            organ_lock = lock_f.result() if lock_f is not None else None
            features = obs_f.result() if obs_f is not None else None
        log.info(
            "%s: gate+organ+ko'rik %.1fs (parallel)", ZIYRAKAI_DISPLAY_NAME, time.time() - t0
        )
        if mismatch:
            log.warning("%s: specimen mismatch lab=%s — tahlil to'xtatildi", ZIYRAKAI_DISPLAY_NAME, lab_type)
            return mismatch

    kb_block = ""
    if lab_type == "histology":
        # Qidiruv tasvirdagi belgilardan quriladi — aks holda har keysga bir xil
        # parchalar kelib, model bir xil tashxisga tortiladi.
        kb_block = histology_kb_prompt_block(
            organ_lock, patient_context, draft=_features_query_text(features) or None
        )

    patient_block = _patient_prompt_prefix(patient_context, lab_type)
    features_block = _features_prompt_block(features) if lab_type == "histology" else ""
    multi_note = _multi_image_protocol(n_img) if n_img > 1 else ""

    # Professor/konsilium so'rovi gpt-4o da tibbiy filtr bilan rad etiladi.
    # Avval ishlagan ichki morfologiya yozuvi, keyin xavfsiz uzaytirish.
    log.info("%s: 1-bosqich ichki morfologiya lab=%s imgs=%s", ZIYRAKAI_DISPLAY_NAME, lab_type, n_img)
    report = ""
    describe_prompt = _describe_user(lab_type, organ_lock, kb_block) + multi_note
    if features_block:
        describe_prompt = features_block + "\n" + describe_prompt
    if patient_block:
        describe_prompt = patient_block + "\n\n" + describe_prompt
    if n_img > 1:
        describe_prompt = (
            f"Birga yuborilgan {n_img} ta rasm — BIR holatning turli rakurs/maydonlari. "
            "HAMMASINI ko'rib, bitta yagona tashxis yoz.\n\n"
            + describe_prompt
        )
    if image_parts:
        report = _chat_complete(
            [
                {"role": "system", "content": _SAFE_SYSTEM},
                {"role": "user", "content": _vision_user(describe_prompt, image_parts)},
            ],
            kwargs,
        )
        if not _usable(report, 600) or _looks_like_wrong_blood_smear(report, lab_type):
            log.warning(
                "%s: tavsif yaroqsiz (%s): %r — varaqa",
                ZIYRAKAI_DISPLAY_NAME,
                len(report or ""),
                _preview(report),
            )
            ws = _worksheet_user(lab_type, organ_lock, kb_block) + multi_note
            if features_block:
                ws = features_block + "\n" + ws
            if patient_block:
                ws = patient_block + "\n\n" + ws
            report = _chat_complete(
                [
                    {"role": "system", "content": _WORKSHEET_SYSTEM},
                    {"role": "user", "content": _vision_user(ws, image_parts)},
                ],
                kwargs,
            )
    else:
        report = _chat_complete(
            [
                {"role": "system", "content": _analysis_system(lab_type)},
                {"role": "user", "content": (patient_block + "\n\n" if patient_block else "") + full_prompt},
            ],
            kwargs,
        )

    if not _usable(report, 200):
        log.warning("%s: hisobot olinmadi: %r", ZIYRAKAI_DISPLAY_NAME, _preview(report))
        return _REFUSAL_FALLBACK_UZ

    if image_parts and (
        _needs_rewrite(report, lab_type, organ_lock)
        or (lab_type == "histology" and _looks_like_weak_generic(report, lab_type, organ_lock))
        or (lab_type == "histology" and _histology_report_organs_conflict(report))
        or (lab_type == "histology" and _histology_report_wrong_organ(report, organ_lock))
        or (lab_type == "histology" and _histology_cancer_overcall(report))
        or len(report) < (MIN_REPORT_CHARS if lab_type == "histology" else 5000)
    ):
        log.info("%s: 2-bosqich uzaytirish (%s belgi) lab=%s", ZIYRAKAI_DISPLAY_NAME, len(report), lab_type)
        expanded = _safe_expand(report, kwargs, image_parts, lab_type, organ_lock, patient_context, features)
        if _usable(expanded, MIN_REPORT_CHARS) and not _looks_like_refusal(expanded):
            organ_bad = lab_type == "histology" and (
                _histology_report_organs_conflict(expanded)
                or _histology_report_wrong_organ(expanded, organ_lock)
                or _histology_cancer_overcall(expanded)
            )
            if organ_bad:
                log.warning("%s: uzaytirishda organ konflikti — qayta qulf bilan", ZIYRAKAI_DISPLAY_NAME)
                fixed = _safe_expand(expanded, kwargs, image_parts, lab_type, organ_lock, patient_context, features)
                if _usable(fixed, 1200) and not (
                    _histology_report_organs_conflict(fixed)
                    or _histology_report_wrong_organ(fixed, organ_lock)
                    or _histology_cancer_overcall(fixed)
                ):
                    report = fixed
                else:
                    report = expanded
            else:
                report = expanded
        else:
            log.warning(
                "%s: uzaytirish rad/qisqa (%s): %r — qoralama saqlanadi, deepen",
                ZIYRAKAI_DISPLAY_NAME,
                len(expanded or ""),
                _preview(expanded),
            )
            kb_retry = histology_kb_prompt_block(organ_lock, patient_context, draft=report)
            deepen_prompt = (
                (_patient_prompt_prefix(patient_context, lab_type) + "\n"
                 + _histology_organ_lock_text(organ_lock)
                 + (("\n" + kb_retry + "\n") if kb_retry else "")
                 + _histology_protocol(organ_lock, patient_context))
                if lab_type == "histology"
                else full_prompt
            )
            deeper = _deepen_report(report, deepen_prompt, kwargs, image_parts, lab_type)
            if _usable(deeper, MIN_REPORT_CHARS) and not _looks_like_technician(deeper):
                report = deeper

    # Tashxis ko'rikdagi belgilarga zid bo'lsa — bir marta qayta yozdiramiz
    if lab_type == "histology" and features and _usable(report, 400):
        conflict = _report_contradicts_features(report, features)
        if conflict:
            log.warning("%s: tashxis ko'rikka zid — %s", ZIYRAKAI_DISPLAY_NAME, conflict)
            retry = _safe_expand(
                report
                + "\n\n==== NAZORAT: "
                + conflict
                + ". Shu tashxisni olib tashla yoki ko'ringan belgilarga mos nom qo'y. "
                "Belgilar yetarli bo'lmasa «Aniq tashxis uchun yetarli emas» deb yoz. ====",
                kwargs, image_parts, lab_type, organ_lock, patient_context, features,
            )
            if _usable(retry, MIN_REPORT_CHARS) and not _report_contradicts_features(retry, features):
                report = retry

    if _usable(report, 400):
        if lab_type == "histology":
            report = _strip_other_organ_differential(report)
        log.info(
            "%s: hisobot tayyor lab=%s imgs=%s belgi=%s %.1fs",
            ZIYRAKAI_DISPLAY_NAME, lab_type, n_img, len(report), time.time() - t_start,
        )
        return report
    log.warning("%s: hisobot olinmadi: %r", ZIYRAKAI_DISPLAY_NAME, _preview(report))
    return _REFUSAL_FALLBACK_UZ


def _has_md_table(text):
    n = 0
    for line in (text or "").splitlines():
        if line.count("|") >= 3:
            n += 1
    return n >= 3


def _resize_img(img, max_px=None):
    """Rasmni OpenAI vision uchun optimallashtirish (tafsilot saqlanadi)."""
    if max_px is None:
        max_px = _openai_image_max_px()
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img

def do_analyze(pil_images, lab_type, custom_prompt=None, microscope_prefix=None, patient_context=None):
    """Ko'p rasm tahlili — pil_images: list of PIL.Image (loading=True allaqachon API da)."""
    global latest_analysis
    if not isinstance(pil_images, list):
        pil_images = [pil_images]
    if not pil_images:
        _publish_analysis({
            "text": "Xato: hech qanday rasm berilmagan",
            "lines": ["Xato: hech qanday rasm berilmagan"],
            "timestamp": time.strftime("%H:%M:%S"),
            "status": "xato",
            "loading": False,
        })
        return
    try:
        with analysis_lock:
            latest_analysis.update({"status": "tahlil_qilinmoqda", "lab_type": lab_type})

        imgs = [_resize_img(img) for img in pil_images]
        if not imgs:
            raise ValueError("Rasmlarni qayta ishlash muvaffaqiyatsiz")

        base = custom_prompt if custom_prompt and custom_prompt.strip() else LAB_PROMPTS.get(lab_type, "Bu mikroskopiya tasvirini O'zbek tilida batafsil tahlil qil.")
        prompt = _full_analysis_prompt(base, microscope_prefix, lab_type, patient_context)

        if len(imgs) > 1:
            prefix = (
                f"Quyida {len(imgs)} ta mikroskopiya tasviri — BIR xil kasallik/holatning "
                "turli rakurs, maydon yoki joylari. "
                "HAR BIRINI ko'rib chiq (TASVIR 1…N), oxirida BITTA yagona tashxis va reja. "
                "Faqat birinchi rasmga tayanma.\n\n"
            )
            content = [prefix + prompt] + imgs
        else:
            content = [prompt, imgs[0]]

        text = _openai_generate(content, lab_type, patient_context)
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        _publish_analysis({
            "text": text, "lines": lines,
            "timestamp": time.strftime('%H:%M:%S'),
            "status": "tayyor", "loading": False,
            "lab_type": lab_type,
            "img_count": len(imgs),
        })
        log.info("%s OK %s (%s rasm), %s belgi", ZIYRAKAI_DISPLAY_NAME, lab_type, len(imgs), len(text))

    except Exception as e:
        err = str(e)
        log.exception("%s tahlil xatosi: %s", ZIYRAKAI_DISPLAY_NAME, err)
        _publish_analysis({
            "text": f"Xato: {err}", "lines": [f"Xato: {err}"],
            "timestamp": time.strftime('%H:%M:%S'),
            "status": "xato", "loading": False,
        })

def do_analyze_video(
    video_bytes,
    lab_type,
    custom_prompt=None,
    extra_images=None,
    microscope_prefix=None,
    original_filename=None,
    patient_context=None,
):
    """Video faylni OpenAI bilan tahlil qilish (loading=True allaqachon API da)."""
    global latest_analysis
    tmp_path = None
    try:
        with analysis_lock:
            latest_analysis.update({"status": "video_tahlil_qilinmoqda", "lab_type": lab_type})

        base = custom_prompt if custom_prompt and custom_prompt.strip() else LAB_PROMPTS.get(lab_type, "Bu mikroskopiya videosini O'zbek tilida batafsil tahlil qilish.")
        prompt = _full_analysis_prompt(base, microscope_prefix, lab_type, patient_context)

        import tempfile
        suf = _video_temp_suffix(original_filename)
        with tempfile.NamedTemporaryFile(suffix=suf, delete=False) as tf:
            tf.write(video_bytes)
            tmp_path = tf.name

        cap = cv2.VideoCapture(tmp_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            fps = 0.0
        if not fps or fps != fps:  # 0 yoki nan
            fps = 25.0
        try:
            max_frames = int(os.environ.get("OPENAI_VIDEO_MAX_FRAMES", "6"))
        except ValueError:
            max_frames = 6
        max_frames = max(4, min(max_frames, 12))
        step = max(1, int(round(fps)))
        frames_data = []
        count = 0
        idx = 0
        while cap.isOpened() and count < max_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                break
            frame = _ensure_bgr_frame(frame)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil = _resize_img(Image.fromarray(rgb))
            frames_data.append(pil)
            idx += step
            count += 1
        cap.release()

        if not frames_data:
            raise ValueError("Videodan kadr olib bo'lmadi")

        if extra_images:
            frames_data = list(extra_images) + frames_data

        content = [f"Bu {len(frames_data)} ta mikroskopiya video/rasm kadri. " + prompt]
        content.extend(frames_data)

        text = _openai_generate(content, lab_type, patient_context)
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        _publish_analysis({
            "text": text, "lines": lines,
            "timestamp": time.strftime('%H:%M:%S'),
            "status": "tayyor", "loading": False,
            "lab_type": lab_type,
            "img_count": len(frames_data),
        })
        log.info("%s video OK %s, %s belgi", ZIYRAKAI_DISPLAY_NAME, lab_type, len(text))

    except Exception as e:
        err = str(e)
        log.exception("%s video xatosi: %s", ZIYRAKAI_DISPLAY_NAME, err)
        _publish_analysis({
            "text": f"Xato: {err}", "lines": [f"Xato: {err}"],
            "timestamp": time.strftime('%H:%M:%S'),
            "status": "xato", "loading": False,
        })
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

