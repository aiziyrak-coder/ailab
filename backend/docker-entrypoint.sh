#!/bin/sh
set -e
cd /app
mkdir -p staticfiles snapshots logs backups
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-1}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-300}" \
  --graceful-timeout 45 \
  --access-logfile - \
  --error-logfile -
