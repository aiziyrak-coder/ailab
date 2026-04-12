#!/usr/bin/env python3
"""
Bir marta: serverda Let's Encrypt + nginx SSL (ailab.ziyrak.org, ailabapi.ziyrak.org).

Avval HTTP ishlayotgan bo'lishi kerak (nginx 80, DNS A yozuvlari serverga).

  set AILAB_SSH_PASSWORD=...
  python scripts/issue_ailab_https_cert.py

Ixtiyoriy: AILAB_CERTBOT_EMAIL=you@domain.com
"""
from __future__ import annotations

import base64
import os
import shlex
import sys

try:
    import paramiko
except ImportError:
    print("paramiko: pip install paramiko", file=sys.stderr)
    sys.exit(1)

HOST = os.environ.get("AILAB_SSH_HOST", "167.71.53.238").strip()
USER = os.environ.get("AILAB_SSH_USER", "root").strip()
PASSWORD = os.environ.get("AILAB_SSH_PASSWORD", "").strip()
CERT_EMAIL = os.environ.get("AILAB_CERTBOT_EMAIL", "").strip()

if CERT_EMAIL:
    email_line = f"EMAIL={shlex.quote(CERT_EMAIL)}"
else:
    email_line = """EMAIL=$(grep -rh "^email" /etc/letsencrypt/renewal/*.conf 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' ' || true)"""

BASH = f"""set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq certbot python3-certbot-nginx >/dev/null

{email_line}
if [ -z "$EMAIL" ]; then
  EMAIL="admin@ziyrak.org"
fi

certbot --nginx \\
  -d ailab.ziyrak.org -d ailabapi.ziyrak.org \\
  --non-interactive --agree-tos --email "$EMAIL" \\
  --redirect \\
  --cert-name ailab.ziyrak.org

nginx -t
systemctl reload nginx
echo OK ailab HTTPS certbot+nginx
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
            timeout=60,
            allow_agent=False,
            look_for_keys=False,
        )
    except Exception as e:
        print(f"SSH: {e}", file=sys.stderr)
        return 1

    cmd = f"echo {b64} | base64 -d | bash"
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=300)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    client.close()

    buf = getattr(sys.stdout, "buffer", None)
    for chunk in (out, err):
        if not chunk:
            continue
        line = chunk if chunk.endswith("\n") else chunk + "\n"
        if buf is not None:
            buf.write(line.encode("utf-8", errors="replace"))
        else:
            print(line, end="")

    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
