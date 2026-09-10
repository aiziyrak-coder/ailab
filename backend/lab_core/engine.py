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
    """Gunicorn/systemd ishlaganda cwd farq qilishi mumkin — .env doim backend/ dan.

    override=False: jarayon muhitida allaqachon bor qiymat ustun turadi.
    Ilgari .env qobiqdagi o'zgaruvchini bosib ketardi — shuning uchun
    `OPENAI_MODEL_ID=... python ...` jimgina e'tiborsiz qolardi va mahalliy
    sinov serverdagidan boshqa modelda ketardi. systemd .env ni allaqachon
    EnvironmentFile orqali yuklaydi, ya'ni bu yerda bosib o'tish keraksiz.
    """
    try:
        from dotenv import load_dotenv

        p = _backend_dotenv_path()
        if os.path.isfile(p):
            load_dotenv(p, override=False)
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
MIN_REPORT_CHARS       = 700
_MAX_REPORT_CHARS      = 6500
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
ZIYRAKAI_DISPLAY_NAME = "DermaPATH"
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
    # Model nomi mijoz allaqachon qurilgan bo'lsa ham yangilanadi: ilgari bu
    # funksiya darrov qaytib ketardi va .env dagi yangi model restartsiz
    # hech qachon qo'llanilmasdi.
    OPENAI_MODEL_ID = (os.environ.get("OPENAI_MODEL_ID") or "gpt-4o").strip()
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

HISOBOTGA esa faqat yakuniy 3 bo'lim tushadi: TASHXIS, NEGA SHU TASHXIS,
FAKT. Fikrlash jarayonini bayon qilma — natijani yoz.
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
            "Hisobot 3 bo'limdan iborat, 2500-4500 belgi. "
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
        "HISOBOT FAQAT 3 BO'LIM: #### TASHXIS, #### NEGA SHU TASHXIS, "
        "#### FAKT (o'lchangan morfologiya). "
        "Jami 2000-4500 belgi. Savol-javob, profilaktika, davolash rejasi, "
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
«NEGA SHU TASHXIS» bo'limi ichida rad etiladi.
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

O'quvchi — shifokor. Unga konsultant-patolog darajasidagi ANIQ xulosa kerak:
tashxis, uning mezonlari, o'lchangan fakt, rad etilgan muqobillar va tasdiqlash yo'li.
Suvli matn, o'quv muhokamasi, umumiy nasihat — hisobot yaroqsiz.
Butun hisobot 2000–4500 belgi. Har qator ma'lumot tashisin; bo'sh jumla yozma.

Hisobotda FAQAT shu 3 bo'lim, shu tartibda:

#### TASHXIS
1-qator: <WHO/Weedon bo'yicha to'liq nom + variant/turi> — <benign | reaktiv | in situ | invaziv>
2-qator: Organ/qatlam: … | Daraja: <grade yoki «qo'llanilmaydi»> | Ishonch: yuqori/o'rta/past |
         Malignite qo'yish huquqi: HA yoki YO'Q
Nom umumiy bo'lmasin: variantni ayt (masalan «akantotik», «nodulyar», «hujayrali»),
agar variantni ajratuvchi belgi ko'rinsa. Ko'rinmasa variantni uydirma.

SHABLON JAVOB TAQIQLANADI. Ro'yxatlardagi nomlar ALIFBO tartibida —
ketma-ketlik ehtimollikni bildirmaydi. Eng ko'p uchraydigan nomni (seboreik keratoz,
dermatofibroma) SUKUT BO'YICHA tanlash — og'ir xato.
Tashxis FAQAT «TASVIRDAN OLINGAN BELGILAR» ro'yxatidan chiqadi.

BELGILAR YETARLI BO'LMASA (bu TO'G'RI javob, kamchilik emas):
«Aniq tashxis uchun yetarli emas» + eng yaqin tavsifiy toifa
(masalan «bazaloid epidermal proliferatsiya, atipiyasiz»), keyin qaysi belgi
yetishmayotgani va nima hal qilishi.

#### NEGA SHU TASHXIS
4–7 qator. Har qator: <mezon nomi> — KO'RINDI: <bir jumlalik aniq dalil, joyi bilan>.
Mezon nomi kitob atamasi bo'lsin (periferik palisad, Grenz zonasi, kollagen tuzog'i,
horn cyst, koilotsit, pagetoid tarqalish...). Ko'rinmagan mezonni YOZMA.

#### FAKT (o'lchangan morfologiya)
6–10 qator, har biri qisqa va SONLI/aniq. Majburiy qatorlar (mos bo'lsa):
- Arxitektura: pattern nomi, simmetriya, chegara turi (itaruvchi/infiltrativ)
- Epidermis: qalinlik o'zgarishi, keratinizatsiya turi, atipiya darajasi
- Hujayra: tur, sitoplazma, yadro o'lchami/xromatin, yadrocha
- Mitoz: /10 HPF (taxminiy son), atipik mitoz bor/yo'q
- Stroma: kollagen, desmoplaziya, musin, elastoz
- Yallig'lanish: turi va zichligi
- Invaziya: yo'q/shubhali/bor — nimaga asoslanib
- Chuqurlik/qalinlik: melanotsitar bo'lsa Breslow (mm), yara bor/yo'q
- Chekka (rezeksiya) holati: erkin / tegib turadi / baholab bo'lmaydi
"ko'p", "oz" yolg'iz yetarli emas — taxminiy son yoki daraja yoz.


TAQIQLANGAN (yozilsa hisobot yaroqsiz):
«Savol:», klinik fikrlash bo'limi, profilaktika, davolash rejasi, kuzatuv rejasi,
1/2/3-professor, rais yakuni, batafsil morfologik tahlil, tashxis izohi,
foizli 60/30/10 vitrina, «baho 1-5», jadval, quyoshdan himoya, umumiy nasihat.
Ehtimollik foizi YOZILMAYDI — «Ishonch: yuqori/o'rta/past».
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
HISOBOT esa faqat 3 bo'lim: #### TASHXIS, #### NEGA SHU TASHXIS,
#### FAKT (o'lchangan morfologiya).
Jami 2000–4500 belgi. Rad etma. Ko'rinmagan narsani uydirma.
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
    "You are a histopathology image reader. Report ONLY what is visible in the H&E "
    "photomicrographs. Return ONE JSON object, no markdown, no commentary. "
    "CRITICAL: do NOT name any disease, tumour, or diagnosis anywhere in the output. "
    "No entity names (no 'keratosis', 'carcinoma', 'nevus', 'dermatofibroma', ...). "
    "Only descriptive morphology. "
    "WORK THROUGH EVERY IMAGE AND EVERY FIELD: a feature is true if it is present in ANY "
    "of the fields shown, false only if you looked and it is absent. Do not leave the form "
    "nearly empty — a real section shows many features at once (layers, architecture, "
    "keratinisation, cell type, nuclei, stroma, inflammation, vessels, adnexa). "
    "A read that marks fewer than eight features true is almost always an incomplete read: "
    "go back over the images before answering. "
    "Use 'noaniq' only where the images genuinely cannot answer the field. "
    "Never refuse; if everything is blurry, set sample_quality past and still describe what "
    "is discernible. "
    "Classic confusions to avoid: a neutrophil pustule is NOT acantholysis; loose oedematous "
    "vascular stroma under a thinned epidermis (polypoid lesion with a collarette) is NOT an "
    "intraepidermal vesicle — look for lobules of capillaries and extravasated red cells; "
    "inflammatory cells in the epidermis are NOT dyskeratosis; a polypoid or exophytic "
    "lesion must be described by its stroma (vascular, fibrous, cellular), not only by "
    "its epidermis."
)

# Ko'rik shakli endi mezon jadvali bilan BIR manbadan quriladi — belgilar
# ro'yxati, o'zbekcha nomlari va tashxis mezonlari bir-biridan ajralib
# ketmasin. Ro'yxat yallig'lanish va infeksion dermatozlar bilan kengaytirildi.
from lab_core import dx_criteria as _dxc  # noqa: E402

_OBSERVE_SCHEMA = _dxc.observe_schema_json()


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


# ─── Kesma va klinik suratni ajratish ────────────────────────────────────────
# Laborant bir keysga yo'llanma varaqasi, bemor tanasining suratlari va mikroskop
# kadrlarini birga yuklaydi. Ilgari ko'rik butun to'plamdan teng oraliqda tanlar
# edi — natijada 43 rasmdan ko'rikka klinik suratlar tushib, morfologik belgilar
# soni 2 taga tushib qolgan. Bundan tashqari tana suratlari model filtrini
# ishga tushirib, "I'm sorry, I can't assist" javobiga sabab bo'lardi.
#
# H&E kesmasi rangi bo'yicha ajralib turadi: gematoksilin (binafsha yadro) va
# eozin (pushti sitoplazma) — piksellar magenta-binafsha yo'lakda; teri surati
# esa to'q sariq tonda. Bu farq bepul va aniq (44 ta haqiqiy rasmda sinalgan:
# kesmalar 0.63–1.00, klinik suratlar 0.00).
SLIDE_SCORE_MIN = 0.35


