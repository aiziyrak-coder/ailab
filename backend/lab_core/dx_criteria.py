"""Dermatopatologiya mezon jadvali — «professor ko'zi» kod shaklida.

NIMA UCHUN. Patolog tashxisni «rasmga o'xshaydi» deb emas, MEZON bo'yicha
qo'yadi: har nozologiyaning majburiy belgilari, qo'llab-quvvatlovchi belgilari
va uni RAD ETUVCHI belgilari bor. Ilgari dasturda buning o'rniga 39 qatorli
«talab qilinadigan belgi» ro'yxati bor edi (har birida 1–3 belgi), ko'rik
ro'yxati esa o'smalarga moslashgan bo'lib, yallig'lanish dermatozlarini —
atlasning asosiy qismini — deyarli ajrata olmasdi: spongioz, interfeys,
granuloma… va tamom. Munro mikroabsessi, akantoliz, subepidermal pufak,
nekrobioz, leykotsitoklaziya, epidermotropizm — so'ralmasdi. So'ralmagan
narsani model ko'rmaydi.

Bu modul UCHTA narsani bir manbadan beradi:
  1. OBSERVE_TEMPLATE  — ko'rik JSON shakli (kengaytirilgan, ~130 belgi);
  2. FEATURE_UZ        — belgilar nomi o'zbekchada;
  3. CRITERIA          — ~70 nozologiya uchun mezonlar.

va ular ustida DETERMINISTIK hakamlik: `rank_candidates(features)` ko'rilgan
belgilardan qaysi tashxislar mos kelishini ballab beradi, `check_name`
esa modelning tanlovini mezonga solishtiradi. Model o'ylab topgan nomni
mezon rad etsa — nom o'tmaydi.

Mezonlar Weedon, Ackerman pattern tahlili va klinika kutubxonasidagi
(Дерматология «до и после», АТЛАС, экзематозные дерматозы) tavsiflarga
asoslangan. Bu jadval — tibbiy hujjat: o'zgartirganda manbaga qarang.
"""

from __future__ import annotations

import json
import math
import re

# ─── 1. Ko'rik shakli ────────────────────────────────────────────────────────
# Har guruh: {kalit: o'zbekcha nom}. Kalit — JSON maydoni, nom — hisobotda.

EPIDERMIS = {
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
    # ── yangi: yallig'lanish va infeksion dermatozlar uchun ──
    "regular_elongated_rete": "rete tekis va bir xil cho'zilgan (psoriaziform)",
    "suprapapillary_thinning": "suprapapillyar yupqalashuv",
    "hypogranulosis": "donador qavat yo'qolgan",
    "hypergranulosis": "donador qavat qalinlashgan (ponasimon)",
    "munro_microabscess": "Munro mikroabsessi (parakeratozda neytrofillar)",
    "spongiform_pustule": "Kogoj spongiform pustulasi",
    "sawtooth_rete": "arra tishi shaklidagi rete",
    "acantholysis": "akantoliz",
    "dyskeratosis": "diskeratoz (corps ronds / grains)",
    "intraepidermal_vesicle": "intraepidermal pufakcha",
    "epidermal_atrophy": "epidermis atrofiyasi",
    "follicular_plugging": "follikulyar tiqin",
    "cornoid_lamella": "kornoid lamella",
    "epidermotropism": "epidermotropizm (epidermisda atipik limfotsitlar)",
    "pautrier_microabscess": "Potrie mikroabsessi",
    "viral_cytopathic_change": "virusli sitopatik o'zgarish (ko'p yadroli, shishasimon)",
    "molluscum_bodies": "mollyusk tanachalari",
    "fungal_hyphae_corneum": "shox qavatda zamburug' gifalari",
    "mite_or_parasite": "kana yoki parazit",
    "keratin_filled_crater": "keratin bilan to'lgan krater",
    "glassy_keratinocytes": "shishasimon keratinotsitlar",
    "necrotic_keratinocytes": "yakka nekrotik keratinotsitlar",
    "confluent_epidermal_necrosis": "epidermisning tutash nekrozi",
    "neutrophils_in_corneum": "shox qavatda neytrofillar",
    "epidermal_collarette": "epidermal kollaret (polip chekkasida epidermis qayrilgan)",
    "polypoid_exophytic": "polipoid / oyoqchali ekzofit tuzilma",
}

JUNCTION = {
    "interface_damage": "interfeys shikasti",
    "band_like_infiltrate": "lentasimon infiltrat",
    "melanocyte_nests": "melanotsitar uyalar",
    "single_melanocyte_proliferation": "yakka melanotsit proliferatsiyasi",
    "pagetoid_spread": "pagetoid tarqalish",
    "clefting_retraction": "stroma retraksiyasi (kleft)",
    "peripheral_palisading": "periferik palisad",
    # ── yangi ──
    "vacuolar_change": "bazal qavat vakuolizatsiyasi",
    "civatte_bodies": "Sivatt tanachalari (apoptotik keratinotsitlar)",
    "subepidermal_blister": "subepidermal pufak",
    "basement_membrane_thickening": "bazal membrana qalinlashgan",
    "lamellar_fibroplasia": "lamellyar fibroplaziya",
    "bridging_nests": "ko'prik hosil qiluvchi uyalar",
    "kamino_bodies": "Kamino tanachalari",
    "nests_regular": "bir xil o'lchamli tartibli uyalar",
    "spindle_epithelioid_melanocytes": "duksimon/epitelioid melanotsitlar",
    "asymmetric_melanocytic_growth": "assimetrik melanotsitar o'sish",
}

DERMIS = {
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
    # ── yangi ──
    "superficial_perivascular_infiltrate": "yuzaki perivaskulyar infiltrat",
    "deep_perivascular_infiltrate": "chuqur perivaskulyar infiltrat",
    "papillary_dermal_edema": "papillyar derma shishi",
    "dilated_tortuous_capillaries": "kengaygan, buralgan papillyar kapillyarlar",
    "necrobiosis": "nekrobioz",
    "palisading_histiocytes": "palisad hosil qilgan histiotsitlar",
    "naked_granulomas": "«yalang'och» (sarkoidal) granulomalar",
    "caseous_necrosis": "kazeoz nekroz",
    "leukocytoclasia": "leykotsitoklaziya (yadro changi)",
    "fibrinoid_vessel_necrosis": "tomir devorining fibrinoid nekrozi",
    "extravasated_erythrocytes": "ekstravazatsiyalangan eritrotsitlar",
    "papillary_dermal_homogenisation": "papillyar derma gomogenizatsiyasi",
    "dermal_sclerosis": "dermal skleroz (qalin, zich kollagen)",
    "adnexal_atrophy": "adneks atrofiyasi / yo'qolishi",
    "mast_cell_infiltrate": "mast hujayralari infiltrati",
    "foam_cells": "ko'pikli hujayralar",
    "amyloid_deposits": "amiloid to'plamlari",
    "atypical_lymphocytes": "atipik limfotsitlar",
    "lymphoid_follicles": "limfoid follikulalar",
    "septal_panniculitis": "septal pannikulit",
    "lobular_panniculitis": "lobulyar pannikulit",
    "keloidal_collagen": "keloid kollageni (shishasimon tutamlar)",
    "shadow_cells": "soya hujayralar",
    "cyst_wall_granular_layer": "donador qavatli kista devori",
    "mucinous_stroma": "musinoz stroma",
    "lobular_capillary_proliferation": "lobulyar kapillyar proliferatsiya",
    "slit_like_vascular_spaces": "tirqishsimon tomir bo'shliqlari",
    "promontory_sign": "promontoriy belgisi",
    "wavy_spindle_neural": "to'lqinsimon nerv tipidagi duksimon hujayralar",
    "mature_adipocytes": "yetuk adipotsitlar",
    "interstitial_histiocytes": "interstitsial histiotsitlar",
    "dermal_neutrophil_rich": "neytrofilga boy dermal infiltrat",
    "dermal_lymphocyte_rich_pattern": "limfotsitar dermal infiltrat",
}

