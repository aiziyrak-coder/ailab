#!/usr/bin/env python3
"""
Serverda GEMINI_API_KEY ni /opt/ailab/backend/.env ga yozadi va gunicorn ni qayta ishga tushiradi.

  set AILAB_SSH_PASSWORD=...
  set GEMINI_API_KEY=sizning-kalit
  python scripts/set_gemini_key_remote.py

Kalit faqat serverda qoladi — repoga kiritilmaydi.
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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if not GEMINI_API_KEY:
    print("GEMINI_API_KEY muhit o'zgaruvchisi kerak.", file=sys.stderr)
    sys.exit(2)

BASH = rf"""set -e
f=/opt/ailab/backend/.env
if grep -q '^GEMINI_API_KEY=' "$f"; then
  sed -i 's|^GEMINI_API_KEY=.*|GEMINI_API_KEY={GEMINI_API_KEY}|' "$f"
else
  echo 'GEMINI_API_KEY={GEMINI_API_KEY}' >> "$f"
fi
echo "GEMINI_API_KEY o'rnatildi:"
grep '^GEMINI_API_KEY=' "$f" | cut -c1-30
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
