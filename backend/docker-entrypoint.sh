#!/bin/sh
set -e
cd /app
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-300}" \
  --access-logfile - \
  --error-logfile -