GLANDULAR = {
    "glands_present": "bezlar",
    "cribriform": "cribriform",
    "papillary_fronds": "papillyar shoxlar",
    "fibrovascular_cores": "fibrovaskulyar o'zak",
    "goblet_cells": "goblet hujayralar",
    "colloid": "kolloid",
    "myoepithelial_layer": "myoepitelial qavat",
}

CYTOLOGY_BOOL = {
    "atypical_mitoses": "atipik mitozlar",
    "prominent_nucleoli": "yirik yadrocha",
    "clear_cytoplasm": "tiniq sitoplazma",
    "keratin_pearls": "keratin marvaridlari",
    "maturation_with_depth": "chuqurlik bo'yicha maturatsiya",
    "cerebriform_nuclei": "serebriform yadrolar",
    "deep_mitoses": "chuqur qismda mitozlar",
}

SPECIAL = {
    "perineural": "perinevral tarqalish",
    "lymphovascular": "limfovaskulyar invaziya",
    "adnexal_involvement": "adneks jalb bo'lgan",
    "pigment_incontinence": "pigment inkontinensiyasi",
    "foreign_material": "begona material",
    "organisms_suspected": "mikroorganizm shubhasi",
    "crush_or_cautery_artifact": "ezilish / kuydirish artefakti",
}

FEATURE_UZ = {}
for _g in (EPIDERMIS, JUNCTION, DERMIS, GLANDULAR, CYTOLOGY_BOOL, SPECIAL):
    FEATURE_UZ.update(_g)

BOOL_GROUPS = ("epidermis", "junction", "dermis", "glandular", "cytology", "special",
               "layers_present")


def _falses(d):
    return {k: False for k in d}


OBSERVE_TEMPLATE = {
    "not_tissue": False,
    "organ": "teri|sut bezi|qovuq|prostata|qalqonsimon|oshqozon-ichak|endometrium|buyrak|o'pka|boshqa",
    "sample_quality": "yaxshi|o'rtacha|past",
    "magnification": "kichik|o'rta|yuqori",
    "layers_present": {"epidermis": False, "dermis": False, "subcutis": False, "adnexa": False},
    "epidermis": _falses(EPIDERMIS),
    "junction": _falses(JUNCTION),
    "dermis": _falses(DERMIS),
    "glandular": _falses(GLANDULAR),
    "cytology": {
        "pleomorphism": "yo'q|yengil|o'rta|kuchli",
        "nuclear_grade": "1|2|3|noaniq",
        "mitoses_10hpf": "0|1-2|3-10|>10|noaniq",
        **_falses(CYTOLOGY_BOOL),
    },
    "invasion": "yo'q|shubhali|bor",
    "invasion_evidence_uz": "bir jumla — nimaga asoslanib",
    "depth": {
        "deepest_level": "epidermis|papillyar derma|retikulyar derma|gipoderma|noaniq",
        "thickness_mm": "taxminiy son yoki noaniq",
        "ulceration": False,
    },
    "margins": {"assessable": False, "involved": "erkin|tegib turadi|noaniq"},
    "special": _falses(SPECIAL),
    "symmetry": "simmetrik|assimetrik|noaniq",
    "border": "itaruvchi|infiltrativ|noaniq",
    "inflammation": {
        "type": "yo'q|limfotsitar|neytrofil|granulomatoz|eozinofil|aralash",
        "density": "yo'q|yengil|o'rta|zich",
        "distribution": "perivaskulyar|lentasimon|diffuz|interstitsial|nodulyar|yo'q",
    },
    "not_assessable_uz": ["baholab bo'lmagan narsalar va sababi — 0-3 ta"],
    "observations_uz": ["ko'rgan narsangiz — 4-8 qisqa jumla"],
    "dominant_pattern": "bir ibora, ingliz tilida: masalan 'psoriasiform', 'spongiotic', "
                        "'lichenoid interface', 'nodular basaloid', 'diffuse dermal'",
}


# ─── Tizimli tavsif protokoli (patolog bergan tartib) ───────────────────────
# Avval yallig'lanish PATTERNI (B. Akkerman), so'ng rog' qavatdan bazal
# qavatgacha, epidermo-dermal chegara, so'rg'ichli derma (amorf modda,
# kollagen, musin, tomirlar, perivaskulyar va interstitsial infiltrat va
# tarkibi), to'rsimon derma (shu parametrlar + qo'shimchalar va ular
# atrofidagi infiltrat), oxirida pigment, atipik hujayralar, pleomorfizm.
ACKERMAN_PATTERNS = (
    "yuzaki perivaskulyar dermatit",
    "yuzaki va chuqur perivaskulyar dermatit",
    "vaskulit",
    "nodulyar va diffuz dermatit",
    "intraepidermal pufakli/pustulali dermatit",
    "subepidermal pufakli dermatit",
    "follikulit va perifollikulit",
    "fibrozlovchi dermatit",
    "pannikulit",
    "yallig'lanish patterni qo'llanilmaydi (neoplastik / o'sma)",
)

# (kalit, hisobotdagi sarlavha, modelga ko'rsatma)
DESCRIPTION_FIELDS = (
    ("inflammatory_pattern", "Yallig'lanish patterni (Akkerman)",
     "one of: " + " | ".join(ACKERMAN_PATTERNS)),
    ("stratum_corneum", "Rog' qavat",
     "orthokeratosis/parakeratosis, compact or basket-weave, thickness, neutrophils, serum, crust"),
    ("granular_layer", "Donador qavat", "present/absent, thickened (wedge) or thinned, keratohyalin"),
    ("spinous_layer", "Tikanli qavat",
     "acanthosis (regular/irregular/psoriasiform), spongiosis, acantholysis, dyskeratosis, "
     "koilocytes, vesicles, exocytosis, atypia"),
    ("basal_layer", "Bazal qavat",
     "intact/vacuolar change, pigment, melanocyte number and nests, basaloid proliferation"),
    ("dej", "Epidermo-dermal chegara",
     "interface change, band-like infiltrate, subepidermal cleft/blister, basement membrane thickening, Civatte bodies"),
    ("papillary_matrix", "So'rg'ichli derma — moddasi",
     "amorphous material (yes/no, what), collagen fibres (normal/oedematous/homogenised/sclerotic), "
     "mucin between fibres (yes/no)"),
    ("papillary_vessels", "So'rg'ichli derma — tomirlar",
     "dilated/tortuous, endothelial swelling, fibrinoid necrosis, extravasated red cells, proliferation"),
    ("papillary_infiltrate", "So'rg'ichli derma — infiltrat",
     "perivascular and/or interstitial; density; composition (lymphocytes, neutrophils, eosinophils, "
     "plasma cells, histiocytes, mast cells, atypical cells)"),
    ("reticular_matrix", "To'rsimon derma — moddasi",
     "collagen (normal/thick/sclerotic/necrobiotic), mucin, amorphous deposits, elastosis, fibrosis"),
    ("reticular_vessels_infiltrate", "To'rsimon derma — tomirlar va infiltrat",
     "deep perivascular/interstitial/nodular/diffuse infiltrate; composition; vasculitis; granulomas"),
    ("adnexa", "Teri qo'shimchalari",
     "hair follicles, sebaceous and sweat glands: present/atrophic/plugged; periadnexal infiltrate and its composition"),
    ("subcutis", "Teri osti yog' qavati", "present? septal/lobular panniculitis; if not in section say 'ko'rinmaydi'"),
    ("pigment", "Pigment", "melanin: where (basal, melanophages, incontinence), amount; hemosiderin"),
    ("atypia_pleomorphism", "Atipik hujayralar va pleomorfizm",
     "atypical cells (where, what kind), nuclear pleomorphism grade, mitoses (typical/atypical, per 10 HPF)"),
)

