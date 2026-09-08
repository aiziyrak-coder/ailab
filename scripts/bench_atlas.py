#!/usr/bin/env python3
"""Atlas benchmark — tizim qanchalik to'g'ri tashxis qo'yishini O'LCHAYDI.

Nima uchun kerak: shu paytgacha hech kim dasturning aniqligini bilmasdi.
Har bir yaxshilash fikr bilan asoslanardi, o'lchov bilan emas. Diagnostik
tizimni o'lchovsiz kuchaytirib bo'lmaydi — qayerda adashayotganini
ko'rmasangiz, nimani tuzatishni ham bilmaysiz.

Sinov to'plami tayyor holda mavjud: kitob atlasidagi 668 rasm allaqachon
kasallik nomi bilan belgilangan (papka nomi = tashxis). Kesma rasmini
yorlig'ini yashirib quvurga beramiz va chiqqan nomni yorliq bilan
solishtiramiz.

MUHIM: sinov paytida atlas ma'lumotnoma rasmlari O'CHIRILADI — aks holda
tizimga aynan o'sha rasmning o'zi javob sifatida qaytariladi.

Ishlatish:
    python scripts/bench_atlas.py --n 12
    python scripts/bench_atlas.py --n 40 --out bench_v2.json
    python scripts/bench_atlas.py --compare bench_v1.json bench_v2.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:                                    # ruscha yorliqlar konsolda buzilmasin
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SECRET_KEY", "bench")


def _load_env():
    env = ROOT / "backend" / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# Atlasdagi rasm sinov namunasi — ma'lumotnoma sifatida qaytarilmasin
os.environ["HISTOLOGY_ATLAS"] = "0"

from PIL import Image  # noqa: E402

# ─── Yorliqlarni tozalash ───────────────────────────────────────────────────
# Papka nomlari bob raqami bilan keladi ("XV. Псориаз"), ba'zilari esa
# umuman tashxis emas (kitob nomi, "Новая папка", sana).

_NUM_PREFIX = re.compile(r"^\s*(?:[IVXLC]+|\d+)\s*[.)]\s*", re.I)
_NOT_A_DIAGNOSIS = (
    "норма и патология", "гистопатология кожи", "принципы диагностики",
    "внутренние болезни", "руководство до и после", "монография",
    "новая папка", "доброкачественные опухоли", "предраковые состояния",
    "пигментные невусы и опухоли", "пороки развития", "аногенитальный дерматозы",
    "бактериальные заболевания", "грибковые заболевания", "вирусные заболевания",
    "буллезные дерматозы", "заболевания сосудов", "нарушение кератинизации",
    "нарушения пигментации", "паразитарные дерматозы", "диффузные болезни",
    "группа пемфигуса", "кисты", "рубцы", "сепсис", "туберкулез",
    "ираклий",
)


def clean_label(raw):
    """Papka nomidan haqiqiy tashxis nomini olish; tashxis bo'lmasa None."""
    s = unicodedata.normalize("NFKC", str(raw or "")).strip()
    s = _NUM_PREFIX.sub("", s).strip()
    s = re.sub(r"\s+", " ", s)
    low = s.lower()
    if len(s) < 4 or any(bad in low for bad in _NOT_A_DIAGNOSIS):
        return None
    if re.search(r"\d{2}-\d{2}-\d{4}", s):
        return None
    return s


# ─── Baholash ───────────────────────────────────────────────────────────────
# Chiqish o'zbek/lotin tilida, yorliq rus tilida. dx_synonyms.py ikkalasini
# bog'laydi. Qo'shimcha ravishda o'zak bo'yicha solishtirish ham qilinadi:
# "псориаз" ↔ "psoriaz", "меланома" ↔ "melanoma".

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# Lotin yozuvidagi tibbiy atamalar bir xil o'zakka keltiriladi
_STEM_FIX = (
    ("cz", "z"), ("ch", "x"), ("sh", "s"), ("kh", "x"), ("ph", "f"),
    ("th", "t"), ("ck", "k"), ("qu", "kv"), ("c", "k"), ("w", "v"),
    ("y", "i"), ("j", "i"), ("ё", "e"),
)


def _translit(s):
    return "".join(_TRANSLIT.get(ch, ch) for ch in s.lower())


def norm_term(s):
    """Til va imlodan qat'i nazar solishtiriladigan o'zak."""
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("'", "").replace("`", "")
    s = _translit(s)
    for a, b in _STEM_FIX:
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_STOP = {
    "teri", "kozi", "koji", "kozhi", "skin", "benign", "malign", "malignant",
    "variant", "tur", "turi", "klassik", "vulgaris", "obiknovennii", "prostoi",
    "kojnii", "kojnaya", "i", "va", "and", "the", "of", "s", "v",
}


