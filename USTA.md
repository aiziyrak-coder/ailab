# DermaPATH — audit va tuzatish yo'l xaritasi

Loyiha: DermaPATH (Radeski Skin Clinic) — gistopatologiya tahlili platformasi
Stack: Django 5 + DRF (backend), vanilla JS (frontend), OpenAI vision + vektor RAG
Server: https://lab.fermi.uz (systemd `lab-fermi-gunicorn`, deploy: `scripts/deploy_lab_fermi.py`)

## Maqsad
Foydalanuvchi: "deyarli barcha funksiyalarda muammo bor" — har bir funksiyani
haqiqatda sinab, nuqsonlarni topish va tuzatish.

## Usul
Ilova lokal ishga tushiriladi (`preview_start medlab`), brauzerda har funksiya
dasturiy tekshiriladi (o'lchov va holat bilan), topilgan nuqson tuzatiladi va
qayta sinaladi. Har bosqich commit qilinadi.

## Bosqichlar

### 1-bosqich — asosiy UI qatlami ✅
Tekshirildi (16/16 o'tdi): funksiya/element mavjudligi, yangi tahlil tozalashi,
namuna ID, fayl qo'shish/o'chirish/takror/tabiiy tartib, video, manba
almashtirish, mikroskop hisobi, ustuvorlik, tarix ochilishi.

### 2-bosqich — hisobot ko'rsatish ✅ (3 nuqson topildi va tuzatildi)
- **Soxta jadval**: "Organ/qatlam: … | Ishonch: … | Malignite: …" qatori `|`
  belgilari tufayli markdown jadvalga aylanib ketardi. Endi jadval faqat `|`
  bilan boshlanib-tugagan va kamida 2 qator bo'lsa chiziladi.
- **Bemor PDF deyarli bo'sh**: `filterPatientReport` eski jadval formatiga
  qurilgan edi, 662 belgidan 112 tasini qaytarardi. Endi bo'lim bo'yicha
  ishlaydi (texnik FAKT chiqariladi), hech qachon bo'sh qolmaydi.
- **Sarlavha uslubi**: hisobot `.r-h3` ishlatadi, brend CSS `h4` ni bo'yardi —
  sarlavhalar oltin rangda emas edi.

### 3-bosqich — tarix va namuna raqami ✅ (1 nuqson)
- **Noto'g'ri namuna raqami**: tarixdan yozuv ochilganda `selectLab` →
  `refreshSampleId` yozuvning raqamini yangisi bilan almashtirardi (AUD0003
  o'rniga 40FSH7OPHIST0002). Hisobot noto'g'ri raqam bilan chop etilishi mumkin
  edi. Endi ochilgan yozuv raqami qulflanadi, "Yangi tahlil" qulfni ochadi.
- Tekshirildi: ro'yxat, nom/ID bo'yicha qidiruv, ochish, o'chirish, bemor
  kartasining to'lishi.

### 4-bosqich — tahlil oqimi va validatsiya ✅
Bemorsiz/rasmsiz tahlil bloklanadi, PDF kutubxonasi yuklanadi, yozuv o'chadi.

### 5-bosqich — qolgan sohalar  <- HOZIR SHU YERDA
Sinaladi: haqiqiy rasm bilan to'liq tahlil oqimi (ko'rik → kitob → hisobot),
kamera/telefon rejimi, chop etish maketi, ko'p foydalanuvchi holati, backend
chekka holatlari (bir vaqtda ikki tahlil, sessiya tugashi, katta fayl).

## Qoidalar
- Har nuqson: sabab → tuzatish → qayta sinash → commit.
- Frontend o'zgarishda `?v=` versiyasi oshiriladi (kesh).
- Django ishlab turgan jarayon shablonni keshlaydi — HTML o'zgarsa server
  qayta ishga tushiriladi (deploy'da gunicorn baribir restart bo'ladi).