OBSERVE_TEMPLATE["description"] = {
    k: (f"{hint}" if k == "inflammatory_pattern" else "1–2 short Uzbek sentences: " + hint)
    for k, _lab, hint in DESCRIPTION_FIELDS
}


def description_lines(desc):
    """Hisobot uchun tizimli tavsif — protokol tartibida (bo'sh maydonlar tashlanadi)."""
    if not isinstance(desc, dict):
        return []
    out = []
    for key, label, _hint in DESCRIPTION_FIELDS:
        v = " ".join(str(desc.get(key) or "").split())
        if not v or v.lower() in ("noaniq", "-", "—", "yo'q", "n/a"):
            continue
        if v.startswith("1–2 short") or v.startswith("one of:"):
            continue          # to'ldirilmagan shablon matni
        out.append(f"{label}: {v[:320]}")
    return out


def observe_schema_json():
    return json.dumps(OBSERVE_TEMPLATE, ensure_ascii=False)


# ─── 2. Mezonlar ─────────────────────────────────────────────────────────────
# Har yozuv:
#   name       — hisobotdagi nom (o'zbek/lotin)
#   aliases    — nomni tanib olish uchun (kichik harf, o'zaklar; uz/ru/en)
#   pattern    — Ackerman patterni (hakamlik va ma'lumot uchun)
#   malignant  — xavfli o'sma
#   essential  — majburiy belgilar; kamida `min_essential` tasi BOR bo'lishi shart
#   supporting — qo'llab-quvvatlovchi
#   excluding  — BOR bo'lsa tashxisni RAD etadi
#
# Belgi yozuvi: oddiy kalit («spongiosis») yoki skalyar shart:
#   "pleomorphism>=o'rta", "mitoses_10hpf>=3-10", "invasion=bor",
#   "inflammation.type=granulomatoz", "inflammation.distribution=lentasimon",
#   "depth.deepest_level=gipoderma", "border=infiltrativ", "symmetry=assimetrik"

_SCALE = {
    "pleomorphism": ["yo'q", "yengil", "o'rta", "kuchli"],
    "mitoses_10hpf": ["0", "1-2", "3-10", ">10"],
    "nuclear_grade": ["1", "2", "3"],
    "invasion": ["yo'q", "shubhali", "bor"],
    "inflammation.density": ["yo'q", "yengil", "o'rta", "zich"],
}


# Klinik ko'rinish turi. Auditda topilgan xato: 5 yoshli bolaning bo'ynidagi
# yakka qizil oyoqchali tugun «Darier kasalligi» deb chiqdi — tarqoq irsiy
# dermatoz, hech qachon yakka tugun bo'lmaydi. Jadval bu farqni bilmasdi.
#   "solitary" — yakka o'choq/tugun/o'sma (biopsiya odatda shu uchun olinadi)
#   "eruption" — tarqoq toshma (ko'p o'choqli dermatoz)
#   "either"   — ikkalasi ham bo'lishi mumkin
_ERUPTION_PATTERNS = {"psoriaziform", "spongiotik", "lixenoid", "interfeys", "pufakli",
                      "vaskulit", "vaskulyar", "pannikulit", "neytrofil", "sklerozlovchi",
                      "follikulyar"}
_SOLITARY_PATTERNS = {"epidermal", "melanotsitar", "dermal", "vaskulyar o'sma", "adneksal",
                      "fibrozlovchi"}


def E(name, aliases, pattern, essential, supporting=(), excluding=(), malignant=False,
      min_essential=None, require_any=(), presentation=None):
    """require_any — FARQLOVCHI belgilar: kamida bittasi bo'lmasa nozologiya
    nomzod bo'lolmaydi. Mezonlari boshqa kasallikning kichik to'plami bo'lgan
    tashxislar uchun (masalan lixenoid dori reaksiyasi ⊂ lichen planus):
    patolog farqlovchi belgisiz kichik to'plamni tanlamaydi."""
    return {
        "name": name, "aliases": tuple(aliases), "pattern": pattern,
        "malignant": malignant, "essential": tuple(essential),
        "supporting": tuple(supporting), "excluding": tuple(excluding),
        "min_essential": min_essential, "require_any": tuple(require_any),
        "presentation": presentation or (
            "eruption" if pattern in _ERUPTION_PATTERNS
            else "solitary" if pattern in _SOLITARY_PATTERNS else "either"),
    }


