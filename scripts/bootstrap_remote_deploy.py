#!/usr/bin/env python3
"""
SSH orqali serverda AILab (faqat yangi nginx + systemd, 8010-port).

  set AILAB_SSH_PASSWORD=...
  python scripts/bootstrap_remote_deploy.py

Parol repoga kiritilmaydi. Root parolini chatda ulashgan bo'lsangiz — darhol o'zgartiring, SSH kalit ishlating.
"""
from __future__ import annotations

import base64
import os
import sys

try:
    import paramiko
except ImportError:
    print("paramiko: pip install paramiko", file=sys.stderr)
    sys.exit(1)

HOST = os.environ.get("AILAB_SSH_HOST", "167.71.53.238").strip()
USER = os.environ.get("AILAB_SSH_USER", "root").strip()
PASSWORD = os.environ.get("AILAB_SSH_PASSWORD", "").strip()
REPO = os.environ.get(
    "AILAB_SSH_REPO",
    "https://github.com/aiziyrak-coder/ailab.git",
).strip()

BASH = f"""set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip nginx >/dev/null

APP=/opt/ailab
REPO="{REPO}"
if [ ! -d "$APP/.git" ]; then
  rm -rf "$APP"
  git clone --depth 1 "$REPO" "$APP"
else
  cd "$APP" && git pull --ff-only || (git fetch origin && git reset --hard origin/main) || (git fetch origin && git reset --hard origin/master)
fi

cd "$APP/backend"
python3 -m venv .venv
. .venv/bin/activate
pip install -q -U pip
pip install -q -r requirements.txt

if [ ! -f .env ]; then
python3 << 'PYENV'
import secrets
from pathlib import Path
p = Path(".env")
secret = secrets.token_urlsafe(48)
p.write_text(
    "\\n".join(
        [
            "DJANGO_DEBUG=0",
            f"DJANGO_SECRET_KEY={{secret}}",
            "DJANGO_ALLOWED_HOSTS=ailab.ziyrak.org,ailabapi.ziyrak.org,127.0.0.1,localhost",
            "MEDLAB_PUBLIC_API_BASE=https://ailabapi.ziyrak.org",
            "SESSION_COOKIE_DOMAIN=.ziyrak.org",
            "CSRF_COOKIE_DOMAIN=.ziyrak.org",
            "CORS_ALLOWED_ORIGINS=https://ailab.ziyrak.org",
            "CSRF_TRUSTED_ORIGINS=https://ailab.ziyrak.org,https://ailabapi.ziyrak.org",
            "SECURE_PROXY_SSL_HEADER=X-Forwarded-Proto,https",
            "SESSION_COOKIE_SECURE=1",
            "DJANGO_SECURE_SSL_REDIRECT=0",
            "DJANGO_ADMIN_ENABLED=0",
            "GEMINI_API_KEY=",
            "GEMINI_MODEL_ID=gemini-2.5-pro",
        ]
    )
    + "\\n"
)
PYENV
fi

mkdir -p staticfiles snapshots
chown -R www-data:www-data staticfiles snapshots
touch db.sqlite3 2>/dev/null || true
chown www-data:www-data db.sqlite3 2>/dev/null || true

. .venv/bin/activate
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear

cp "$APP/deploy/ailab-gunicorn.service" /etc/systemd/system/ailab-gunicorn.service
cp "$APP/deploy/nginx-ailab.conf" /etc/nginx/sites-available/ailab-ziyrak
ln -sf /etc/nginx/sites-available/ailab-ziyrak /etc/nginx/sites-enabled/ailab-ziyrak

systemctl daemon-reload
systemctl enable ailab-gunicorn
systemctl restart ailab-gunicorn
nginx -t
systemctl reload nginx
echo OK ailab gunicorn 127.0.0.1:8010
"""


def main() -> int:
    if not PASSWORD:
        print("AILAB_SSH_PASSWORD kerak.", file=sys.stderr)
        return 2

    b64 = base64.b64encode(BASH.encode("utf-8")).decode("ascii")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            HOST,
            username=USER,
            password=PASSWORD,
            timeout=45,
            allow_agent=False,
            look_for_keys=False,
        )
    except Exception as e:
        print(f"SSH: {e}", file=sys.stderr)
        return 1

    cmd = f"echo {b64} | base64 -d | bash"
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    client.close()

    def _safe_write(s: str, stream) -> None:
        if not s:
            return
        buf = getattr(stream, "buffer", None)
        line = s if s.endswith("\n") else s + "\n"
        if buf is not None:
            buf.write(line.encode("utf-8", errors="replace"))
        else:
            print(line, end="")

    _safe_write(out, sys.stdout)
    _safe_write(err, sys.stderr)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
