#!/usr/bin/env python3
"""Mavjud server .env da DJANGO_ADMIN_ENABLED=1 qilib, gunicorn qayta ishga tushiradi."""
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
f=/opt/ailab/backend/.env
if grep -q '^DJANGO_ADMIN_ENABLED=' "$f"; then
  sed -i 's/^DJANGO_ADMIN_ENABLED=.*/DJANGO_ADMIN_ENABLED=1/' "$f"
else
  echo 'DJANGO_ADMIN_ENABLED=1' >> "$f"
fi
grep '^DJANGO_ADMIN_ENABLED=' "$f" || true
systemctl restart ailab-gunicorn
echo OK
"""


def main() -> int:
    if not PASSWORD:
        print("AILAB_SSH_PASSWORD kerak", file=sys.stderr)
        return 2
    b64 = base64.b64encode(BASH.encode()).decode()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)
    _, o, e = c.exec_command(f"echo {b64} | base64 -d | bash", get_pty=True)
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    code = o.channel.recv_exit_status()
    c.close()
    sys.stdout.buffer.write((out + err).encode("utf-8", errors="replace"))
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