CRITERIA = [
    # ── Psoriaziform ──
    E("Psoriasis vulgaris", ["psoriaz", "psoriasis", "псориаз"], "psoriaziform",
      ["regular_elongated_rete", "parakeratosis", "munro_microabscess", "suprapapillary_thinning",
       "hypogranulosis"],
      ["dilated_tortuous_capillaries", "neutrophils_in_corneum", "spongiform_pustule",
       "acanthosis", "superficial_perivascular_infiltrate"],
      ["acantholysis", "subepidermal_blister", "granuloma", "pagetoid_spread"],
      min_essential=3),
    E("Pustulyoz psoriaz", ["pustul", "пустулез"], "psoriaziform",
      ["spongiform_pustule", "neutrophils_in_corneum", "parakeratosis"],
      ["regular_elongated_rete", "munro_microabscess", "dilated_tortuous_capillaries"],
      ["acantholysis", "granuloma"], min_essential=2),
    E("Lichen simplex chronicus (Vidal)", ["lichen simplex", "vidal", "видал", "neyrodermit",
      "нейродермит", "oddiy lixen", "prurigo", "пруриго"], "psoriaziform",
      ["acanthosis", "hypergranulosis", "hyperkeratosis"],
      ["papillary_dermal_homogenisation", "fibrosis", "regular_elongated_rete",
       "superficial_perivascular_infiltrate"],
      ["munro_microabscess", "hypogranulosis", "spongiform_pustule", "pagetoid_spread"],
      min_essential=3),
    E("Pityriasis rubra pilaris", ["rubra pilaris", "devergie", "девержи", "волосяной лишай"],
      "psoriaziform",
      ["acanthosis", "follicular_plugging", "parakeratosis"],
      ["hypergranulosis", "regular_elongated_rete"],
      ["munro_microabscess", "spongiform_pustule"], min_essential=2),

    # ── Spongiotik ──
    E("Ekzema (spongiotik dermatit)", ["ekzema", "eczema", "экзем", "spongiotik", "spongiotic",
      "dermatit", "дерматит"], "spongiotik",
      ["spongiosis"],
      ["intraepidermal_vesicle", "superficial_perivascular_infiltrate", "eosinophils",
       "parakeratosis", "acanthosis", "papillary_dermal_edema"],
      ["acantholysis", "granuloma", "pagetoid_spread", "atypical_lymphocytes",
       "subepidermal_blister", "munro_microabscess", "spongiform_pustule"]),
    E("Allergik kontakt dermatit", ["allergik kontakt", "allergic contact", "аллергический контактный"],
      "spongiotik",
      ["spongiosis", "eosinophils"],
      ["intraepidermal_vesicle", "superficial_perivascular_infiltrate", "papillary_dermal_edema"],
      ["acantholysis", "granuloma", "atypical_lymphocytes", "munro_microabscess",
       "spongiform_pustule"], min_essential=2,
      require_any=["eosinophils"]),
    E("Pityriasis rosea", ["rosea", "розовый лишай", "жибер", "pushti temiratki"], "spongiotik",
      ["spongiosis", "parakeratosis"],
      ["extravasated_erythrocytes", "superficial_perivascular_infiltrate", "papillary_dermal_edema"],
      ["acantholysis", "granuloma", "munro_microabscess"], min_essential=2),
    E("Seboreyali dermatit", ["seboreyali dermatit", "seboreik dermatit", "seborrheic dermatitis",
      "себорейный дерматит", "seboreya"], "spongiotik",
      ["spongiosis", "parakeratosis"],
      ["neutrophils_in_corneum", "acanthosis", "superficial_perivascular_infiltrate"],
      ["acantholysis", "granuloma", "munro_microabscess"], min_essential=2),

    # ── Lixenoid / interfeys ──
    E("Lichen planus (qizil yassi temiratki)", ["lichen planus", "lixen planus", "yassi lixen",
      "qizil yassi", "красный плоский", "плоский лихен", "лихен плоский", "lixenoid",
      "lichenoid"], "lixenoid",
      ["band_like_infiltrate", "vacuolar_change", "civatte_bodies", "sawtooth_rete",
       "hypergranulosis"],
      ["hyperkeratosis", "pigment_incontinence", "acanthosis"],
      ["parakeratosis", "spongiosis", "eosinophils", "plasma_cells", "atypical_lymphocytes",
       "pagetoid_spread"],
      min_essential=3),
    E("Lixenoid dori reaksiyasi", ["dori reaksiya", "drug", "токсидермия", "toksidermiya",
      "лекарственн"], "lixenoid",
      ["band_like_infiltrate", "vacuolar_change", "civatte_bodies"],
      ["eosinophils", "parakeratosis", "plasma_cells", "deep_perivascular_infiltrate"],
      ["granuloma", "acantholysis"], min_essential=2,
      require_any=["eosinophils", "parakeratosis", "plasma_cells", "deep_perivascular_infiltrate"]),
    E("Erythema multiforme", ["multiforme", "многоформная", "ko'p shaklli eritema"], "interfeys",
      ["vacuolar_change", "necrotic_keratinocytes"],
      ["superficial_perivascular_infiltrate", "papillary_dermal_edema", "subepidermal_blister"],
      ["acanthosis", "band_like_infiltrate", "granuloma", "acantholysis"], min_essential=2),
    E("Toksik epidermal nekroliz / Stivens-Jonson", ["nekroliz", "necrolysis", "stevens",
      "стивенс", "лайелл"], "interfeys",
      ["confluent_epidermal_necrosis", "subepidermal_blister"],
      ["necrotic_keratinocytes", "vacuolar_change"],
      ["acanthosis", "acantholysis", "granuloma"], min_essential=1),
    E("Diskoid qizil yugurik (DLE)", ["lupus", "qizil yugurik", "волчанка", "dle"], "interfeys",
      ["vacuolar_change", "basement_membrane_thickening", "follicular_plugging"],
      ["deep_perivascular_infiltrate", "mucin", "epidermal_atrophy", "hyperkeratosis",
       "pigment_incontinence", "civatte_bodies"],
      ["acantholysis", "pagetoid_spread", "atypical_lymphocytes"], min_essential=2),
    E("Dermatomiozit", ["dermatomyosit", "дерматомиозит"], "interfeys",
      ["vacuolar_change", "mucin", "epidermal_atrophy"],
      ["superficial_perivascular_infiltrate", "dilated_tortuous_capillaries"],
      ["band_like_infiltrate", "acantholysis", "granuloma"], min_essential=2),
    E("Lichen sclerosus", ["sclerosus", "sklerotik lixen", "sklerotik", "склеротический",
      "склероатрофический", "склератрофический"], "interfeys",
      ["papillary_dermal_homogenisation", "epidermal_atrophy", "vacuolar_change"],
      ["hyperkeratosis", "follicular_plugging", "band_like_infiltrate", "dermal_sclerosis"],
      ["acantholysis", "pagetoid_spread", "granuloma"], min_essential=2),
    E("Fiksatsiyalangan dori toshmasi", ["fixed drug", "фиксированная"], "interfeys",
      ["vacuolar_change", "necrotic_keratinocytes", "pigment_incontinence"],
      ["eosinophils", "neutrophils", "deep_perivascular_infiltrate"],
      ["granuloma", "acantholysis"], min_essential=2),
    E("Pityriasis lichenoides", ["lichenoides", "лихеноидный питириаз", "питириаз лихеноидный",
      "mucha", "muxa"], "interfeys",
      ["vacuolar_change", "parakeratosis", "extravasated_erythrocytes"],
      ["necrotic_keratinocytes", "superficial_perivascular_infiltrate",
       "deep_perivascular_infiltrate"],
      ["acantholysis", "granuloma", "cerebriform_nuclei"], min_essential=2),
    E("Ko'chirilgan transplantatga qarshi reaksiya (GVHD)", ["gvhd", "graft"], "interfeys",
      ["vacuolar_change", "necrotic_keratinocytes"], ["civatte_bodies"],
      ["granuloma", "acantholysis"], min_essential=2),

    # ── Pufakli ──
    E("Pemphigus vulgaris", ["pemphigus vulgaris", "pemfigus vulgaris", "пузырчатка обыкновенная",
      "пемфигус вульгарный", "pemfigus", "pemphigus"], "pufakli",
      ["acantholysis", "intraepidermal_vesicle"],
      ["eosinophils", "superficial_perivascular_infiltrate"],
      ["subepidermal_blister", "granuloma", "pagetoid_spread", "spongiform_pustule",
       "munro_microabscess"], min_essential=2),
    E("Pemphigus foliaceus", ["foliaceus", "листовидн"], "pufakli",
      ["acantholysis", "intraepidermal_vesicle"], ["neutrophils_in_corneum", "eosinophils"],
      ["subepidermal_blister", "granuloma", "munro_microabscess", "regular_elongated_rete"],
      min_essential=1, require_any=["intraepidermal_vesicle", "eosinophils"]),
    E("Hailey-Hailey kasalligi", ["hailey", "хейли"], "pufakli",
      ["acantholysis", "acanthosis"], ["dyskeratosis", "intraepidermal_vesicle"],
      ["subepidermal_blister", "band_like_infiltrate", "spongiform_pustule",
       "munro_microabscess", "regular_elongated_rete", "neutrophils_in_corneum", "eosinophils"],
      min_essential=2, require_any=["acanthosis", "dyskeratosis"]),
    E("Darier kasalligi", ["darier", "дарье"], "pufakli",
      ["dyskeratosis", "acantholysis"], ["hyperkeratosis", "papillomatosis"],
      ["subepidermal_blister", "band_like_infiltrate", "granuloma"], min_essential=2),
    E("Bulloz pemfigoid", ["pemphigoid", "pemfigoid", "пемфигоид"], "pufakli",
      ["subepidermal_blister", "eosinophils"],
      ["papillary_dermal_edema", "superficial_perivascular_infiltrate"],
      ["acantholysis", "granuloma", "necrobiosis"], min_essential=2),
    E("Gerpetiform dermatit (Duhring)", ["herpetiform", "герпетиформ", "дюринг", "duhring"],
      "pufakli",
      ["subepidermal_blister", "neutrophils"],
      ["papillary_dermal_edema", "leukocytoclasia"],
      ["acantholysis", "eosinophils", "granuloma"], min_essential=2),
    E("Orttirilgan bulloz epidermoliz", ["epidermoliz", "epidermolysis", "эпидермолиз"], "pufakli",
      ["subepidermal_blister"], ["neutrophils", "fibrosis"],
      ["acantholysis", "granuloma"], min_essential=1),
    E("Kechki teri porfiriyasi", ["porfiri", "порфири"], "pufakli",
      ["subepidermal_blister"], ["basement_membrane_thickening", "solar_elastosis"],
      ["acantholysis", "granuloma", "eosinophils"], min_essential=1),

    # ── Granulomatoz ──
    E("Sarkoidoz", ["sarkoid", "sarcoid", "саркоид"], "granulomatoz",
      ["naked_granulomas", "granuloma"],
      ["inflammation.type=granulomatoz", "fibrosis"],
      ["caseous_necrosis", "necrobiosis", "palisading_histiocytes", "mucin",
       "organisms_suspected", "acantholysis"], min_essential=1),
    E("Granuloma annulare", ["annulare", "halqasimon", "кольцевидная"], "granulomatoz",
      ["palisading_histiocytes", "necrobiosis", "mucin"],
      ["interstitial_histiocytes", "granuloma", "superficial_perivascular_infiltrate"],
      ["caseous_necrosis", "naked_granulomas", "organisms_suspected", "acantholysis"],
      min_essential=2),
    E("Necrobiosis lipoidica", ["lipoidica", "lipoid", "липоидн"], "granulomatoz",
      ["necrobiosis", "palisading_histiocytes"],
      ["plasma_cells", "dermal_sclerosis", "granuloma", "fibrosis"],
      ["mucin", "caseous_necrosis", "naked_granulomas"], min_essential=2),
    E("Begona jism granulomasi", ["begona jism", "foreign body", "инородн"], "granulomatoz",
      ["granuloma", "foreign_material"], ["fibrosis", "neutrophils"],
      ["naked_granulomas", "acantholysis"], min_essential=2),
    E("Teri sili (lupus vulgaris)", ["tuberkul", "tubercul", "туберкул", "sil", "скрофулодерм"],
      "granulomatoz",
      ["granuloma", "caseous_necrosis"], ["plasma_cells", "organisms_suspected"],
      ["necrobiosis", "mucin", "acantholysis"], min_essential=2),
    E("Teri leyshmaniozi", ["leishman", "лейшман"], "granulomatoz",
      ["granuloma", "organisms_suspected", "plasma_cells"],
      ["neutrophils", "necrosis"],
      ["necrobiosis", "acantholysis"], min_essential=2),
    E("Rozatsea (granulomatoz)", ["rosacea", "rozatsea", "розацеа", "demodekoz", "демодекоз"],
      "granulomatoz",
      ["granuloma", "dilated_tortuous_capillaries"],
      ["mite_or_parasite", "superficial_perivascular_infiltrate", "plasma_cells"],
      ["necrobiosis", "acantholysis", "caseous_necrosis"], min_essential=1,
      require_any=["granuloma", "mite_or_parasite"]),

    # ── Vaskulyar ──
    E("Leykotsitoklastik vaskulit", ["vaskulit", "vasculitis", "васкулит", "leykotsitoklast"],
      "vaskulit",
      ["vasculitis", "leukocytoclasia", "fibrinoid_vessel_necrosis"],
      ["extravasated_erythrocytes", "neutrophils", "papillary_dermal_edema"],
      ["granuloma", "acantholysis", "pagetoid_spread"], min_essential=2),
    E("Urtikariya", ["urtikar", "urticar", "крапивниц", "eshakemi"], "vaskulyar",
      ["papillary_dermal_edema", "superficial_perivascular_infiltrate"],
      ["eosinophils", "neutrophils"],
      ["spongiosis", "acanthosis", "vasculitis", "granuloma", "acantholysis"], min_essential=2),
    E("Pigmentli purpura dermatozi", ["purpur", "пурпур", "пигментно-пурпурозн"], "vaskulyar",
      ["extravasated_erythrocytes", "hemosiderin", "superficial_perivascular_infiltrate"],
      ["spongiosis"],
      ["fibrinoid_vessel_necrosis", "leukocytoclasia", "granuloma"], min_essential=2),
    E("Erythema nodosum", ["nodosum", "узловатая", "tugunli eritema"], "pannikulit",
      ["septal_panniculitis"], ["granuloma", "neutrophils", "fibrosis"],
      ["lobular_panniculitis", "vasculitis", "acantholysis"], min_essential=1),
    E("Sweet sindromi", ["sweet", "свит"], "neytrofil",
      ["dermal_neutrophil_rich", "papillary_dermal_edema"],
      ["leukocytoclasia", "neutrophils"],
      ["fibrinoid_vessel_necrosis", "granuloma", "acantholysis"], min_essential=2),
    E("Gangrenoz piodermiya", ["gangren", "гангренозн", "pyoderma"], "neytrofil",
      ["dermal_neutrophil_rich", "ulceration"], ["necrosis", "neutrophils"],
      ["granuloma", "acantholysis"], min_essential=2),

    # ── Skleroz ──
    E("Morfea / sklerodermiya", ["morfea", "morphea", "sklerodermi", "scleroderm", "склеродерм"],
      "sklerozlovchi",
      ["dermal_sclerosis", "adnexal_atrophy"],
      ["plasma_cells", "deep_perivascular_infiltrate", "lymphoid_follicles"],
      ["papillary_dermal_homogenisation", "acantholysis", "granuloma"], min_essential=2),
    E("Keloid", ["keloid", "келоид", "rubtsov", "рубц", "chandiq"], "fibrozlovchi",
      ["keloidal_collagen"], ["fibrosis", "spindle_cells"],
      ["storiform_pattern", "collagen_trapping", "granuloma"], min_essential=1),

    # ── Infeksion ──
    E("Verruca vulgaris", ["verruca", "verruka", "so'gal", "бородавк", "wart"], "epidermal",
      ["koilocytes", "papillomatosis", "hyperkeratosis"],
      ["acanthosis", "parakeratosis", "hypergranulosis", "dilated_tortuous_capillaries"],
      ["horn_cysts", "full_thickness_atypia", "pagetoid_spread", "acantholysis"],
      min_essential=2),
    E("Molluscum contagiosum", ["molluscum", "mollyusk", "моллюск"], "epidermal",
      ["molluscum_bodies"], ["acanthosis"], ["koilocytes", "pagetoid_spread"], min_essential=1),
    E("Herpes (oddiy / belbog')", ["herpes", "gerpes", "герпес", "belbog"], "epidermal",
      ["viral_cytopathic_change", "intraepidermal_vesicle"],
      ["acantholysis", "necrotic_keratinocytes", "neutrophils"],
      ["koilocytes", "granuloma", "subepidermal_blister"], min_essential=1),
    E("Dermatofitiya (mikoz)", ["dermatofit", "mikoz", "mycos", "микоз", "zamburug", "tinea",
      "trixofit", "трихофит"], "epidermal",
      ["fungal_hyphae_corneum"],
      ["neutrophils_in_corneum", "spongiosis", "parakeratosis"],
      ["acantholysis", "pagetoid_spread"], min_essential=1),
    E("Qo'tir (scabies)", ["scabies", "qo'tir", "qotir", "чесотк"], "epidermal",
      ["mite_or_parasite"], ["spongiosis", "eosinophils", "intraepidermal_vesicle"],
      ["granuloma", "acantholysis"], min_essential=1),
    E("Sifilis (ikkilamchi)", ["sifilis", "syphil", "сифилис"], "lixenoid",
      ["plasma_cells", "band_like_infiltrate"],
      ["psoriasiform_note", "vascular_proliferation", "granuloma", "acanthosis"],
      ["acantholysis", "pagetoid_spread"], min_essential=2),

    # ── Epidermal o'smalar ──
    E("Seboreik keratoz", ["seboreik keratoz", "seboreyali keratoz", "seborrheic keratosis",
      "seborrhoeic", "себорейный кератоз", "seboreik", "keratoz seborey", "verruca seborrheica",
      "себорейная бородавка"], "epidermal",
      ["horn_cysts", "basaloid_proliferation", "acanthosis"],
      ["papillomatosis", "hyperkeratosis", "basal_pigment"],
      ["full_thickness_atypia", "pagetoid_spread", "koilocytes", "peripheral_palisading",
       "invasion=bor"], min_essential=2),
    E("Aktinik keratoz", ["aktinik", "actinic", "актиническ", "солнечный кератоз"], "epidermal",
      ["parakeratosis", "solar_elastosis"],
      ["acanthosis", "basal_pigment", "hyperkeratosis"],
      ["full_thickness_atypia", "invasion=bor", "horn_cysts"], min_essential=2),
    E("Bowen kasalligi (SCC in situ)", ["bowen", "боуэн", "in situ"], "epidermal",
      ["full_thickness_atypia"],
      ["parakeratosis", "acanthosis", "atypical_mitoses", "dyskeratosis"],
      ["invasion=bor", "horn_cysts", "koilocytes"], min_essential=1),
    E("Invaziv skvamoz hujayrali karsinoma", ["skvamoz", "squamous", "scc", "yassi hujayrali",
      "плоскоклеточн", "плоскоклеточный рак", "rak", "рак кожи"], "epidermal",
      ["invasion=bor", "keratin_pearls", "full_thickness_atypia", "pleomorphism>=o'rta"],
      ["desmoplasia", "atypical_mitoses", "mitoses_10hpf>=3-10", "border=infiltrativ",
       "perineural"],
      ["horn_cysts", "koilocytes", "peripheral_palisading", "melanocyte_nests"],
      malignant=True, min_essential=2),
    E("Keratoakantoma", ["keratoakantom", "keratoacanthoma", "кератоакантом"], "epidermal",
      ["keratin_filled_crater", "glassy_keratinocytes"],
      ["acanthosis", "neutrophils", "symmetry=simmetrik"],
      ["pleomorphism>=kuchli", "atypical_mitoses", "desmoplasia", "perineural"], min_essential=1),
    E("Bazal hujayrali karsinoma (bazalioma)", ["bazalioma", "basalioma", "bazal hujayrali",
      "basal cell", "bcc", "базалиом"], "epidermal",
      ["basaloid_proliferation", "peripheral_palisading", "clefting_retraction"],
      ["mucinous_stroma", "solar_elastosis", "necrosis", "invasion=bor", "mitoses_10hpf>=1-2"],
      ["horn_cysts", "keratin_pearls", "koilocytes", "melanocyte_nests", "shadow_cells"],
      malignant=True, min_essential=2),
    E("Porokeratoz", ["porokerat", "порокерат"], "epidermal",
      ["cornoid_lamella"], ["hypogranulosis", "epidermal_atrophy"],
      ["horn_cysts", "acantholysis"], min_essential=1),

    # ── Melanotsitar ──
    E("Melanotsitar nevus", ["nevus", "невус", "xol", "родинк"], "melanotsitar",
      ["melanocyte_nests", "nests_regular", "maturation_with_depth"],
      ["symmetry=simmetrik", "basal_pigment"],
      ["pagetoid_spread", "asymmetric_melanocytic_growth", "deep_mitoses",
       "pleomorphism>=kuchli", "atypical_mitoses"], min_essential=2),
    E("Displastik nevus", ["displastik", "dysplastic", "диспластическ", "clark", "кларк"],
      "melanotsitar",
      ["melanocyte_nests", "bridging_nests", "lamellar_fibroplasia"],
      ["single_melanocyte_proliferation", "pleomorphism>=yengil", "maturation_with_depth"],
      ["pagetoid_spread", "deep_mitoses", "pleomorphism>=kuchli"], min_essential=2),
    E("Spitz nevusi", ["spitz", "шпиц"], "melanotsitar",
      ["spindle_epithelioid_melanocytes", "kamino_bodies", "maturation_with_depth"],
      ["symmetry=simmetrik", "clefting_retraction", "melanocyte_nests"],
      ["deep_mitoses", "asymmetric_melanocytic_growth", "ulceration"], min_essential=2),
    E("Melanoma", ["melanom", "меланом"], "melanotsitar",
      ["pagetoid_spread", "asymmetric_melanocytic_growth", "single_melanocyte_proliferation",
       "pleomorphism>=o'rta", "deep_mitoses"],
      ["atypical_mitoses", "melanocyte_nests", "ulceration", "solar_elastosis",
       "symmetry=assimetrik", "invasion=bor", "prominent_nucleoli"],
      ["maturation_with_depth", "kamino_bodies", "horn_cysts", "peripheral_palisading",
       "keratin_pearls"],
      malignant=True, min_essential=2),

    # ── Dermal / mezenximal ──
    E("Dermatofibroma", ["dermatofibrom", "дерматофибром", "gistiotsitom"], "dermal",
      ["collagen_trapping", "spindle_cells", "tumour_nodule"],
      ["grenz_zone", "acanthosis", "basal_pigment", "hemosiderin", "foam_cells"],
      ["storiform_pattern", "pleomorphism>=kuchli", "atypical_mitoses",
       "depth.deepest_level=gipoderma"], min_essential=2),
    E("Dermatofibrosarcoma protuberans (DFSP)", ["dfsp", "protuberans", "выбухающ",
      "dermatofibrosarcom"], "dermal",
      ["storiform_pattern", "spindle_cells", "depth.deepest_level=gipoderma"],
      ["border=infiltrativ", "mature_adipocytes", "mitoses_10hpf>=1-2"],
      ["collagen_trapping", "grenz_zone", "foam_cells", "peripheral_palisading"],
      malignant=True, min_essential=2),
    E("Gemangioma", ["hemangiom", "gemangiom", "гемангиом", "angiom"], "vaskulyar o'sma",
      ["vascular_proliferation"], ["tumour_nodule", "symmetry=simmetrik"],
      ["slit_like_vascular_spaces", "promontory_sign", "pleomorphism>=o'rta", "storiform_pattern"],
      min_essential=1),
    E("Piogen granuloma", ["piogen", "pyogenic", "пиогенн", "botriomikom", "angiom",
      "lobulyar kapillyar", "lobular capillary"], "vaskulyar o'sma",
      ["lobular_capillary_proliferation", "vascular_proliferation"],
      ["epidermal_collarette", "polypoid_exophytic", "ulceration", "neutrophils",
       "papillary_dermal_edema", "extravasated_erythrocytes"],
      ["slit_like_vascular_spaces", "promontory_sign", "storiform_pattern"], min_essential=1),
    E("Kaposi sarkomasi", ["kaposi", "капоши"], "vaskulyar o'sma",
      ["slit_like_vascular_spaces", "spindle_cells", "promontory_sign"],
      ["extravasated_erythrocytes", "hemosiderin", "plasma_cells", "vascular_proliferation"],
      ["lobular_capillary_proliferation", "collagen_trapping", "peripheral_palisading"],
      malignant=True, min_essential=2),
    E("Neyrofibroma", ["neyrofibrom", "neurofibrom", "нейрофибром"], "dermal",
      ["wavy_spindle_neural"], ["mast_cell_infiltrate", "mucin"],
      ["storiform_pattern", "pleomorphism>=kuchli", "collagen_trapping"], min_essential=1),
    E("Lipoma", ["lipom", "липом"], "dermal",
      ["mature_adipocytes"], [], ["spindle_cells", "pleomorphism>=o'rta"], min_essential=1),

    # ── Adneksal / kista ──
    E("Pilomatriksoma (Malerb epiteliomasi)", ["pilomatri", "malerb", "малерб", "пиломатрикс"],
      "adneksal",
      ["shadow_cells", "basaloid_proliferation"], ["foreign_material", "granuloma"],
      ["peripheral_palisading", "clefting_retraction", "horn_cysts"], min_essential=1),
    E("Epidermal (infundibulyar) kista", ["kista", "cyst", "киста", "ateroma", "атером"],
      "adneksal",
      ["cyst_wall_granular_layer"], ["granuloma", "foreign_material"],
      ["basaloid_proliferation", "pleomorphism>=o'rta"], min_essential=1),
    E("Trixoepitelioma", ["trixoepiteliom", "trichoepitheliom", "трихоэпител"], "adneksal",
      ["basaloid_proliferation", "horn_cysts"],
      ["papillary_dermal_homogenisation", "symmetry=simmetrik"],
      ["clefting_retraction", "mucinous_stroma", "invasion=bor"], min_essential=2),

    # ── Limfoid ──
    E("Mycosis fungoides", ["mycosis fungoides", "mikoz fungoides", "грибовидный микоз",
      "fungoides"], "limfoid",
      ["epidermotropism", "atypical_lymphocytes", "cerebriform_nuclei", "pautrier_microabscess"],
      ["band_like_infiltrate", "papillary_dermal_homogenisation", "dense_lymphoid_infiltrate"],
      ["spongiosis", "acantholysis", "granuloma", "melanocyte_nests"],
      malignant=True, min_essential=2),
    E("Teri B-hujayrali limfomasi", ["b-hujayra", "b-клеточн", "limfom", "lymphom", "лимфом"],
      "limfoid",
      ["dense_lymphoid_infiltrate", "atypical_lymphocytes", "grenz_zone"],
      ["lymphoid_follicles", "deep_perivascular_infiltrate", "mitoses_10hpf>=3-10"],
      ["epidermotropism", "spongiosis", "granuloma"], malignant=True, min_essential=2),
    E("Psevdolimfoma", ["psevdolimfom", "pseudolymphom", "псевдолимфом"], "limfoid",
      ["dense_lymphoid_infiltrate", "lymphoid_follicles"],
      ["grenz_zone", "eosinophils", "plasma_cells"],
      ["atypical_lymphocytes", "cerebriform_nuclei", "epidermotropism"], min_essential=2),
    E("Limfomatoid papulyoz", ["limfomatoid", "lymphomatoid", "лимфоматоидн"], "limfoid",
      ["atypical_lymphocytes", "dense_lymphoid_infiltrate"],
      ["neutrophils", "eosinophils", "ulceration", "necrotic_keratinocytes"],
      ["granuloma", "acantholysis"], min_essential=2),

    # ── Boshqa ──
    E("Mastotsitoz", ["mastotsit", "mastocyt", "мастоцит", "urticaria pigmentosa"], "dermal",
      ["mast_cell_infiltrate"], ["basal_pigment", "eosinophils"],
      ["granuloma", "atypical_lymphocytes"], min_essential=1),
    E("Ksantoma", ["ksantom", "xanthom", "ксантом"], "dermal",
      ["foam_cells"], ["fibrosis", "granuloma"],
      ["necrobiosis", "atypical_lymphocytes"], min_essential=1),
    E("Teri amiloidozi", ["amiloid", "amyloid", "амилоид"], "dermal",
      ["amyloid_deposits"], ["pigment_incontinence", "acanthosis"],
      ["granuloma", "acantholysis"], min_essential=1),
    E("Alopetsiya areata", ["areata", "alopet", "alopeci", "алопец", "gnezdn", "гнездн"],
      "follikulyar",
      ["dense_lymphoid_infiltrate", "adnexal_involvement"], ["eosinophils", "mast_cell_infiltrate"],
      ["dermal_sclerosis", "granuloma"], min_essential=1),
    E("Follikulit", ["follikulit", "folliculit", "фолликулит", "furunkul", "фурункул"],
      "follikulyar",
      ["neutrophils", "adnexal_involvement"], ["dermal_neutrophil_rich", "granuloma"],
      ["acantholysis", "pagetoid_spread"], min_essential=2),
    E("Akne", ["akne", "acne", "акне", "ugri", "угр"], "follikulyar",
      ["follicular_plugging", "neutrophils"], ["granuloma", "adnexal_involvement"],
      ["acantholysis", "pagetoid_spread"], min_essential=1),
]

