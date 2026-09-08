# DermaPATH — test qo'llanmasi

Bu hujjat tizimni sinab ko'rish va natijani qanday baholash haqida.

## 0. Boshlashdan oldin

1. **OpenAI hisobida kredit bo'lishi shart.** Tekshirish:
   `https://lab.fermi.uz/api/health` — `"ziyrakai_ready": true` bo'lsin.
   `false` bo'lsa `ziyrakai_problem` sababni aytadi (`kredit`, `kalit`, `aloqa`…),
   sahifaning tepasida esa qizil chiziq chiqadi.
2. Tizim `https://lab.fermi.uz` da, kirish — odatdagi login.

## 1. Qanday rasm yuklash kerak

Natija sifati **kirishdagi rasmga** bog'liq. Har keys uchun:

| Kerak | Nima uchun |
|---|---|
| **10× umumiy ko'rinish** — lezyon va chekkasi bir kadrda | arxitektura, simmetriya, chuqurlik shundan o'qiladi |
| **40× hujayra tafsiloti** — 2–4 kadr, turli maydonlardan | mitoz, atipiya, yadro, mikroabsess |
| To'g'ri H&E bo'yoq (binafsha yadro, pushti sitoplazma) | oq balans avtomatik to'g'rilanadi, lekin yomon bo'yoqni tuzatib bo'lmaydi |
| Fokusda | xira kadr «namuna sifati: past» deb belgilanadi va foizni tushiradi |

Tana surati (klinik foto) bo'lsa — **alohida** «Klinik rasm» maydoniga yuklang,
kesmalar bilan aralashtirmang. Yo'llanmadagi tashxisni «Klinik tashxis»
maydonida tanlang yoki yozing — u gipoteza sifatida hisobga olinadi.

## 2. Hisobot qanday ko'rinishi kerak

```
#### TASHXIS
YAKUNIY XULOSA: Psoriasis vulgaris
Ishonchlilik: 78%  ·  teri, epidermis

#### NEGA SHU TASHXIS
Munro mikroabsessi — KO'RINDI: parakeratozda 3 ta neytrofil to'plami
Rete tekis cho'zilgan — KO'RINDI: bir xil uzunlikdagi 6 ta rete
Rad etildi: Ekzema — spongioz yo'q

#### FAKT (o'lchangan morfologiya)
Mitoz: 0/10 HPF
Chekka: baholab bo'lmaydi
```

- **Bitta nom, bitta foiz.** «Ishonch: past», «barqaror emas» kabi qatorlar yo'q.
- Foiz o'lchangan narsalardan hisoblanadi: maydonlar kelishuvi, belgi soni,
  mezon mosligi, namuna sifati. **100% hech qachon chiqmaydi.**
- Xavfli o'sma nomi 55% dan past bo'lsa — bitta xavfsizlik qatori chiqadi.
- «Tavsifiy morfologiya: …» chiqsa — model aytgan nom ko'rikdagi belgilarga
  zid edi va qo'riqchi uni rad etdi. Bu xato emas, halollik.

## 3. Natijani qanday baholash

Har sinov keysi uchun yozib boring:

| Namuna № | Patolog tashxisi | Dastur tashxisi | Foiz | To'g'ri / qisman / xato | Izoh |
|---|---|---|---|---|---|

«Qisman» — pattern to'g'ri, nozologiya boshqa (masalan psoriaz o'rniga
«psoriaziform dermatit»).

Keys arxivi **yoqilgan** (`CASE_ARCHIVE=1`): har tahlilning kesmalari, ko'rik
JSON'i va tashxis yozuvi serverda `/opt/lab-fermi/backend/data/cases/YYYY-MM/`
papkasida saqlanadi (bemor ismi va palatasi yozilmaydi, 180 kundan keyin
o'chadi). Xato keysning namuna raqamini aytsangiz — uni ochib, qayerda
adashganini ko'rish mumkin.

## 4. O'lchov (dasturchi uchun)

```bash
# Atlas benchmark — kitob rasmlarida aniqlik (yorliq yashirilgan)
python scripts/bench_atlas.py --n 24 --model gpt-5.6-sol --out bench_v2.json

# Ikki versiyani solishtirish
python scripts/bench_atlas.py --compare bench_v1.json bench_v2.json

# Arxivdagi keyslarni yangi kod bilan qayta ishga tushirish (regressiya)
python scripts/bench_atlas.py --cases /opt/lab-fermi/backend/data/cases --model gpt-5.6-sol
```

Bazaviy natija (2026-09-08, eski arxitektura): **~25% aniq, ~50% qisman bilan**.
Har o'zgarish shu raqamga solishtiriladi.

## 5. Sozlamalar (`backend/.env`)

| O'zgaruvchi | Ma'nosi |
|---|---|
| `HISTOLOGY_STRUCTURED=1` | tuzilgan tashxis yo'li (0 — eski matn yo'li) |
| `CASE_ARCHIVE=1` | keys arxivi (0 — o'chiradi) |
| `CASE_ARCHIVE_DAYS=180` | saqlash muddati |
| `HISTOLOGY_OBSERVE_PASSES=3` | mustaqil ko'rik guruhlari soni |
| `OPENAI_MODEL_ID=gpt-5.6-sol` | asosiy model |
