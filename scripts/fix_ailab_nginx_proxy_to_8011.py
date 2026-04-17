#!/usr/bin/env python3
"""
1Panel: /etc/nginx/conf.d/1panel.conf ichida server_name ailab.ziyrak.org bo'lgan
blokda proxy_pass 18888 (1Panel UI) -> 8011 (ailab-gunicorn).

Faqat shu server blokiga tegadi; default_server (server_name _) o'zgarmaydi.

Ishlatish (serverda root):
  sudo python3 /opt/ailab/scripts/fix_ailab_nginx_proxy_to_8011.py
  sudo nginx -t && sudo systemctl reload nginx
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

CONF = Path("/etc/nginx/conf.d/1panel.conf")
MARKER = "server_name ailab.ziyrak.org ailabapi.ziyrak.org"
OLD_PROXY = "proxy_pass http://127.0.0.1:18888"
NEW_PROXY = "proxy_pass http://127.0.0.1:8011"
BODY = "    client_max_body_size 220M;\n"


def extract_ailab_server_block(text: str) -> tuple[int, int] | None:
    pos = text.find(MARKER)
    if pos == -1:
        return None
    start = text.rfind("server {", 0, pos)
    if start == -1:
        return None
    nxt = text.find("server {", pos + 20)
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
    if "client_max_body_size" not in new_block:
        new_block = new_block.replace(
            MARKER,
            MARKER + "\n\n" + BODY.rstrip("\n"),
            1,
        )
    return text[:start] + new_block + text[end:], True, "8011 ga yangilandi (kerak bo'lsa client_max_body_size 220M qo'shildi)"


def main() -> int:
    if not CONF.is_file():
        print(f"Yo'q: {CONF}", file=sys.stderr)
        return 1
    raw = CONF.read_text(encoding="utf-8", errors="replace")
    new_text, changed, msg = patch(raw)
    print(msg)
    if not changed:
        return 0
    bak = CONF.with_suffix(CONF.suffix + ".bak-ailab8011")
    shutil.copy2(CONF, bak)
    print(f"Zaxira: {bak}")
    CONF.write_text(new_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
