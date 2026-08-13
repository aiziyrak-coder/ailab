#!/usr/bin/env bash
# Oxirgi deploy.sh dan oldingi commitga qaytish.
set -euo pipefail
APP="${AILAB_APP:-/opt/ailab}"
SHA_FILE=/tmp/ailab.prev_sha
if [ ! -f "$SHA_FILE" ]; then
  echo "Rollback nuqtasi yo'q ($SHA_FILE). Qo'lda: git log && git checkout <sha>"
  exit 1
fi
SHA="$(cat "$SHA_FILE")"
cd "$APP"
git checkout --force "$SHA"
cd "$APP/backend"
. .venv/bin/activate
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear
systemctl restart ailab-gunicorn
sleep 2
curl -fsS "http://127.0.0.1:8011/api/health" >/dev/null
echo "OK rollback $SHA"