def tokens(s):
    return [t for t in norm_term(s).split() if len(t) > 3 and t not in _STOP]


def build_alias_map():
    """Ruscha yorliq → o'zbekcha/lotincha nomlar to'plami."""
    try:
        from lab_core.dx_synonyms import DX_SYNONYMS
    except Exception:
        return {}
    out = {}
    for uz, ru, _grp in DX_SYNONYMS:
        key = norm_term(ru)
        if key:
            out.setdefault(key, set()).add(norm_term(uz))
    return out


ALIASES = build_alias_map()


def quick_match(pred, label):
    """Arzon tekshiruv — aniq mos kelsa hakamga bormaymiz."""
    p, l = norm_term(pred), norm_term(label)
    if not p or not l:
        return False
    if p == l:
        return True
    pt, lt = set(tokens(pred)), set(tokens(label))
    for ru_key, uz_set in ALIASES.items():
        if not (ru_key in l or l in ru_key):
            continue
        for uz in uz_set:
            ut = set(tokens(uz))
            if ut and (ut <= pt or pt <= ut):
                return True
    return bool(lt) and all(any(w in q or q in w for q in pt) for w in lt)


# Nomlarni satr sifatida solishtirish tibbiyotda ishlamaydi: yorliq rus
# tilida ("Рак кожи"), chiqish lotinchada ("Invaziv skvamoz hujayrali
# karsinoma") — bu bir xil tashxis. Shuning uchun hakam sifatida model
# ishlatiladi, lekin qat'iy mezon bilan.
_JUDGE_SYSTEM = (
    "Siz — dermatopatologiya bo'yicha imtihon hakamisiz. Sizga kitob atlasidagi "
    "rasmning HAQIQIY yorlig'i (rus tilida) va dastur chiqargan TASHXIS beriladi.\n"
    "Vazifa: dastur javobi yorliqqa mos keladimi — qat'iy baholang.\n"
    "Bahoyingiz:\n"
    "— \"togri\": bir xil kasallik (til, sinonim, lotincha nom, kichik variant farqi hisobga olinmaydi). "
    "Yorliq umumiy bo'lsa (masalan «Рак кожи») va javob shu guruhning aniq a'zosi bo'lsa "
    "(«skvamoz hujayrali karsinoma») — bu ham \"togri\".\n"
    "— \"qisman\": to'g'ri reaksiya patterni yoki oila, lekin boshqa nozologiya "
    "(masalan yorliq «псориаз», javob «psoriaziform dermatit»).\n"
    "— \"xato\": boshqa kasallik.\n"
    "— \"yorliq_yaroqsiz\": yorliq tashxis emas (kitob bo'limi, «Норма», papka nomi) — baholab bo'lmaydi.\n"
    "Xayrixohlik qilmang: shubha bo'lsa pastroq baho qo'ying. "
    "Javob QAT'IY JSON: {\"verdict\": \"togri|qisman|xato|yorliq_yaroqsiz\", \"why\": \"qisqa sabab\"}"
)


def judge(pred, label, model="gpt-4o"):
    """Model-hakam bahosi — (verdict, sabab)."""
    from lab_core import engine as eng

    if not str(pred or "").strip():
        return "xato", "javob bo'sh"
    last = "javob o'qilmadi"
    for attempt in range(4):
        try:
            raw = eng._chat_complete(
                [
                    {"role": "system", "content": _JUDGE_SYSTEM},
                    {"role": "user", "content": f"YORLIQ: {label}\nDASTUR JAVOBI: {pred}"},
                ],
                {"max_tokens": 300, "temperature": 0.0},
                model=model,
            )
            data = eng._parse_observation(raw) or {}
            v = str(data.get("verdict") or "").strip().lower()
            if v in ("togri", "qisman", "xato", "yorliq_yaroqsiz"):
                return v, str(data.get("why") or "")[:120]
            last = f"kutilmagan baho: {v[:40]}"
        except Exception as e:
            last = str(e)[:100]
            time.sleep(3 * (attempt + 1))
    return "hakam_xato", last


# ─── Sinov ──────────────────────────────────────────────────────────────────

def pick_cases(atlas_root, n, kind="slide", seed=7):
    index = Path(atlas_root) / "atlas.json"
    rows = json.loads(index.read_text(encoding="utf-8"))["images"]
    cases = []
    for r in rows:
        if r.get("kind") != kind:
            continue
        lab = clean_label(r.get("label"))
        if not lab:
            continue
        f = Path(atlas_root) / r["file"]
        if f.is_file():
            cases.append({"label": lab, "file": str(f), "kind": kind})
    # Har tashxisdan bittadan — bir kasallik natijani egallab olmasin
    by_label = {}
    for c in cases:
        by_label.setdefault(norm_term(c["label"]), []).append(c)
    rnd = random.Random(seed)
    keys = sorted(by_label)
    rnd.shuffle(keys)
    picked = [rnd.choice(by_label[k]) for k in keys[:n]]
    return picked


