# MedLab AI

- **`frontend/`** — barcha UI: `index.html`, `static/css`, `static/js`, logo.
- **`backend/`** — Django + Django REST Framework: kamera, ZiyrakAi tahlil (OpenAI orqali), API.

## Ishga tushirish

1. `backend/.env.example` ni `backend/.env` ga nusxalang, `OPENAI_API_KEY` va `DJANGO_SECRET_KEY` kiriting.
2. `pip install -r backend/requirements.txt`
3. `cd backend && python manage.py migrate`
4. Asosiy foydalanuvchi: `python manage.py create_demo_user`  
   - **Login:** `12345`  
   - **Parol:** `1234512345`
5. `python manage.py runserver 0.0.0.0:8000` yoki loyiha ildizidagi `start.bat`

Brauzer: **http://127.0.0.1:8000** — avtomatik `/login` ga yo‘naltiriladi.

API va brauzer **Django sessiyasi** + **CSRF** (`X-CSRFToken`) orqali bog‘langan; kamera oqimi (`/video_feed`) cookie bilan ishlaydi. Alohida domen/portda frontend bo‘lsa, `CORS_ALLOWED_ORIGINS` va kerak bo‘lsa `CSRF_TRUSTED_ORIGINS` ni to‘g‘ri yozing.

Alohida frontend server (masalan Live Server) ishlatsangiz, `frontend/index.html` ichida `window.__MEDLAB_API_BASE__ = 'http://127.0.0.1:8000';` qo‘ying va `backend/.env` da `CORS_ALLOWED_ORIGINS` ni o‘sha origin bilan to‘ldiring.

## API (REST)

| Metod | Yo‘l | Tavsif |
|--------|------|--------|
| POST | `/api/auth/login` | JSON: `username`, `password` (sessiya) |
| POST | `/api/auth/register` | JSON: `username`, `password`, `password_confirm`, `email?` |
| POST | `/api/auth/logout` | Chiqish (kirgan foydalanuvchi) |
| GET | `/api/auth/me` | Joriy foydalanuvchi |
| GET | `/api/auth/check` | `{ authenticated, username }` |
| GET | `/api/health` | Monitoring (ochiq): `database`, `snapshot_dir_writable`, `ziyrakai_ready`, `product`; muammo bo‘lsa **503** |
| GET | `/api/scan_cameras` | Kamera ro‘yxati |
| POST | `/api/start_camera` | JSON: `{"index": 0}` |
| POST | `/api/stop_camera` | Kamerani to‘xtatish |
| POST | `/api/analyze` | JSON yoki `multipart/form-data` (fayllar) |
| GET | `/api/analysis_result` | Oxirgi tahlil (faqat joriy foydalanuvchi) |
| GET | `/api/analyses?q=&lab_type=&page=` | Tahlillar tarixi (ID qidiruv, sahifalash) |
| GET | `/api/analyses/<public_id>` | Bitta tahlil (`ML-YYMMDD-NNNN`) |
| POST | `/api/capture` | Snapshot |
| GET | `/api/status` | Oqim + tahlil holati |
| GET | `/video_feed` | MJPEG oqim |

Ishlab chiqarish: `gunicorn` (Linux) yoki `waitress-serve` (Windows) — `config.wsgi:application`.

