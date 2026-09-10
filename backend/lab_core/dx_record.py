"""Tashxis — matn emas, tuzilgan yozuv.

MUAMMO (audit topgan). Ilgari har bosqich oddiy MATN chiqarardi, keyin
o'nga yaqin qadam o'sha matnni regex bilan qayta o'qib yamardi: sarlavhani
qidirish, nom qatorini topish, «Ishonch:» ni almashtirish, ogohlantirish
qo'shish. Ularning sakkiztasi «#### TASHXIS» sarlavhasi aynan shu shaklda
kelishiga bog'liq edi. Model bir marta «####» ni tushirib qoldirsa —
haqiqiy keysda shunday bo'ldi — hammasi JIMGINA o'tkazib yuborilardi:
yakuniy xulosa yo'q, foiz yo'q, dalil qoidalari tekshirilmagan. Hisobot
esa tashqaridan normal ko'rinardi.

YECHIM. Tashxis bitta tuzilgan yozuv (`DxRecord`) bo'ladi:
  — modeldan QAT'IY JSON olinadi (matn emas),
  — qo'riqchilar maydonlar ustida ishlaydi (regex emas),
  — hisobot matni eng oxirida shu yozuvdan CHIQARILADI.

Shunda sarlavha yo'qolmaydi, foiz tushib qolmaydi, va matnni yamash
umuman kerak bo'lmaydi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# Hisobot bo'limlari — bitta joyda, chunki endi ular koddan yoziladi
H_DX = "#### TASHXIS"
H_WHY = "#### NEGA SHU TASHXIS"
H_FACT = "#### FAKT (o'lchangan morfologiya)"
H_DESC = "#### MIKROSKOPIK TAVSIF (Akkerman patterni → yuqoridan pastga)"

FINAL_PREFIX = "YAKUNIY XULOSA: "

# Ishonchlilik chegaralari
CONFIDENCE_MIN = 5
CONFIDENCE_MAX = 95
CONFIDENCE_LOW = 55

CERTAIN_DEFINITE = "aniq"
CERTAIN_PROVISIONAL = "taxminiy"
CERTAIN_DESCRIPTIVE = "tavsifiy"


@dataclass
class Evidence:
    """Bitta dalil qatori: qaysi belgi ko'rindi va aynan nimasi bilan."""

    feature: str = ""
    seen: bool = True
    detail: str = ""

    def line(self):
        mark = "KO'RINDI" if self.seen else "YO'Q"
        base = f"{self.feature} — {mark}"
        return f"{base}: {self.detail}" if self.detail else base


@dataclass
class Differential:
    """Rad etilgan variant va uni nima rad etgani."""

    name: str = ""
    excluded_by: str = ""

    def line(self):
        return (f"{self.name} — {self.excluded_by}" if self.excluded_by
                else f"{self.name} — mezonlari to'liq emas")