def run_case(case, lab_type="histology"):
    from lab_core import engine as eng

    t0 = time.time()
    img = Image.open(case["file"]).convert("RGB")
    img = eng._resize_img(img)
    prompt = eng.LAB_PROMPTS.get(lab_type, "")
    rec = {"label": case["label"], "file": case["file"]}
    text = ""
    for attempt in range(3):        # 429 — sekinlashtirib qayta urinamiz
        try:
            text = eng._openai_generate([prompt, img], lab_type, None)
            break
        except Exception as e:
            msg = str(e)
            if attempt == 2 or "429" not in msg:
                rec.update({"error": msg[:300], "secs": round(time.time() - t0, 1)})
                return rec
            time.sleep(20 * (attempt + 1))

    # Sarlavha «#### TASHXIS» shaklida kelmasa, butun keyingi qayta ishlash
    # jimgina o'tkazib yuboriladi — buni alohida qayd etamiz.
    rec["heading_ok"] = bool(re.search(r"^\s*#+\s*tashxis\b", text or "", re.I | re.M))

    block = eng._histology_dx_block(text) or ""
    name = (eng._dx_name_only(block) or "").strip()
    name = re.sub(r"^\s*yakuniy\s+xulosa\s*:\s*", "", name, flags=re.I).strip()
    if quick_match(name, case["label"]):
        verdict, why = "togri", "aynan mos"
    else:
        verdict, why = judge(name, case["label"])
    m = re.search(r"Ishonchlilik:\s*(\d{1,3})\s*%", text)
    rec.update({
        "pred": name,
        "verdict": verdict,
        "ok": verdict == "togri",
        "why": why,
        # Tavsifiy nomga tushib qolish — quvurdagi qo'riqchi ishga tushgani
        "descriptive": name.lower().startswith("tavsifiy morfologiya"),
        "provisional": "taxminiy" in block.lower(),
        "confidence": int(m.group(1)) if m else None,
        "chars": len(text),
        "secs": round(time.time() - t0, 1),
        "report": text,
    })
    return rec


