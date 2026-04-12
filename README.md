# MedLab AI

- **`frontend/`** — barcha UI: `index.html`, `static/css`, `static/js`, logo.
- **`backend/`** — Django + Django REST Framework: kamera, ZiyrakAi tahlil (Google Generative AI orqali), API.

## Ishga tushirish

1. `backend/.env.example` ni `backend/.env` ga nusxalang, `GEMINI_API_KEY` va `DJANGO_SECRET_KEY` kiriting.
2. `pip install -r backend/requirements.txt`
3. `cd backend && python manage.py migrate`
4. **Demo foydalanuvchi**: `python manage.py create_demo_user` (`DJANGO_DEBUG=0` bo‘lsa faqat `create_demo_user --force` — tavsiya etilmaydi)  
   - **Login:** `demo`  
   - **Parol:** `MedLabDemo2026!`
5. `python manage.py runserver 0.0.0.0:8000` yoki loyiha ildizidagi `start.bat`

Brauzer: **http://127.0.0.1:8000** — avtomatik `/login` ga yo‘naltiriladi. Yangi hisob: **http://127.0.0.1:8000/register**

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
| GET | `/api/analysis_result` | Oxirgi tahlil |
| POST | `/api/capture` | Snapshot |
| GET | `/api/status` | Oqim + tahlil holati |
| GET | `/video_feed` | MJPEG oqim |

Ishlab chiqarish: `gunicorn` (Linux) yoki `waitress-serve` (Windows) — `config.wsgi:application`.

**Docker (PostgreSQL bilan namuna):** loyiha ildizidan `docker compose up --build`. `DJANGO_SECRET_KEY` va `GEMINI_API_KEY` ni `.env` yoki `docker compose` muhitida bering. Ma’lumotlar bazasi: `DATABASE_URL` (bo‘sh bo‘lsa SQLite).

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
- **ZiyrakAi (texnik)**: `GEMINI_MAX_RETRIES`, `GEMINI_RETRY_DELAY_SEC` — vaqtinchalik API xatolarida qayta urinish.
