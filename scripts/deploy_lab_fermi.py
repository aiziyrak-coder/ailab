#!/usr/bin/env python3
"""lab.fermi.uz ga deploy. Faqat shu vhost + lab-fermi-gunicorn (8011). Boshqa nginx saytlar o'zgarmaydi."""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("pip install paramiko", file=sys.stderr)
    sys.exit(1)

def _load_ssh_env():
    """SSH sozlamalari backend/.env dan ham o'qiladi (parol terminal tarixiga tushmasin)."""
    env = Path(__file__).resolve().parents[1] / "backend" / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("AILAB_") and not os.environ.get(k):
            os.environ[k] = v.strip().strip('"').strip("'")


_load_ssh_env()


HOST = os.environ.get("AILAB_SSH_HOST", "192.168.0.101").strip()
PORT = int(os.environ.get("AILAB_SSH_PORT", "22"))
USER = os.environ.get("AILAB_SSH_USER", "admin_root").strip()
PASSWORD = os.environ.get("AILAB_SSH_PASSWORD", "").strip()
REPO = os.environ.get("AILAB_SSH_REPO", "https://github.com/aiziyrak-coder/ailab.git").strip()
DOMAIN = "lab.fermi.uz"


def local_openai_key() -> str:
    env = Path(__file__).resolve().parents[1] / "backend" / ".env"
    if not env.is_file():
        return ""
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("OPENAI_API_KEY=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def sudo_bash(client: paramiko.SSHClient, script: str, timeout: int = 600) -> tuple[int, str]:
    inner = f"echo {shlex.quote(PASSWORD)} | sudo -S -p '' bash -lc {shlex.quote(script)}"
    stdin, stdout, stderr = client.exec_command(inner, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    text = (out + ("\n" + err if err.strip() else "")).strip()
    return code, text


def main() -> int:
    if not PASSWORD:
        print("AILAB_SSH_PASSWORD kerak", file=sys.stderr)
        return 2

    openai_key = local_openai_key()
    key_py = (
        "import pathlib,re\n"
        "p=pathlib.Path('/opt/lab-fermi/backend/.env')\n"
        "t=p.read_text(encoding='utf-8') if p.exists() else ''\n"
        f"k={openai_key!r}\n"
        "if k:\n"
        "    if re.search(r'^OPENAI_API_KEY=.*$', t, re.M):\n"
        "        t=re.sub(r'^OPENAI_API_KEY=.*$', 'OPENAI_API_KEY='+k, t, count=1, flags=re.M)\n"
        "    else:\n"
        "        t=t.rstrip()+'\\nOPENAI_API_KEY='+k+'\\n'\n"
        "    p.write_text(t, encoding='utf-8')\n"
        "    print('openai_key_updated')\n"
        "else:\n"
        "    print('openai_key_skipped')\n"
    )

    remote = rf"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
APP=/opt/lab-fermi
REPO={shlex.quote(REPO)}
mkdir -p /var/www/certbot

if [ ! -d "$APP/.git" ]; then
  rm -rf "$APP"
  git clone --depth 1 "$REPO" "$APP"
else
  cd "$APP"
  git fetch origin
  git checkout -f main || git checkout -f master
  git reset --hard origin/main || git reset --hard origin/master
fi

cd "$APP/backend"
python3 -m venv .venv
. .venv/bin/activate
pip install -q -U pip
pip install -q -r requirements.txt

if [ ! -f .env ]; then
python3 - << 'PYENV'
import secrets
from pathlib import Path
secret = secrets.token_urlsafe(48)
Path(".env").write_text(
    "\n".join([
        "DJANGO_DEBUG=0",
        "DJANGO_ENV=production",
        f"DJANGO_SECRET_KEY={{secret}}",
        "DJANGO_ALLOWED_HOSTS=lab.fermi.uz,127.0.0.1,localhost",
        "MEDLAB_PUBLIC_API_BASE=",
        "MEDLAB_PUBLIC_UI_BASE=https://lab.fermi.uz",
        "CORS_ALLOWED_ORIGINS=https://lab.fermi.uz",
        "CSRF_TRUSTED_ORIGINS=https://lab.fermi.uz,http://lab.fermi.uz",
        "SECURE_PROXY_SSL_HEADER=X-Forwarded-Proto,https",
        "SESSION_COOKIE_SECURE=1",
        "CSRF_COOKIE_SECURE=1",
        "DJANGO_SECURE_SSL_REDIRECT=0",
        "DJANGO_ADMIN_ENABLED=0",
        "OPENAI_API_KEY=",
        "OPENAI_MODEL_ID=gpt-4o",
        "OPENAI_ROUTER_MODEL=gpt-4o-mini",
    ]) + "\n"
)
print("env_created")
PYENV
else
  python3 - << 'PYFIX'
from pathlib import Path
p = Path(".env")
t = p.read_text(encoding="utf-8")
repls = {{
    "DJANGO_ALLOWED_HOSTS=": "DJANGO_ALLOWED_HOSTS=lab.fermi.uz,127.0.0.1,localhost",
    "MEDLAB_PUBLIC_UI_BASE=": "MEDLAB_PUBLIC_UI_BASE=https://lab.fermi.uz",
    "CORS_ALLOWED_ORIGINS=": "CORS_ALLOWED_ORIGINS=https://lab.fermi.uz",
    "CSRF_TRUSTED_ORIGINS=": "CSRF_TRUSTED_ORIGINS=https://lab.fermi.uz,http://lab.fermi.uz",
    "OPENAI_ROUTER_MODEL=": "OPENAI_ROUTER_MODEL=gpt-4o-mini",
}}
lines = []
seen = set()
for line in t.splitlines():
    hit = False
    for prefix, new in repls.items():
        if line.startswith(prefix) or line.startswith("# " + prefix):
            lines.append(new)
            seen.add(prefix)
            hit = True
            break
    if not hit:
        lines.append(line)
for prefix, new in repls.items():
    if prefix not in seen:
        lines.append(new)
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("env_updated")
PYFIX
fi

python3 - << 'PYKEY'
{key_py}
PYKEY

mkdir -p staticfiles snapshots logs backups
chown -R www-data:www-data staticfiles snapshots logs backups
touch db.sqlite3
chown www-data:www-data db.sqlite3 "$APP/backend"
chmod 664 db.sqlite3
chgrp www-data .env
chmod 640 .env

. .venv/bin/activate
python manage.py migrate --noinput
find "$APP/backend" -maxdepth 1 -name 'db.sqlite3*' -exec chown www-data:www-data {{}} \;
sudo -u www-data .venv/bin/python manage.py create_demo_user --force || true
python manage.py collectstatic --noinput --clear
find "$APP/backend" -maxdepth 1 -name 'db.sqlite3*' -exec chown www-data:www-data {{}} \;

cp "$APP/deploy/lab-fermi-gunicorn.service" /etc/systemd/system/lab-fermi-gunicorn.service
cp "$APP/deploy/nginx-ailab.conf" /etc/nginx/sites-available/lab.fermi.uz.conf
ln -sfn /etc/nginx/sites-available/lab.fermi.uz.conf /etc/nginx/sites-enabled/lab.fermi.uz.conf

systemctl daemon-reload
systemctl enable lab-fermi-gunicorn
systemctl restart lab-fermi-gunicorn
nginx -t
systemctl reload nginx
sleep 2
curl -fsS -H 'Host: lab.fermi.uz' http://127.0.0.1/api/health
echo
echo OK_HTTP $(git -C "$APP" rev-parse --short HEAD)
"""

    # Fix f-string braces for the remote script: we used {{ }} for python in remote.
    remote = remote.replace("{key_py}", key_py)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"SSH {USER}@{HOST}:{PORT}")
    client.connect(
        HOST,
        port=PORT,
        username=USER,
        password=PASSWORD,
        timeout=45,
        allow_agent=False,
        look_for_keys=False,
    )
    print("Deploy (clone/pull, gunicorn 8011, faqat lab.fermi.uz nginx)...")
    code, text = sudo_bash(client, remote, timeout=900)
    # redact openai key if it leaked
    if openai_key and openai_key in text:
        text = text.replace(openai_key, "***")
    print(text.encode("utf-8", "replace").decode("utf-8", "replace"))
    if code != 0:
        print("deploy failed", code, file=sys.stderr)
        client.close()
        return 1

    cert = r"""
set -euo pipefail
EMAIL=$(grep -rh '^email' /etc/letsencrypt/renewal/*.conf 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' ' || true)
if [ -z "$EMAIL" ]; then EMAIL="admin@fermi.uz"; fi
if [ ! -d /etc/letsencrypt/live/lab.fermi.uz ]; then
  certbot --nginx -d lab.fermi.uz --non-interactive --agree-tos --email "$EMAIL" --redirect --cert-name lab.fermi.uz
fi
# Deploy HTTP-only shablon SSL ni buzmasin — nginx-ailab.conf allaqachon 443 blokini o'z ichiga oladi
nginx -t
systemctl reload nginx
echo OK_HTTPS
curl -fsS --resolve lab.fermi.uz:443:127.0.0.1 https://lab.fermi.uz/api/health || curl -kfsS https://127.0.0.1/api/health -H 'Host: lab.fermi.uz'
echo
"""
    print("TLS (faqat lab.fermi.uz)...")
    c2, t2 = sudo_bash(client, cert, timeout=180)
    print(t2)
    client.close()
    if c2 != 0:
        print("HTTPS ogohlantirish, HTTP ishlashi mumkin", file=sys.stderr)
        return 0
    print("DONE https://lab.fermi.uz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