# «psoriasiform_note» — sifilisda psoriaziform akantoz; kalit ko'rikda yo'q,
# ball bermaydi. Bu yerda faqat hujjat uchun.

_FOLD = (
    ("sch", "sh"), ("ch", "x"), ("kh", "x"), ("ph", "f"), ("th", "t"),
    ("ck", "k"), ("c", "k"), ("w", "v"), ("y", "i"), ("j", "i"), ("'", ""), ("‘", ""), ("’", ""),
)
# Yo'llanmalar rus tilida keladi («Ангиома?») — kirill ham bir o'zakka keltiriladi,
# aks holda ruscha nom lotincha aliasga hech qachon tushmaydi.
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "j",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "ў": "u", "қ": "k", "ғ": "g", "ҳ": "x",
}


def _fold(s):
    s = "".join(_CYR.get(ch, ch) for ch in str(s or "").lower())
    for a, b in _FOLD:
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


_BY_ALIAS = [(tuple(_fold(a) for a in e["aliases"]), e) for e in CRITERIA]


def find_entity(name):
    """Nom bo'yicha mezon yozuvi — eng uzun mos kelgan alias g'olib."""
    low = _fold(name)
    if not low:
        return None
    best, best_len = None, 0
    for aliases, e in _BY_ALIAS:
        for a in aliases:
            if a and a in low and len(a) > best_len:
                best, best_len = e, len(a)
    return best


