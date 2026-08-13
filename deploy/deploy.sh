#!/usr/bin/env bash
# Serverda: /opt/ailab da git pull + migrate + restart.
# Ishlatish: sudo bash /opt/ailab/deploy/deploy.sh
set -euo pipefail
APP="${AILAB_APP:-/opt/ailab}"
cd "$APP"

PREV="$(git rev-parse HEAD)"
echo "$PREV" > /tmp/ailab.prev_sha
echo "Oldingi commit: $PREV"

git fetch origin
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git pull --ff-only origin "$BRANCH" || git reset --hard "origin/$BRANCH"

cd "$APP/backend"
. .venv/bin/activate
pip install -q -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear
chown -R www-data:www-data staticfiles snapshots logs backups 2>/dev/null || true

systemctl restart ailab-gunicorn
sleep 2
curl -fsS "http://127.0.0.1:8011/api/health" >/dev/null
echo "OK deploy $(git -C "$APP" rev-parse --short HEAD)  (rollback: sudo bash $APP/deploy/rollback.sh)"
