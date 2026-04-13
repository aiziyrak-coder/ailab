#!/usr/bin/env python3
"""
Serverda:
1) MEDLAB_PUBLIC_API_BASE= (bo'sh) — brauzer barcha API ni UI domenida chaqiradi (CSRF/sessiya ishonchli).
2) create_demo_user --force — demo / MedLabDemo2026!
3) ailab-gunicorn restart

  set AILAB_SSH_PASSWORD=...
  python scripts/ensure_prod_login_remote.py
"""
from __future__ import annotations

import base64
import os
import sys

try:
    import paramiko
except ImportError:
    print("pip install paramiko", file=sys.stderr)
    sys.exit(1)

PASSWORD = os.environ.get("AILAB_SSH_PASSWORD", "").strip()
HOST = os.environ.get("AILAB_SSH_HOST", "167.71.53.238").strip()

BASH = r"""set -e
ENV=/opt/ailab/backend/.env
if [ ! -f "$ENV" ]; then echo "No $ENV"; exit 1; fi
chgrp www-data "$ENV" 2>/dev/null || true
chmod 640 "$ENV" 2>/dev/null || true
if grep -q '^MEDLAB_PUBLIC_API_BASE=' "$ENV"; then
  sed -i 's|^MEDLAB_PUBLIC_API_BASE=.*|MEDLAB_PUBLIC_API_BASE=|' "$ENV"
else
  echo 'MEDLAB_PUBLIC_API_BASE=' >> "$ENV"
fi
grep '^MEDLAB_PUBLIC_API_BASE=' "$ENV" || true
cd /opt/ailab/backend
sudo -u www-data .venv/bin/python manage.py create_demo_user --force
systemctl restart ailab-gunicorn
sleep 2
systemctl is-active ailab-gunicorn
echo OK ensure_prod_login
"""


def main() -> int:
    if not PASSWORD:
        print("AILAB_SSH_PASSWORD kerak", file=sys.stderr)
        return 2
    b64 = base64.b64encode(BASH.encode()).decode()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=45, allow_agent=False, look_for_keys=False)
    _, o, e = c.exec_command(f"echo {b64} | base64 -d | bash", get_pty=True, timeout=120)
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    code = o.channel.recv_exit_status()
    c.close()
    buf = getattr(sys.stdout, "buffer", None)
    for chunk in (out, err):
        if not chunk:
            continue
        line = chunk if chunk.endswith("\n") else chunk + "\n"
        if buf:
            buf.write(line.encode("utf-8", errors="replace"))
        else:
            print(line, end="")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