# ─── 3. Belgilarni tekshirish ────────────────────────────────────────────────

def _bool_feature(features, key):
    for g in BOOL_GROUPS:
        sub = features.get(g)
        if isinstance(sub, dict) and key in sub:
            return sub[key] is True
    return False


def _scalar(features, path):
    cur = features
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    if cur is None and "." not in path:
        # cytology ichidagi skalyarlar
        cyt = features.get("cytology")
        if isinstance(cyt, dict):
            cur = cyt.get(path)
    return str(cur).strip().lower().replace("‘", "'").replace("’", "'") if cur is not None else None


def _rank(scale, value):
    try:
        return scale.index(value)
    except ValueError:
        return -1


def feature_present(features, spec):
    """Belgi yoki skalyar shart bajarilganmi. Noma'lum/baholanmagan → False."""
    if not isinstance(features, dict) or not spec:
        return False
    if ">=" in spec:
        key, val = spec.split(">=", 1)
        scale = _SCALE.get(key)
        cur = _scalar(features, key)
        if not scale or cur is None:
            return False
        return _rank(scale, cur) >= _rank(scale, val.lower()) >= 0
    if "=" in spec:
        key, val = spec.split("=", 1)
        cur = _scalar(features, key)
        return cur is not None and cur == val.lower()
    return _bool_feature(features, spec)