**Production runbook** (deploy, backup, rollback, health): [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

Linux server: `sudo bash deploy/deploy.sh` — migrate + collectstatic + gunicorn restart. Qaytarish: `sudo bash deploy/rollback.sh`.

Health (auth yo‘q): `GET /health` yoki `GET /api/health`.

**Docker (PostgreSQL bilan namuna):** loyiha ildizidan `docker compose up --build`. `DJANGO_SECRET_KEY` va `OPENAI_API_KEY` ni `.env` yoki `docker compose` muhitida bering. Ma’lumotlar bazasi: `DATABASE_URL` (bo‘sh bo‘lsa SQLite).

**PostgreSQL:** `DATABASE_URL=postgresql://...` va `pip install` (requirements da `psycopg` bor). `DB_CONN_MAX_AGE` — ulanish pool (sekund).

## Sifat tekshiruvi (audit / CI)

```bash
cd backend
set DJANGO_SECRET_KEY=your-48-char-random-string-for-local-check
python manage.py check
python manage.py test api.tests -v 2
```

Ishlab chiqarishdan oldin (Linux/macOS):

```bash
cd backend && DJANGO_DEBUG=0 DJANGO_SECRET_KEY="$(python -c "import secrets; print(secrets.token_urlsafe(48))")" python manage.py check --deploy
```

GitHub Actions: `.github/workflows/ci.yml` — har push/PR da `check` + `test`.

## Xavfsizlik va ishlab chiqarish

- **`backend/.env` repoga kirmasin** — `.gitignore` da; faqat `.env.example` nusxalang.
- **`DJANGO_DEBUG=0`**: `DJANGO_SECRET_KEY` kamida **40 belgi**, tasodifiy (`secrets.token_urlsafe(48)`).
- **500 xatolik**: `DEBUG=0` da mijozga umumiy xabar, tafsilotlar faqat server jurnalida.
- **API javoblari**: barcha `/api/*` marshrutlar uchun `Cache-Control: no-store` (sessiya/tahlil sizib chiqmasin).
- **CORS**: `CORS_ALLOWED_ORIGINS` bo‘sh qoldirilganda `DEBUG=1` da barcha origin ruxsat etiladi, lekin **cookie yuborilmaydi** (brauzer qoidasi). Alohida frontend uchun aniq origin ro‘yxatini yozing.
- **Tezlik**: `THROTTLE_ANALYZE` / `THROTTLE_CAMERA` — `.env.example` ga qarang.
- **413**: Juda katta yuklama JSON xato bilan qaytadi.
- **409** (`/api/analyze`): Oldingi tahlil tugamaguncha yangi so‘rov — `busy: true`; brauzer natijani kuzatishni davom ettiradi.
- **Bir nechta video**: bir vaqtda faqat **birinchi** video tahlil qilinadi (ogohlantirish `warnings` da).
- **Statik fayllar (prod)**: `cd backend && python manage.py collectstatic --noinput` — `WhiteNoise` `staticfiles/` dan beradi.
- **Sessiya**: `SESSION_COOKIE_AGE` (sekund, standart 8 soat).
- **Admin**: `DJANGO_ADMIN_ENABLED=1` — `https://<API-domen>/admin/` (masalan `ailabapi`). `0` bo‘lsa marshrutlar umuman yo‘q — 404. Kuchli parol + kerak bo‘lsa nginx da IP cheklovi.
- **Kirish (prod)**: brauzer **https://lab.fermi.uz** — login **`12345`**.
- **ZiyrakAi (texnik)**: `OPENAI_MAX_RETRIES`, `OPENAI_RETRY_DELAY_SEC` — vaqtinchalik API xatolarida qayta urinish.

## Bilim bazasi — dermatopatologiya kitoblari (RAG)

MedLab tashxisni **kitob mezonlari** asosida qo'yadi. Kitoblar vektor indeksiga
aylantiriladi (`backend/data/histology_kb/`), tahlil paytida faqat mos parchalar
promptga qo'shiladi — kitob matni gitga yozilmaydi va so'zma-so'z ko'chirilmaydi.

Indeksdagi manbalar:

| Manba | Rol |
|-------|-----|
| Weedon's Skin Pathology (3rd ed) + Essentials | dermatopatologiya etaloni |
| Dermatopathology: Diagnosis by First Impression | pattern-tanish (skaner kuchi) |
| Dermatopathology Vademecum / The Basics / Color Atlas | tashxis mezonlari |
| Pathology of Vascular Skin Lesions | tomir lezyonlari |
| Genetics of Melanoma | melanotsitar molekulyar kontekst |
| Атлас диагностических биопсий кожи, Дерматоонкопатология, Цветкова | rus manbalari |
| Junqueira, Langman, Alberts (MBOC) | umumiy gistologiya kanoni |

### O'qitish (indeks yaratish)

```bash
python scripts/ingest_histology_kb.py --pdf-dir "C:\Users\me\Desktop\Gistalogiya Kitoblar" --ocr
```

- Har bir kitob `sha256` bo'yicha keshlanadi — takroriy ishga tushirishda faqat yangi kitob embed qilinadi.
- Skanerlangan (matn qatlami yo'q) PDF `--ocr` bilan OpenAI vision orqali transkripsiya qilinadi, natija keshda saqlanadi.
- PDF endi diskda bo'lmasa: `--keep-source junqueira --keep-source mboc` bilan eski vektorlar saqlab qolinadi.
- Holat: `python scripts/ingest_histology_kb.py --status` yoki `GET /api/health` → `knowledge_base`.

### Teri holatida qo'shimcha protokol

Namuna joyi yoki organ qulfi **teri** bo'lsa, promptga Weedon/Ackerman uslubidagi
algoritm qo'shiladi: skaner kuchi → to'qima reaksiya patterni → hujayra yo'nalishi →
melanotsitar xavfsizlik (Breslow, mitoz, pagetoid) → BCC/SCC/DF-DFSP farqlash → IHC.
Melanoma yetakchi tashxis bo'lishi uchun Breslow, mitoz va pagetoid/assimetriya
dalillari matnda bo'lishi shart — aks holda hisobot qayta yoziladi.