@dataclass
class DxRecord:
    """Bitta keysning yakuniy tashxisi — hisobotning yagona manbasi."""

    name: str = ""
    certainty: str = CERTAIN_DEFINITE
    malignant: bool = False
    organ: str = ""
    layer: str = ""
    grade: str = ""
    evidence: list = field(default_factory=list)
    differentials: list = field(default_factory=list)
    facts: list = field(default_factory=list)       # "Mitoz: 0/10 HPF"
    confidence: int = 0
    confidence_why: str = ""
    caution: str = ""
    notes: list = field(default_factory=list)       # qo'riqchi izohlari (jurnal)
    confidence_cap: int = 0                         # qo'riqchi qo'ygan shift (0 — yo'q)
    criteria: dict = field(default_factory=dict)    # mezon jadvali bahosi (arxiv uchun)
    clinical: str = ""                              # tana suratidan klinik ko'rinish
    discordance: str = ""                           # klinik-gistologik nomuvofiqlik
    gestalt_agreement: str = ""                     # umumiy ko'rinish bilan kelishuv
    gestalt_bonus: int = 0
    survey_line: str = ""                           # kadr-kadr qidiruv sanog'i
    description: dict = field(default_factory=dict) # tizimli tavsif (protokol tartibida)

    # ── Yordamchilar ────────────────────────────────────────────────────
    def display_name(self):
        n = " ".join(str(self.name or "").split()).strip(" .;,")
        if not n:
            return ""
        if self.certainty == CERTAIN_PROVISIONAL and "taxminiy" not in n.lower():
            n += " — taxminiy"
        return n

    def site(self):
        parts = [p for p in (self.organ, self.layer) if str(p or "").strip()]
        return ", ".join(" ".join(str(p).split()) for p in parts)

    def to_dict(self):
        return asdict(self)

    # ── Hisobot matni ───────────────────────────────────────────────────
    def render(self):
        """Hisobot matni — shu yozuvdan chiqariladi, qayta o'qilmaydi."""
        out = [H_DX, FINAL_PREFIX + (self.display_name() or "aniqlanmagan morfologiya")]

        meta = f"Ishonchlilik: {self.confidence}%"
        site = self.site()
        if self.grade and self.grade.lower() not in _EMPTY_GRADE:
            meta += f"  ·  Daraja: {self.grade}"
        if site:
            meta += f"  ·  {site}"
        out.append(meta)
        if self.caution:
            out.append(self.caution)
        if self.clinical:
            # Shifokor klinik surat hisobga olinganini shu yerda ko'radi
            out.append("Klinik ko'rinish (tana surati): " + self.clinical)
        if self.discordance:
            out.append(self.discordance)
        if self.gestalt_agreement and self.gestalt_agreement != "mos":
            out.append("Umumiy ko'rinish bilan " + self.gestalt_agreement)
        if self.survey_line:
            out.append(self.survey_line)

        out.append("")
        out.append(H_WHY)
        seen = [e for e in self.evidence if e.seen]
        for e in seen[:10]:
            out.append(e.line())
        if not seen:
            out.append(
                "Kesmada tashxisni tasdiqlovchi aniq morfologik belgi ajratilmadi — "
                "quyidagi o'lchovlar shuni ko'rsatadi."
            )
        for d in self.differentials[:5]:
            out.append("Rad etildi: " + d.line())

        # Patolog protokoli: pattern → rog' qavat → … → pigment/atipiya. Shifokor
        # o'zi o'rgangan tartibda o'qiydi va nima ko'rilganini tekshira oladi.
        from .dx_criteria import description_lines
        desc = description_lines(self.description)
        if desc:
            out.append("")
            out.append(H_DESC)
            out.extend(desc)

        out.append("")
        out.append(H_FACT)
        for f in self.facts[:16]:
            out.append(f)
        if not self.facts:
            out.append("O'lchanadigan ko'rsatkich ajratilmadi.")

        return "\n".join(out).rstrip() + "\n"


_EMPTY_GRADE = ("qo'llanilmaydi", "qollanilmaydi", "yo'q", "yoq", "noaniq", "-", "—", "")


# ─── Modeldan kelgan JSON → DxRecord ────────────────────────────────────────

_DECISION_SCHEMA = (
    '{"diagnosis": "kasallik nomi — bitta, aniq", '
    '"malignant": false, '
    '"organ": "teri", "layer": "epidermis|papillyar derma|retikulyar derma|gipoderma", '
    '"grade": "daraja yoki qo\'llanilmaydi", '
    '"evidence": [{"feature": "belgi nomi", "detail": "kesmada aynan nima ko\'rindi — son yoki joy bilan"}], '
    '"differentials": [{"name": "muqobil nom", "excluded_by": "qaysi belgi yo\'qligi uni rad etadi"}], '
    '"facts": ["Mitoz: 0/10 HPF", "Chuqurlik: papillyar derma", "Chekka: baholab bo\'lmaydi"]}'
)