def _normalize_he(img, target=243.0, max_gain=2.2):
    """Oq shisha fonini neytral oqqa keltirib, rang og'ishini olib tashlash.

    Mikroskopga telefon bilan olingan kadrda oq balans ko'pincha buziladi:
    butun kadr binafsha tusga kiradi, fon ham oq emas va eozin/gematoksilin
    farqi yo'qoladi. Model bunday kadrdan belgilarni ajrata olmaydi (haqiqiy
    keysda 60 ta belgidan atigi 3 tasi topilgan).

    Fon — preparatdagi bo'sh shisha, ya'ni haqiqatda oq. Eng yorug'
    piksellarning o'rtachasi bo'yicha har kanal kattalashtiriladi.
    Kuchaytirish cheklangan, aks holda shovqin ko'tariladi.
    """
    try:
        rgb = img.convert("RGB")
        small = rgb.copy()
        small.thumbnail((256, 256))
        px = list(small.getdata())
        if not px:
            return rgb
        order = sorted(range(len(px)), key=lambda i: -(px[i][0] + px[i][1] + px[i][2]))
        top = order[: max(30, len(px) // 8)]
        bg = [sum(px[i][c] for i in top) / len(top) for c in range(3)]
        if min(bg) < 20:
            return rgb  # deyarli qora kadr — tegilmaydi
        gains = [min(max_gain, target / max(1.0, bg[c])) for c in range(3)]
        if max(gains) < 1.05:
            return rgb  # balans allaqachon joyida
        lut = []
        for c in range(3):
            g = gains[c]
            lut.extend([min(255, int(round(v * g))) for v in range(256)])
        return rgb.point(lut)
    except Exception:
        return img


def _slide_score(img):
    """0..1 — tasvir H&E gistologik kesmaga qanchalik o'xshaydi."""
    try:
        small = img.convert("RGB")
        small.thumbnail((96, 96))
        hsv = small.convert("HSV")
        px = list(hsv.getdata())
    except Exception:
        return 0.0
    if not px:
        return 0.0
    purple = skin = strong = 0
    for h, sat, val in px:
        if sat < 45 or val < 35:
            continue  # oq fon va soya hisobga olinmaydi
        strong += 1
        if 175 <= h <= 245:
            purple += 1
        elif h <= 30 or h >= 250:
            skin += 1
    if strong < 40:
        return 0.0
    return max(0.0, min(1.0, purple / strong - skin / strong))


def _order_slides_first(pairs):
    """[(part, score)] → kesmalar oldinda, har guruh ichida asl tartib saqlanadi."""
    slides = [p for p, sc in pairs if sc >= SLIDE_SCORE_MIN]
    others = [p for p, sc in pairs if sc < SLIDE_SCORE_MIN]
    return slides, others


def _pick_images(pairs, k):
    """k ta rasm: avval kesmalardan teng oraliqda, yetmasa qolganidan to'ldiriladi."""
    slides, others = _order_slides_first(pairs)
    if not slides:
        return _spread_pick([p for p, _ in pairs], k)
    picked = _spread_pick(slides, k)
    if len(picked) < k and others:
        picked = picked + _spread_pick(others, k - len(picked))
    return picked


def _spread_pick(image_parts, k):
    """Ko'p rasmdan teng oraliqda k tasini tanlash (birinchi va oxirgisi kiradi).

    O'nlab rasm bir chaqiruvga tiqilsa model diqqati tarqaladi va ko'rik bo'shab
    qoladi — shuning uchun butun to'plamni qamrab oluvchi kichik namuna olinadi.
    """
    parts = list(image_parts or [])
    if len(parts) <= k:
        return parts
    step = (len(parts) - 1) / float(k - 1)
    idx = sorted({int(round(i * step)) for i in range(k)})
    return [parts[i] for i in idx]


def _report_max_images():
    """Hisobot chaqiruviga yuboriladigan rasm soni.

    O'nlab tasvir + uzun tibbiy so'rov birga kelganda model javob berishdan
    bosh tortishi mumkin; qamrovni yo'qotmaslik uchun teng oraliqda tanlanadi.
    """
    try:
        v = int(os.environ.get("HISTOLOGY_REPORT_IMAGES", "10"))
    except ValueError:
        v = 10
    return max(3, min(v, 20))


def _observe_max_images():
    try:
        v = int(os.environ.get("HISTOLOGY_OBSERVE_IMAGES", "6" if _economy_enabled() else "8"))
    except ValueError:
        v = 6
    return max(2, min(v, 16))


# ─── Ko'p bosqichli ko'rik va natijalarni birlashtirish ──────────────────────
# Bitta chaqiruvda 8 ta kadr berilganda model 60 ta belgidan atigi 3-4 tasini
# belgilardi: diqqat tarqaladi va u ehtiyotkorlik bilan hammasini "false"
# qoldiradi. Kadrlarni kichik guruhlarga bo'lib alohida ko'rish har guruhda
# chuqurroq qarashga majbur qiladi, so'ng natijalar birlashtiriladi.
#
# Birlashtirish qoidasi — patologik mantiq:
#   · mantiqiy belgi: bitta maydonda ko'rinsa, u BOR (topilma yo'qolmaydi)
#   · daraja (pleomorfizm, mitoz, invaziya): eng yuqorisi olinadi
#   · chekka: xavfsizlik tomonga — "tegib turadi" ustun
#   · "baholab bo'lmadi": faqat HAMMA guruhda baholanmagan bo'lsa qoladi

_SCALES = {
    "pleomorphism": ["yo'q", "yengil", "o'rta", "kuchli"],
    "mitoses_10hpf": ["0", "1-2", "3-10", ">10"],
    "nuclear_grade": ["1", "2", "3"],
    "invasion": ["yo'q", "shubhali", "bor"],
    "density": ["yo'q", "yengil", "o'rta", "zich"],
}
_QUALITY_ORDER = ["past", "o'rtacha", "yaxshi"]


def _norm_scale_value(v):
    return str(v or "").strip().lower().replace("\u2018", "'").replace("\u2019", "'")


def _pick_strongest(key, values):
    scale = _SCALES.get(key)
    vals = [_norm_scale_value(v) for v in values if _norm_scale_value(v) not in ("", "noaniq")]
    if not vals:
        return None
    if not scale:
        return vals[0]
    best, rank = None, -1
    for v in vals:
        if v in scale and scale.index(v) > rank:
            rank, best = scale.index(v), v
    return best or vals[0]


def _merge_observations(parts):
    """Bir necha ko'rik natijasini bitta belgilar to'plamiga yig'ish."""
    parts = [p for p in parts if isinstance(p, dict)]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]

    out = {}
    keys = set()
    for p in parts:
        keys.update(p.keys())

    for key in keys:
        vals = [p.get(key) for p in parts if key in p]
        sample = next((v for v in vals if v is not None), None)

        if isinstance(sample, dict):
            merged = {}
            subkeys = set()
            for v in vals:
                if isinstance(v, dict):
                    subkeys.update(v.keys())
            for sk in subkeys:
                svals = [v.get(sk) for v in vals if isinstance(v, dict) and sk in v]
                if any(isinstance(x, bool) for x in svals):
                    merged[sk] = any(x is True for x in svals)
                else:
                    merged[sk] = _pick_strongest(sk, svals) or next(
                        (x for x in svals if x not in (None, "")), None
                    )
            # Chekka: xavfsizlik tomonga
            if key == "margins" and "involved" in merged:
                inv = [v.get("involved") for v in vals if isinstance(v, dict)]
                if any(_norm_scale_value(x) == "tegib turadi" for x in inv):
                    merged["involved"] = "tegib turadi"
            out[key] = merged
        elif any(isinstance(v, bool) for v in vals):
            out[key] = any(v is True for v in vals)
        elif isinstance(sample, list):
            seen, joined = set(), []
            for v in vals:
                for item in v or []:
                    t = str(item).strip()
                    if t and t.lower() not in seen:
                        seen.add(t.lower())
                        joined.append(t)
            if key == "not_assessable_uz":
                # faqat hamma guruhda uchraganini qoldiramiz
                common = None
                for v in vals:
                    cur = {str(x).strip().lower() for x in (v or [])}
                    common = cur if common is None else (common & cur)
                joined = [x for x in joined if x.lower() in (common or set())]
            out[key] = joined[:8]
        elif key == "sample_quality":
            ranked = [
                _norm_scale_value(v) for v in vals
                if _norm_scale_value(v) in _QUALITY_ORDER
            ]
            out[key] = (
                max(ranked, key=_QUALITY_ORDER.index) if ranked else (sample or "noaniq")
            )
        elif key == "dominant_pattern":
            out[key] = max(
                (str(v) for v in vals if v), key=len, default=sample
            )
        else:
            out[key] = _pick_strongest(key, vals) or sample
    return out


def _observe_groups():
    """Ko'rik necha guruhga bo'linadi."""
    try:
        v = int(os.environ.get("HISTOLOGY_OBSERVE_PASSES", "1" if _economy_enabled() else "3"))
    except ValueError:
        v = 1
    return max(1, min(v, 5))


def _split_groups(parts, n):
    """Kadrlarni n ta guruhga navbat bilan taqsimlash (har guruhda turli chuqurlik)."""
    groups = [[] for _ in range(n)]
    for i, p in enumerate(parts):
        groups[i % n].append(p)
    return [g for g in groups if g]


def _observe_histology(image_parts, patient_context=None):
    """Tasvirdagi belgilarni tashxis nomisiz yig'ish — har keys uchun o'ziga xos."""
    if not image_parts or not _observe_enabled():
        return None
    p = _normalize_patient_context(patient_context)
    site = (p.get("specimen_site") or "").strip() or "—"
    try:
        n_all = len(image_parts)
        user = (
            f"Clinical specimen site: {site}. "
            f"{n_all} field(s) from ONE case; the images below are a spread across them. "
            "Work in this fixed order (dermatopathology protocol): (1) name the inflammatory "
            "pattern per Ackerman (or state it does not apply — neoplastic); (2) descend from "
            "the stratum corneum through the granular, spinous and basal layers; (3) the "
            "dermo-epidermal junction; (4) the papillary dermis — amorphous material, state of "
            "collagen, mucin between fibres, vessels, perivascular and interstitial infiltrate and "
            "its composition; (5) the reticular dermis — the same parameters, plus adnexa and any "
            "periadnexal infiltrate and its composition; (6) subcutis if present; (7) overall: "
            "pigment, atypical cells, pleomorphism, mitoses. Fill the 'description' object in "
            "that order in short Uzbek sentences, then the boolean features. "
            "Use this JSON shape and these keys (no extra keys):\n"
            + _OBSERVE_SCHEMA
            + "\nTo keep the answer short, OMIT boolean keys whose value is false — an omitted "
            "boolean means false. Write only the booleans that are TRUE, plus every "
            "non-boolean field. "
            "\nEvery decision must be a deliberate yes/no from the images, not a default. "
            "observations_uz: 4-8 short Uzbek sentences of what you actually see. "
            "organ: the organ this tissue most likely comes from (teri if skin). "
            "Remember: NO diagnosis names anywhere."
        )
        picked = _spread_pick(image_parts, _observe_max_images())
        groups = _split_groups(picked, _observe_groups()) or [picked]

        def _one(group):
            try:
                raw = _chat_complete(
                    [
                        {"role": "system", "content": _HISTOLOGY_OBSERVE_SYSTEM},
                        {"role": "user", "content": _vision_user(user, group)},
                    ],
                    {"max_tokens": 3500, "temperature": 0.0, "top_p": 0.1},
                    label="ko'rik",
                )
            except Exception as e:
                log.warning("%s: ko'rik guruhi xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
                return None
            return _parse_observation(raw)

        if len(groups) == 1:
            results = [_one(groups[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(groups))) as pool:
                results = list(pool.map(_one, groups))
        results = [r for r in results if r]
        if not results:
            log.warning("%s: ko'rik JSON o'qilmadi", ZIYRAKAI_DISPLAY_NAME)
            return None
        data = _merge_observations(results)
        if data is not None and len(results) > 1:
            # Barqarorlik tekshiruvi uchun guruhlar alohida saqlanadi
            data["_groups"] = results
        if len(results) > 1:
            counts = [len(_true_features(r)) for r in results]
            log.info(
                "%s: ko'rik %s guruh — belgilar %s → birlashgan %s",
                ZIYRAKAI_DISPLAY_NAME, len(results), counts, len(_true_features(data)),
            )
        if not data:
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
    "perineural": "perinevral tarqalish",
    "lymphovascular": "limfovaskulyar invaziya",
    "adnexal_involvement": "adneks jalb bo'lgan",
    "pigment_incontinence": "pigment inkontinensiyasi",
    "foreign_material": "begona material",
    "organisms_suspected": "mikroorganizm shubhasi",
    "crush_or_cautery_artifact": "ezilish / kuydirish artefakti",
}
# Kengaytirilgan belgilar nomi — mezon jadvali bilan bir manbadan
_FEATURE_UZ.update(_dxc.FEATURE_UZ)

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
    # Hakamlik nomzodlarini ham shu jadval tekshiradi — qamrovi kengaytirildi
    "lichen planus": ("band_like_infiltrate", "interface_damage"),
    "qizil yassi temiratki": ("band_like_infiltrate", "interface_damage"),
    "lixenoid": ("band_like_infiltrate", "interface_damage"),
    "ekzema": ("spongiosis",),
    "eczema": ("spongiosis",),
    "spongiotic": ("spongiosis",),
    "spongiotik": ("spongiosis",),
    "sarcoidos": ("granuloma",),
    "sarkoidoz": ("granuloma",),
    "granuloma annulare": ("granuloma",),
    "granulomatous": ("granuloma",),
    "granulomatoz": ("granuloma",),
    "vasculitis": ("vasculitis",),
    "vaskulit": ("vasculitis",),
    "mycosis fungoides": ("pagetoid_spread", "dense_lymphoid_infiltrate"),
    "gribovidn": ("pagetoid_spread", "dense_lymphoid_infiltrate"),
    "lymphoma": ("dense_lymphoid_infiltrate",),
    "limfoma": ("dense_lymphoid_infiltrate",),
    "kaposi": ("vascular_proliferation", "spindle_cells"),
    "hemangiom": ("vascular_proliferation",),
    "gemangiom": ("vascular_proliferation",),
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
    for group in ("epidermis", "junction", "dermis", "glandular", "cytology", "special"):
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
    def _sub(name):
        v = features.get(name)
        return v if isinstance(v, dict) else {}

    cyt = _sub("cytology")
    depth = _sub("depth")
    margins = _sub("margins")
    special = _sub("special")
    infl = _sub("inflammation")
    lines = [
        "#### TASVIRDAN OLINGAN BELGILAR (avtomatik ko'rik — tashxis SHU ro'yxatdan chiqadi)",
        f"Pattern: {_truncate_field(features.get('dominant_pattern'), 120) or '—'}",
        f"Simmetriya: {features.get('symmetry') or 'noaniq'} | "
        f"Chegara: {features.get('border') or 'noaniq'}",
        f"Invaziya: {features.get('invasion') or 'noaniq'}"
        + (f" ({_truncate_field(features.get('invasion_evidence_uz'), 140)})"
           if features.get("invasion_evidence_uz") else ""),
        f"Chuqurlik: {depth.get('deepest_level') or 'noaniq'} | "
        f"Qalinlik: {depth.get('thickness_mm') or 'noaniq'} | "
        f"Yara: {'bor' if depth.get('ulceration') is True else 'yo`q'}",
        f"Chekka: {'baholanadi' if margins.get('assessable') is True else 'baholab bo`lmaydi'}"
        f" — {margins.get('involved') or 'noaniq'}",
        f"Pleomorfizm: {cyt.get('pleomorphism') or 'noaniq'} | "
        f"Yadro darajasi: {cyt.get('nuclear_grade') or 'noaniq'} | "
        f"Mitoz/10HPF: {cyt.get('mitoses_10hpf') or 'noaniq'}",
        f"Yallig'lanish: {infl.get('type') or 'noaniq'}, {infl.get('density') or 'noaniq'}, "
        f"{infl.get('distribution') or 'noaniq'}",
        f"Namuna sifati: {features.get('sample_quality') or 'noaniq'} | "
        f"Kattalashtirish: {features.get('magnification') or 'noaniq'}",
        "KO'RINGAN: " + (", ".join(present[:30]) if present else "—"),
        "KO'RINMAGAN (muhim): " + (", ".join(absent[:14]) if absent else "—"),
    ]
    flags = [k for k, v in special.items() if v is True]
    if flags:
        lines.append("Alohida belgilar: " + ", ".join(_FEATURE_UZ.get(f, f) for f in flags))
    gaps = features.get("not_assessable_uz")
    if isinstance(gaps, list) and gaps:
        lines.append("Baholab bo'lmadi: " + "; ".join(_truncate_field(g, 120) for g in gaps[:3] if g))
    desc = _dxc.description_lines(features.get("description"))
    if desc:
        lines.append("Tizimli tavsif (Akkerman → yuqoridan pastga):")
        for d in desc:
            lines.append(f"- {_truncate_field(d, 220)}")
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
        "yetarli bo'lmasa — eng ehtimolli NOMNI yoz, «Ishonch: past» qo'y va "
        "nima kerakligini ayt. Nomsiz javob TAQIQLANADI. "
        "Yuqoridagi sonlar va darajalar «FAKT» bo'limiga ko'chiriladi; «Baholab bo'lmadi» "
        "qatorlari FAKT bo'limida «baholab bo'lmadi» deb belgilanadi."
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
        + "Jadval YOZMA. Baho 1-5 ISHLATMA. Faqat 3 bo'lim: TASHXIS, NEGA SHU TASHXIS, "
        "FAKT.\n"
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
            f"Write a CONSULTANT-GRADE report in Uzbek: the diagnosis with its variant/grade, "
            f"the criteria seen, the measured facts (mitoses per 10 HPF, depth, margins), the "
            f"alternatives excluded by a discriminating feature, the confirmatory panel, and what "
            f"could not be assessed. 2000-4500 characters total. "
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

    Asos bo'limida rad etilgan nomlar (masalan «DFSP — storiform
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


# ─── Dalil darajasi: ishonch ko'rikdan kelib chiqadi ─────────────────────────
MIN_FEATURES_FOR_ENTITY = 4      # shundan kam belgi — aniq nozologiya qo'yilmaydi
MIN_FEATURES_FOR_HIGH = 8        # «Ishonch: yuqori» uchun kerak bo'lgan belgi soni

# Eski matn: dalil kam bo'lganda tashxis nomi shu bilan almashtirilardi.
# Endi nom saqlanadi (_mark_provisional), bu satr faqat eski hisobotlarni
# tanib olish uchun qoldirilgan.
_INSUFFICIENT_DX = "Aniq tashxis uchun yetarli emas"


def _evidence_level(features):
    """(belgi soni, ruxsat etilgan eng yuqori ishonch) — ko'rik natijasidan."""
    n = len(_true_features(features)) if isinstance(features, dict) else 0
    quality = str((features or {}).get("sample_quality") or "").lower()
    if n >= MIN_FEATURES_FOR_HIGH and quality.startswith("yaxshi"):
        return n, "yuqori"
    if n >= MIN_FEATURES_FOR_ENTITY:
        return n, "o'rta"
    return n, "past"


def _cap_confidence(text, max_level):
    """Hisobotdagi «Ishonch: …» qatorini dalil darajasidan oshirmaslik."""
    order = {"past": 0, "o'rta": 1, "orta": 1, "yuqori": 2}
    cap = order.get(max_level, 0)

    def fix(m):
        cur = order.get(m.group(1).strip().lower().replace("‘", "'").replace("’", "'"), 2)
        return "Ishonch: " + (m.group(1) if cur <= cap else max_level)

    return re.sub(r"Ishonch:\s*([A-Za-z'‘’o]+)", fix, text or "", flags=re.I)


# Ko'rik pattern nomini ingliz tilida qaytaradi; hisobot o'zbekcha bo'lgani
# uchun eng ko'p uchraydigan atamalar o'giriladi.
_PATTERN_UZ = (
    ("psoriasiform", "psoriaziform"),
    ("spongiotic", "spongiotik"),
    ("lichenoid", "lixenoid"),
    ("interface", "interfeys"),
    ("granulomatous", "granulomatoz"),
    ("papillomatous", "papillomatoz"),
    ("acanthotic", "akantotik"),
    ("acanthosis", "akantoz"),
    ("hyperkeratotic", "giperkeratotik"),
    ("hyperkeratosis", "giperkeratoz"),
    ("parakeratosis", "parakeratoz"),
    ("verrucous", "verrukoz"),
    ("nodular", "nodulyar"),
    ("cystic", "kistoz"),
    ("fibrous", "fibroz"),
    ("spindle cell", "duksimon hujayrali"),
    ("basaloid", "bazaloid"),
    ("squamous", "yassi hujayrali"),
    ("melanocytic", "melanotsitar"),
    ("vascular", "vaskulyar"),
    ("inflammatory", "yallig'lanishli"),
    ("lymphoid", "limfoid"),
    ("lymphocytic", "limfotsitar"),
    ("infiltrate", "infiltrat"),
    ("dermatitis", "dermatit"),
    ("epidermal", "epidermal"),
    ("dermal", "dermal"),
    ("lesion", "lezyon"),
    ("proliferation", "proliferatsiya"),
    ("thickened epidermis", "qalinlashgan epidermis"),
    ("with", "va"),
)


def _pattern_uz(text):
    out = (text or "").strip()
    low = out.lower()
    for en, uz in _PATTERN_UZ:
        if en in low:
            low = low.replace(en, uz)
    return low.strip().capitalize()


def _descriptive_dx(features):
    """Ko'rikdagi pattern asosida tavsifiy tashxis nomi.

    Klinik gipoteza (masalan yo'llanmadagi «psoriaz») morfologiya bilan
    tasdiqlanmasa, o'sha nomni tashxis o'rnida qoldirib bo'lmaydi — hisobot
    o'zini o'zi rad etadi. Bunday holda ko'rilgan pattern nomlanadi.
    """
    pattern = _strip_atypia_claim(
        _truncate_field((features or {}).get("dominant_pattern"), 80)
    )
    if not pattern:
        return ""
    return f"Tavsifiy morfologiya: {_pattern_uz(pattern)}"


def _mark_provisional(text, features, reason, rename=False):
    """Dalil kam bo'lsa tashxis NOMINI saqlab, uni taxminiy deb belgilash.

    Ilgari bu funksiya nomni butunlay «Aniq tashxis uchun yetarli emas» ga
    almashtirardi. Shifokorga bu foydasiz: hisobotda hech qanday nom qolmasdi.
    Endi nom o'z joyida qoladi, yoniga «(taxminiy)» qo'shiladi, ishonch «past»
    ga tushiriladi va sabab ko'rsatiladi — ya'ni o'qigan odam nima deb
    o'ylanayotganini ham, nega ishonch pastligini ham biladi.
    """
    if not text:
        return text
    reason_line = f"Taxminiy — sabab: {reason}"

    lines = (text or "").splitlines()
    out = []
    state = "before"  # before → sarlavha topilmagan; heading → nom qatorini kutamiz
    for line in lines:
        if state == "before" and re.match(r"^\s*#+\s*(?:aniq\s+)?tashxis\b", line, flags=re.I):
            out.append(line)
            state = "heading"
            continue
        if state == "heading":
            if not line.strip():
                out.append(line)
                continue
            name = line.strip()
            if rename:
                # Nom ko'rikka zid — uni saqlab qolish mumkin emas
                alt = _descriptive_dx(features)
                if alt:
                    name = alt
            low = name.lower()
            if "taxminiy" not in low:
                name = name.rstrip(" .") + " — taxminiy"
            out.append(name)
            out.append(reason_line)
            state = "done"
            continue
        out.append(line)

    joined = "\n".join(out) if state == "done" else (text or "")
    joined = _cap_confidence(joined, "past")
    if "Malignite qo'yish huquqi" not in joined:
        joined = joined.replace(reason_line, "Malignite qo'yish huquqi: YO'Q\n" + reason_line, 1)
    return joined


# ─── Hisobot ichidagi ziddiyatlar ─────────────────────────────────────────────
# Foydalanuvchi shikoyati: hisobot bir bo'limda o'lchagan narsani boshqasida
# "baholanmagan" deydi, tekshiruv taklif qilib turib "shart emas" deb yozadi,
# "atipiyasiz" deb turib mitoz sanaydi. Bunday hisobot mantiqsiz ko'rinadi va
# shifokorning ishonchini yo'qotadi. Quyidagi tekshiruvlar shu zidliklarni
# topadi; topilsa hisobot bir marta qayta yozdiriladi.

_SEC_RE = {
    "tashxis": r"#+\s*(?:aniq\s+)?tashxis\b",
    "nega": r"#+\s*nega\s+shu\s+tashxis\b",
    "fakt": r"#+\s*fakt\b",
    "boshqasi": r"#+\s*nega\s+boshqasi\b",
    "tasdiq": r"#+\s*tasdiqlash\b",
    "baholanmagan": r"#+\s*baholanmagan\b",
}


_WRAPPER_LINE_RE = re.compile(r"^\s*={3,}.*?={3,}\s*$", re.M)


# TASHXIS bo'limida turishi kerak bo'lmagan qatorlar: bemor ma'lumoti kartada
# allaqachon bor, «ishchi taassurotlar 1) 2) 3)» esa aniq xulosani yo'qotadi —
# shifokor nima deyilganini tushunmay qoladi.
_DX_NOISE_RE = re.compile(
    r"^\s*(bemor\s*:|namuna\s*(№|no|nomer)|ishchi\s+taassurot|"
    r"working\s+impression|tasdiqlash\s*:|klinik\s+ma'lumot\s*:)",
    re.I,
)


def _clean_dx_section(text):
    """TASHXIS bo'limini faqat xulosa va uning meta qatorlariga qisqartirish."""
    if not text:
        return text
    lines = text.splitlines()
    out, state, removed = [], "before", 0
    for line in lines:
        if state == "before":
            out.append(line)
            if re.match(r"^\s*#+\s*(?:aniq\s+)?tashxis\b", line, flags=re.I):
                state = "inside"
            continue
        if state == "inside":
            if re.match(r"^\s*#+\s", line):
                state = "after"
                out.append(line)
                continue
            if _DX_NOISE_RE.match(line):
                removed += 1
                continue
        out.append(line)
    if removed:
        log.info(
            "%s: tashxis bo'limidan %s ta ortiqcha qator olib tashlandi",
            ZIYRAKAI_DISPLAY_NAME, removed,
        )
    return "\n".join(out)


# ─── Tuzilgan tashxis: qaror bosqichi ───────────────────────────────────────
# Audit topgan xato: hisobot bosqichdan bosqichga MATN bo'lib o'tardi va
# o'nga yaqin qadam uni regex bilan yamardi. Sakkiztasi «#### TASHXIS»
# sarlavhasiga bog'liq edi — model «####» ni tushirsa, hammasi jimgina
# o'tkazib yuborilardi. Endi qaror QAT'IY JSON bo'lib keladi, qo'riqchilar
# maydonlar ustida ishlaydi, hisobot esa oxirida koddan chiqariladi.


def _structured_enabled():
    v = (os.environ.get("HISTOLOGY_STRUCTURED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _hypothesis_text(patient_context, gestalt=None):
    """Yo'llanma + klinik izoh + umumiy ko'rinish nomlari — gipoteza matni."""
    return " ".join(x for x in [_referral_text(patient_context)] + _gestalt_names(gestalt) if x)


def _decide_diagnosis(features, adj, kb_block, kwargs, organ_lock=None,
                      patient_context=None, image_parts=None,
                      clinical_block="", clinical_parts=None, gestalt=None, survey=None):
    """Yakuniy tashxisni tuzilgan yozuv sifatida olish. Bo'lmasa None."""
    from . import dx_record as dxr

    if not isinstance(features, dict):
        return None
    feats = _features_prompt_block(features)
    if not feats:
        return None

    blocks = [feats]
    ref_text = _hypothesis_text(patient_context, gestalt)
    gb = _gestalt_block(gestalt)
    if gb:
        blocks.append(gb)
    sb = _survey_block(survey)
    if sb:
        blocks.append(sb)
    crit = _dxc.criteria_block(features, clinical_text=clinical_block, referral_text=ref_text)
    if crit:
        blocks.append(crit)
    if adj:
        blocks.append(_adjudication_block(adj))
    if kb_block:
        # Mezon jadvali deterministik bilimni olib keladi; kitob matni — qo'shimcha.
        # 15 ming belgilik blok har chaqiruvda ~4 ming token yeyardi.
        blocks.append(_trim_block(kb_block, 6000 if _economy_enabled() else 15000))
    if organ_lock and organ_lock.get("organ"):
        blocks.append(f"Organ qulfi: {organ_lock['organ']} — boshqa organ tashxisi yozilmaydi.")
    ref = _referral_dx_block(patient_context)
    if ref:
        blocks.append(ref)
    if clinical_block:
        blocks.append(clinical_block)
        blocks.append(
            "Klinik ko'rinish (tana surati) morfologiya bilan mos kelsa — buni "
            "«evidence» ichida alohida qator qilib ayting (feature: «Klinik moslik»); "
            "mos kelmasa — «differentials» yoki «facts» da nima uchun, ayting. "
            "Klinik surat gistologik belgi EMAS."
        )
    blocks.append(
        "Yuqoridagilarga tayanib yakuniy tashxisni JSON shaklida qaytaring. "
        "Rasmlar ham berilgan — dalil tafsilotini ulardan oling."
    )
    user_text = "\n\n".join(b for b in blocks if b)

    parts = _spread_pick(list(image_parts or []), 3 if _economy_enabled() else 6)
    if clinical_parts:
        # Tana surati past sifatda (≈85 token) — model kesma bilan solishtira oladi
        parts = parts + [
            {"type": "image_url", "image_url": {"url": (p.get("image_url") or {}).get("url", ""),
                                                "detail": "low"}}
            for p in list(clinical_parts)[:2]
        ]
    try:
        raw = _complete_resilient(
            dxr.DECISION_SYSTEM, [user_text], parts,
            {**kwargs, "max_tokens": 3500, "temperature": 0.0}, "qaror",
        )
    except CaseBudgetExceeded:
        raise
    except Exception as e:
        log.warning("%s: qaror bosqichi xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return None

    rec = dxr.from_json(_parse_observation(raw))
    if rec is None:
        log.warning("%s: qaror JSON o'qilmadi: %r", ZIYRAKAI_DISPLAY_NAME, _preview(raw))
        return None
    log.info(
        "%s: qaror — %r (%s dalil, %s differensial)",
        ZIYRAKAI_DISPLAY_NAME, rec.name[:50], len(rec.evidence), len(rec.differentials),
    )
    return rec


def _referral_text(patient_context):
    """Yo'llanma va klinik izoh matni — gipoteza va ko'rinish turi uchun."""
    p = _normalize_patient_context(patient_context)
    return " ".join(x for x in (p.get("clinical_dx"), p.get("clinical_note"),
                                p.get("specimen_site")) if x)


# ─── Umumiy ko'rinish (gestalt) — patologning birinchi qadami ────────────────
# 3-keys darsi: 124 belgili ro'yxat bilan boshlaganda model tafsilotga g'arq
# bo'lib, yakka polipoid tomirli tugunni «akantoliz», «bezli», «bazaloid» deb
# o'qidi — to'rt o'tkazishda to'rt xil. O'sha kadrlarning MONTAJI (kichik
# kattalashtirish o'rnida) + tana surati + yo'llanma bilan bitta to'g'ri savol
# bir zumda «lobulyar kapillyar gemangioma, ishonch yuqori» dedi. Patolog ham
# avval «bu qanday lezyon?» deydi, keyin belgilarni sanaydi. Endi quvur ham.

_GESTALT_SYSTEM = (
    "You are a senior dermatopathologist signing out a case. Work the way you do at the "
    "microscope: FIRST the scanning-magnification gestalt (what kind of lesion is this: "
    "inflammatory vs neoplastic — if inflammatory, which of Ackerman's patterns: superficial "
    "perivascular, superficial and deep perivascular, vasculitis, nodular/diffuse, intraepidermal "
    "vesicular/pustular, subepidermal vesicular, folliculitis, fibrosing, panniculitis; if "
    "neoplastic: epidermal, melanocytic, adnexal, vascular, fibrous, lymphoid; "
    "polypoid/exophytic vs flat; symmetric vs not), THEN the "
    "confirming features, THEN clinicopathologic correlation. Be decisive but honest: "
    "if the fields cannot support a diagnosis, say so and name what would settle it. "
    "Return ONE JSON object only."
)

_GESTALT_SCHEMA = (
    '{"gestalt": "one sentence: lesion category and architecture at low power", '
    '"ackerman_pattern": "inflammatory pattern per Ackerman, or: qo‘llanilmaydi (neoplastik)", '
    '"diagnosis": "single most likely diagnosis — Latin or Uzbek name as used in reports", '
    '"confidence": "low|moderate|high", '
    '"decisive_features": ["3-5 features that carry the diagnosis, each tied to what is visible"], '
    '"alternatives": [{"name": "...", "why_less_likely": "..."}], '
    '"clinicopathologic_fit": "does the histology fit the clinical picture? one sentence", '
    '"what_would_settle_it": "if not certain: which field, level or stain would settle it"}'
)


def _montage(pils, cols=6, tile=(320, 180), max_tiles=36):
    """Barcha kesma kadrlaridan bitta varaq — kichik kattalashtirish o'rnini bosadi."""
    pils = list(pils or [])[:max_tiles]
    if not pils:
        return None
    rows = -(-len(pils) // cols)
    sheet = Image.new("RGB", (cols * tile[0], rows * tile[1]), (245, 245, 245))
    for i, im in enumerate(pils):
        t = im.copy()
        t.thumbnail(tile)
        sheet.paste(t, ((i % cols) * tile[0], (i // cols) * tile[1]))
    return _resize_img(sheet, 1600)


def _gestalt_stage(slide_pils, detail_parts, clinical_parts, patient_context, kwargs,
                   clinical_block=""):
    """Umumiy ko'rinish: montaj + 3 tafsilot + tana surati → yetakchi tashxis."""
    sheet = _montage(slide_pils)
    if sheet is None:
        return None
    p = _normalize_patient_context(patient_context)
    who = ", ".join(x for x in (
        f"age {p.get('age')}" if p.get("age") else "",
        p.get("sex") or "", f"site: {p.get('specimen_site')}" if p.get("specimen_site") else "",
    ) if x)
    lines = [f"Case: {who or 'no demographics'}."]
    if p.get("clinical_dx"):
        lines.append(f"Clinician's impression: {p['clinical_dx']}.")
    if p.get("clinical_note"):
        lines.append(f"Clinical note: {p['clinical_note']}.")
    if clinical_block:
        lines.append("Clinical appearance from the photograph: " + _clinical_summary_line(clinical_block))
    n_detail = len(detail_parts or [])
    lines.append(
        f"Image 1: contact sheet of ALL {min(len(slide_pils), 36)} H&E fields (scanning overview). "
        + (f"Images 2-{1 + n_detail}: representative fields at higher power. " if n_detail else "")
        + ("Last image: clinical photograph." if clinical_parts else "")
    )
    lines.append("Return exactly this JSON:\n" + _GESTALT_SCHEMA)
    parts = [{"type": "image_url", "image_url": {"url": _pil_to_data_url(sheet), "detail": "high"}}]
    parts += list(detail_parts or [])
    if clinical_parts:
        parts.append({"type": "image_url", "image_url": {
            "url": (clinical_parts[0].get("image_url") or {}).get("url", ""), "detail": "low"}})
    try:
        raw = _complete_resilient(
            _GESTALT_SYSTEM, ["\n".join(lines)], parts,
            {**kwargs, "max_tokens": 4000, "temperature": 0.0}, "umumiy ko'rinish",
        )
    except CaseBudgetExceeded as e:
        log.warning("%s: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return None
    data = _parse_observation(raw)
    if not isinstance(data, dict) or not str(data.get("diagnosis") or "").strip():
        # Bir marta qayta: qisqaroq javob so'raladi (kesilgan/yaroqsiz JSON holati)
        log.warning("%s: umumiy ko'rinish o'qilmadi: %r — qayta urinish", ZIYRAKAI_DISPLAY_NAME, _preview(raw))
        try:
            raw = _complete_resilient(
                _GESTALT_SYSTEM, ["\n".join(lines) + "\nKeep every value short (one clause)."],
                parts[:1] + parts[-1:], {**kwargs, "max_tokens": 4000, "temperature": 0.0},
                "umumiy ko'rinish (qayta)",
            )
        except CaseBudgetExceeded as e:
            log.warning("%s: %s", ZIYRAKAI_DISPLAY_NAME, e)
            return None
        data = _parse_observation(raw)
        if not isinstance(data, dict) or not str(data.get("diagnosis") or "").strip():
            log.warning("%s: umumiy ko'rinish qayta ham o'qilmadi", ZIYRAKAI_DISPLAY_NAME)
            return None
    data["_sheet"] = sheet
    log.info(
        "%s: umumiy ko'rinish — %r (%s): %s",
        ZIYRAKAI_DISPLAY_NAME, str(data.get("diagnosis"))[:50], data.get("confidence"),
        str(data.get("gestalt"))[:90],
    )
    return data


def _gestalt_names(g):
    if not isinstance(g, dict):
        return []
    out = [str(g.get("diagnosis") or "").strip()]
    for a in (g.get("alternatives") or [])[:3]:
        if isinstance(a, dict) and a.get("name"):
            out.append(str(a["name"]).strip())
    return [n for n in out if n]


def _gestalt_block(g):
    """Qaror bosqichi uchun: senior o'qish — ustuvor, lekin belgilar bilan tekshiriladi."""
    if not isinstance(g, dict):
        return ""
    lines = ["#### UMUMIY KO'RINISH (kichik kattalashtirish, barcha kadrlar montaji — senior o'qish)"]
    lines.append(f"Lezyon: {str(g.get('gestalt') or '').strip()}")
    if g.get("ackerman_pattern"):
        lines.append(f"Yallig'lanish patterni (Akkerman): {str(g['ackerman_pattern']).strip()[:120]}")
    lines.append(f"Yetakchi tashxis: {g.get('diagnosis')} (ishonch: {g.get('confidence') or 'noaniq'})")
    feats = [str(x).strip() for x in (g.get("decisive_features") or []) if str(x).strip()]
    if feats:
        lines.append("Hal qiluvchi belgilar: " + "; ".join(feats[:5]))
    for a in (g.get("alternatives") or [])[:3]:
        if isinstance(a, dict) and a.get("name"):
            lines.append(f"Muqobil: {a['name']} — {str(a.get('why_less_likely') or '').strip()[:160]}")
    if g.get("clinicopathologic_fit"):
        lines.append(f"Klinik moslik: {str(g['clinicopathologic_fit']).strip()[:200]}")
    if g.get("what_would_settle_it"):
        lines.append(f"Hal qiluvchi qadam: {str(g['what_would_settle_it']).strip()[:200]}")
    ent = _dxc.find_entity(str(g.get("diagnosis") or ""))
    if ent:
        ess = ", ".join(_dxc.feature_label(k) for k in ent["essential"][:5])
        exc = ", ".join(_dxc.feature_label(k) for k in ent["excluding"][:6])
        lines.append(f"Jadval bo'yicha «{ent['name']}» majburiy belgilari: {ess or '—'}")
        lines.append(f"Uni RAD ETUVCHI belgilar: {exc or '—'}")
    lines.append(
        "QOIDA: bu senior o'qish (kichik kattalashtirish + klinik surat) USTUVOR. Boshqa nom "
        "faqat ikki holatda: (1) yuqoridagi RAD ETUVCHI belgilardan biri kesmada aniq "
        "ko'rilgan; (2) majburiy belgilarning birortasi ham yo'q. Qo'shimcha tasodifiy "
        "belgilar (koilotsit, parakeratoz, giperkeratoz va sh.k.) senior o'qishni bekor "
        "qilmaydi — ular «facts» ga yoziladi."
    )
    return "\n".join(lines) + "\n"


def _record_from_gestalt(g):
    """Qaror chaqiruvi o'tmasa — umumiy ko'rinishdan yozuv (taxminiy)."""
    from . import dx_record as dxr

    if not isinstance(g, dict) or not str(g.get("diagnosis") or "").strip():
        return None
    rec = dxr.DxRecord(name=str(g["diagnosis"]).strip()[:160], organ="teri",
                       certainty=dxr.CERTAIN_PROVISIONAL)
    for f in (g.get("decisive_features") or [])[:5]:
        t = str(f).strip()
        if t:
            rec.evidence.append(dxr.Evidence(feature=t[:80], seen=True, detail="umumiy ko'rinishda"))
    for a in (g.get("alternatives") or [])[:3]:
        if isinstance(a, dict) and a.get("name"):
            rec.differentials.append(dxr.Differential(
                name=str(a["name"])[:80], excluded_by=str(a.get("why_less_likely") or "")[:160]))
    rec.notes.append("qaror chaqiruvi o'tmadi — umumiy ko'rinish yozuvi")
    return rec


def _trim_block(text, limit):
    """Matnni paragraf chegarasida qisqartirish."""
    t = text or ""
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit("\n", 1)[0]
    return cut + "\n(… qisqartirildi)"


def _organ_from_observation(features, patient_context=None):
    """Organ qulfi — alohida chaqiruvsiz: klinik yo'nalish yoki ko'rik javobi."""
    p = _normalize_patient_context(patient_context)
    forced = _organ_from_text(p.get("specimen_site")) or _organ_from_text(p.get("clinical_note"))
    if forced:
        return {"organ": forced, "confidence": "high", "reason_uz": "Klinik yo'nalish"}
    organ = str((features or {}).get("organ") or "").strip().lower() if isinstance(features, dict) else ""
    if organ:
        mapped = _organ_from_text(organ) or organ
        return {"organ": mapped, "confidence": "medium", "reason_uz": "Ko'rikdan"}
    return {"organ": "teri", "confidence": "low", "reason_uz": "Standart: teri"}


def _mismatch_from_observation(features, lab_type):
    """Namuna tekshiruvi — ko'rikning not_tissue javobidan (alohida chaqiruvsiz)."""
    if isinstance(features, dict) and features.get("not_tissue") is True:
        return (
            "Yuklangan rasm(lar)da to'qima kesmasi ko'rinmadi — bu mikroskop kadri emas "
            "yoki tanlangan tahlil turiga mos emas. H&E kesma kadrini yuklang."
        )
    return None


def _record_from_criteria(features):
    """Model qaror bera olmaganda — mezon jadvalidan deterministik yozuv.

    Bu «bo'sh qo'l bilan qaytmaslik» yo'li: ko'rilgan belgilar bo'yicha eng
    mos nozologiya, taxminiy deb belgilangan holda. Model chaqiruvi yo'q.
    """
    from . import dx_record as dxr

    rows = _dxc.rank_candidates(features, 3)
    if not rows:
        alt = _descriptive_dx(features)
        if not alt:
            return None
        return dxr.DxRecord(name=alt, certainty=dxr.CERTAIN_DESCRIPTIVE, organ="teri")
    top = rows[0]
    rec = dxr.DxRecord(
        name=top["name"], certainty=dxr.CERTAIN_PROVISIONAL,
        malignant=bool(top["malignant"]), organ="teri",
    )
    for spec in top["essential_present"][:6]:
        rec.evidence.append(dxr.Evidence(feature=_dxc.feature_label(spec), seen=True,
                                         detail="ko'rikda belgilangan"))
    for r in rows[1:3]:
        miss = ", ".join(_dxc.feature_label(x) for x in r["essential_absent"][:2]) or "mezon kamroq mos"
        rec.differentials.append(dxr.Differential(name=r["name"], excluded_by=miss))
    rec.notes.append("model qaror bermadi — mezon jadvalidan deterministik yozuv")
    return rec


# ─── Hal qiluvchi belgilarni qayta tekshirish ────────────────────────────────
# Kuzatilgan xato: bir xil kesma ikki o'tkazishda «spongioform pustula» va
# «akantoliz» deb o'qildi — ikkinchisi psoriazni Hailey–Hailey ga aylantirib,
# 87% ishonch berdi. Bitta ko'rikdagi shovqin to'g'ridan-to'g'ri tashxisga
# o'tib ketadi. Patolog bunday joyda kattalashtirib QAYTA QARAYDI — aynan shu
# belgiga. Shu qadam: tanlangan tashxisning tayanch belgilari va uni rad
# etadigan/muqobilni qo'llaydigan belgilar uchun aniq ta'rifli savol.
# Narxi — kichik chaqiruv (≈2.5k kirish, ~150 chiqish).

_VERIFY_SYSTEM = (
    "You are a dermatopathologist re-examining a few H&E fields of ONE case to settle "
    "specific findings. Answer 'bor' if the feature is present in ANY field. "
    "Decide each finding strictly from what is visible. "
    "Use the definitions given — they exist because these features are confused. "
    "Return ONE JSON object: {\"<key>\": \"bor\"|\"yo'q\"|\"noaniq\", ...}, keys exactly "
    "as given, nothing else. Answer 'noaniq' only if the field truly cannot show it."
)

# Chalkashadigan belgilar uchun ta'rif — savolda beriladi
_VERIFY_DEFS = {
    "acantholysis": "TRUE acantholysis: keratinocytes lose intercellular bridges and round up "
                    "as separate cells (dilapidated brick wall / tombstones). NOT a neutrophil "
                    "pustule, NOT spongiosis.",
    "spongiform_pustule": "Kogoj pustule: neutrophils within a sponge-like epidermal meshwork in "
                          "the upper spinous layer; keratinocytes still attached.",
    "spongiosis": "Intercellular oedema widening spaces between keratinocytes, bridges visible.",
    "munro_microabscess": "Neutrophils collected inside PARAKERATOTIC stratum corneum.",
    "regular_elongated_rete": "Rete ridges elongated to a similar length in a regular pattern.",
    "pagetoid_spread": "Melanocytes scattered singly in upper epidermal layers.",
    "peripheral_palisading": "Nuclei lined up like a fence at the edge of basaloid islands.",
    "horn_cysts": "Round keratin-filled cysts within the epithelial proliferation.",
    "koilocytes": "Keratinocytes with perinuclear halo and shrunken, wrinkled nucleus.",
    "band_like_infiltrate": "Dense lichenoid lymphocytic band hugging the junction.",
    "vacuolar_change": "Vacuoles in basal keratinocytes at the junction.",
    "necrobiosis": "Altered, smudged, hypocellular collagen surrounded by histiocytes.",
    "granuloma": "Organised aggregate of epithelioid histiocytes.",
    "leukocytoclasia": "Nuclear dust of fragmented neutrophils around vessels.",
    "subepidermal_blister": "Cleft/blister BELOW the epidermis with an intact roof.",
    "intraepidermal_vesicle": "Fluid space WITHIN the epidermis.",
    "epidermotropism": "Atypical lymphocytes in epidermis without spongiosis.",
    "full_thickness_atypia": "Atypical keratinocytes through the whole epidermal thickness.",
    "storiform_pattern": "Cartwheel/pinwheel arrangement of spindle cells.",
    "collagen_trapping": "Collagen bundles caught between tumour cells at the edge.",
    "lobular_capillary_proliferation": "Lobules of small capillaries with plump endothelium in an "
                                       "oedematous stroma, often under a thinned epidermis with a "
                                       "collarette (pyogenic granuloma).",
    "vascular_proliferation": "Increased number of vascular channels lined by endothelium.",
    "extravasated_erythrocytes": "Red blood cells outside vessels, in the stroma.",
    "dyskeratosis": "Individual keratinocytes keratinising prematurely (corps ronds/grains) — "
                    "NOT oedematous stroma, NOT inflammatory cells.",
    "intraepidermal_vesicle": "Fluid-filled space WITHIN the epidermis, bounded by keratinocytes "
                              "— NOT loose oedematous dermal stroma beneath a thin epidermis.",
}


def _verify_enabled():
    v = (os.environ.get("HISTOLOGY_VERIFY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _decisive_features(rec, features, referral_text=""):
    """Qayta tekshiriladigan belgilar: tanlovning tayanchi + uni rad etuvchilar
    + eng yaqin muqobillarning yo'q deb topilgan majburiy belgilari
    + yo'llanma gipotezasining majburiy belgilari (ko'rik ularni umuman
    belgilamagan bo'lishi mumkin — aynan shu joyda adashadi)."""
    keys = []
    hinted = _dxc.referral_entities(referral_text)[:2] + _dxc.clinical_entities(referral_text)[:2]
    for name in hinted:
        ent = _dxc.find_entity(name)
        if ent:
            keys += [k for k in ent["essential"] if "=" not in k][:3]
    ev = _dxc.check_name(rec.name, features)
    if ev:
        keys += [k for k in ev["essential_present"] if "=" not in k][:3]
        keys += [k for k in ev["excluding_present"] if "=" not in k][:2]
    for r in _dxc.rank_candidates(features, 3):
        if ev and r["name"] == ev["name"]:
            continue
        keys += [k for k in r["essential_absent"] if "=" not in k][:2]
        keys += [k for k in r["essential_present"] if "=" not in k][:1]
    out = []
    for k in keys:
        if k not in out and k in _FEATURE_UZ:
            out.append(k)
    return out[:8]


# ─── Kadr-kadr qidiruv ───────────────────────────────────────────────────────
# 6-keys darsi: bir xil kesma ikki o'tkazishda «seboreik keratoz» va «verruca»
# bo'ldi. Ikkalasi papillomatoz keratoz; farqi — shox psevdokistalari va
# bazaloid hujayralar (SK) yoki koilotsitlar va dag'al keratogialin (verruca).
# Ko'rik 6 kadrni ko'radi, tekshiruv 3 tasini; qolgan 12 kadr ko'rilmaydi.
# Patolog esa shubha bo'lsa HAMMA maydonni shu belgilar uchun ko'zdan kechiradi
# va «koilotsitlar 4- va 9-maydonda» deb yozadi. Endi dastur ham shunday:
# gipotezalarni ajratuvchi belgilar barcha kadrlarda, 6 tadan, izlanadi.

_SURVEY_SYSTEM = (
    "You are a dermatopathologist scanning every field of ONE case for specific findings. "
    "You will get several numbered H&E fields and a list of features with definitions. "
    "For EACH field report which of the features are definitely present in THAT field. "
    "Be strict: report a feature only when you actually see it in that field. "
    "Return ONE JSON object: {\"fields\": {\"<field number>\": [\"<feature key>\", ...]}} "
    "with an entry for every field (empty list if nothing). Keys exactly as given."
)


def _survey_enabled():
    v = (os.environ.get("HISTOLOGY_SURVEY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _survey_keys(gestalt, referral_text="", clinical_text="", rec=None, limit=10):
    """Gipotezalarni bir-biridan AJRATUVCHI belgilar: har gipotezaning majburiy va
    rad etuvchi belgilari (skalyar shartlarsiz)."""
    names = _gestalt_names(gestalt)
    if rec is not None and rec.name:
        names.insert(0, rec.name)
    names += _dxc.referral_entities(referral_text)[:2]
    names += _dxc.clinical_entities((clinical_text or "") + " " + (referral_text or ""))[:2]
    seen, ents = set(), []
    for n in names:
        e = _dxc.find_entity(n)
        if e and e["name"] not in seen:
            seen.add(e["name"])
            ents.append(e)
    keys = []
    for e in ents[:4]:
        for k in list(e["essential"]) + list(e["excluding"])[:3]:
            if "=" not in k and k in _FEATURE_UZ and k not in keys:
                keys.append(k)
    return keys[:limit]


def _frame_survey(keys, slide_parts, kwargs, batch=6):
    """Har kadrda qaysi belgilar bor — {key: {"count": n, "frames": [1-based]}}.
    None — qidiruv o'tmadi."""
    parts = list(slide_parts or [])
    if not keys or not parts or not _survey_enabled():
        return None
    defs = []
    for k in keys:
        d = _VERIFY_DEFS.get(k, "")
        defs.append(f"- {k} ({_FEATURE_UZ.get(k, k)})" + (f": {d}" if d else ""))
    found = {k: [] for k in keys}
    answered = 0
    for start in range(0, len(parts), batch):
        chunk = parts[start:start + batch]
        numbered = []
        content = [{"type": "text", "text": (
            f"Fields {start + 1}–{start + len(chunk)} of {len(parts)}. Features to look for:\n"
            + "\n".join(defs)
            + "\nReturn JSON with a key for every field number listed."
        )}]
        for i, p in enumerate(chunk, start=start + 1):
            content.append({"type": "text", "text": f"FIELD {i}:"})
            content.append(p)
            numbered.append(i)
        try:
            raw = _chat_complete(
                [{"role": "system", "content": _SURVEY_SYSTEM},
                 {"role": "user", "content": content}],
                {**kwargs, "max_tokens": 2000, "temperature": 0.0},
                label=f"kadr qidiruv {start + 1}-{start + len(chunk)}",
            )
        except CaseBudgetExceeded as e:
            log.warning("%s: %s", ZIYRAKAI_DISPLAY_NAME, e)
            break
        except Exception as e:
            log.warning("%s: kadr qidiruv xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
            continue
        data = _parse_observation(raw)
        fields = (data or {}).get("fields") if isinstance(data, dict) else None
        if not isinstance(fields, dict):
            log.warning("%s: kadr qidiruv javobi o'qilmadi: %r", ZIYRAKAI_DISPLAY_NAME, _preview(raw))
            continue
        for fno, feats in fields.items():
            try:
                n = int(str(fno).strip().lstrip("field FIELD"))
            except ValueError:
                continue
            if n not in numbered:
                continue
            answered += 1
            for f_ in feats or []:
                key = str(f_).strip()
                if key in found and n not in found[key]:
                    found[key].append(n)
    if not answered:
        return None
    out = {k: {"count": len(v), "frames": sorted(v)} for k, v in found.items()}
    log.info(
        "%s: kadr qidiruv — %s kadr: %s",
        ZIYRAKAI_DISPLAY_NAME, answered,
        "; ".join(f"{_FEATURE_UZ.get(k, k)}={d['count']}" for k, d in out.items()),
    )
    return {"n_frames": len(parts), "answered": answered, "found": out}


def _apply_survey(features, survey):
    """Qidiruv natijasi ko'rik belgilariga o'tkaziladi: topilgan → True, hech qaysi
    kadrda topilmagan → False. Kadr raqamlari alohida saqlanadi."""
    if not isinstance(features, dict) or not survey:
        return []
    changed = []
    frames = {}
    for key, d in (survey.get("found") or {}).items():
        present = d["count"] >= 1
        if present:
            frames[key] = d["frames"]
        if _feature_true(features, key) != present:
            _set_feature(features, key, present)
            changed.append(f"{_FEATURE_UZ.get(key, key)}: {'bor' if present else 'yo`q'}")
    features["_frames"] = frames
    return changed


def _survey_line(survey, keys=None):
    """Hisobot uchun bitta satr: «Kadr sanog'i (18 kadr): koilotsitlar 4 (№7,8,9,11); …»."""
    if not survey:
        return ""
    found = survey.get("found") or {}
    items = []
    for key in (keys or list(found.keys())):
        d = found.get(key)
        if d is None:
            continue
        lab = _FEATURE_UZ.get(key, key).split(" (")[0]
        if d["count"]:
            items.append(f"{lab} {d['count']} (№{','.join(str(x) for x in d['frames'][:6])})")
        else:
            items.append(f"{lab} 0")
    if not items:
        return ""
    return f"Kadr sanog'i ({survey['answered']}/{survey['n_frames']} kadr): " + "; ".join(items[:8])


def _survey_block(survey):
    if not survey:
        return ""
    lines = [f"#### KADR-KADR QIDIRUV ({survey['answered']}/{survey['n_frames']} kadr ko'rildi — sanoq, taxmin emas)"]
    for key, d in (survey.get("found") or {}).items():
        lab = _FEATURE_UZ.get(key, key)
        if d["count"]:
            lines.append(f"- {lab}: {d['count']} kadrda (№ {', '.join(str(x) for x in d['frames'][:8])})")
        else:
            lines.append(f"- {lab}: hech bir kadrda topilmadi")
    lines.append(
        "Tashxis shu sanoqqa tayansin: gipotezaning majburiy belgisi hech bir kadrda "
        "topilmagan bo'lsa, u gipoteza qo'yilmaydi; dalil qatorida kadr raqamini ayting."
    )
    return "\n".join(lines) + "\n"


def _verify_decisive_features(rec, features, image_parts, kwargs, referral_text=""):
    """Hal qiluvchi belgilarni bitta rasmda qayta so'rash; features yangilanadi.
    Qaytaradi: o'zgargan belgilar ro'yxati."""
    """None — tekshiruv o'tmadi (ikkinchi ko'z yo'q); [] — o'tdi, o'zgarish yo'q."""
    if rec is None or not isinstance(features, dict) or not image_parts or not _verify_enabled():
        return None
    keys = _decisive_features(rec, features, referral_text)
    if not keys:
        return []
    lines = []
    for k in keys:
        d = _VERIFY_DEFS.get(k, "")
        lines.append(f"- {k} ({_FEATURE_UZ.get(k, k)}): {d}" if d else f"- {k} ({_FEATURE_UZ.get(k, k)})")
    user = (
        "Re-examine this field and settle the following findings:\n" + "\n".join(lines)
        + "\nJSON only, keys exactly as listed."
    )
    try:
        # Fikrlovchi model javob oldidan token sarflaydi — 300 yetmadi, javob bo'sh keldi
        raw = _complete_resilient(
            _VERIFY_SYSTEM, [user], _spread_pick(list(image_parts), 3),
            {**kwargs, "max_tokens": 800, "temperature": 0.0}, "tekshiruv",
        )
    except CaseBudgetExceeded as e:
        log.warning("%s: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return None
    data = _parse_observation(raw)
    if not isinstance(data, dict):
        log.warning("%s: tekshiruv javobi o'qilmadi: %r", ZIYRAKAI_DISPLAY_NAME, _preview(raw))
        return None
    changed = []
    for k in keys:
        v = str(data.get(k) or "").strip().lower().replace("‘", "'").replace("’", "'")
        if v not in ("bor", "yo'q"):
            continue
        new = v == "bor"
        old = _feature_true(features, k)
        if new != old:
            _set_feature(features, k, new)
            changed.append(f"{_FEATURE_UZ.get(k, k)}: {'bor' if new else 'yo`q'}")
    features.pop("_group_names", None)
    if changed:
        log.info("%s: tekshiruv — o'zgardi: %s", ZIYRAKAI_DISPLAY_NAME, "; ".join(changed))
    else:
        log.info("%s: tekshiruv — %s belgi tasdiqlandi", ZIYRAKAI_DISPLAY_NAME, len(keys))
    return changed


def _set_feature(features, key, value):
    for g in ("epidermis", "junction", "dermis", "glandular", "cytology", "special"):
        sub_ = features.get(g)
        if isinstance(sub_, dict) and key in sub_:
            sub_[key] = bool(value)
            return
    for g, table in (("epidermis", _dxc.EPIDERMIS), ("junction", _dxc.JUNCTION),
                     ("dermis", _dxc.DERMIS), ("cytology", _dxc.CYTOLOGY_BOOL),
                     ("special", _dxc.SPECIAL)):
        if key in table:
            features.setdefault(g, {})[key] = bool(value)
            return


def _clinical_summary_line(block):
    """Klinik blokdan hisobot uchun qisqa satr (sarlavha va ko'rsatmasiz)."""
    body = []
    for line in (block or "").splitlines():
        t = line.strip()
        if not t or t.startswith("###") or t.startswith("Bu klinik kontekst"):
            continue
        body.append(t)
    text = " ".join(" ".join(body).split())
    return text[:260].rsplit(" ", 1)[0] + ("…" if len(text) > 260 else "") if text else ""


def _material_changes(name, changes, ranked):
    """Tekshiruv o'zgarishlaridan tashxisga ta'sir qiladiganlari."""
    keys = set()
    ent = _dxc.find_entity(name)
    if ent:
        keys |= {k for k in ent["essential"] + ent["excluding"] if "=" not in k}
    for r in (ranked or [])[:2]:
        if ent and r["name"] == ent["name"]:
            continue
        alt = _dxc.find_entity(r["name"])
        if alt:
            keys |= {k for k in alt["essential"] if "=" not in k}
    labels = {_FEATURE_UZ.get(k, k).lower() for k in keys}
    out = []
    for c in changes or []:
        label = c.split(":")[0].strip().lower()
        if any(label == l or label.split(" (")[0] == l.split(" (")[0] for l in labels):
            out.append(c)
    return out


def _finish_record(rec, features, adj, names, verified_changes=None, clinical_text="",
                   referral_text="", gestalt=None, referral_pure=""):
    """Qo'riqchilar + foiz + hisobot matni. Har doim to'liq ishlaydi."""
    from . import dx_record as dxr

    # Umumiy ko'rinish, qaror va klinika — oldindan hisoblanadi: tekshiruv
    # bloki shunga qarab nomni almashtiradi yoki almashtirmaydi.
    _g_dx = str((gestalt or {}).get("diagnosis") or "").strip() if isinstance(gestalt, dict) else ""
    _g_ent = _dxc.find_entity(_g_dx) if _g_dx else None
    _r_ent = _dxc.find_entity(rec.name)
    agree_early = bool(_g_dx) and (
        _same_entity(_g_dx, rec.name) or (_g_ent is not None and _r_ent is not None and _g_ent is _r_ent))
    _g_strong = str((gestalt or {}).get("confidence") or "").lower() in ("high", "moderate") \
        if isinstance(gestalt, dict) else False
    _clinic_names = set(_dxc.referral_entities(referral_pure) + _dxc.clinical_entities(
        (clinical_text or "") + " " + (referral_pure or "")))
    clinic_backs_early = _g_ent is not None and _g_ent["name"] in _clinic_names

    if verified_changes is None and _economy_enabled() and _verify_enabled():
        # Ikkinchi ko'z bo'lmadi — bitta ko'rikka to'liq ishonib bo'lmaydi
        rec.confidence_cap = min(rec.confidence_cap or 100, 75)
        rec.notes.append("hal qiluvchi belgilar tekshiruvi o'tmadi — ishonch 75% bilan cheklandi")
    if verified_changes:
        # Tekshiruv hal qiluvchi belgini o'zgartirdi. Uch holat:
        #  (a) tanlangan nomning tayanchi qolmadi → jadvalning yangi 1-o'rni;
        #  (b) nom jadvalda yo'q, lekin tekshiruv klinik/yo'llanma gipotezasini
        #      qo'llab-quvvatladi → gipoteza nomi (3-keys: «tomir proliferatsiyasi:
        #      bor» topildi, nom esa SCAP bo'lib qolaverdi);
        #  (c) aks holda nom qoladi, ammo taxminiy.
        # Har holda tasdiqlangan belgi dalilga yoziladi va unga zid differensial
        # olib tashlanadi — hisobot o'zini o'zi rad etmasin.
        ev = _dxc.check_name(rec.name, features)
        ranked = _dxc.rank_candidates(features, 3, clinical_text, referral_text)
        top = ranked[:1]
        hinted = _dxc.referral_entities(referral_text)[:2] + _dxc.clinical_entities(
            (clinical_text or "") + " " + (referral_text or ""))[:2]
        lost = ev is not None and (ev["essential_hits"] == 0 or ev["excluding_present"])
        hinted_top = next((r for r in ranked if r["name"] in hinted), None)
        swap = None
        if lost and top and not _same_entity(top[0]["name"], rec.name):
            swap, why = top[0], "tayanchsiz qoldi"
        elif ev is None and hinted_top is not None:
            swap, why = hinted_top, "jadvalda yo'q, tekshiruv gipotezani qo'llab-quvvatladi"
        if swap is not None and agree_early and _g_strong:
            # Umumiy ko'rinish va qaror bir nomga kelgan — ro'yxat shovqini nomni
            # almashtira olmaydi (3-keys: PG → «Vitiligo» → PG sakrashi).
            rec.notes.append(
                f"tekshiruvdan keyin ro'yxat «{swap['name']}» ni ko'tardi, lekin umumiy "
                f"ko'rinish va qaror «{rec.name}» da kelishgan — nom saqlanadi"
            )
            swap = None
        if swap is not None:
            rec.notes.append(
                f"tekshiruvdan keyin «{rec.name}» {why} — "
                f"mezon jadvali bo'yicha «{swap['name']}» ga almashtirildi"
            )
            rec.differentials = [d for d in rec.differentials
                                 if not _same_entity(d.name, swap["name"])]
            rec.differentials.insert(0, dxr.Differential(
                name=rec.name, excluded_by="tekshiruvdan keyin mezonlari kamroq mos"))
            rec.name = swap["name"]
            rec.malignant = bool(swap["malignant"])
            rec.evidence = [
                dxr.Evidence(feature=_dxc.feature_label(k), seen=True, detail="tekshiruvda tasdiqlandi")
                for k in swap["essential_present"][:6]
            ]
        # Tasdiqlangan (yo'q → bor) belgilar dalilga — faqat tashxisga aloqadorlari;
        # ularni rad etgan differensiallar olib tashlanadi
        material_now = _material_changes(rec.name, verified_changes, ranked)
        present = [c.split(":")[0].strip() for c in verified_changes if c.endswith(": bor")]
        for label in present:
            relevant = any(label in m for m in material_now)
            if relevant and not any(e.feature.lower() == label.lower() for e in rec.evidence):
                rec.evidence.append(dxr.Evidence(feature=label, seen=True,
                                                 detail="qayta tekshiruvda tasdiqlandi"))
            rec.differentials = [
                d for d in rec.differentials
                if label.lower().split(" (")[0] not in (d.excluded_by or "").lower()
            ]
        # Faqat AHAMIYATLI o'zgarish shift qo'yadi: tanlangan nozologiyaning
        # majburiy/rad etuvchi belgisi yoki eng yaqin muqobilning majburiy
        # belgisi. 3-keys: PG to'g'ri tanlangan edi, tekshiruv «bazal
        # vakuolizatsiya: bor» dedi — PG uchun ahamiyatsiz, ammo 60% shifti tushdi.
        material = _material_changes(rec.name, verified_changes, ranked)
        if swap is not None or material:
            rec.certainty = dxr.CERTAIN_PROVISIONAL
            rec.confidence_cap = min(rec.confidence_cap or 100, 60)
            rec.notes.append("tekshiruv hal qiluvchi belgini o'zgartirdi: " + "; ".join(material[:4]))
        else:
            rec.notes.append("tekshiruv ikkinchi darajali belgilarni o'zgartirdi: "
                             + "; ".join(verified_changes[:4]))
    rec = dxr.apply_guards(rec, features, _DX_REQUIRED_FEATURES, _descriptive_dx(features))
    if rec is None:
        return None, ""
    # Klinik-gistologik muvofiqlik. 3-keys: yakka qizil oyoqchali tugun (bola,
    # «Ангиома?») kesmada «Hailey–Hailey» deb o'qildi — tarqoq irsiy dermatoz.
    # Patolog bunday holatda tashxisni qat'iy qo'ymaydi: nomuvofiqlikni yozadi,
    # qayta kesma/qayta ko'rik so'raydi. Dastur ham shunday qiladi.
    hint = _dxc.clinical_hint(clinical_text) or _dxc.clinical_hint(referral_text)
    ent = _dxc.find_entity(rec.name)
    if hint and ent and ent["presentation"] != "either" and ent["presentation"] != hint:
        expected = _dxc.referral_entities(referral_text)[:2] + _dxc.clinical_entities(
            (clinical_text or "") + " " + (referral_text or ""))[:2]
        expected = [n for i, n in enumerate(expected) if n not in expected[:i]]
        kind = "yakka o'choq/tugun" if hint == "solitary" else "tarqoq toshma"
        rec.discordance = (
            f"Klinik-gistologik NOMUVOFIQLIK: klinik ko'rinish — {kind}"
            + (f" (kutilgan: {', '.join(expected[:2])})" if expected else "")
            + f"; «{rec.name}» esa {'tarqoq dermatoz' if ent['presentation'] == 'eruption' else 'yakka o‘sma'}. "
            "Kesmadagi belgilar shu tashxisga to'g'ri kelsa ham, klinik rasm unga mos emas — "
            "qayta kesma (seriyali), qayta ko'rik va klinik ma'lumot bilan solishtirish tavsiya etiladi."
        )
        rec.certainty = dxr.CERTAIN_PROVISIONAL
        rec.confidence_cap = min(rec.confidence_cap or 100, 45)
        rec.notes.append(f"klinik nomuvofiqlik: {hint} vs {ent['presentation']} ({rec.name})")
    # Umumiy ko'rinish bilan kelishuv: senior o'qish va yakuniy nom bir xil bo'lsa
    # ishonch ko'tariladi; zid bo'lsa — ikki o'qish bir-biriga qarama-qarshi,
    # bu ochiq aytiladi va ishonch cheklanadi.
    if isinstance(gestalt, dict) and str(gestalt.get("diagnosis") or "").strip():
        gdx = str(gestalt["diagnosis"]).strip()
        ge, re_ = _dxc.find_entity(gdx), _dxc.find_entity(rec.name)
        same = _same_entity(gdx, rec.name) or (ge is not None and re_ is not None and ge is re_)
        if same:
            rec.gestalt_agreement = "mos"
            rec.gestalt_bonus = {"high": 12, "moderate": 8}.get(
                str(gestalt.get("confidence") or "").lower(), 0)
            if isinstance(features, dict):
                features["_gestalt_agrees"] = True
        else:
            # Klinika (yo'llanma yoki tana surati) gestalt bilan bir nomga kelganmi?
            # Ha bo'lsa — ikki mustaqil manba qarorga qarshi; qaror faqat rad etuvchi
            # belgi bilan g'olib bo'la oladi. 3-keys: gestalt PG, klinika «angioma»,
            # qaror esa «koilotsit» tufayli Verruca dedi — koilotsit PG ni rad etmaydi.
            clinic_names = set(_dxc.referral_entities(referral_pure) +
                               _dxc.clinical_entities((clinical_text or "") + " " + (referral_pure or "")))
            clinic_backs = ge is not None and ge["name"] in clinic_names
            gev = _dxc.evaluate(ge, features) if ge is not None else None
            gestalt_excluded = bool(gev and gev["excluding_present"])
            strong = str(gestalt.get("confidence") or "").lower() in ("high", "moderate")
            # Qaror klinik ko'rinish turiga zid nom qo'ygan (yakka tugunga tarqoq
            # dermatoz), gestalt esa turiga mos — bu ham gestalt foydasiga ikkinchi
            # mustaqil dalil (6-keys: gestalt SK, qaror «lichen simplex»).
            hint_ = _dxc.clinical_hint(clinical_text) or _dxc.clinical_hint(referral_pure)
            re_ = _dxc.find_entity(rec.name)
            presentation_backs = bool(
                hint_ and ge is not None and re_ is not None
                and ge["presentation"] in (hint_, "either")
                and re_["presentation"] not in (hint_, "either")
            )
            if (clinic_backs or presentation_backs) and strong and not gestalt_excluded:
                rec.notes.append(
                    f"qaror «{rec.name}» senior o'qish «{gdx}» ni rad etuvchi belgisiz bekor qildi; "
                    + ("klinika gestaltni qo'llaydi" if clinic_backs else "qaror nomi klinik ko'rinish turiga zid")
                    + " — nom gestaltniki"
                )
                rec.differentials = [
                    d for d in rec.differentials
                    if not _same_entity(d.name, gdx) and _dxc.find_entity(d.name) is not ge
                ]
                rec.differentials.insert(0, dxr.Differential(
                    name=rec.name[:80],
                    excluded_by="ko'rikdagi qo'shimcha belgilar; umumiy ko'rinish va klinika boshqa nomga keldi"))
                rec.name = ge["name"]
                rec.malignant = bool(ge["malignant"])
                keep = [e for e in rec.evidence if not any(
                    w in e.feature.lower() for w in ("koilotsit", "virus", "parakeratoz", "granulyoz"))]
                rec.evidence = keep[:4]
                for f_ in (gestalt.get("decisive_features") or [])[:4]:
                    t = str(f_).strip()
                    if t and not any(t[:24].lower() in e.feature.lower() for e in rec.evidence):
                        rec.evidence.append(dxr.Evidence(feature=t[:90], seen=True,
                                                         detail="umumiy ko'rinishda (barcha kadrlar)"))
                rec.gestalt_agreement = "mos"
                rec.gestalt_bonus = 4
                if rec.discordance and ge["presentation"] in (hint_, "either"):
                    rec.discordance = ""          # nomuvofiqlik qaror nomiga tegishli edi
                    rec.confidence_cap = 0
                rec.certainty = dxr.CERTAIN_PROVISIONAL
                rec.confidence_cap = min(rec.confidence_cap or 100, 72)
                if isinstance(features, dict):
                    features["_gestalt_agrees"] = True
            else:
                rec.gestalt_agreement = f"zid: umumiy ko'rinish «{gdx}» dedi"
                rec.confidence_cap = min(rec.confidence_cap or 100, 60)
                rec.certainty = dxr.CERTAIN_PROVISIONAL
                rec.notes.append(f"umumiy ko'rinish «{gdx}», qaror «{rec.name}» — kelishmadi")
                if not any(_same_entity(d.name, gdx) for d in rec.differentials):
                    rec.differentials.insert(0, dxr.Differential(
                        name=gdx[:80], excluded_by="umumiy ko'rinishda yetakchi edi — belgilar bilan kelishmadi"))
    if isinstance(features, dict):
        features["_chosen_name"] = rec.name
        features["_clinical_text"] = clinical_text or ""
        features["_referral_text"] = referral_text or ""
        if isinstance(features.get("description"), dict) and not rec.description:
            rec.description = dict(features["description"])
        # Dalil qatorlariga kadr raqamlari: «koilotsitlar (kadr: 4, 9)»
        frames = features.get("_frames") or {}
        if frames:
            for e in rec.evidence:
                low = e.feature.lower()
                for key, fr in frames.items():
                    lab = _FEATURE_UZ.get(key, key).lower().split(" (")[0]
                    if fr and (lab in low or low in lab) and "kadr:" not in e.detail:
                        e.detail = (e.detail + " " if e.detail else "") + \
                            f"(kadr: {', '.join(str(x) for x in fr[:6])})"
                        break
            # Qidiruv hech bir kadrda topmagan «dalil» — dalil emas
            absent = {k for k, fr in frames.items() if not fr}
            sv = features.get("_survey") or {}
            for key, d in (sv.get("found") or {}).items():
                if d["count"] == 0:
                    absent.add(key)
            keep = []
            for e in rec.evidence:
                low = e.feature.lower()
                bad = any((_FEATURE_UZ.get(k, k).lower().split(" (")[0] in low) for k in absent)
                if bad:
                    rec.notes.append(f"dalil «{e.feature}» olib tashlandi — qidiruvda hech bir kadrda topilmadi")
                else:
                    keep.append(e)
            rec.evidence = keep
    pct, why = _confidence_percent(features, adj, names)
    if rec.certainty == dxr.CERTAIN_DESCRIPTIVE:
        pct = min(pct, 40)          # tavsifiy nom — nozologiya emas
    if getattr(rec, "gestalt_bonus", 0):
        pct += rec.gestalt_bonus
        why += "; umumiy ko'rinish mos"
    # Kadr-kadr qidiruv: tanlangan nozologiyaning majburiy belgilari nechta kadrda
    sv = (features or {}).get("_survey") if isinstance(features, dict) else None
    ent_ = _dxc.find_entity(rec.name)
    if sv and ent_:
        found = sv.get("found") or {}
        ess = [k for k in ent_["essential"] if k in found]
        if ess:
            hits = sum(1 for k in ess if found[k]["count"] >= 1)
            multi = sum(1 for k in ess if found[k]["count"] >= 2)
            if hits == 0:
                pct = min(pct, 40)
                why += "; qidiruv: majburiy belgi hech bir kadrda yo'q"
            elif multi:
                pct += 6
                why += f"; qidiruv: {hits}/{len(ess)} majburiy belgi, {multi} tasi >=2 kadrda"
            else:
                why += f"; qidiruv: {hits}/{len(ess)} majburiy belgi (1 kadrda)"
        excl = [k for k in ent_["excluding"] if k in found and found[k]["count"] >= 2]
        if excl:
            pct = min(pct, 50)
            why += "; qidiruv: rad etuvchi belgi >=2 kadrda"
    if rec.confidence_cap:
        pct = min(pct, rec.confidence_cap)   # mezon qo'riqchisi qo'ygan shift
    # Uch mustaqil manba — umumiy ko'rinish, qaror va klinika — bir nomda bo'lsa,
    # ro'yxat shovqini ishonchni 65% dan pastga tushirmaydi (nomuvofiqlik bo'lmasa).
    # Uchinchi manba — klinika YOKI mezon jadvalining 1-o'rni
    _top1 = _dxc.rank_candidates(features, 1, str(features.get("_clinical_text") or ""),
                                 str(features.get("_referral_text") or "")) if isinstance(features, dict) else []
    table_backs = bool(_top1) and _same_entity(_top1[0]["name"], rec.name)
    if (rec.gestalt_agreement == "mos" and (clinic_backs_early or table_backs)
            and not rec.discordance and not dxr.looks_malignant(rec)):
        if pct < 65:
            why += "; umumiy ko'rinish + qaror + klinika kelishdi"
        pct = max(pct, 65)
        if rec.certainty == dxr.CERTAIN_PROVISIONAL and not rec.discordance:
            rec.certainty = dxr.CERTAIN_DEFINITE
    dxr.set_confidence(rec, pct, why)
    for note in rec.notes:
        log.info("%s: qo'riqchi — %s", ZIYRAKAI_DISPLAY_NAME, note)
    log.info("%s: ishonchlilik %s%% — %s", ZIYRAKAI_DISPLAY_NAME, rec.confidence, why)
    return rec, rec.render()


# ─── Ishonchlilik foizi ─────────────────────────────────────────────────────
# «Ishonch: past», «Malignite qo'yish huquqi: YO'Q», «DIQQAT — barqaror emas»
# — uchtasi bir narsani aytadi: dalil qanchalik kuchli. Shifokor uchun bu uzun
# va tushunarsiz. O'sha ma'lumotning hammasi bitta songa sig'adi.
#
# Foizni model o'ylab topmaydi (promptlarda foiz hamon TAQIQLANGAN) — u
# quyidagi O'LCHANGAN signallardan kodda yig'iladi:
#
#   mustaqil maydonlar bir nomga keldimi          0–40
#   ko'rikda topilgan morfologik belgi soni       0–25
#   kitob mezonlariga moslik (hakamlik)           0–26
#   namuna sifati                                 0–15
#
# Natija 5–95 oralig'ida. 100% yozilmaydi: bironta morfologik tashxis
# mutlaq emas, va bu raqam shuni ochiq ko'rsatib turishi kerak.

CONFIDENCE_MIN = 5
CONFIDENCE_MAX = 95
CONFIDENCE_LOW = 55       # shundan past — dalil bitta nomga yetmagan
_FEATURES_FOR_FULL = 10   # shuncha belgi topilsa, belgi bandi to'liq ball


def _confidence_percent(features=None, adj=None, names=None):
    """Hisobot ishonchliligi — foiz va qisqa sabab (jurnal uchun)."""
    score = 0.0
    why = []
    caps = []       # yig'indi qancha bo'lishidan qat'i nazar, shundan oshmaydi

    # 1) Mustaqil maydonlar bir xil nomga keldimi — eng og'ir signal
    names = [str(n).strip() for n in (names or []) if str(n or "").strip()]
    if len(names) >= 2:
        norm = [_normalize_dx_name(n) for n in names]
        agree = norm.count(max(set(norm), key=norm.count))
        score += 40.0 * agree / len(norm)
        why.append(f"maydonlar {agree}/{len(norm)}")
        # Ko'pchilik yig'ilmagan bo'lsa, boshqa bandlar qanchalik kuchli
        # bo'lmasin — bu tashxis hali bitta nomga kelmagan.
        if agree < max(2, (len(norm) + 1) // 2):
            caps.append(45)
        elif agree < len(norm):
            caps.append(80)
    else:
        # Tejamkor yo'lda mustaqil guruhlar yo'q. O'rniga: modelning tanlovi
        # mezon jadvalining deterministik tartibi bilan kelishadimi.
        top = _dxc.rank_candidates(
            features, 3, str(features.get("_clinical_text") or ""),
            str(features.get("_referral_text") or ""),
        ) if isinstance(features, dict) else []
        chosen = str((adj or {}).get("chosen") or "") if isinstance(adj, dict) else ""
        chosen = chosen or str((features or {}).get("_chosen_name") or "")
        if top and chosen:
            pos = next((i for i, r in enumerate(top) if _same_entity(chosen, r["name"])), None)
            # Bitta belgi bilan 1-o'ringa chiqqan nomzod to'liq ball olmasin:
            # ball tayanch belgilar soniga qarab (3 ta va undan ko'p — to'liq).
            weight = min(1.0, top[pos]["essential_hits"] / 3.0) if pos is not None else 0.0
            agreed = bool((features or {}).get("_gestalt_agrees"))
            if pos == 0:
                score += 40.0 * weight
                why.append(f"mezon jadvali: 1-o'rin ({top[0]['essential_hits']} tayanch)")
            elif pos is not None:
                # Umumiy ko'rinish va qaror bir nomga kelgan bo'lsa, jadvalda
                # 2–3-o'rin — mustaqil uchinchi manbaning qo'llab-quvvatlashi
                score += (32.0 if agreed else 24.0) * max(weight, 0.7 if agreed else 0.0)
                why.append(f"mezon jadvali: {pos + 1}-o'rin")
            else:
                score += 8.0
                why.append("mezon jadvalidan tashqari")
        else:
            score += 22.0  # o'lchanmagan — o'rtacha ball

    # 2) Ko'rikda haqiqatan ko'rilgan belgilar
    n = len(_true_features(features)) if isinstance(features, dict) else 0
    score += 25.0 * min(1.0, n / float(_FEATURES_FOR_FULL))
    why.append(f"{n} belgi")
    if n < MIN_FEATURES_FOR_ENTITY:
        # Nozologiya qo'yish uchun yetarli belgi ko'rilmagan
        caps.append(40)

    # 3) Kitob mezonlariga moslik
    if isinstance(adj, dict):
        chosen = str(adj.get("chosen") or "").strip()
        fit = ""
        for c in adj.get("candidates") or []:
            if chosen and _same_entity(chosen, str(c.get("name") or "")):
                fit = str(c.get("fit") or "").strip().lower()
                break
        score += {"mos": 20.0, "qisman": 9.0}.get(fit, 5.0)
        if fit.startswith("mos emas"):
            caps.append(35)
        conf = str(adj.get("confidence") or "").strip().lower()
        score += {"yuqori": 6.0, "o'rta": 3.0, "orta": 3.0}.get(conf, 0.0)
        if fit:
            why.append(f"mezon {fit}")
    else:
        score += 7.0

    # 4) Namuna sifati — yomon kesmadan yaxshi tashxis chiqmaydi
    quality = str((features or {}).get("sample_quality") or "").lower()
    quality = quality.replace("‘", "'").replace("’", "'")
    if quality.startswith("yaxshi"):
        score += 15.0
    elif quality.startswith(("o'rta", "orta")):
        score += 8.0
    else:
        score += 3.0
    if quality:
        why.append(f"namuna {quality}")

    if caps:
        score = min(score, float(min(caps)))
    pct = int(round(max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, score))))
    return pct, "; ".join(why)


# TASHXIS bo'limining meta qatorlari — foiz kelgach bularning keragi qolmaydi
_DX_META_RE = re.compile(
    r"^\s*(organ\s*/?\s*qatlam|ishonch|ishonchlilik|malignite|daraja)\b", re.I
)
_INLINE_META_RE = re.compile(
    r"\s*\|\s*(?:ishonch|malignite[^|\n]*|daraja)[^|\n]*", re.I
)
_ORGAN_VALUE_RE = re.compile(r"organ\s*/?\s*qatlam\s*:\s*([^|\n]+)", re.I)
_GRADE_VALUE_RE = re.compile(r"daraja\s*:\s*([^|\n]+)", re.I)
_EMPTY_GRADE = ("qo'llanilmaydi", "qollanilmaydi", "yo'q", "yoq", "noaniq", "-", "—")


def _apply_confidence(text, pct, caution=""):
    """TASHXIS bo'limini ikki qatorga keltirish: nom va ishonchlilik foizi."""
    if not text:
        return text

    dx = _histology_dx_block(text) or ""
    organ = ""
    m = _ORGAN_VALUE_RE.search(dx)
    if m:
        organ = " ".join(m.group(1).split()).strip(" .|;")
    grade = ""
    m = _GRADE_VALUE_RE.search(dx)
    if m:
        g = " ".join(m.group(1).split()).strip(" .|;")
        if g.lower().replace("‘", "'").replace("’", "'") not in _EMPTY_GRADE:
            grade = g

    row = f"Ishonchlilik: {pct}%"
    if grade:
        row += f"  ·  Daraja: {grade}"
    if organ:
        row += f"  ·  {organ}"

    lines = text.splitlines()
    start = end = -1
    for i, line in enumerate(lines):
        if start < 0:
            if re.match(r"^\s*#+\s*(?:aniq\s+)?tashxis\b", line, flags=re.I):
                start = i + 1
            continue
        if re.match(r"^\s*#+\s", line):
            end = i
            break
    if start < 0:
        return text
    if end < 0:
        end = len(lines)

    body, name_at = [], -1
    for line in lines[start:end]:
        t = line.strip()
        if not t:
            if body:            # bo'limning boshidagi bo'sh qatorlar kerak emas
                body.append("")
            continue
        if t.lower().startswith("diqqat") or _DX_META_RE.match(t):
            continue
        if name_at < 0:
            line = _INLINE_META_RE.sub("", line).rstrip(" |;")
            name_at = len(body)
        body.append(line)

    at = name_at + 1 if name_at >= 0 else 0
    if caution:
        body.insert(at, caution)
    body.insert(at, row)
    while body and not body[-1].strip():
        body.pop()
    body.append("")
    return "\n".join(lines[:start] + body + lines[end:])


def _finalize_confidence(text, features, adj, names):
    """Foizni hisoblab, TASHXIS bo'limini yakuniy ko'rinishga keltirish.

    Yagona qoladigan ogohlantirish — xavfli o'sma nomi past ishonchlilik
    bilan qo'yilgan holat. Bu qator emas, xavfsizlik chizig'i: rakni
    tasdiqlanmagan holda davolashga o'tib ketmaslik uchun.
    """
    pct, why = _confidence_percent(features, adj, names)
    caution = ""
    name = _dx_name_only(_histology_dx_block(text)) or ""
    if pct < CONFIDENCE_LOW and _MALIGN_LEAD_RE.search(name):
        caution = (
            "Xavfli o'sma shu hisobot bilan TASDIQLANMAYDI — davolash qarori "
            "patolog ko'rigi va IHC dan keyin qabul qilinadi."
        )
    log.info("%s: ishonchlilik %s%% — %s", ZIYRAKAI_DISPLAY_NAME, pct, why)
    return _apply_confidence(text, pct, caution)


_FINAL_PREFIX = "YAKUNIY XULOSA: "


def _mark_final_conclusion(text):
    """TASHXIS bo'limining nom qatorini aniq belgilash.

    Shifokor hisobotni ochganda birinchi ko'radigan narsa xulosa bo'lishi
    kerak — ogohlantirish, bemor ma'lumoti yoki muqobillar ro'yxati emas.
    """
    if not text:
        return text
    lines = text.splitlines()
    out, state, done = [], "before", False
    for line in lines:
        if state == "before" and re.match(r"^\s*#+\s*(?:aniq\s+)?tashxis\b", line, flags=re.I):
            out.append(line)
            state = "name"
            continue
        if state == "name" and not done:
            t = line.strip()
            if not t:
                out.append(line)
                continue
            low = t.lower()
            if low.startswith(("diqqat", "yakuniy xulosa")) or low.startswith(
                ("organ", "ishonch", "malignite", "daraja", "bemor", "namuna")
            ):
                out.append(line)
                if low.startswith("yakuniy xulosa"):
                    done = True
                continue
            out.append(_FINAL_PREFIX + t)
            done = True
            continue
        out.append(line)
    return "\n".join(out)


def _strip_preamble(text):
    """Hisobotdan oldingi bo'sh gaplarni olib tashlash.

    Model ba'zan "Quyidagi ma'lumotlar asosida tahlil qilaman:" kabi kirish
    qatori bilan boshlaydi — bu hisobot emas, shifokorga keraksiz.
    """
    if not text:
        return text
    m = re.search(r"^\s*#+\s*\S", text, flags=re.M)
    if not m or m.start() == 0:
        return text.strip()
    head = text[: m.start()].strip()
    # Faqat qisqa kirish tashlanadi; uzun matn hisobotning o'zi bo'lishi mumkin
    if len(head) <= 300 and "####" not in head:
        return text[m.start():].strip()
    return text.strip()


def _strip_wrappers(text):
    """Model ba'zan «==== HISOBOT ====» chegara qatorlarini ham nusxalaydi."""
    if not text:
        return text
    out = _WRAPPER_LINE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# «atipiyasiz» kabi sifatlash tashxis sarlavhasida turib, FAKT dagi mitoz va
# pleomorfizm bilan ziddiyat hosil qilardi. Dalil yetarli emas deb yopilayotgan
# hisobotda atipiya haqida umuman da'vo bo'lmasligi kerak.
_ATYPIA_QUALIFIER_RE = re.compile(
    r"\s*[,;—-]?\s*(atipiyasiz|atipiya\s+yo'q|atipik\s+emas|atipiyali|atipiya\s+bor)\s*",
    re.I,
)


def _strip_atypia_claim(text):
    return _ATYPIA_QUALIFIER_RE.sub(" ", text or "").strip(" ,;—-")


def _report_section(text, key):
    """Hisobotning bitta bo'limi (sarlavhasiz)."""
    pat = _SEC_RE.get(key)
    if not pat or not text:
        return ""
    m = re.search(pat + r"[^\n]*\n(.*?)(?=\n#+\s|\Z)", text, flags=re.I | re.S)
    return (m.group(1) if m else "").strip()


_NO_EXTRA_TEST_RE = re.compile(
    r"qo'shimcha\s+tekshiruv\s+(?:shart|kerak|zarur)\s+emas|"
    r"qo'shimcha\s+(?:bo'yash|tekshiruv)ga?\s+hojat\s+yo'q|"
    r"ihc\s+(?:shart|kerak)\s+emas",
    re.I,
)
_TEST_STEP_RE = re.compile(
    r"\bihc\b|immunogisto|\bcd\d+\b|\bs100\b|sox10|melan|\bp63\b|\bpas\b|"
    r"qo'shimcha\s+kesma|chuqurroq\s+kesma|qayta\s+bo'yash|serial\s+kesma|"
    r"klinik\s+ma'lumot\s+so'ra",
    re.I,
)
# "Aniq tashxis uchun yetarli emas", "aniqlanmadi", "noaniq jarayon" — bular
# tashxis nomi emas; shifokorga foydasi yo'q.
_NO_NAME_RE = re.compile(
    r"yetarli\s+emas|aniqlanmadi|aniqlab\s+bo'lmadi|noaniq\s+jarayon|"
    r"tashxis\s+qo'yib\s+bo'lmaydi|nomsiz",
    re.I,
)
_NO_ATYPIA_RE = re.compile(r"atipiyasiz|atipiya\s+yo'q|atipik\s+emas|atipiya:\s*yo'q", re.I)
# "pleomorfizm o'rta" ham, "o'rta pleomorfizm" ham uchraydi
_PLEO_STRONG_RE = re.compile(
    r"pleomorfizm[^\n]{0,24}?(o'rta|kuchli|yuqori|belgili)"
    r"|(o'rta|kuchli|yuqori|belgili)[^\n]{0,16}?pleomorfizm",
    re.I,
)
_MITOSIS_RE = re.compile(r"mitoz[^\n]*?(\d+)\s*(?:[-–—]\s*(\d+))?\s*/\s*10\s*hpf", re.I)
_MARGIN_MEASURED_RE = re.compile(
    r"chekka[^\n]*?(tegib\s+turadi|toza|erkin|musbat|manfiy|\d+\s*mm)", re.I
)
_MARGIN_UNASSESSED_RE = re.compile(r"chekka[^\n]*?(baholanmadi|baholanmagan|baholab bo'lmaydi)", re.I)
_WORD_RE = re.compile(r"[a-zа-яo'‘’\w]{4,}", re.I)

# Asosda "aniqlik" belgisi: son, o'lchov birligi yoki morfologik joylashuv
_SPECIFIC_RE = re.compile(
    r"\d|\bmm\b|\u00b5m|\bmkm\b|\bhpf\b|%|qatlam|chekka|yuzasi|chuqur|"
    r"sath|zona|tizma|dasta|uya|papillyar|retikulyar|bazal|shox|donador|"
    r"tikan|grenz|perivaskulyar|lentasimon|diffuz|fokal|o'choq",
    re.I,
)


def _tautology_lines(section):
    """«X — KO'RINDI: X mavjud» qatorlari — yangi ma'lumot bermaydi."""
    bad = []
    for line in (section or "").splitlines():
        line = line.strip(" -•\t")
        if not line or ":" not in line:
            continue
        head, _, tail = line.partition(":")
        name = re.split(r"—|–|-{1,2}\s", head)[0]
        name_words = {w.lower() for w in _WORD_RE.findall(name)}
        tail_words = {w.lower() for w in _WORD_RE.findall(tail)}
        if not name_words or not tail_words:
            continue
        extra = tail_words - name_words - {
            "mavjud", "bor", "ko'rinadi", "kuzatiladi", "aniqlandi", "korinadi",
            "kuzatilmadi", "topilmadi", "hujayra", "hujayralar", "belgi",
            "belgilar", "tasvirlarda", "tasvirda",
        }
        # Asos qatori aniq bo'lishi kerak: yo son/o'lchov, yo joylashuv atamasi,
        # yo kamida 4 ta yangi mazmunli so'z. Aks holda u belgi nomining
        # boshqacha aytilishi — shifokorga hech narsa bermaydi.
        if len(extra) < 4 and not _SPECIFIC_RE.search(tail):
            bad.append(line[:110])
    return bad


# "<nom> emas, chunki ..." — muqobilni rad etish qatori
_DENY_LINE_RE = re.compile(r"^\s*[-•\u2022]?\s*(.{3,60}?)\s+emas\b[,:]?\s*(.*)$", re.I)


def _dx_name_only(dx_block):
    """Tashxis bo'limidan faqat kasallik nomini olish."""
    for line in (dx_block or "").splitlines():
        t = line.strip(" -•\t")
        if not t:
            continue
        low = t.lower()
        if low.startswith("diqqat") or low.startswith("taxminiy") or ":" in t.split(" ")[0]:
            continue
        if low.startswith(("organ/", "organ ", "ishonch", "malignite", "daraja")):
            continue
        # "Psoriaz — benign — taxminiy" → "Psoriaz"
        name = re.split(r"\s+[—–-]\s+", t)[0]
        return name.strip(" .")
    return ""


def _deny_lines(section):
    """[(rad etilgan nom, sabab)] — «X emas, chunki Y» qatorlari."""
    out = []
    for line in (section or "").splitlines():
        m = _DENY_LINE_RE.match(line.strip())
        if not m:
            continue
        name = m.group(1).strip(" .,:—–-")
        reason = re.sub(r"^\s*chunki\s*", "", m.group(2).strip(), flags=re.I).strip(" .")
        if name and len(name) <= 60:
            out.append((name, reason))
    return out


def _same_entity(a, b):
    """Ikki nom bir kasallikni bildiradimi (qisqartma va variant hisobga olinadi)."""
    na, nb = _normalize_dx_name(a), _normalize_dx_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    sa, sb = set(na.split()), set(nb.split())
    if not sa or not sb:
        return False
    # bittasi ikkinchisining ichida (masalan "psoriaz" va "psoriaz vulgaris")
    return sa <= sb or sb <= sa


def _find_contradictions(text, features=None):
    """Hisobotdagi aniq ichki zidliklar ro'yxati (bo'sh = toza)."""
    if not text:
        return []
    tashxis = _report_section(text, "tashxis")
    nega = _report_section(text, "nega")
    fakt = _report_section(text, "fakt")
    out = []

    # Tashxis o'rnida nom bo'lishi shart — "aniqlanmadi" javob emas.
    # Faqat NOM qatoriga qaraladi: barqarorlik ogohlantirishida ham
    # "yetarli emas" iborasi bor va u yolg'on signal berardi.
    if _NO_NAME_RE.search(_dx_name_only(tashxis) or tashxis[:0]):
        out.append(
            "TASHXIS bo'limida kasallik nomi yo'q — «yetarli emas / aniqlanmadi» "
            "o'rniga eng ehtimolli nomni yozib, «Ishonch: past» qo'ying."
        )

    if len([l for l in nega.splitlines() if l.strip()]) < 4:
        out.append(
            "NEGA SHU TASHXIS bo'limi juda qisqa — kamida 4 ta mezon "
            "ko'rilgan dalil bilan yozilishi kerak."
        )

    no_atypia = _NO_ATYPIA_RE.search(tashxis) or _NO_ATYPIA_RE.search(nega)
    if no_atypia:
        if _PLEO_STRONG_RE.search(fakt):
            out.append(
                "Tashxisda «atipiyasiz» deyilgan, FAKT da esa pleomorfizm "
                "o'rta/kuchli deb yozilgan."
            )
        m = _MITOSIS_RE.search(fakt)
        if m:
            hi = int(m.group(2) or m.group(1) or 0)
            if hi > 2:
                out.append(
                    f"Tashxisda «atipiyasiz» deyilgan, FAKT da esa mitoz "
                    f"{m.group(0).strip()} — bu son atipiyasiz tavsifga mos kelmaydi."
                )

    # Hisobot o'z tashxisini rad etmasin: "Psoriaz" deb qo'yib, pastda
    # "Psoriaz emas" deb yozish — o'qigan odam uchun mantiqsiz.
    dx_name = _dx_name_only(tashxis)
    denies = _deny_lines(nega)
    if dx_name:
        for name, _reason in denies:
            if _same_entity(dx_name, name):
                out.append(
                    f"TASHXIS «{dx_name}» deb qo'yilgan, lekin asosda «{name} emas» "
                    "deyilgan — bittasini tanlang: yo nomni o'zgartiring, yo shu "
                    "rad etish qatorini olib tashlang."
                )
                break

    # Har xil kasallik bir xil sabab bilan rad etilmasin
    seen_reasons = {}
    for name, reason in denies:
        key = _normalize_dx_name(reason)
        if not key or len(key) < 8:
            continue
        if key in seen_reasons and not _same_entity(seen_reasons[key], name):
            out.append(
                f"«{seen_reasons[key]}» va «{name}» bir xil sabab bilan rad etilgan "
                "— har bir muqobil o'ziga xos ajratuvchi belgi bilan rad etilsin."
            )
            break
        seen_reasons[key] = name

    taut = _tautology_lines(nega)
    if taut:
        out.append(
            "NEGA SHU TASHXIS bo'limida belgi nomi takrorlangan, yangi ma'lumot "
            "yo'q: " + "; ".join(taut[:3])
        )

    return out


_COHERENCE_SYSTEM = (
    "Siz — patomorfologiya kafedrasi mudirisiz. Sizga imzo qo'yilishi kerak "
    "bo'lgan hisobot va undagi ANIQ ichki ziddiyatlar ro'yxati beriladi. "
    "Vazifangiz: hisobotni shu ziddiyatlarsiz qayta yozish.\n"
    "Qat'iy shartlar:\n"
    "— YANGI topilma o'ylab topmang. Faqat hisobotdagi o'lchangan faktlarga "
    "tayaning; zid bo'lgan joyda FAKT bo'limidagi o'lchov ustun turadi.\n"
    "— TASHXIS qatoridagi sifatlashni ham tuzating: agar FAKT da pleomorfizm "
    "yoki mitoz bo'lsa, sarlavhada «atipiyasiz» deb yozilmasin (sifatlashni "
    "olib tashlang yoki o'lchovga moslang). Tashxis NOMINI o'zgartirmang.\n"
    "— Belgi nomini takrorlamang: har bir asos qayerda, qanday, qancha "
    "ekanini aytsin.\n"
    "— TASHXIS o'rnida albatta kasallik NOMI tursin; «yetarli emas» yozmang, "
    "dalil kam bo'lsa nomni qoldirib «Ishonch: past» qiling.\n"
    "— O'sha 3 bo'lim (TASHXIS, NEGA SHU TASHXIS, FAKT), o'zbek tili, "
    "2500–4500 belgi.\n"
    "— Faqat yakuniy hisobotni qaytaring, izohsiz."
)


def _coherence_pass(text, kwargs, features=None, lab_type="histology"):
    """Ziddiyat topilsa hisobotni bir marta qayta yozdirish.

    Model javob bermasa yoki natija yomon bo'lsa — asl matn qoladi (hisobot
    yo'qolmaydi), lekin jurnalga yoziladi.
    """
    if lab_type != "histology" or not text:
        return text
    if (os.environ.get("HISTOLOGY_COHERENCE") or "1").strip().lower() in ("0", "false", "no", "off"):
        return text
    issues = _find_contradictions(text, features)
    if not issues:
        return text
    log.warning(
        "%s: hisobotda %s ta ichki ziddiyat — qayta yozilmoqda: %s",
        ZIYRAKAI_DISPLAY_NAME, len(issues), " | ".join(i[:70] for i in issues),
    )
    # Ko'rik natijasi berilsa, takroriy asosni haqiqiy o'lchov bilan
    # almashtirish mumkin bo'ladi ("mavjud" o'rniga qayerda, qanday, qancha).
    feats = _features_prompt_block(features) if features else ""
    user = (
        ((feats + "\n\n") if feats else "")
        + "==== HISOBOT ====\n" + text[:9000] + "\n==== HISOBOT TUGADI ====\n\n"
        "TOPILGAN ZIDDIYATLAR:\n"
        + "\n".join(f"{i}) {t}" for i, t in enumerate(issues, 1))
        + "\n\nShu ziddiyatlarni yo'qotib, hisobotni to'liq qayta yozing. "
        "Takroriy asosni yuqoridagi ko'rik natijasidagi o'lchov bilan "
        "almashtiring: qayerda, qanday joylashgan, qancha."
    )
    fix_kwargs = dict(kwargs or {})
    fix_kwargs["temperature"] = 0.0
    try:
        out = _chat_complete(
            [
                {"role": "system", "content": _COHERENCE_SYSTEM},
                {"role": "user", "content": user},
            ],
            fix_kwargs,
        )
    except Exception as e:
        log.warning("%s: mantiq tuzatuvi xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return text
    out = _strip_wrappers(out)
    if not _usable(out, MIN_REPORT_CHARS) or _looks_like_refusal(out):
        log.warning("%s: mantiq tuzatuvi yaroqsiz — asl hisobot saqlandi", ZIYRAKAI_DISPLAY_NAME)
        return text
    left = _find_contradictions(out, features)
    if len(left) >= len(issues):
        log.warning(
            "%s: qayta yozish yaxshilamadi (%s → %s) — asl hisobot saqlandi",
            ZIYRAKAI_DISPLAY_NAME, len(issues), len(left),
        )
        return text
    log.info(
        "%s: ziddiyat %s → %s ga tushdi", ZIYRAKAI_DISPLAY_NAME, len(issues), len(left)
    )
    return out


# ─── Tashxis barqarorligi ────────────────────────────────────────────────────
# Bir xil kesmada dastur uch marta uch xil nom bergani kuzatildi (seboreik
# keratoz / trichoepithelioma / dermatofibroma). Bu — dalil kamligi belgisi:
# tizim bo'shliqni ishonarli ko'ringan nom bilan to'ldiryapti. Buni yashirish
# xavfli, shuning uchun o'lchanadi va hisobotda ochiq aytiladi.
#
# Usul: har bir mustaqil ko'rik guruhi bo'yicha ALOHIDA nom so'raladi (arzon
# matnli chaqiruv), so'ng nomlar solishtiriladi. Kelishmovchilik bo'lsa —
# hisobotga ogohlantirish qatori qo'shiladi.

_DX_NAME_SYSTEM = (
    "You are a dermatopathologist. Given ONLY a list of observed morphological "
    "features, name the single most likely histopathological entity. "
    "Answer with the entity name alone — no explanation, no punctuation, "
    "max 6 words. If the features fit no entity, answer exactly: NOANIQ."
)


def _normalize_dx_name(name):
    t = re.sub(r"[^a-zа-яё\s]", " ", str(name or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    for w in ("teri", "kozhi", "skin", "benign", "malign", "variant", "tur", "klassik"):
        t = t.replace(w, " ")
    return re.sub(r"\s+", " ", t).strip()


def _stability_enabled():
    v = (os.environ.get("HISTOLOGY_STABILITY_CHECK") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _group_dx_names(features, kwargs=None):
    """Har mustaqil ko'rik guruhi bo'yicha alohida tashxis nomi.

    Bu nomlar ikki joyda ishlatiladi: barqarorlikni o'lchash va differensial
    hakamlikka nomzod berish.
    """
    if not isinstance(features, dict):
        return []
    cached = features.get("_group_names")
    if cached is not None:
        return list(cached)
    groups = features.get("_groups") or []
    names = []
    for g in groups:
        block = _features_prompt_block(g)
        if not block:
            continue
        try:
            out = _chat_complete(
                [
                    {"role": "system", "content": _DX_NAME_SYSTEM},
                    {"role": "user", "content": block[:4000]},
                ],
                {"max_tokens": 40, "temperature": 0.0},
                model=_router_model(),
            )
        except Exception as e:
            log.warning("%s: nomzod chaqiruvi xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
            continue
        name = (out or "").strip().splitlines()[0][:60] if out else ""
        if name and not _looks_like_refusal(name) and name.upper() != "NOANIQ":
            names.append(name)
    features["_group_names"] = names
    return names


def _dx_stability(features, kwargs=None):
    """[(nom, ...)] → (barqarormi, nomlar ro'yxati). Guruh bo'lmasa (None, [])."""
    if not _stability_enabled() or not isinstance(features, dict):
        return None, []
    groups = features.get("_groups") or []
    if len(groups) < 2:
        return None, []
    names = _group_dx_names(features, kwargs)
    if len(names) < 2:
        return None, names
    norm = [_normalize_dx_name(n) for n in names]
    top = max(set(norm), key=norm.count)
    agree = norm.count(top)
    stable = agree >= max(2, (len(norm) + 1) // 2)
    log.info(
        "%s: barqarorlik %s/%s — %s",
        ZIYRAKAI_DISPLAY_NAME, agree, len(norm), " | ".join(names),
    )
    return stable, names


# ─── Differensial hakamlik ───────────────────────────────────────────────────
# Mustaqil maydonlar turli nom berganda tizim ulardan birini asossiz tanlardi.
# Patolog bunday qilmaydi: u har nomzodning MEZONINI oladi va ko'rgan belgilari
# bilan bandma-band solishtiradi, so'ng qaysi biri mos kelishini AYTADI.
# Shu qadam kitoblarni aynan qaror nuqtasida ishlatadi.

_ADJUDICATE_SYSTEM = (
    "Siz — dermatopatologiya bo'yicha konsultantsiz. Sizga bitta kesmadan "
    "olingan O'LCHANGAN belgilar ro'yxati va bir necha nomzod tashxis, "
    "hamda har nomzod uchun darslik mezonlari beriladi.\n"
    "Vazifa: har nomzodni mezonlari bo'yicha tekshirib, qaysi biri belgilarga "
    "eng mos kelishini aniqlash.\n"
    "Qat'iy shartlar:\n"
    "— Faqat berilgan belgilarga tayaning; yangi topilma o'ylab topmang.\n"
    "— Nomzodning ASOSIY (majburiy) mezoni belgilar ro'yxatida YO'Q bo'lsa, "
    "uni tanlamang: bunday tashxis dalilga zid bo'ladi.\n"
    "— Har nomzod uchun uning ASOSIY mezonlarini sanang va har birini "
    "BOR / YO'Q / BAHOLANMAGAN deb belgilang.\n"
    "— Hal qiluvchi belgini alohida ayting.\n"
    "— Agar hech bir nomzod mos kelmasa yoki ikkitasi teng bo'lsa, buni "
    "ochiq ayting.\n"
    "Javob QAT'IY JSON: {\"chosen\": \"nom yoki \", "
    "\"confidence\": \"past|o'rta|yuqori\", "
    "\"deciding\": \"hal qiluvchi belgi, bir jumla\", "
    "\"candidates\": [{\"name\": \"...\", \"fit\": \"mos|qisman|mos emas\", "
    "\"criteria\": [{\"c\": \"mezon\", \"v\": \"bor|yo'q|baholanmagan\"}], "
    "\"why\": \"bir jumla\"}]}"
)


def _adjudicate_enabled():
    v = (os.environ.get("HISTOLOGY_ADJUDICATE") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _candidate_criteria(name, organ_lock=None, patient_context=None):
    """Nomzod uchun kitoblardan qisqa mezon parchasi."""
    try:
        from .histology_kb import retrieve
    except Exception:
        return ""
    queries = [
        f"{name} histopathology diagnostic criteria essential features",
        f"{name} патоморфология критерии диагноза гистология",
    ]
    try:
        hits = retrieve(queries, k=3, organ=(organ_lock or {}).get("organ") or "teri")
    except Exception as e:
        log.warning("%s: mezon qidiruvi xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return ""
    if not hits:
        return ""
    parts = []
    for h in hits[:3]:
        body = re.sub(r"\s+", " ", (h.get("text") or "")).strip()
        if body:
            parts.append(body[:420])
    return "\n".join(parts)


def _candidate_blocked(name, features):
    """Nomzodning barcha asosiy belgilari ko'rikda yo'qmi.

    Hakam ilgari shunday nomzodni ham tanlashi mumkin edi, keyin tekshirgich
    uni rad etardi va hisobot tavsifiy nomga tushib qolardi. Bunday nomzodni
    umuman taklif qilmaslik to'g'riroq: hakam qolgan haqiqiy variantlarni
    solishtirsin.
    """
    if not name or not isinstance(features, dict):
        return ""
    ev = _dxc.check_name(name, features)
    if ev is not None:
        if ev["essential_hits"] == 0 and ev["essential_total"]:
            return ", ".join(_dxc.feature_label(s) for s in ev["essential_absent"][:3])
        return ""
    low = str(name).lower()
    for key, required in _DX_REQUIRED_FEATURES.items():
        if key not in low:
            continue
        missing = [k for k in required if not _feature_true(features, k)]
        if len(missing) == len(required):
            return ", ".join(_FEATURE_UZ.get(m, m) for m in missing)
    return ""


def _adjudicate_diagnosis(features, candidates, kwargs, organ_lock=None, patient_context=None):
    """Nomzodlarni kitob mezonlari bo'yicha tekshirib, birini tanlash."""
    if not _adjudicate_enabled() or not isinstance(features, dict):
        return None
    names, blocked = [], []
    for c in candidates or []:
        c = str(c or "").strip()
        if not c or any(_same_entity(c, x) for x in names):
            continue
        why = _candidate_blocked(c, features)
        if why:
            blocked.append((c, why))
            continue
        names.append(c)
    if blocked:
        log.info(
            "%s: hakamlikdan chiqarildi — %s",
            ZIYRAKAI_DISPLAY_NAME,
            "; ".join(f"{n} ({w})" for n, w in blocked[:3]),
        )
    if len(names) < 2:
        return None
    names = names[:4]

    feats = _features_prompt_block(features)
    if not feats:
        return None

    blocks = []
    for n in names:
        crit = _candidate_criteria(n, organ_lock, patient_context)
        blocks.append(
            f"== NOMZOD: {n} ==\n"
            + (f"Darslik mezonlari:\n{crit}\n" if crit else "Darslik parchasi topilmadi.\n")
        )

    user = (
        feats
        + "\n\n==== NOMZODLAR VA MEZONLAR ====\n"
        + "\n".join(blocks)
        + "\n==== TUGADI ====\n"
        "Har nomzodni mezonlari bo'yicha tekshiring va JSON qaytaring."
    )
    try:
        raw = _chat_complete(
            [
                {"role": "system", "content": _ADJUDICATE_SYSTEM},
                {"role": "user", "content": user},
            ],
            {"max_tokens": 1600, "temperature": 0.0},
        )
    except Exception as e:
        log.warning("%s: hakamlik xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return None
    data = _parse_observation(raw)
    if not isinstance(data, dict) or not data.get("candidates"):
        log.warning("%s: hakamlik javobi o'qilmadi", ZIYRAKAI_DISPLAY_NAME)
        return None
    log.info(
        "%s: hakamlik — tanlandi=%r ishonch=%s (%s nomzod)",
        ZIYRAKAI_DISPLAY_NAME,
        str(data.get("chosen"))[:40],
        data.get("confidence"),
        len(data.get("candidates") or []),
    )
    return data


def _adjudication_block(adj):
    """Hakamlik natijasini hisobot promptiga qo'shiladigan matn."""
    if not isinstance(adj, dict):
        return ""
    lines = ["### DIFFERENSIAL TEKSHIRUV (mezonlar bo'yicha)"]
    for c in (adj.get("candidates") or [])[:4]:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        marks = []
        for it in (c.get("criteria") or [])[:6]:
            cc = str(it.get("c") or "").strip()
            vv = str(it.get("v") or "").strip()
            if cc:
                marks.append(f"{cc}: {vv or 'noaniq'}")
        lines.append(
            f"- {name} — {c.get('fit') or 'noaniq'}"
            + (f" ({'; '.join(marks)})" if marks else "")
            + (f". {str(c.get('why') or '').strip()}" if c.get("why") else "")
        )
    chosen = str(adj.get("chosen") or "").strip()
    if chosen:
        lines.append(
            f"Mezonlar bo'yicha eng mos: {chosen} "
            f"(ishonch: {adj.get('confidence') or 'noaniq'})."
        )
    if adj.get("deciding"):
        lines.append(f"Hal qiluvchi belgi: {str(adj['deciding']).strip()[:200]}")
    lines.append(
        "Shu tekshiruvni hisobotda ishlat: TASHXIS nomi mezonlarga mos bo'lsin, "
        "asosda hal qiluvchi belgini ayt. Mos kelmasa — nima uchun, ayt."
    )
    return "\n".join(lines) + "\n"


def _apply_evidence_rules(text, features, lab_type="histology"):
    """Ishonch va tashxis darajasini ko'rikdagi dalil bilan moslashtirish."""
    if lab_type != "histology" or not text:
        return text
    if not isinstance(features, dict):
        return text
    n, max_level = _evidence_level(features)
    quality = str(features.get("sample_quality") or "noaniq")
    text = _cap_confidence(text, max_level)
    low_dx = _histology_dx_block(text).lower()
    already = "taxminiy" in low_dx
    if n < MIN_FEATURES_FOR_ENTITY and not already:
        log.warning(
            "%s: dalil kam (%s ta belgi, sifat=%s) — tashxis taxminiy deb belgilanadi",
            ZIYRAKAI_DISPLAY_NAME, n, quality,
        )
        return _mark_provisional(
            text,
            features,
            f"tasvirdan {n} ta ishonchli morfologik belgi olindi "
            f"(namuna sifati: {quality}). Tasdiqlash uchun 10× umumiy ko'rinish "
            "va 40× hujayra tafsiloti aniq fokusda kerak.",
        )
    return text


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
CHIQISH (qat'iy): faqat 3 bo'lim, jami 2500–4500 belgi.
#### TASHXIS
#### NEGA SHU TASHXIS
#### FAKT (o'lchangan morfologiya)
Boshqa bo'lim (differensial ro'yxati, tasdiqlash rejasi, baholanmagan ro'yxati)
ALOHIDA SARLAVHA bilan YOZILMAYDI.
Yulduzcha ** yo'q. Jadval yo'q. Ehtimollik foizi yo'q. Boshqa sarlavha yo'q.
Har qator ma'lumot tashisin: son, daraja yoki aniq morfologik atama bo'lsin.

ASOSIY QOIDA — TASHXIS NOMI ALBATTA BO'LSIN:
Birinchi qatorda kasallikning ANIQ NOMI turadi (masalan «Verruca vulgaris»,
«Dermatofibroma», «Psoriaz», «Bazal hujayrali karsinoma, nodulyar tur»).
«Aniq tashxis uchun yetarli emas», «aniqlanmadi», «noaniq jarayon» kabi
javob TASHXIS o'rniga YOZILMAYDI. Dalil kam bo'lsa — eng ehtimolli nomni
yozing va shu qatordagi «Ishonch:» ni past/o'rta qilib qo'ying. Ya'ni nom
har doim bor, ishonch darajasi esa dalilga qarab o'zgaradi.
Faqat bitta istisno: kadrda to'qima umuman bo'lmasa (bo'sh shisha, artefakt).

TASHXIS bo'limi (3–5 qator) — SHIFOKOR BIRINCHI O'QIYDIGAN JOY:
1-qator — «YAKUNIY XULOSA: <kasallik nomi>» (va bo'lsa varianti/turi).
   Faqat BITTA nom. Ro'yxat, raqamlangan variantlar («1) … 2) … 3) …»),
   «ishchi taassurot», bemor ismi/yoshi/namuna raqami — bu bo'limga
   YOZILMAYDI. Bemor ma'lumotlari kartada allaqachon bor.
2-qator — Organ/qatlam: … | Ishonch: past/o'rta/yuqori | Malignite qo'yish huquqi: BOR/YO'Q
3-qator — bir jumlada: bu qanday jarayon (xavfsiz/chegaraviy/xavfli) va nima
qilish kerakligi (kuzatuv, kesib olish, IHC bilan tasdiqlash).
Muqobil variantlar kerak bo'lsa, ular «NEGA SHU TASHXIS» bo'limida
rad etiladi — TASHXIS bo'limida emas.

NEGA SHU TASHXIS bo'limi — HISOBOTNING ASOSIY QISMI (6–10 qator, batafsil):
— Har qator bitta mezon: «<mezon nomi> — <QAYERDA, QANDAY, QANCHA ko'rindi>».
  Noto'g'ri: «Psoriaziform giperplaziya — KO'RINDI: akantoz va giperkeratoz mavjud».
  To'g'ri: «Psoriaziform giperplaziya — epidermis bir tekis qalinlashgan, rete
  tizmalari cho'zilgan va uchlari yo'g'onlashgan, sopralapillyar plastinka
  yupqalashgan, shox qatlam 3–4 barobar qalin».
— Kamida 4 ta mezon KO'RILGAN dalil bilan bo'lsin.
— Oxirgi 2–3 qatorda muqobillar shu yerda rad etilsin, alohida sarlavhasiz:
  «Bunga o'xshash <muqobil> emas, chunki <qaysi KO'RILGAN belgi mos kelmayapti>».
  Eng xavfli muqobil (karsinoma, melanoma, sarkoma) birinchi rad etilsin.
— Kerak bo'lsa oxirida bitta qator: «Tasdiqlash uchun: <IHC/bo'yoq/kesma>» —
  faqat tashxisni hal qiladigani, alohida sarlavhasiz.

MANTIQ QOIDALARI — hisobot o'z ichida zid bo'lmasin:
1) TAKROR YO'Q: mezon nomini izohda qaytarma, yangi ma'lumot ber.
2) ATIPIYA izchil bo'lsin. «Atipiyasiz» desang, FAKT da pleomorfizm
   «o'rta/kuchli» yoki mitoz 2/10HPF dan ko'p bo'lmasin va aksincha.
3) Malignite qo'yish huquqi YO'Q bo'lsa, tashxis nomi xavfli o'sma
   bo'lmasin — xavfsiz yoki chegaraviy nom tanlang.
4) Davolash rejasi, dori, profilaktika, kuzatuv jadvali YOZILMAYDI.
"""


def _append_output_format(prompt, lab_type=None):
    return (prompt or "").rstrip() + "\n\n" + OUTPUT_FORMAT_HISTOLOGY_UZ

# ─── Klinik rasm va yo'llanmadagi tashxis ────────────────────────────────────
# Patolog kesmani bo'sh joyda o'qimaydi: u yo'llanmani va bemorning terisini
# ko'rgan holda o'qiydi. Ilgari dasturga faqat kesma tushardi va klinik kontekst
# bir qatorlik "klinik izoh" bo'lib qolib ketardi.

_CLINICAL_LOOK_SYSTEM = (
    "You are a dermatologist describing what a skin lesion LOOKS LIKE on the "
    "patient — not through a microscope. Report only what is visible: primary "
    "lesion type (macule, papule, plaque, nodule, vesicle, pustule, ulcer), "
    "colour, surface (scale, crust, erosion), border, size if judgeable, "
    "number and distribution, and the body site. "
    "Answer in Uzbek, 3-6 short sentences. NEVER name a disease or diagnosis."
)


def _atlas_reference(names, detail="low"):
    """Tashxis nomlari uchun klinika atlasidan ma'lumotnoma rasm va izoh.

    Matn mezoni "periferik palisad" deydi — uni KO'RISH boshqa narsa.
    Shifokor kesmani atlasdagi rasm bilan solishtiradi; dastur ham shuni
    qila olishi kerak. Rasmlar kitob papkalaridagi kasallik yorlig'i bo'yicha
    tanlanadi va FAQAT hisobot chaqiruviga qo'shiladi — morfologik ko'rik
    faqat bemor kesmasini ko'rishi kerak.
    """
    if (os.environ.get("HISTOLOGY_ATLAS") or "1").strip().lower() in ("0", "false", "no", "off"):
        return "", []
    clean = [str(n).strip() for n in (names or []) if str(n or "").strip()]
    if not clean:
        return "", []
    try:
        from .atlas_images import find_reference_images, image_parts, reference_block
    except Exception as e:
        log.warning("%s: atlas moduli yuklanmadi: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return "", []
    try:
        refs = find_reference_images(clean)
        if not refs:
            return "", []
        return reference_block(refs), image_parts(refs, detail=detail)
    except Exception as e:
        log.warning("%s: atlas xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return "", []


def _dx_terms_for_atlas(patient_context=None, draft=None):
    """Atlasda qidiriladigan nomlar: klinik gipoteza + qoralamadagi tashxis."""
    out = []
    p = _normalize_patient_context(patient_context)
    picked = (p.get("clinical_dx") or "").strip()
    if picked:
        out.extend([x.strip() for x in re.split(r"\||;|,", picked) if x.strip()][:3])
    if draft:
        name = _dx_name_only(_histology_dx_block(draft))
        if name and len(name) > 3:
            out.append(name)
    return out[:4]


def _clinical_appearance(clinical_parts, patient_context=None):
    """Tanadagi rasmlardan klinik ko'rinish tavsifi (tashxis nomisiz)."""
    if not clinical_parts:
        return ""
    p = _normalize_patient_context(patient_context)
    site = (p.get("specimen_site") or "").strip() or "—"
    picked = _spread_pick(list(clinical_parts), 4)
    user = (
        f"Namuna joyi: {site}. {len(clinical_parts)} ta klinik rasm. "
        "Toshmaning ko'rinishini tasvirla — kasallik nomini YOZMA."
    )
    try:
        out = _chat_complete(
            [
                {"role": "system", "content": _CLINICAL_LOOK_SYSTEM},
                {"role": "user", "content": _vision_user(user, picked)},
            ],
            {"max_tokens": 400, "temperature": 0.0},
            label="klinik surat",
        )
    except Exception as e:
        log.warning("%s: klinik ko'rinish xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return ""
    out = (out or "").strip()
    if not out or _looks_like_refusal(out) or len(out) < 60:
        log.warning("%s: klinik ko'rinish olinmadi (rad yoki bo'sh)", ZIYRAKAI_DISPLAY_NAME)
        return ""
    # Yo'llanma varaqasi yoki hujjat surati klinik rasm sifatida kelib qolsa,
    # javob toshma tavsifi bo'lmaydi — bunday matn promptga kiritilmaydi.
    if not re.search(
        r"toshma|papula|blyashka|dog'|yara|tugun|pufak|qichim|qizar|po'st|"
        r"tangacha|eroziya|qobiq|infiltrat|o'choq|teri|lezion|makula|eritema|"
        r"pigment|qizil|pushti|jigarrang|shish|chegara|rang|o'lcham|diametr|"
        r"yuza|qavat|yaltir|quruq|nam|zich|yumshoq",
        out,
        re.I,
    ):
        log.warning(
            "%s: klinik rasm toshma tavsifini bermadi — kontekstga qo'shilmadi",
            ZIYRAKAI_DISPLAY_NAME,
        )
        return ""
    log.info("%s: klinik ko'rinish olindi (%s belgi)", ZIYRAKAI_DISPLAY_NAME, len(out))
    return (
        "### KLINIK KO'RINISH (bemor tanasidagi rasm — kesma EMAS)\n"
        + out[:900]
        + "\nBu klinik kontekst: morfologik xulosani tasdiqlash yoki rad etishda "
        "hisobga ol. Uni gistologik belgi sifatida FAKT bo'limiga yozma.\n"
    )


def _referral_dx_block(patient_context=None):
    """Yo'llanmadagi klinik tashxis — gipoteza sifatida."""
    p = _normalize_patient_context(patient_context)
    picked = (p.get("clinical_dx") or "").strip()
    note = (p.get("clinical_note") or "").strip()
    if not picked and not note:
        return ""
    if picked:
        # Shifokor tanlagan yoki yozgan tashxis(lar) — aniqroq manba
        items = [x.strip() for x in re.split(r"\||;|,", picked) if x.strip()]
        head = "; ".join(items[:5])
    else:
        # "Псориаз? · Yo'llanma №26/2026; Shifokor: ..." — tashxis qismi boshida
        head = re.split(r"[·|;]| - ", note)[0].strip()
        if len(head) < 3:
            head = note[:120]
    return (
        "### KLINIK TASHXIS (gipoteza — tasdiq emas)\n"
        f"Yuboruvchi shifokor / laborant: «{head[:200]}»\n"
        "Buni GIPOTEZA deb ol va morfologiya bilan solishtir:\n"
        "— mos kelsa: qaysi KO'RINGAN belgilar uni tasdiqlayotganini aniq ayt;\n"
        "— mos kelmasa: nima uchun mos emasligini va kesmada nima "
        "ko'rinayotganini ayt.\n"
        "Gipotezani ko'r-ko'rona qabul qilma, lekin sababsiz ham rad etma. "
        "Klinik tashxis morfologiya bilan tasdiqlansa — bu ishonchni oshiradi.\n"
        "Bir nechta gipoteza berilgan bo'lsa, HAR BIRINI ko'rib chiq: qaysi biri "
        "morfologiyaga mos kelishini ayt, qolganini nima uchun rad "
        "etayotganingni KO'RILGAN belgi bilan asosla.\n"
        "MUHIM: agar morfologiya gipotezani TASDIQLAMASA, o'sha gipoteza nomini "
        "TASHXIS qatoriga YOZMA. Bunday holda ko'ringan patternni nomla "
        "(masalan «Akantotik-papillomatoz epidermal lezyon») va nega klinik "
        "gipoteza tasdiqlanmaganini asosda tushuntir. Hisobot o'zi qo'ygan "
        "tashxisni pastda rad etmasin.\n"
    )


def _full_analysis_prompt(base, microscope_prefix, lab_type=None, patient_context=None):
    """Bemor konteksti + yo'nalish protokoli."""
    merged = _merge_prompt_with_microscope(base, microscope_prefix)
    lock = _lab_lock_text(lab_type or "histology")
    patient = _patient_prompt_prefix(patient_context, lab_type or "histology")
    parts = [lock]
    if patient:
        parts.append(patient)
    referral = _referral_dx_block(patient_context)
    if referral:
        parts.append(referral)
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
        ("clinical_dx", 300),
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
        v = int(os.environ.get("OPENAI_IMAGE_MAX_PX", "1280"))
    except ValueError:
        v = 1280
    # 2048 px da har rasm ≈ 2000 token edi; 1280 px da ≈ 1100 — morfologiya
    # 40× kadrda baribir o'qiladi, sarf esa deyarli ikki barobar kam.
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
    "Belgilar kam bo'lsa ham TASHXIS NOMINI yoz va «Ishonch: past» qo'y — «yetarli emas» degan javob TAQIQLANADI. "
    "Dalilsiz rak/karsinoma YOZMA. "
    "HISOBOT FAQAT 3 BO'LIM: #### TASHXIS, #### NEGA SHU TASHXIS, #### FAKT (o'lchangan morfologiya). Jami 2500-4500 belgi. Har qator sonli yoki aniq atamali bo'lsin. Uzun muhokama, savol-javob, profilaktika, davolash rejasi, professor bo'limlari, jadval, ehtimollik foizi - TAQIQLANADI. "
    "Rad etma. Faqat MedLab gistologiya."
)

_WORKSHEET_SYSTEM = (
    "Sen MedLab gistologiya hisobotini to'ldirasan (LIS). Adashishga haqqi YO'Q. "
    "WHO/Weedon NOMINI aniq yoz. Dalilsiz malignite qo'yma - avval benign/reaktiv. "
    "FAQAT gistologiya. O'zbek tili. "
    "HISOBOT FAQAT 3 BO'LIM: #### TASHXIS, #### NEGA SHU TASHXIS, #### FAKT (o'lchangan morfologiya). Jami 2500-4500 belgi. Har qator sonli yoki aniq atamali bo'lsin. Uzun muhokama, savol-javob, profilaktika, davolash rejasi, professor bo'limlari, jadval, ehtimollik foizi - TAQIQLANADI. "
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
    "HISOBOT FAQAT 3 BO'LIM: #### TASHXIS, #### NEGA SHU TASHXIS, #### FAKT (o'lchangan morfologiya). Jami 2500-4500 belgi. Har qator sonli yoki aniq atamali bo'lsin. Uzun muhokama, savol-javob, profilaktika, davolash rejasi, professor bo'limlari, jadval, ehtimollik foizi - TAQIQLANADI. "
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
    "Jami 2500-4500 belgi. "
    "Muhokama, o'quv matni, foiz, jadval yozma. O'zbek tili. Yulduzcha ** yo'q.\n\n"
)

_RETRY_DEEP_USER = (
    "Oldingi matn talabga mos emas. Qayta yoz: aniq tashxis, uning sababi va ko'ringan fakt. "
    "Faqat 3 bo'lim, 2500-4500 belgi. Ortiqcha gap, savol-javob, foiz, jadval - o'chir. "
    "Rad etma.\n\n"
    "==== OLDINGI MATN ====\n"
)

_REFUSAL_MARKERS = (
    # O'zbekcha rad javoblari: model uzun va muloyim rad etganda ingliz
    # markerlari ushlamasdi va rad matni promptga kirib ketardi.
    "kechirasiz, men",
    "tahlil qila olmayman",
    "yordam bera olmayman",
    "javob bera olmayman",
    "iltimos, rasmni taqdim eting",
    "shaxsiy ma'lumotlarni",
    "shaxsiy ma\u2019lumotlarni",
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
    "Hisobot tayyorlanmadi — model bu to'plam bo'yicha javob bermadi.\n\n"
    "Nima qilish kerak:\n"
    "- Rasm sonini kamaytiring (4-8 ta yetarli): 10x umumiy ko'rinish + 40x hujayra tafsiloti.\n"
    "- Fokusdan chiqqan, qorong'i yoki takroriy kadrlarni olib tashlang.\n"
    "- Namuna joyi va klinik izoh to'ldirilganiga ishonch hosil qiling.\n"
    "- Keyin «Tahlil qil» ni qayta bosing."
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


# ─── Model parametrlari mosligi ──────────────────────────────────────────────
# gpt-4o `max_tokens`, `temperature`, `top_p` ni oladi; yangi avlod modellari
# esa `max_completion_tokens` kutadi va temperature/top_p/seed ni rad etadi.
# Har model uchun to'g'ri uslub bir marta aniqlanib, keyin keshdan olinadi —
# aks holda har chaqiruvda bitta ortiqcha 400 javob bo'lardi.
_PARAM_STYLE = {}
_PARAM_LOCK = threading.Lock()

_LEGACY_ONLY = ("temperature", "top_p", "seed", "presence_penalty", "frequency_penalty")


def _adapt_kwargs(kwargs, style):
    """`style`: 'legacy' (gpt-4o) yoki 'modern' (max_completion_tokens)."""
    out = dict(kwargs or {})
    if style == "legacy":
        if "max_completion_tokens" in out:
            out["max_tokens"] = out.pop("max_completion_tokens")
        return out
    # modern
    if "max_tokens" in out:
        out["max_completion_tokens"] = out.pop("max_tokens")
    for k in _LEGACY_ONLY:
        out.pop(k, None)
    return out


def _param_style(model_id):
    with _PARAM_LOCK:
        return _PARAM_STYLE.get(model_id)


def _remember_style(model_id, style):
    with _PARAM_LOCK:
        _PARAM_STYLE[model_id] = style


def _is_param_error(exc):
    msg = str(exc).lower()
    return any(
        m in msg
        for m in (
            "max_tokens",
            "max_completion_tokens",
            "unsupported parameter",
            "unsupported value",
            "not supported with this model",
        )
    )


# ─── Token hisobi ────────────────────────────────────────────────────────────
# Audit topgan xato: bitta keys 15–20 ta model chaqiruvi qilar, rasmlar har
# chaqiruvda 2048 px da qayta yuborilar, `usage` esa hech qayerda o'qilmasdi —
# sarf umuman hisoblanmagan. Endi har chaqiruvning HAQIQIY token soni
# (resp.usage) keys bo'yicha yig'iladi, jurnalga yoziladi va chegaradan
# oshsa keys to'xtatiladi — hisob minusga tushmasin.

_case_meter = threading.local()


def _meter_reset():
    _case_meter.calls = 0
    _case_meter.prompt = 0
    _case_meter.completion = 0
    _case_meter.log = []
    _case_meter.open = False


def _meter_begin():
    """Keys boshlandi — shu paytdan hamma chaqiruv (klinik surat ham) hisobga kiradi."""
    _meter_reset()
    _case_meter.open = True


def _meter_end():
    _meter_state().open = False


def _meter_ensure():
    """_openai_generate to'g'ridan-to'g'ri chaqirilsa (benchmark) — o'zi boshlaydi;
    do_analyze ichida bo'lsa — ochiq hisobni buzmaydi."""
    if not getattr(_meter_state(), "open", False):
        _meter_begin()


def _meter_state():
    if not hasattr(_case_meter, "calls"):
        _meter_reset()
    return _case_meter


def _meter_add(label, usage, n_images, text_chars):
    m = _meter_state()
    m.calls += 1
    p = int(getattr(usage, "prompt_tokens", 0) or 0)
    c = int(getattr(usage, "completion_tokens", 0) or 0)
    if not p:            # usage kelmasa — taxmin (rasm ≈ 800, matn ≈ 4 belgi/token)
        p = text_chars // 4 + n_images * 800
    m.prompt += p
    m.completion += c
    m.log.append({"label": label, "prompt": p, "completion": c, "images": n_images})
    log.info(
        "%s: token — %s: %s+%s (rasm %s) | keys jami %s chaqiruv, %s token",
        ZIYRAKAI_DISPLAY_NAME, label, p, c, n_images, m.calls, m.prompt + m.completion,
    )


def _meter_summary():
    m = _meter_state()
    return {"calls": m.calls, "prompt_tokens": m.prompt,
            "completion_tokens": m.completion, "total_tokens": m.prompt + m.completion,
            "stages": list(m.log)}


def _max_calls_per_case():
    try:
        # klinik surat + umumiy ko'rinish + ko'rik + kadr qidiruv (≤3 paket) + qaror + tekshiruv
        return max(1, int(os.environ.get("OPENAI_MAX_CALLS_PER_CASE", "9")))
    except ValueError:
        return 9


def _max_tokens_per_case():
    try:
        return max(5000, int(os.environ.get("OPENAI_MAX_TOKENS_PER_CASE", "60000")))
    except ValueError:
        return 60000


class CaseBudgetExceeded(RuntimeError):
    """Keys uchun chaqiruv/token chegarasi tugadi."""


def _budget_check(label):
    m = _meter_state()
    if m.calls >= _max_calls_per_case():
        raise CaseBudgetExceeded(
            f"keys chegarasi: {m.calls} chaqiruv ({_max_calls_per_case()} ruxsat) — {label}"
        )
    if m.prompt + m.completion >= _max_tokens_per_case():
        raise CaseBudgetExceeded(
            f"keys chegarasi: {m.prompt + m.completion} token "
            f"({_max_tokens_per_case()} ruxsat) — {label}"
        )


def _message_stats(messages):
    n_img, chars = 0, 0
    for msg in messages or []:
        c = msg.get("content")
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            for part in c:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    n_img += 1
                elif part.get("type") == "text":
                    chars += len(part.get("text") or "")
    return n_img, chars


def _economy_enabled():
    """Tejamkor quvur: keysga ≤3 chaqiruv. 0 — eski to'liq quvur."""
    v = (os.environ.get("HISTOLOGY_ECONOMY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _chat_complete(messages, kwargs, model=None, label=""):
    _budget_check(label or "chaqiruv")
    max_retries = max(1, int(os.environ.get("OPENAI_MAX_RETRIES", "3")))
    base_delay = float(os.environ.get("OPENAI_RETRY_DELAY_SEC", "2"))
    model_id = (model or OPENAI_MODEL_ID).strip() or OPENAI_MODEL_ID
    style = _param_style(model_id)
    call_kwargs = _adapt_kwargs(kwargs, style or "legacy")
    for attempt in range(max_retries):
        try:
            resp = openai_client.chat.completions.create(
                model=model_id,
                messages=messages,
                **call_kwargs,
            )
            if style is None:
                _remember_style(model_id, "legacy")
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
                _note_api_ok()
                _n_img, _chars = _message_stats(messages)
                _meter_add(label or "chaqiruv", getattr(resp, "usage", None), _n_img, _chars)
                return text
            return (
                "%s javob matni bo'sh yoki to'liq emas (finish_reason=%s). "
                "Keyinroq qayta urinib ko'ring."
            ) % (ZIYRAKAI_DISPLAY_NAME, fr)
        except Exception as e:
            if style is None and _is_param_error(e):
                # Yangi avlod modeli — boshqa parametr nomlari bilan qayta urinamiz
                _remember_style(model_id, "modern")
                style = "modern"
                call_kwargs = _adapt_kwargs(kwargs, "modern")
                log.info(
                    "%s: %s uchun yangi parametr uslubi qo'llanildi",
                    ZIYRAKAI_DISPLAY_NAME, model_id,
                )
                continue
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
            _note_api_error(e)
            raise


# ─── Xizmat holati ──────────────────────────────────────────────────────────
# Audit topgan xato: /api/health «ziyrakai_ready: true» deb turardi, chunki u
# faqat kalit satri borligini tekshirardi. Hisobda kredit tugaganda tizim
# sog'lom ko'rinar, ammo har bir tahlil xato bilan tugardi. Endi holat
# HAQIQIY chaqiruv natijasidan olinadi.

_api_state = {"ok": None, "at": 0.0, "error": "", "kind": ""}
_api_state_lock = threading.Lock()


def _classify_api_error(e):
    """Xatoni shifokorga tushunarli turga ajratish."""
    s = str(e).lower()
    if "no credits" in s or "insufficient_quota" in s or "billing" in s:
        return "kredit"
    if "rate limit" in s or "429" in s:
        return "band"
    if "401" in s or "invalid_api_key" in s or "incorrect api key" in s:
        return "kalit"
    if "timeout" in s or "timed out" in s or "connection" in s:
        return "aloqa"
    if "does not exist" in s or "model_not_found" in s or "404" in s:
        return "model"
    return "boshqa"


# Shifokor ekranida inglizcha xom xato va billing havolasi chiqmasin
_API_ERROR_UZ = {
    "kredit": "Tahlil xizmatining hisobida mablag' tugagan. Administrator OpenAI "
              "hisobiga kredit qo'shishi kerak — shundan keyin tahlil darhol ishlaydi.",
    "band": "Tahlil xizmati hozir band. Bir necha daqiqadan so'ng qayta urinib ko'ring.",
    "kalit": "Tahlil xizmatining kaliti yaroqsiz. Administrator .env dagi "
             "OPENAI_API_KEY ni yangilashi kerak.",
    "aloqa": "Tahlil xizmatiga ulanib bo'lmadi. Internet aloqasini tekshiring va "
             "qayta urinib ko'ring.",
    "model": "Tanlangan model mavjud emas. Administrator OPENAI_MODEL_ID ni "
             "tekshirishi kerak.",
    "boshqa": "Tahlil xizmatida kutilmagan xato. Qayta urinib ko'ring; takrorlansa "
              "administratorga xabar bering.",
}


def api_error_uz(e):
    """Xatoning o'zbekcha, harakatga yo'naltirilgan matni."""
    return _API_ERROR_UZ.get(_classify_api_error(e), _API_ERROR_UZ["boshqa"])


def _note_api_ok():
    with _api_state_lock:
        _api_state.update({"ok": True, "at": time.time(), "error": "", "kind": ""})


def _note_api_error(e):
    kind = _classify_api_error(e)
    with _api_state_lock:
        _api_state.update({"ok": False, "at": time.time(), "error": str(e)[:300], "kind": kind})
    log.error("%s: xizmat xatosi (%s): %s", ZIYRAKAI_DISPLAY_NAME, kind, str(e)[:200])


def api_status(probe_after_sec=600):
    """Tahlil xizmati haqiqatan ishlayaptimi.

    Yaqinda haqiqiy chaqiruv bo'lgan bo'lsa — o'sha natija. Bo'lmasa arzon
    sinov chaqiruvi qilinadi, natija esa keshlanadi.
    """
    if openai_client is None:
        return {"ready": False, "kind": "kalit", "detail": "kalit sozlanmagan"}
    with _api_state_lock:
        st = dict(_api_state)
    fresh = st["ok"] is not None and (time.time() - st["at"]) < probe_after_sec
    if fresh:
        return {
            "ready": bool(st["ok"]),
            "kind": st["kind"] or "",
            "detail": st["error"][:200] if not st["ok"] else "",
        }
    try:
        _chat_complete(
            [{"role": "user", "content": "ok"}],
            {"max_tokens": 4, "temperature": 0.0},
        )
        return {"ready": True, "kind": "", "detail": ""}
    except Exception as e:
        _note_api_error(e)
        return {"ready": False, "kind": _classify_api_error(e), "detail": str(e)[:200]}


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
        return not (has_dx and has_why and has_fact)
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
            "Bitta yagona TASHXIS va bitta hisobot (3 bo'lim). Maydonlar orasida "
            "farq bo'lsa, eng og'ir topilma hisobga olinadi. Alohida «rasmlar sintezi» "
            "bo'limi yozilmaydi."
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


def _complete_resilient(system, user_texts, image_parts, kwargs, label=""):
    """Filtr rad etsa — ko'rsatma matnini yengillatib qayta urinish.

    Kuzatilgan xatti-harakat: juda uzun va zich ko'rsatma bloki tasvir bilan
    birga kelganda model "I'm sorry, I can't assist with that" deb javob
    beradi. Tekshirildi: o'sha blokning har bir bandi ALOHIDA o'tadi, faqat
    butun blok rad etiladi — ya'ni sabab aniq ibora emas, umumiy hajm.

    Shuning uchun avval MATN qisqartiriladi (rasm qoladi — morfologiya asosiy
    manba), va faqat oxirgi chorada rasmsiz urinib ko'riladi. Ilgari teskarisi
    edi: rasm tashlanardi va model ko'rmasdan yozardi.
    """
    if isinstance(user_texts, str):
        user_texts = [user_texts]
    texts = [t for t in user_texts if t]
    parts = list(image_parts or [])
    attempts = [(t, parts) for t in texts]
    if parts and texts:
        attempts.append((texts[-1], []))  # oxirgi chora: eng qisqa matn, rasmsiz
    last = ""
    for i, (text, imgs) in enumerate(attempts):
        content = _vision_user(text, imgs) if imgs else text
        try:
            out = _chat_complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                kwargs,
                label=label or "chaqiruv",
            )
        except CaseBudgetExceeded:
            raise
        except Exception as e:
            log.warning("%s: %s chaqiruv xato: %s", ZIYRAKAI_DISPLAY_NAME, label, e)
            return last
        last = out
        if not _looks_like_refusal(out):
            if i:
                log.info(
                    "%s: %s — %s-urinishda o'tdi (matn %s belgi, %s rasm)",
                    ZIYRAKAI_DISPLAY_NAME, label, i + 1, len(text), len(imgs),
                )
            return out
        log.warning(
            "%s: %s rad etildi (matn %s belgi, %s rasm) — yengilroq urinish",
            ZIYRAKAI_DISPLAY_NAME, label, len(text), len(imgs),
        )
    return last


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
        "BIR organ. Hisobot FAQAT 3 bo'lim: #### TASHXIS, #### NEGA SHU TASHXIS, "
        "#### FAKT (o'lchangan morfologiya). Jami 2500-4500 belgi, 6500 dan oshmasin. "
        "Savol-javob, profilaktika, davolash, professor bo'limlari, jadval, foiz — YO'Q. "
        + (f"Barcha {n_img} ta rasmni sintez qil; faqat 1-rasmga tayanma. " if n_img > 1 else "")
        + "Har mezon qatori: <mezon> — KO'RINDI: <bir jumlalik dalil>. "
        "Ko'rinmagan mezonni yozma. Boshqa organ differensiali YO'Q. "
        "Bemor jinsi va namuna joyiga zid yozma. "
        "Agar organ qulfi TERI bo'lsa buyrak rakini / RCC ni YOZMA. "
        "Malignite qo'yish huquqi YO'Q bo'lsa asosiy tashxis benign/reaktiv bo'ladi. "
        "Sog'lom/dalilsiz holatga rak qo'yish — hisobot yaroqsiz."
    )
    # Yengilroq variantlar: protokol matni tushadi, kitob parchasi qisqaradi,
    # lekin ko'rik natijasi, qoralama va chiqish shakli har doim qoladi.
    tail = (
        "\n\n==== ICHKI QORALAMA (shu asosda YAKUNIY QISQA hisobotni yoz) ====\n"
        + (draft or "")[:6000]
        + "\n==== TUGADI ====\n"
        "Hisobot FAQAT 3 bo'lim: #### TASHXIS, #### NEGA SHU TASHXIS, "
        "#### FAKT (o'lchangan morfologiya). Jami 2500-4500 belgi — "
        "bundan qisqa hisobot QABUL QILINMAYDI.\n"
        "TASHXIS: 1-qator kasallik NOMI (variant bilan); 2-qator "
        "Organ/qatlam | Ishonch | Malignite qo'yish huquqi; 3-qator bir jumlada "
        "jarayon tabiati va keyingi qadam.\n"
        "NEGA SHU TASHXIS — eng katta bo'lim, 6-10 qator:\n"
        "  · har qator: <mezon> — <QAYERDA, QANDAY, QANCHA ko'rindi>; "
        "belgi nomini takrorlash TAQIQLANADI;\n"
        "  · kamida 4 ta mezon ko'rilgan dalil bilan;\n"
        "  · oxirgi 2-3 qatorda muqobillar shu yerda rad etilsin: "
        "«<muqobil> emas, chunki <qaysi ko'rilgan belgi mos emas>»; "
        "eng xavflisi (karsinoma, melanoma, sarkoma) birinchi;\n"
        "  · kerak bo'lsa oxirgi qator: «Tasdiqlash uchun: <IHC/bo'yoq/kesma>».\n"
        "FAKT: 6-8 qator, har birida son yoki daraja.\n"
        "Ko'rinmagan mezonni yozma. Dalilsiz rak yozma."
    )
    mid_text = (
        (patient + "\n" if patient else "")
        + lock
        + (feats + "\n" if feats else "")
        + (kb[:5000] if kb else "")
        + tail
    )
    light_text = (feats + "\n" if feats else "") + lock + tail

    expand_kwargs = dict(kwargs or {})
    if lab_type == "histology":
        expand_kwargs["temperature"] = min(float(expand_kwargs.get("temperature", 0.12) or 0.12), 0.15)
    return _complete_resilient(
        _SAFE_SYSTEM,
        [user_text, mid_text, light_text],
        image_parts,
        expand_kwargs,
        "uzaytirish",
    )


_EXPERT_REVIEW_SYSTEM = (
    "You are the head of a histopathology department doing the final sign-out check of a "
    "trainee's draft. You see the same slide images, the machine-extracted feature list and "
    "the retrieved textbook criteria. Your job is NOT to praise or discuss: you return the "
    "CORRECTED FINAL REPORT only, in Uzbek, in the required 3-section format "
    "Fix silently: a name that the features do not support, a missing variant/grade, "
    "vague wording where a number belongs, an alternative dismissed without a discriminator, "
    "a missing confirmation panel, and anything the draft failed to declare as unassessable. "
    "Never invent a finding that is not in the features or visible in the image. "
    "The diagnosis line must always carry a NAME. If the features are thin, give the "
    "most probable entity, mark it provisional and set confidence to low — never answer "
    "'not enough for a diagnosis'. "
    "Output the report only — no commentary, no meta text, no headings other than the three."
)


def _expert_review_enabled():
    v = (os.environ.get("HISTOLOGY_EXPERT_REVIEW") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _expert_review(draft, kwargs, image_parts=None, lab_type="histology",
                   organ_lock=None, patient_context=None, features=None, kb_block=""):
    """Kafedra mudiri tekshiruvi: qoralamani belgilar va mezonlar bo'yicha yakunlash."""
    if lab_type != "histology" or not _expert_review_enabled():
        return draft
    feats = _features_prompt_block(features)
    lock = _histology_organ_lock_text(organ_lock)
    patient = _patient_prompt_prefix(patient_context, lab_type)
    n_img = len(image_parts or [])
    user_text = (
        (patient + "\n" if patient else "")
        + lock
        + (feats + "\n" if feats else "")
        + ((kb_block + "\n") if kb_block else "")
        + _HISTOLOGY_TEACHING_DEEP
        + "\n==== SHOGIRD QORALAMASI ====\n"
        + (draft or "")[:9000]
        + "\n==== QORALAMA TUGADI ====\n\n"
        "IMZO OLDIDAN TEKSHIRUV RO'YXATI (har bandni jimgina tuzat):\n"
        "1) Tashxis nomi belgilarga mos keladimi? Variant/daraja ko'rsatilganmi?\n"
        "2) Har bir mezon qatorida KO'RINGAN dalil bormi? Uydirma mezon yo'qmi?\n"
        "3) FAKT bo'limida sonlar bormi: mitoz/10HPF, qalinlik, chuqurlik, chekka?\n"
        "4) Muqobillar NEGA SHU TASHXIS ichida ajratuvchi belgi bilan rad etilganmi?\n"
        "5) Tashxis o'rnida NOM turibdimi? «Yetarli emas / aniqlanmadi» yozilmaganmi?\n"
        "6) Ishonch darajasi dalilga mos keladimi?\n"
        "7) Malignite qo'yish huquqi qoidasi buzilmaganmi?\n"
        + (f"8) Barcha {n_img} ta maydon hisobga olinganmi?\n" if n_img > 1 else "")
        + "\nFAQAT yakuniy hisobotni qaytar (3 bo'lim, 2500-4500 belgi). Izoh yozma."
    )
    review_kwargs = dict(kwargs or {})
    review_kwargs["temperature"] = 0.0
    try:
        light_review = (
            (feats + "\n" if feats else "")
            + lock
            + "\n==== SHOGIRD QORALAMASI ====\n"
            + (draft or "")[:6000]
            + "\n==== QORALAMA TUGADI ====\n\n"
            "Hisobotni tekshirib, yakuniy variantini qaytar: 3 bo'lim, "
            "tashxis o'rnida NOM, har asos qatorida ko'rilgan dalil. Izoh yozma."
        )
        out = _complete_resilient(
            _EXPERT_REVIEW_SYSTEM,
            [user_text, light_review],
            image_parts,
            review_kwargs,
            "imzo tekshiruvi",
        )
    except Exception as e:
        log.warning("%s: professor tekshiruvi xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return draft
    if not _usable(out, MIN_REPORT_CHARS) or _looks_like_refusal(out):
        log.warning("%s: professor tekshiruvi yaroqsiz — qoralama saqlanadi", ZIYRAKAI_DISPLAY_NAME)
        return draft
    if features and _report_contradicts_features(out, features):
        log.warning("%s: professor tekshiruvi ham belgiga zid — qoralama saqlanadi",
                    ZIYRAKAI_DISPLAY_NAME)
        return draft
    if _too_verbose(out, lab_type):
        log.warning("%s: professor tekshiruvi juda uzun — qoralama saqlanadi", ZIYRAKAI_DISPLAY_NAME)
        return draft
    log.info("%s: professor tekshiruvi qo'llandi (%s → %s belgi)",
             ZIYRAKAI_DISPLAY_NAME, len(draft or ""), len(out))
    return out


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
                {"type": "image_url", "image_url": {"url": url, "detail": "high"}}
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


# ─── Rad javobidan chiqish yo'li ─────────────────────────────────────────────
# gpt-4o ba'zan uzun "tashxis qo'y" so'rovini + o'nlab tibbiy tasvirni rad etadi.
# Bunday holatda so'rov qisqartiriladi va neytral ramkaga solinadi: bu ichki
# morfologik tavsif, bemorga beriladigan xulosa emas.
_RECOVERY_SYSTEM = (
    "You are a histopathology teaching assistant helping a licensed pathologist put their own "
    "observations into a structured internal note. You are NOT giving medical advice to a "
    "patient and you are NOT signing out a case: the pathologist reviews and signs. "
    "You will be given a list of morphological features that were already extracted from the "
    "slide. Organise them into the requested Uzbek sections and name the morphological pattern "
    "that best matches those features. If the features are too few for a specific entity, say "
    "so plainly. Answer in Uzbek. Do not refuse — this is descriptive morphology, not diagnosis "
    "for a patient."
)


def _recovery_report(features, organ_lock, patient_context, kwargs, image_parts=None):
    """Rad javobidan keyin: qisqa so'rov, kam rasm, neytral ramka."""
    feats = _features_prompt_block(features)
    if not feats:
        return ""
    organ = _HISTOLOGY_ORGAN_UZ.get((organ_lock or {}).get("organ") or "", "Teri")
    p = _normalize_patient_context(patient_context)
    site = p.get("specimen_site") or organ
    user = (
        f"Namuna: {site}. Quyida shu preparatdan olingan morfologik belgilar ro'yxati.\n\n"
        + feats
        + "\nShu belgilar asosida o'zbek tilida qisqa ichki yozuv tayyorla. "
        "Faqat quyidagi bo'limlar, jami 1500-3500 belgi:\n"
        "#### TASHXIS — belgilarni eng yaxshi tushuntiruvchi morfologik nom "
        "(yetarli bo'lmasa «Aniq tashxis uchun yetarli emas» + tavsifiy ko'rinish), "
        "keyin: Organ/qatlam, Ishonch: yuqori/o'rta/past\n"
        "#### NEGA SHU TASHXIS — 3-6 qator: <belgi> — KO'RINDI: <izoh>\n"
        "#### FAKT (o'lchangan morfologiya) — 5-8 qator, sonlar bilan\n"
        "(muqobillar va tasdiqlash yo'li NEGA SHU TASHXIS ichida yoziladi)\n"
        "Ro'yxatda yo'q belgini yozma."
    )
    picked = _spread_pick(image_parts or [], 4)
    content = _vision_user(user, picked) if picked else user
    rk = dict(kwargs or {})
    rk["temperature"] = 0.15
    rk["max_tokens"] = min(int(rk.get("max_tokens") or 4096), 4096)
    try:
        out = _chat_complete(
            [
                {"role": "system", "content": _RECOVERY_SYSTEM},
                {"role": "user", "content": content},
            ],
            rk,
        )
    except Exception as e:
        log.warning("%s: qutqaruv chaqiruvi xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
        return ""
    if _looks_like_refusal(out) or not _usable(out, MIN_REPORT_CHARS):
        # Rasmsiz, faqat belgilar ro'yxati bilan oxirgi urinish
        try:
            out = _chat_complete(
                [
                    {"role": "system", "content": _RECOVERY_SYSTEM},
                    {"role": "user", "content": user},
                ],
                rk,
            )
        except Exception as e:
            log.warning("%s: qutqaruv (rasmsiz) xato: %s", ZIYRAKAI_DISPLAY_NAME, e)
            return ""
    if _looks_like_refusal(out) or not _usable(out, MIN_REPORT_CHARS):
        return ""
    log.info("%s: qutqaruv hisoboti tayyorlandi (%s belgi)", ZIYRAKAI_DISPLAY_NAME, len(out))
    return out


def _openai_generate(content_list, lab_type="histology", patient_context=None,
                     ref_parts=None, ref_block="", trace=None,
                     clinical_block="", clinical_parts=None):
    """trace berilsa — ko'rik va tashxis yozuvi unga qo'yiladi (arxiv uchun).

    clinical_block / clinical_parts — bemor tanasidagi surat tavsifi va rasmi.
    Audit topgan xato: bu tavsif faqat full_prompt ichida turar, uni esa eski
    matn yo'li o'qirdi — yangi qaror bosqichi klinik suratni umuman ko'rmasdi.
    """
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
    _pils = [item for item in content_list if isinstance(item, Image.Image)]
    _scores = [_slide_score(im) for im in _pils]
    # Kesma kadrlarining oq balansi to'g'irlanadi — telefon orqali olingan
    # rasmlarda binafsha og'ish morfologiyani ko'rinmas qilib qo'yadi.
    _prepped = [
        _normalize_he(im) if (sc >= SLIDE_SCORE_MIN and lab_type == "histology") else im
        for im, sc in zip(_pils, _scores)
    ]
    _n_fixed = sum(
        1 for im, p, sc in zip(_pils, _prepped, _scores)
        if p is not im and sc >= SLIDE_SCORE_MIN
    )
    if _n_fixed:
        log.info("%s: %s kesmaning oq balansi to'g'irlandi", ZIYRAKAI_DISPLAY_NAME, _n_fixed)
    image_parts = [
        {
            "type": "image_url",
            "image_url": {"url": _pil_to_data_url(item), "detail": "high"},
        }
        for item in _prepped
    ]
    # Har rasm uchun "kesmami?" bahosi — tanlash shu bo'yicha ustuvorlashadi
    _scored = list(zip(image_parts, _scores))
    _n_slides = sum(1 for _, sc in _scored if sc >= SLIDE_SCORE_MIN)
    # Ko'rik, namuna turi va organ qulfi FAQAT kesmalarga qaraydi — tana
    # suratlari morfologik belgilarni suyultirib yuborardi. Kesma bo'lmasa
    # butun to'plam ishlatiladi (rejim o'zgarmaydi).
    _vision_parts = [p for p, sc in _scored if sc >= SLIDE_SCORE_MIN] or image_parts
    kwargs = _openai_generation_kwargs()
    if lab_type == "histology":
        kwargs["temperature"] = 0.0
        kwargs["top_p"] = min(float(kwargs.get("top_p", 0.85) or 0.85), 0.5)
        # Qayta tahlilda barqarorroq (model qo'llab-quvvatlasa)
        kwargs.setdefault("seed", 42)

    n_img = len(image_parts)
    organ_lock = None
    features = None
    # Hisobot bosqichiga butun to'plamdan teng oraliqdagi namuna boradi
    report_parts = _pick_images(_scored, _report_max_images()) if image_parts else []
    # Atlas rasmlari hisobot chaqiruvining OXIRIGA qo'shiladi: ko'rik, organ
    # qulfi va namuna tekshiruvi ularni ko'rmaydi (ular bemor kesmasi emas).
    if ref_parts:
        report_parts = report_parts + list(ref_parts)
        if ref_block:
            full_prompt = full_prompt + "\n\n" + ref_block
        log.info(
            "%s: hisobotga %s ta atlas rasmi qo'shildi",
            ZIYRAKAI_DISPLAY_NAME, len(ref_parts),
        )
    if image_parts:
        log.info(
            "%s: hisobot uchun %s rasmdan %s tasi tanlandi (kesma: %s/%s)",
            ZIYRAKAI_DISPLAY_NAME, n_img, len(report_parts), _n_slides, n_img,
        )
        if not _n_slides and lab_type == "histology":
            log.warning(
                "%s: to'plamda H&E kesmasi topilmadi — klinik suratlar bilan ishlanmoqda",
                ZIYRAKAI_DISPLAY_NAME,
            )
    _meter_ensure()
    gestalt = None
    survey = None
    economy = lab_type == "histology" and _economy_enabled() and _structured_enabled()
    if image_parts and economy:
        # TEJAMKOR YO'L. Avval UMUMIY KO'RINISH (montaj + tafsilot + tana surati) —
        # patologning birinchi qadami; so'ng ko'rik — belgilar, montaj ham unda.
        t0 = time.time()
        _slide_pils = [im for im, sc in zip(_prepped, _scores) if sc >= SLIDE_SCORE_MIN] or _prepped
        gestalt = _gestalt_stage(
            _slide_pils, _spread_pick(_vision_parts, 3), clinical_parts, patient_context,
            kwargs, clinical_block,
        )
        _obs_parts = list(_vision_parts)
        if gestalt and gestalt.get("_sheet") is not None:
            _obs_parts = [{"type": "image_url", "image_url": {
                "url": _pil_to_data_url(gestalt["_sheet"]), "detail": "high"}}] + _obs_parts
        features = _observe_histology(_obs_parts, patient_context)
        organ_lock = _organ_from_observation(features, patient_context)
        mismatch = _mismatch_from_observation(features, lab_type)
        log.info("%s: ko'rik (tejamkor, 1 chaqiruv) %.1fs", ZIYRAKAI_DISPLAY_NAME, time.time() - t0)
        # Kadr-kadr qidiruv: gipotezalarni ajratuvchi belgilar HAMMA kadrlarda
        if isinstance(features, dict) and len(_vision_parts) >= 2:
            _hyp = gestalt
            if not _hyp:
                _top = _dxc.rank_candidates(features, 2, clinical_block, _referral_text(patient_context))
                _hyp = {"diagnosis": _top[0]["name"] if _top else "",
                        "alternatives": [{"name": r["name"]} for r in _top[1:]]}
            _skeys = _survey_keys(_hyp, _referral_text(patient_context), clinical_block)
            survey = _frame_survey(_skeys, _vision_parts, kwargs)
            if survey:
                _sch = _apply_survey(features, survey)
                if _sch:
                    log.info("%s: qidiruv ko'rikni tuzatdi: %s", ZIYRAKAI_DISPLAY_NAME, "; ".join(_sch[:6]))
        if mismatch:
            log.warning("%s: specimen mismatch lab=%s — tahlil to'xtatildi", ZIYRAKAI_DISPLAY_NAME, lab_type)
            return mismatch
    elif image_parts:
        # Namuna turi tekshiruvi va organ qulfi bir-biriga bog'liq emas — parallel bajariladi
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=3) as pool:
            gate_f = pool.submit(_gate_specimen_match, _vision_parts, lab_type)
            lock_f = (
                pool.submit(_lock_histology_organ, _vision_parts, patient_context)
                if lab_type == "histology"
                else None
            )
            obs_f = (
                pool.submit(_observe_histology, _vision_parts, patient_context)
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

    # Differensial hakamlik: mustaqil maydonlar bergan nomlar + klinik gipoteza
    # kitob mezonlari bo'yicha tekshiriladi. Bu — qaror nuqtasi, shuning uchun
    # kitoblar aynan shu yerda ishlashi kerak.
    adj_block = ""
    adj = None      # hisobot oxirida ishonchlilik foiziga ham kerak bo'ladi
    if lab_type == "histology" and isinstance(features, dict) and not economy:
        cands = list(_group_dx_names(features, kwargs))
        cands.extend(_dx_terms_for_atlas(patient_context))
        # Deterministik mezon jadvali — ko'rilgan belgilardan hisoblangan nomzodlar
        cands.extend(r["name"] for r in _dxc.rank_candidates(features, 5))
        adj = _adjudicate_diagnosis(features, cands, kwargs, organ_lock, patient_context)
        adj_block = _adjudication_block(adj)

    # Tuzilgan qaror — asosiy yo'l. Matn qayta o'qilmaydi, hisobot shu
    # yozuvdan chiqariladi, shuning uchun sarlavha yoki foiz yo'qolmaydi.
    if lab_type == "histology" and _structured_enabled() and isinstance(features, dict):
        try:
            _rec = _decide_diagnosis(
                features, adj, kb_block, kwargs, organ_lock, patient_context, _vision_parts,
                clinical_block=clinical_block, clinical_parts=clinical_parts, gestalt=gestalt,
                survey=survey,
            )
        except CaseBudgetExceeded as e:
            log.warning("%s: %s", ZIYRAKAI_DISPLAY_NAME, e)
            _rec = None
        if _rec is None and economy:
            # Ikkinchi, yengilroq urinish (kamroq rasm), so'ng — mezon jadvalidan
            # deterministik yozuv. Eski 8 chaqiruvli matn zanjiriga QAYTILMAYDI.
            try:
                _rec = _decide_diagnosis(
                    features, adj, kb_block[:2500], kwargs, organ_lock, patient_context,
                    _vision_parts[:1],
                )
            except CaseBudgetExceeded as e:
                log.warning("%s: %s", ZIYRAKAI_DISPLAY_NAME, e)
            if _rec is None:
                _rec = _record_from_gestalt(gestalt) or _record_from_criteria(features)
        if _rec is not None:
            _names = []
            if not economy:
                _stable, _names = _dx_stability(features, kwargs)
                if _stable is False:
                    log.warning(
                        "%s: tashxis barqaror emas: %s",
                        ZIYRAKAI_DISPLAY_NAME, " | ".join(_names[:3]),
                    )
            _changes = []
            if economy and survey:
                _changes = []          # qidiruv allaqachon barcha kadrlarni ko'rdi
                if isinstance(features, dict):
                    features["_survey"] = survey
                # Sanoq hisobotda: tanlangan va gestalt nozologiyalarining ajratuvchi belgilari
                _ents = [e for e in (_dxc.find_entity(_rec.name), _dxc.find_entity(
                    str((gestalt or {}).get("diagnosis") or ""))) if e]
                _keys = []
                for e in _ents:
                    _keys += [k for k in e["essential"] if k in (survey.get("found") or {}) and k not in _keys]
                _rec.survey_line = _survey_line(survey, _keys or None)
            elif economy and _rec.certainty != "tavsifiy":
                _changes = _verify_decisive_features(
                    _rec, features, _vision_parts, kwargs,
                    _hypothesis_text(patient_context, gestalt) + " " + (clinical_block or ""),
                )
            if clinical_block:
                _rec.clinical = _clinical_summary_line(clinical_block)
            _rec, _text = _finish_record(
                _rec, features, adj, _names, _changes,
                clinical_text=clinical_block or "",
                referral_text=_hypothesis_text(patient_context, gestalt), gestalt=gestalt,
                referral_pure=_referral_text(patient_context),
            )
            if _text:
                if isinstance(trace, dict):
                    trace["features"] = features
                    trace["record"] = _rec.to_dict()
                    trace["tokens"] = _meter_summary()
                    if gestalt:
                        trace["gestalt"] = {k: v for k, v in gestalt.items() if k != "_sheet"}
                _m = _meter_summary()
                log.info(
                    "%s: keys sarfi — %s chaqiruv, %s token (so'rov %s, javob %s)",
                    ZIYRAKAI_DISPLAY_NAME, _m["calls"], _m["total_tokens"],
                    _m["prompt_tokens"], _m["completion_tokens"],
                )
                log.info(
                    "%s: hisobot tayyor (tuzilgan) imgs=%s belgi=%s %.1fs",
                    ZIYRAKAI_DISPLAY_NAME, n_img, len(_text), time.time() - t_start,
                )
                return _text
        log.warning(
            "%s: tuzilgan qaror olinmadi — eski matn yo'liga o'tildi",
            ZIYRAKAI_DISPLAY_NAME,
        )

    patient_block = _patient_prompt_prefix(patient_context, lab_type)
    features_block = (
        (_features_prompt_block(features) if lab_type == "histology" else "")
        + ("\n" + adj_block if adj_block else "")
    )
    multi_note = _multi_image_protocol(n_img) if n_img > 1 else ""

    # Professor/konsilium so'rovi gpt-4o da tibbiy filtr bilan rad etiladi.
    # Avval ishlagan ichki morfologiya yozuvi, keyin xavfsiz uzaytirish.
    log.info("%s: 1-bosqich ichki morfologiya lab=%s imgs=%s", ZIYRAKAI_DISPLAY_NAME, lab_type, n_img)
    report = ""
    from_recovery = False
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
                {"role": "user", "content": _vision_user(describe_prompt, report_parts)},
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
                    {"role": "user", "content": _vision_user(ws, report_parts)},
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
        log.warning(
            "%s: hisobot olinmadi (%s) — qutqaruv ramkasi bilan qayta urinish",
            ZIYRAKAI_DISPLAY_NAME, _preview(report),
        )
        if lab_type == "histology" and features:
            rescued = _recovery_report(features, organ_lock, patient_context, kwargs, report_parts)
            if rescued:
                report = rescued
                from_recovery = True
        if not _usable(report, 200):
            return _REFUSAL_FALLBACK_UZ

    if image_parts and not from_recovery and (
        _needs_rewrite(report, lab_type, organ_lock)
        or (lab_type == "histology" and _looks_like_weak_generic(report, lab_type, organ_lock))
        or (lab_type == "histology" and _histology_report_organs_conflict(report))
        or (lab_type == "histology" and _histology_report_wrong_organ(report, organ_lock))
        or (lab_type == "histology" and _histology_cancer_overcall(report))
        or len(report) < (MIN_REPORT_CHARS if lab_type == "histology" else 5000)
    ):
        log.info("%s: 2-bosqich uzaytirish (%s belgi) lab=%s", ZIYRAKAI_DISPLAY_NAME, len(report), lab_type)
        expanded = _safe_expand(report, kwargs, report_parts, lab_type, organ_lock, patient_context, features)
        if _usable(expanded, MIN_REPORT_CHARS) and not _looks_like_refusal(expanded):
            organ_bad = lab_type == "histology" and (
                _histology_report_organs_conflict(expanded)
                or _histology_report_wrong_organ(expanded, organ_lock)
                or _histology_cancer_overcall(expanded)
            )
            if organ_bad:
                log.warning("%s: uzaytirishda organ konflikti — qayta qulf bilan", ZIYRAKAI_DISPLAY_NAME)
                fixed = _safe_expand(expanded, kwargs, report_parts, lab_type, organ_lock, patient_context, features)
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
            deeper = _deepen_report(report, deepen_prompt, kwargs, report_parts, lab_type)
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
                "Belgilar yetarli bo'lmasa ham NOM yoz — eng ehtimolli tashxisni "
                "qo'y va ishonchni «past» qil. ====",
                kwargs, report_parts, lab_type, organ_lock, patient_context, features,
            )
            if _usable(retry, MIN_REPORT_CHARS) and not _report_contradicts_features(retry, features):
                report = retry
            else:
                # Tuzatib bo'lmadi — asossiz nomni chiqarish mumkin emas
                log.warning(
                    "%s: zid tashxis tuzatilmadi — tavsifiy nomga almashtirildi",
                    ZIYRAKAI_DISPLAY_NAME,
                )
                report = _mark_provisional(report, features, conflict, rename=True)

    # Yakuniy imzo tekshiruvi: mezon, sonlar, differensial, tasdiqlash, cheklovlar
    if lab_type == "histology" and not from_recovery and _usable(report, MIN_REPORT_CHARS):
        report = _expert_review(
            report, kwargs, report_parts, lab_type, organ_lock, patient_context, features, kb_block
        )

    if lab_type == "histology" and features:
        report = _apply_evidence_rules(report, features, lab_type)

    # Imzo oldidan oxirgi qadam: hisobot o'z ichida zid bo'lmasin.
    # _apply_evidence_rules tashxis qatorini almashtirgan bo'lishi mumkin —
    # shuning uchun tekshiruv aynan shundan keyin turadi.
    if lab_type == "histology" and not from_recovery and _usable(report, MIN_REPORT_CHARS):
        report = _coherence_pass(report, kwargs, features, lab_type)

    # Tashxis barqarorligi eng oxirida o'lchanadi: mustaqil maydonlar bir xil
    # nomga olib keladimi? Natija endi alohida ogohlantirish emas — u
    # ishonchlilik foizining eng og'ir bandiga kiradi.
    _names = []
    if lab_type == "histology" and features and _usable(report, 400):
        _stable, _names = _dx_stability(features, kwargs)
        if _stable is False:
            log.warning(
                "%s: tashxis barqaror emas: %s",
                ZIYRAKAI_DISPLAY_NAME, " | ".join(_names[:3]),
            )

    if _usable(report, 400):
        report = _strip_preamble(report)
        if lab_type == "histology":
            report = _clean_dx_section(report)
            report = _mark_final_conclusion(report)
            report = _strip_other_organ_differential(report)
            report = _finalize_confidence(report, features, adj, _names)
        log.info(
            "%s: hisobot tayyor lab=%s imgs=%s belgi=%s %.1fs",
            ZIYRAKAI_DISPLAY_NAME, lab_type, n_img, len(report), time.time() - t_start,
        )
        return report
    if lab_type == "histology" and features:
        rescued = _recovery_report(features, organ_lock, patient_context, kwargs, report_parts)
        if rescued:
            return _apply_evidence_rules(rescued, features, lab_type)
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

# ─── Yo'llanma varaqasini o'qish ─────────────────────────────────────────────
# Klinikadan kelgan "Патоморфологик текширувга йўлланма" blankasi rasmga olinadi;
# undan bemor kartasi maydonlari avtomatik to'ldiriladi. Bu tashxis emas — hujjatdan
# matn ko'chirish.
_REFERRAL_SYSTEM = (
    "You transcribe a filled-in medical referral form (Uzbek/Russian, often handwritten or "
    "typed on a clinic letterhead). Return ONE JSON object only, no markdown, no commentary. "
    "Copy what is written; never invent a value. Use an empty string for anything you cannot "
    "read. Keep the original spelling of names. Never refuse — this is document transcription, "
    "not medical advice."
)

_REFERRAL_SCHEMA = (
    '{"clinic": "klinika nomi (blankaning yuqorisida)", '
    '"referral_no": "yo\'llanma yoki gistologik raqami", '
    '"referral_date": "yo\'llanma sanasi, KK.OO.YYYY", '
    '"patient_name": "bemor F.I.Sh.", '
    '"sex": "Erkak|Ayol|\"\"", '
    '"age": "yosh (faqat son) yoki tug\'ilgan yil", '
    '"address": "manzil / viloyat", '
    '"clinical_note": "klinik ma\'lumot (tashxisdan tashqari)", '
    '"clinical_dx": "yo\'llanmada yozilgan KLINIK TASHXIS — faqat tashxis nomi, '
    'masalan: Psoriaz, Ekzema, Bazalioma. Bir nechta bo\'lsa vergul bilan", '
    '"procedure": "jarrohlik amaliyoti turi va sanasi", '
    '"specimen_site": "namuna olingan joy/organ (matndan), masalan: Teri, o\'ng oyoq", '
    '"doctor": "davolovchi shifokor F.I.Sh.", '
    '"phone": "telefon raqami", '
    '"received_date": "olib kelingan sana", '
    '"is_referral": true}'
)

_REFERRAL_SITE_HINTS = (
    ("teri", "Teri"), ("кожа", "Teri"), ("кожн", "Teri"), ("skin", "Teri"),
    ("почесух", "Teri"), ("биопси", "Teri"),
    # Dermatologiya klinikasida punch/shave biopsiya — teri
    ("punch", "Teri"), ("панч", "Teri"), ("shave", "Teri"), ("biopsi", "Teri"),
    ("dermat", "Teri"), ("nevus", "Teri"), ("papillom", "Teri"), ("keratoz", "Teri"),
    ("ekzema", "Teri"), ("экзем", "Teri"), ("psoriaz", "Teri"), ("псориаз", "Teri"),
    ("sut bezi", "Sut bezi"), ("молочн", "Sut bezi"),
    ("qovuq", "Qovuq"), ("мочев", "Qovuq"),
    ("prostat", "Prostata"), ("простат", "Prostata"),
    ("qalqon", "Qalqonsimon bez"), ("щитовид", "Qalqonsimon bez"),
    ("ichak", "Oshqozon-ichak"), ("кишеч", "Oshqozon-ichak"), ("желуд", "Oshqozon-ichak"),
    ("endometr", "Endometrium"), ("матк", "Endometrium"),
    ("yumurtalik", "Yumurtalik"), ("яичник", "Yumurtalik"),
    ("buyrak", "Buyrak"), ("почк", "Buyrak"),
    ("o'pka", "O'pka"), ("лёгк", "O'pka"), ("легк", "O'pka"),
)


def _referral_sex(raw):
    t = (raw or "").strip().lower()
    if not t:
        return ""
    if t.startswith(("erkak", "муж", "m", "э")):
        return "Erkak"
    if t.startswith(("ayol", "жен", "f", "а")):
        return "Ayol"
    return ""


def _referral_age(raw, referral_date=""):
    """Yosh yoki tug'ilgan yil — ikkalasi ham qabul qilinadi."""
    digits = re.findall(r"\d{1,4}", str(raw or ""))
    if not digits:
        return ""
    n = int(digits[0])
    if 1900 <= n <= 2100:  # tug'ilgan yil berilgan
        year = None
        m = re.search(r"(20\d{2})", str(referral_date or ""))
        if m:
            year = int(m.group(1))
        if year is None:
            from datetime import date

            year = date.today().year
        age = year - n
        return str(age) if 0 < age < 130 else ""
    return str(n) if 0 < n < 130 else ""


def _referral_site(data):
    """Namuna joyini yo'llanma matnidan aniqlash."""
    blob = " ".join(
        str(data.get(k) or "")
        for k in ("specimen_site", "procedure", "clinical_note", "referral_no")
    ).lower()
    for needle, value in _REFERRAL_SITE_HINTS:
        if needle in blob:
            return value
    return ""


def parse_referral_image(pil_image):
    """Yo'llanma rasmidan bemor kartasi maydonlari (dict) yoki xato sababi."""
    if openai_client is None:
        raise RuntimeError("Xizmat kaliti sozlanmagan")
    img = _resize_img(pil_image, max_px=1800)
    part = {
        "type": "image_url",
        "image_url": {"url": _pil_to_data_url(img), "detail": "high"},
    }
    user = (
        "Transcribe this referral form into JSON with exactly these keys:\n"
        + _REFERRAL_SCHEMA
        + "\nIf the sheet is not a referral form (for example it is a microscope "
        'photograph), return {"is_referral": false}.'
    )
    raw = _chat_complete(
        [
            {"role": "system", "content": _REFERRAL_SYSTEM},
            {"role": "user", "content": [{"type": "text", "text": user}, part]},
        ],
        {"max_tokens": 900, "temperature": 0.0, "top_p": 0.1},
    )
    data = _parse_observation(raw)  # bir xil JSON o'qish mantiqi
    if not isinstance(data, dict):
        log.warning("%s: yo'llanma JSON o'qilmadi: %r", ZIYRAKAI_DISPLAY_NAME, _preview(raw))
        return None
    if data.get("is_referral") is False:
        return {"is_referral": False}

    out = {
        "is_referral": True,
        "patient_name": _truncate_field(data.get("patient_name"), 120),
        "sex": _referral_sex(data.get("sex")),
        "age": _referral_age(data.get("age"), data.get("referral_date")),
        "clinical_note": _truncate_field(data.get("clinical_note"), 200),
        # Sxema tashxisni so'raydi, lekin natijaga o'tkazilmasdi — «Ангиома?»
        # yo'llanmadan o'qilib, so'ng jimgina yo'qolardi.
        "clinical_dx": _truncate_field(data.get("clinical_dx"), 300),
        "specimen_site": _referral_site(data),
        "ward": _truncate_field(data.get("clinic") or data.get("address"), 80),
        "sample_id": re.sub(r"[^A-Za-z0-9]", "", str(data.get("referral_no") or ""))[:40],
        "referral_no": _truncate_field(data.get("referral_no"), 40),
        "referral_date": _truncate_field(data.get("referral_date"), 24),
        "doctor": _truncate_field(data.get("doctor"), 120),
        "phone": _truncate_field(data.get("phone"), 32),
        "procedure": _truncate_field(data.get("procedure"), 160),
        "address": _truncate_field(data.get("address"), 80),
        "clinic_name": _truncate_field(data.get("clinic"), 80),
    }
    filled = sum(1 for k, v in out.items() if k != "is_referral" and v)
    log.info(
        "%s: yo'llanma o'qildi — %s maydon (bemor=%r, joy=%r)",
        ZIYRAKAI_DISPLAY_NAME, filled, out["patient_name"][:30], out["specimen_site"],
    )
    return out


def do_analyze(pil_images, lab_type, custom_prompt=None, microscope_prefix=None,
               patient_context=None, clinical_images=None):
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
    _meter_begin()
    try:
        with analysis_lock:
            latest_analysis.update({"status": "tahlil_qilinmoqda", "lab_type": lab_type})

        imgs = [_resize_img(img) for img in pil_images]
        if not imgs:
            raise ValueError("Rasmlarni qayta ishlash muvaffaqiyatsiz")

        base = custom_prompt if custom_prompt and custom_prompt.strip() else LAB_PROMPTS.get(lab_type, "Bu mikroskopiya tasvirini O'zbek tilida batafsil tahlil qil.")
        prompt = _full_analysis_prompt(base, microscope_prefix, lab_type, patient_context)

        # Klinik rasmlar alohida ko'riladi: ular kesma emas, shuning uchun
        # morfologik ko'rikka aralashmaydi — faqat kontekst beradi.
        clinical_block = ""
        c_parts = []
        if clinical_images:
            c_parts = [
                {
                    "type": "image_url",
                    "image_url": {"url": _pil_to_data_url(_resize_img(im)), "detail": "low"},
                }
                for im in clinical_images
            ]
            clinical_block = _clinical_appearance(c_parts, patient_context)
        if clinical_block:
            prompt = clinical_block + "\n" + prompt

        # Klinik gipoteza bo'yicha atlasdan ma'lumotnoma rasm(lar)
        atlas_block, atlas_parts = _atlas_reference(_dx_terms_for_atlas(patient_context))

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

        trace = {}
        text = _openai_generate(
            content, lab_type, patient_context, atlas_parts, atlas_block, trace,
            clinical_block=clinical_block, clinical_parts=c_parts,
        )
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        # Keysni saqlash — xato tashxisni keyin ko'rib chiqish va algoritm
        # yaxshilangach qayta ishga tushirish uchun. Ixtiyoriy (CASE_ARCHIVE=1).
        try:
            from . import case_archive

            case_archive.save(
                imgs, text, trace.get("features"), trace.get("record"),
                patient_context, clinical_images, lab_type,
            )
        except Exception as e:
            log.warning("%s: keys arxivi: %s", ZIYRAKAI_DISPLAY_NAME, e)

        _publish_analysis({
            "text": text, "lines": lines,
            "timestamp": time.strftime('%H:%M:%S'),
            "status": "tayyor", "loading": False,
            "lab_type": lab_type,
            "img_count": len(imgs),
        })
        log.info("%s OK %s (%s rasm), %s belgi", ZIYRAKAI_DISPLAY_NAME, lab_type, len(imgs), len(text))
        _meter_end()

    except Exception as e:
        _meter_end()
        # Shifokorga inglizcha xom xato va billing havolasi emas,
        # tushunarli va harakatga yo'naltirilgan matn. Xomi jurnalda.
        log.exception("%s tahlil xatosi: %s", ZIYRAKAI_DISPLAY_NAME, e)
        _note_api_error(e)
        err = api_error_uz(e)
        _publish_analysis({
            "text": err, "lines": [err],
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
        # Shifokorga inglizcha xom xato va billing havolasi emas,
        # tushunarli va harakatga yo'naltirilgan matn. Xomi jurnalda.
        log.exception("%s video xatosi: %s", ZIYRAKAI_DISPLAY_NAME, e)
        _note_api_error(e)
        err = api_error_uz(e)
        _publish_analysis({
            "text": err, "lines": [err],
            "timestamp": time.strftime('%H:%M:%S'),
            "status": "xato", "loading": False,
        })
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

