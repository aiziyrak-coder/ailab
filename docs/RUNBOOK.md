# MedLab AI — production runbook

Server: systemd `ailab-gunicorn` (127.0.0.1:8011) + nginx (`ailab.ziyrak.org`).
Lab kompyuter: Windows, mikroskop USB, `start.bat` yoki `runserver` (kamera shu mashinada).

## Muhitlar

| Muhit | `DJANGO_ENV` | `DJANGO_DEBUG` | DB |
|--------|----------------|----------------|-----|
| development | `development` | `1` | SQLite `backend/db.sqlite3` |
| staging | `staging` | `0` | Docker PostgreSQL (`docker compose`) |
| production | `production` | `0` | SQLite yoki `DATABASE_URL` |

`DJANGO_ENV=production` va `DJANGO_DEBUG=1` birga **ishlamaydi**.

Secretlar faqat `backend/.env` (gitda yo‘q). Namuna: `backend/.env.example`.

## Ishga tushirish (lab / dev)

```bat
start.bat
```

yoki `cd backend && python manage.py migrate && python manage.py runserver 0.0.0.0:8000`

## Deploy (Linux server)

```bash
sudo bash /opt/ailab/deploy/deploy.sh
```

Bu: `git pull` → `migrate` → `collectstatic` → `systemctl restart ailab-gunicorn` → `/api/health`.

### Rollback

```bash
sudo bash /opt/ailab/deploy/rollback.sh
```

Oxirgi `deploy.sh` saqlagan commitga qaytadi.

### HTTPS

```bash
certbot --nginx -d ailab.ziyrak.org -d ailabapi.ziyrak.org --redirect
```

Certbot nginx conf ni o‘zgartirgach, `AILAB_RESET_NGINX=1` bilan conf ni qayta yozmang.

## Backup va tiklash

Kunlik timer:

```bash
sudo cp /opt/ailab/deploy/ailab-backup.service /etc/systemd/system/
sudo cp /opt/ailab/deploy/ailab-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ailab-backup.timer
```

Qo‘lda:

```bash
cd /opt/ailab/backend
sudo -u www-data .venv/bin/python manage.py backup_db
```

SQLite tiklash:

```bash
sudo systemctl stop ailab-gunicorn
sudo -u www-data .venv/bin/python manage.py restore_db backups/db_YYYYMMDD_HHMMSS.sqlite3 --yes
sudo systemctl start ailab-gunicorn
```

PostgreSQL: `psql "$DATABASE_URL" < backups/db_....sql`

**Restore ni avval stagingda sinab ko‘ring.**

## Monitoring

- Health (load balancer / uptime): `GET /health` yoki `GET /api/health` — auth yo‘q.
  `ok=false` → HTTP 503 (DB yoki snapshots yozilmayapti).
- Loglar: journald (`journalctl -u ailab-gunicorn -f`) va `backend/logs/medlab.log` (5 MB × 10, rotatsiya).
- Ixtiyoriy: `SENTRY_DSN` + `pip install sentry-sdk`.

## Tez-tez uchraydigan muammolar

| Belgi | Nima qilish |
|--------|-------------|
| 502 nginx | `systemctl status ailab-gunicorn`; `journalctl -u ailab-gunicorn -n 80` |
| Login CSRF | Brauzer cookie; `CSRF_TRUSTED_ORIGINS` HTTPS originlari |
| Tahlil 503 | `OPENAI_API_KEY` `.env` da; `systemctl restart ailab-gunicorn` |
| Kamera yo‘q | Bu Linux serverda USB yo‘q — lab Windows mashinasida ishlatiladi |
| Disk to‘ldi | `backend/logs`, `backend/backups`, `snapshots` ni tekshiring |
| Health 503 | SQLite ruxsati: `chown www-data db.sqlite3`; `snapshots` yoziladimi |

## Gunicorn worker

Standart: **1 worker, 8 thread**. Kamera va jonli tahlil xotirasi jarayonlar o‘rtasida ulashilmaydi.
Faqat rasm yuklash (Tarix DB orqali) uchun `workers>1` mumkin.

## Checklist (chiqarishdan oldin)

- [ ] `DJANGO_DEBUG=0`, `DJANGO_ENV=production`
- [ ] `DJANGO_SECRET_KEY` 48+ tasodifiy belgi
- [ ] HTTPS + `SESSION_COOKIE_SECURE=1`
- [ ] `/health` 200
- [ ] `backup_db` ishladi; restore stagingda sinovdan o‘tdi
- [ ] `ailab-backup.timer` yoqilgan
- [ ] Demo parol o‘zgartirilgan yoki `--force` olib tashlangan