def feature_label(spec):
    if ">=" in spec:
        k, v = spec.split(">=", 1)
        return f"{k} ≥ {v}"
    if "=" in spec:
        k, v = spec.split("=", 1)
        return f"{k} = {v}"
    return FEATURE_UZ.get(spec, spec)


def evaluate(entity, features):
    """Bitta nozologiyani ko'rikka solishtirish."""
    ess = [(s, feature_present(features, s)) for s in entity["essential"]]
    sup = [(s, feature_present(features, s)) for s in entity["supporting"]]
    exc = [(s, feature_present(features, s)) for s in entity["excluding"]]
    ess_hits = sum(1 for _, ok in ess if ok)
    sup_hits = sum(1 for _, ok in sup if ok)
    exc_hits = [s for s, ok in exc if ok]
    n_ess = len(ess)
    need = entity["min_essential"]
    if need is None:
        need = max(1, math.ceil(n_ess / 2)) if n_ess else 0
    qualifies = ess_hits >= need
    req = entity.get("require_any") or ()
    if req and not any(feature_present(features, s) for s in req):
        qualifies = False
    ess_frac = ess_hits / n_ess if n_ess else 1.0
    sup_frac = sup_hits / len(sup) if sup else 0.0
    score = 0.65 * ess_frac + 0.35 * sup_frac - 0.35 * len(exc_hits)
    score = max(0.0, min(1.0, score))
    return {
        "name": entity["name"],
        "pattern": entity["pattern"],
        "malignant": entity["malignant"],
        "qualifies": qualifies,
        "score": round(score, 3),
        "essential_hits": ess_hits,
        "essential_total": n_ess,
        "essential_need": need,
        "essential_present": [s for s, ok in ess if ok],
        "essential_absent": [s for s, ok in ess if not ok],
        "supporting_hits": sup_hits,
        "supporting_total": len(sup),
        "excluding_present": exc_hits,
    }


