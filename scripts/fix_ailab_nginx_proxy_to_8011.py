#!/usr/bin/env python3
"""
1Panel: /etc/nginx/conf.d/1panel.conf ichida server_name ailab.ziyrak.org bo'lgan
server blokida proxy_pass 18888 -> 8011 (faqat shu almashtirish).

client_max_body_size qo'shilmaydi (oldingi versiya server_name qatorini buzib,
nginx: unexpected ";" xatosiga olib kelgan).

Yozilgach `nginx -t` va muvaffaq bo'lsa `systemctl reload nginx`; xato bo'lsa fayl zaxiraga qaytadi.

Zaxiradan qo'lda tiklash (birinchi patchdan oldingi nusxa):
  sudo cp /etc/nginx/conf.d/1panel.conf.bak-ailab8011 /etc/nginx/conf.d/1panel.conf
  sudo nginx -t && sudo systemctl reload nginx
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

CONF = Path("/etc/nginx/conf.d/1panel.conf")
MARKER = "server_name ailab.ziyrak.org ailabapi.ziyrak.org"
OLD_PROXY = "proxy_pass http://127.0.0.1:18888"
NEW_PROXY = "proxy_pass http://127.0.0.1:8011"


def extract_ailab_server_block(text: str) -> tuple[int, int] | None:
    pos = text.find(MARKER)
    if pos == -1:
        return None
    start = text.rfind("server {", 0, pos)
    if start == -1:
        return None
    nxt = text.find("\nserver {", pos + len(MARKER))
    end = len(text) if nxt == -1 else nxt
    return start, end


def patch(text: str) -> tuple[str, bool, str]:
    span = extract_ailab_server_block(text)
    if span is None:
        return text, False, "ailab server_name topilmadi (1panel.conf tekshiring)"
    start, end = span
    block = text[start:end]
    if OLD_PROXY not in block:
        if NEW_PROXY in block:
            return text, False, "allaqachon 8011 ga yo'naltirilgan"
        return text, False, "18888 proxy_pass topilmadi — qo'lda tekshiring"
    new_block = block.replace(OLD_PROXY, NEW_PROXY, 1)
    return text[:start] + new_block + text[end:], True, "8011 ga yangilandi"


def main() -> int:
    if not CONF.is_file():
        print(f"Yo'q: {CONF}", file=sys.stderr)
        return 1
    raw = CONF.read_text(encoding="utf-8", errors="replace")
    new_text, changed, msg = patch(raw)
    print(msg)
    if not changed:
        return 0
    bak = CONF.parent / f"1panel.conf.pre-patch-{int(time.time())}.bak"
    shutil.copy2(CONF, bak)
    print(f"Zaxira (yozishdan oldin): {bak}")
    CONF.write_text(new_text, encoding="utf-8")
    test = subprocess.run(
        ["nginx", "-t"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if test.returncode != 0:
        shutil.copy2(bak, CONF)
        print("nginx -t xato — fayl zaxiradan qaytarildi:", file=sys.stderr)
        if test.stderr:
            print(test.stderr, file=sys.stderr)
        if test.stdout:
            print(test.stdout, file=sys.stderr)
        return 1
    print("nginx -t: OK")
    rel = subprocess.run(
        ["systemctl", "reload", "nginx"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if rel.returncode != 0:
        print("systemctl reload nginx xato:", file=sys.stderr)
        if rel.stderr:
            print(rel.stderr, file=sys.stderr)
        if rel.stdout:
            print(rel.stdout, file=sys.stderr)
        return 1
    print("nginx reload: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