DECISION_SYSTEM = (
    "Siz — dermatopatologiya bo'yicha konsultantsiz. Sizga bitta kesmadan olingan "
    "O'LCHANGAN morfologik belgilar, kitob mezonlari va differensial tekshiruv natijasi "
    "beriladi.\n"
    "Vazifa: yakuniy tashxisni QAT'IY JSON shaklida qaytarish. Matn, sarlavha, izoh yo'q.\n"
    "Qoidalar:\n"
    "— «diagnosis» — BITTA nom. Ro'yxat, «yoki», «ehtimol» yozilmaydi. "
    "Nomsiz javob yaroqsiz: dalil kam bo'lsa ham eng ehtimolli nomni yozing.\n"
    "— «evidence» — faqat berilgan belgilar ro'yxatida BOR bo'lgan belgilar. "
    "Har birida «detail» aniq bo'lsin: son, o'lcham yoki joy. Umumiy gap yozmang.\n"
    "— Belgilar ro'yxatida YO'Q narsani dalil sifatida yozish TAQIQLANADI.\n"
    "— «differentials» — rad etilgan variantlar va ularni AYNAN nima rad etgani.\n"
    "— «facts» — o'lchangan sonlar (mitoz, chuqurlik, qalinlik, chekka). "
    "O'lchanmagan bo'lsa «baholab bo'lmaydi» deb yozing, son o'ylab topmang.\n"
    "— Foiz, ehtimollik, «ishonch» YOZILMAYDI — ular dastur tomonidan hisoblanadi.\n"
    "Javob faqat shu JSON:\n" + _DECISION_SCHEMA
)


def _clean(s, limit=300):
    t = " ".join(str(s or "").split())
    return t[:limit].strip()


def from_json(data):
    """Model qaytargan JSON dan DxRecord. Yaroqsiz bo'lsa None."""
    if not isinstance(data, dict):
        return None
    name = _clean(data.get("diagnosis"), 160)
    if not name or name.lower() in ("noaniq", "aniqlanmadi", "yo'q", "-"):
        return None

    ev = []
    for it in (data.get("evidence") or [])[:12]:
        if not isinstance(it, dict):
            continue
        f = _clean(it.get("feature"), 90)
        if f:
            ev.append(Evidence(feature=f, seen=True, detail=_clean(it.get("detail"), 260)))

    diffs = []
    for it in (data.get("differentials") or [])[:6]:
        if not isinstance(it, dict):
            continue
        n = _clean(it.get("name"), 90)
        if n:
            diffs.append(Differential(name=n, excluded_by=_clean(it.get("excluded_by"), 200)))

    facts = []
    for it in (data.get("facts") or [])[:16]:
        t = _clean(it, 180)
        if t:
            facts.append(t)

    return DxRecord(
        name=name,
        malignant=bool(data.get("malignant")),
        organ=_clean(data.get("organ"), 60),
        layer=_clean(data.get("layer"), 80),
        grade=_clean(data.get("grade"), 60),
        evidence=ev,
        differentials=diffs,
        facts=facts,
    )


# ─── Qo'riqchilar — endi matn ustida emas, maydonlar ustida ─────────────────

_MALIGN_RE = re.compile(
    r"carcinom|karsinom|adenokarsinom|sarkom|sarcom|melanom|"
    r"\brcc\b|renal cell|yomon\s+o'sma|yomon\s+osma|"
    r"invaziv\s+scc|invasive\s+squamous|\bmalignant\b|\bmalign\b",
    re.I,
)


def looks_malignant(rec):
    """Nom xavfli o'smani bildiradimi — bayroqqa emas, nomga ham qaraymiz."""
    return bool(rec.malignant or _MALIGN_RE.search(rec.name or ""))