_SOLITARY_WORDS = re.compile(
    r"yakka|bitta|solitar|solitary|tugun|nodul|o'sma|osma|papilloma|oyoqcha|"
    r"pedunk|polip|yakkam|единичн|одиночн|узел|узелок|опухол|ножк|полип|"
    r"biopsiya.*(tugun|o'sma)|eksizion", re.I)
_ERUPTION_WORDS = re.compile(
    r"toshma|tarqoq|ko'p o'choq|ko'p sonli|simmetrik|generalizat|blyashkalar|papulalar|"
    r"сыпь|высыпан|множествен|распростран|бляшки|папулы|диссемин", re.I)


def clinical_hint(text):
    """Klinik tavsif/yo'llanmadan ko'rinish turi: 'solitary' | 'eruption' | ''."""
    t = str(text or "")
    if not t.strip():
        return ""
    sol = len(_SOLITARY_WORDS.findall(t))
    eru = len(_ERUPTION_WORDS.findall(t))
    if sol > eru:
        return "solitary"
    if eru > sol:
        return "eruption"
    return ""


# Klinik tavsifdagi kalit so'zlar → tekshirib ko'rish kerak bo'lgan nozologiyalar.
# Bu tashxis emas — «shu belgilarni qidir» degan ishora (patolog klinik
# ko'rinishdan kelib chiqib aynan shunday qiladi).
_CLINICAL_CUES = (
    (re.compile(r"(qizil|pushti|красн|розов|ярко).{0,40}(tugun|papula|oyoqch|pedunk|polip|узел|ножк|полип)|"
                r"(tugun|papula|узел).{0,40}(qizil|красн)|qon(a|ay)|кровоточ|yaltiroq qizil", re.I),
     ["Piogen granuloma", "Gemangioma"]),
    (re.compile(r"pigment|qora|jigarrang|to'q|пигмент|чёрн|черн|коричнев|xol|родинк|невус", re.I),
     ["Melanotsitar nevus", "Melanoma", "Seboreik keratoz"]),
    (re.compile(r"marvarid|telangiekt|yaltiroq.{0,20}(chegara|tugun)|перламутр|телеангиэкт|yara.{0,30}bitmay|незажива", re.I),
     ["Bazal hujayrali karsinoma (bazalioma)", "Invaziv skvamoz hujayrali karsinoma"]),
    (re.compile(r"so'gal|бородав|g'adir|verrukoz|веррук", re.I),
     ["Verruca vulgaris", "Seboreik keratoz"]),
    (re.compile(r"qattiq.{0,20}tugun|dermatofibrom|плотн.{0,20}узел|chuqurlash|dimple", re.I),
     ["Dermatofibroma"]),
    (re.compile(r"tangacha|kumushsimon|чешуй|серебрист|blyashka|бляшк", re.I),
     ["Psoriasis vulgaris", "Lichen simplex chronicus (Vidal)"]),
    (re.compile(r"pufak|pufakcha|пузыр|буллез|eroziya|эрози", re.I),
     ["Pemphigus vulgaris", "Bulloz pemfigoid", "Herpes (oddiy / belbog')"]),
)


def clinical_entities(text):
    """Klinik tavsif/yo'llanma matnidan tekshirilishi kerak bo'lgan nozologiyalar."""
    t = str(text or "")
    if not t.strip():
        return []
    out = []
    for rx, names in _CLINICAL_CUES:
        if rx.search(t):
            out += [n for n in names if n not in out]
    return out


def referral_entities(text):
    """Yo'llanma/klinik tashxis matnida nomi tilga olingan nozologiyalar."""
    t = _fold(text)
    if not t:
        return []
    out = []
    for aliases, e in _BY_ALIAS:
        if any(a and a in t for a in aliases) and e["name"] not in out:
            out.append(e["name"])
    return out


def rank_candidates(features, limit=8, clinical_text="", referral_text=""):
    """Ko'rikdan kelib chiqib eng mos nozologiyalar — deterministik hakamlik.

    clinical_text — tana surati tavsifi; referral_text — yo'llanma/klinik tashxis.
    Morfologiya asosiy manba; klinik mantiq faqat TUZATADI: yakka tugun uchun
    tarqoq dermatoz nomzodlari pasaytiriladi, yo'llanma gipotezasi ozgina ko'tariladi.
    """
    if not isinstance(features, dict):
        return []
    hint = clinical_hint(clinical_text) or clinical_hint(referral_text)
    named = set(referral_entities(referral_text))
    rows = [evaluate(e, features) for e in CRITERIA]
    for r, e in zip(rows, CRITERIA):
        r["presentation"] = e["presentation"]
        if hint == "solitary" and e["presentation"] == "eruption":
            r["score"] = round(r["score"] * 0.5, 3)
            r["clinical_note"] = "yakka o'choqqa mos emas (tarqoq dermatoz)"
        elif hint == "eruption" and e["presentation"] == "solitary":
            r["score"] = round(r["score"] * 0.6, 3)
            r["clinical_note"] = "tarqoq toshmaga mos emas (yakka o'sma)"
        if e["name"] in named and r["qualifies"]:
            r["score"] = round(min(1.0, r["score"] + 0.12), 3)
            r["clinical_note"] = "yo'llanma gipotezasi"
    rows = [r for r in rows if r["qualifies"] and r["score"] > 0]
    rows.sort(key=lambda r: (-r["score"], -r["essential_hits"], r["name"]))
    return rows[:limit]


def check_name(name, features):
    """Model tanlagan nomni mezonga solishtirish.

    Natija: None (nom jadvalda yo'q) yoki evaluate() natijasi.
    """
    e = find_entity(name)
    if e is None or not isinstance(features, dict):
        return None
    return evaluate(e, features)


def criteria_block(features, limit=6, clinical_text="", referral_text=""):
    """Qaror bosqichi uchun matn: mezon bo'yicha eng mos nomzodlar."""
    rows = rank_candidates(features, limit, clinical_text, referral_text)
    hint = clinical_hint(clinical_text) or clinical_hint(referral_text)
    named = referral_entities(referral_text)
    if not rows and not named:
        return ""
    lines = ["#### MEZON JADVALI (deterministik — ko'rilgan belgilardan hisoblangan)"]
    if hint:
        lines.append("Klinik ko'rinish turi: " + (
            "YAKKA o'choq/tugun — tarqoq dermatozlar (Darier, pemfigus, psoriaz, ekzema…) "
            "bu holatga mos kelmaydi" if hint == "solitary"
            else "TARQOQ toshma — yakka o'sma nomzodlari kam ehtimol"))
    if named:
        lines.append("Yo'llanma gipotezasi: " + ", ".join(named[:3]) +
                     " — ko'rikda uning belgilari bormi, aniq tekshirilsin.")
    for r in rows:
        present = ", ".join(feature_label(s) for s in r["essential_present"][:4]) or "—"
        absent = ", ".join(feature_label(s) for s in r["essential_absent"][:3]) or "—"
        excl = ", ".join(feature_label(s) for s in r["excluding_present"][:2])
        line = (
            f"- {r['name']} — moslik {r['score']:.2f}; asosiy {r['essential_hits']}/{r['essential_total']}"
            f" (bor: {present}; yo'q: {absent}); qo'shimcha {r['supporting_hits']}/{r['supporting_total']}"
        )
        if excl:
            line += f"; RAD ETUVCHI BOR: {excl}"
        if r.get("clinical_note"):
            line += f" [{r['clinical_note']}]"
        lines.append(line)
    lines.append(
        "Tashxis shu jadvaldagi nomzodlardan tanlanadi, jadvaldan tashqari nom faqat "
        "belgilar ro'yxati uni aniq talab qilsa. RAD ETUVCHI belgisi bor nomzod tanlanmaydi."
    )
    return "\n".join(lines) + "\n"


def entity_names():
    return [e["name"] for e in CRITERIA]