def replay_cases(args):
    """Arxivdagi keyslarni yangi kod bilan qayta ishga tushirib, eski tashxis
    bilan solishtirish. Bu regressiya sinovi: algoritm o'zgargach, ilgari
    to'g'ri chiqqan keyslar buzilmadimi — shuni ko'rsatadi."""
    import logging

    logging.getLogger("medlab").setLevel(logging.WARNING)
    from lab_core import engine as eng

    eng.ensure_openai_from_env()
    if args.model:
        os.environ["OPENAI_MODEL_ID"] = args.model
        eng.OPENAI_MODEL_ID = args.model

    root = Path(args.cases)
    found = sorted(root.rglob("keys.json"))
    print(f"model={eng.OPENAI_MODEL_ID}  arxiv={root}  keyslar={len(found)}\n")
    rows = []
    for meta_p in found[: args.n] if args.n else found:
        d = meta_p.parent
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except Exception:
            continue
        imgs = sorted(d.glob("kesma_*.jpg"))
        if not imgs:
            continue
        old = ((meta.get("tashxis") or {}).get("name") or "").strip()
        pil = [eng._resize_img(Image.open(p).convert("RGB")) for p in imgs[:12]]
        prompt = eng.LAB_PROMPTS.get(meta.get("lab_type") or "histology", "")
        t0 = time.time()
        try:
            text = eng._openai_generate([prompt] + pil, meta.get("lab_type") or "histology",
                                        meta.get("kontekst") or None)
        except Exception as e:
            print(f"  ERR   {d.name[:40]:40s} {str(e)[:60]}")
            continue
        new = re.sub(r"^\s*yakuniy\s+xulosa\s*:\s*", "",
                     eng._dx_name_only(eng._histology_dx_block(text) or "") or "", flags=re.I)
        same = quick_match(new, old) if old else None
        mark = "  =  " if same else ("  ≠  " if same is False else "  ?  ")
        m = re.search(r"Ishonchlilik:\s*(\d{1,3})\s*%", text)
        print(f"{mark}{d.name[:34]:34s} {old[:28]:28s} → {new[:34]}  "
              f"{(m.group(1) + '%') if m else ''}  {time.time()-t0:.0f}s")
        rows.append({"case": str(d), "old": old, "new": new, "same": same, "report": text})
    changed = sum(1 for r in rows if r["same"] is False)
    print(f"\n{len(rows)} keys qayta ishlandi, {changed} tasida tashxis o'zgardi")
    out = args.out or f"replay_{time.strftime('%Y%m%d_%H%M%S')}.json"
    Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saqlandi: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--kind", default="slide", choices=["slide", "clinical"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default="")
    ap.add_argument("--atlas", default=os.environ.get("HISTOLOGY_ATLAS_DIR") or os.path.join(
        os.environ.get("HISTOLOGY_KB_DIR")
        or str(ROOT / "backend" / "data" / "histology_kb"), "atlas"))
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    ap.add_argument("--model", default="", help="serverdagi model bilan bir xil bo'lsin")
    ap.add_argument("--cases", default="",
                    help="keys arxivi papkasi: saqlangan keyslarni qayta ishga tushirish")
    args = ap.parse_args()

    if args.cases:
        return replay_cases(args)

    if args.compare:
        a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        pa = {r["label"]: r for r in a["cases"]}
        pb = {r["label"]: r for r in b["cases"]}
        both = sorted(set(pa) & set(pb))
        print(f"{args.compare[0]}: {a['accuracy']:.0%}   {args.compare[1]}: {b['accuracy']:.0%}")
        for k in both:
            if pa[k].get("ok") != pb[k].get("ok"):
                arrow = "→ TUZALDI" if pb[k].get("ok") else "→ BUZILDI"
                print(f"  {arrow}  {k}\n      A: {pa[k].get('pred')}\n      B: {pb[k].get('pred')}")
        return

    import logging
    logging.getLogger("medlab").setLevel(logging.WARNING)

    from lab_core import engine as eng

    # engine.py .env ni override=True bilan yuklaydi — ya'ni qobiqdagi
    # o'zgaruvchi bosib ketiladi. Modelni import DAN KEYIN o'rnatamiz,
    # aks holda serverdagi model o'rniga .env dagisi sinaladi.
    eng.ensure_openai_from_env()
    if args.model:
        os.environ["OPENAI_MODEL_ID"] = args.model
        eng.OPENAI_MODEL_ID = args.model     # global, .env dan keyin qo'yiladi
    model = eng.OPENAI_MODEL_ID

    cases = pick_cases(args.atlas, args.n, args.kind, args.seed)
    print(f"model={model}  namunalar={len(cases)}  turi={args.kind}\n")

    MARK = {"togri": "TO'G'RI", "qisman": "QISMAN", "xato": "XATO  ",
            "yorliq_yaroqsiz": "y.yaroqsiz", "hakam_xato": "hakam?"}
    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rec in pool.map(run_case, cases):
            results.append(rec)
            mark = MARK.get(rec.get("verdict"), "ERR   ")
            conf = f"{rec.get('confidence')}%" if rec.get("confidence") else "  - "
            flag = "  [tavsifiy]" if rec.get("descriptive") else (
                "  [taxminiy]" if rec.get("provisional") else "")
            print(f"  {mark:10s} {conf:>5}  {rec['label'][:32]:32s} → "
                  f"{str(rec.get('pred') or rec.get('error'))[:42]}{flag}")

    scored = [r for r in results if r.get("verdict") in ("togri", "qisman", "xato")]
    hit = sum(1 for r in scored if r["verdict"] == "togri")
    part = sum(1 for r in scored if r["verdict"] == "qisman")
    acc = hit / len(scored) if scored else 0.0
    skipped = len(results) - len(scored)
    print(f"\nANIQLIK: {hit}/{len(scored)} = {acc:.0%}"
          f"   (+{part} qisman = {(hit+part)/len(scored):.0%})"
          f"   [{skipped} baholanmadi]   {time.time()-t0:.0f}s")

    if scored:
        desc = sum(1 for r in scored if r.get("descriptive"))
        prov = sum(1 for r in scored if r.get("provisional"))
        print(f"Qo'riqchi ishga tushgan: tavsifiy nom {desc}/{len(scored)}, "
              f"taxminiy {prov}/{len(scored)}")
        bad_head = sum(1 for r in results if r.get("heading_ok") is False)
        print(f"«#### TASHXIS» sarlavhasi yo'q (qayta ishlash o'tkazib yuborilgan): "
              f"{bad_head}/{len(results)}")
        okc = [r["confidence"] for r in scored if r["verdict"] == "togri" and r.get("confidence")]
        noc = [r["confidence"] for r in scored if r["verdict"] == "xato" and r.get("confidence")]
        if okc and noc:
            print(f"O'rtacha ishonchlilik — to'g'ri: {sum(okc)/len(okc):.0f}%  "
                  f"xato: {sum(noc)/len(noc):.0f}%")

    out = args.out or f"bench_{args.kind}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    Path(out).write_text(json.dumps({
        "model": model, "kind": args.kind, "n": len(results), "seed": args.seed,
        "accuracy": acc, "cases": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saqlandi: {out}")


if __name__ == "__main__":
    main()