def apply_guards(rec, features, required_map, descriptive_name=""):
    """Yozuvni dalilga solishtirib tekshirish.

    Matnni qayta yozish o'rniga maydonlar to'g'rilanadi — shuning uchun
    hech qanday qadam «jimgina o'tkazib yuborilishi» mumkin emas.
    """
    if rec is None:
        return None

    # 1) Dalil qatorlari haqiqatan ko'rilgan belgilarga tayanadimi
    before = len(rec.evidence)
    rec.evidence = [e for e in rec.evidence if e.detail or e.feature]
    if len(rec.evidence) != before:
        rec.notes.append("dalilsiz qatorlar olib tashlandi")

    # 2) Mezon jadvali — professor tekshiruvi. Nom jadvalda bo'lsa, shu hal
    #    qiladi: majburiy belgilardan birortasi yo'q → nom tayanchsiz;
    #    rad etuvchi belgi bor → nom taxminiy, foiz shiftlanadi.
    from . import dx_criteria as dxc

    ev = dxc.check_name(rec.name, features)
    if ev is not None:
        rec.criteria = {
            "entity": ev["name"], "score": ev["score"],
            "essential": f"{ev['essential_hits']}/{ev['essential_total']}",
            "excluding_present": list(ev["excluding_present"]),
        }
        if ev["essential_total"] and ev["essential_hits"] == 0:
            absent = ", ".join(dxc.feature_label(s) for s in ev["essential_absent"][:3])
            if descriptive_name:
                rec.notes.append(
                    f"«{rec.name}» mezonga zid — birorta majburiy belgi yo'q ({absent}); "
                    "tavsifiy nomga almashtirildi"
                )
                rec.name = descriptive_name
                rec.certainty = CERTAIN_DESCRIPTIVE
                rec.malignant = False
            else:
                rec.notes.append(f"«{rec.name}» majburiy belgilari yo'q: {absent}")
                rec.certainty = CERTAIN_PROVISIONAL
                rec.confidence_cap = 40
        elif ev["excluding_present"]:
            excl = ", ".join(dxc.feature_label(s) for s in ev["excluding_present"][:2])
            rec.notes.append(f"«{rec.name}» uchun rad etuvchi belgi bor: {excl} — taxminiy")
            rec.certainty = CERTAIN_PROVISIONAL
            rec.confidence_cap = 50
        elif not ev["qualifies"]:
            rec.notes.append(
                f"«{rec.name}» majburiy belgilar yetarli emas "
                f"({ev['essential_hits']}/{ev['essential_total']}, kerak {ev['essential_need']})"
            )
            rec.certainty = CERTAIN_PROVISIONAL
            rec.confidence_cap = 60
        missing = []
    else:
        # Jadvalda yo'q nom — eski qisqa ro'yxat bilan tekshiriladi
        missing = _missing_required(rec.name, features, required_map)
    if missing:
        if descriptive_name:
            rec.notes.append(
                f"«{rec.name}» ko'rikka zid ({', '.join(missing[:3])}) — tavsifiy nomga almashtirildi"
            )
            rec.name = descriptive_name
            rec.certainty = CERTAIN_DESCRIPTIVE
            rec.malignant = False
        else:
            rec.notes.append(f"«{rec.name}» majburiy belgilari yo'q: {', '.join(missing[:3])}")
            rec.certainty = CERTAIN_PROVISIONAL

    # 3) Dalil juda kam — nom saqlanadi, lekin taxminiy
    if len([e for e in rec.evidence if e.seen]) < 2 and rec.certainty == CERTAIN_DEFINITE:
        rec.certainty = CERTAIN_PROVISIONAL
        rec.notes.append("ikkitadan kam dalil — taxminiy")

    return rec


def _missing_required(name, features, required_map):
    """Nom talab qiladigan belgilardan ko'rikda BORLARI bormi."""
    if not name or not isinstance(features, dict) or not required_map:
        return []
    low = re.sub(r"[^a-zа-яё ]+", " ", str(name).lower())
    for key, needed in (required_map or {}).items():
        if key not in low:
            continue
        seen = set()
        for group in ("epidermis", "junction", "dermis", "glandular", "cytology"):
            sub = features.get(group)
            if isinstance(sub, dict):
                seen.update(k for k, v in sub.items() if v is True)
        if not (set(needed) & seen):
            return list(needed)
    return []


def set_confidence(rec, pct, why="", low_threshold=CONFIDENCE_LOW):
    """Foizni yozuvga qo'yish va kerak bo'lsa yagona xavfsizlik qatorini berish."""
    if rec is None:
        return None
    rec.confidence = int(max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, pct)))
    rec.confidence_why = why
    # Xavfli o'sma past ishonchlilik bilan chiqsa — bu qator qoladi.
    # Rakni tasdiqlanmagan holda davolashga o'tib ketish qaytarib bo'lmaydi.
    if rec.confidence < low_threshold and looks_malignant(rec):
        rec.caution = (
            "Xavfli o'sma shu hisobot bilan TASDIQLANMAYDI — davolash qarori "
            "patolog ko'rigi va IHC dan keyin qabul qilinadi."
        )
    else:
        rec.caution = ""
    return rec
