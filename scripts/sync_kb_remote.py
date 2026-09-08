#!/usr/bin/env python3
"""Kitob vektor indeksini serverga yuborish (git'da yo'q — hajmi katta).

Indeks: backend/data/histology_kb/{embeddings.npy, chunks.jsonl, meta.json}

Ishlatish:
  set AILAB_SSH_PASSWORD=...
  python scripts/sync_kb_remote.py
  python scripts/sync_kb_remote.py --app /opt/ailab --service ailab-gunicorn
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("pip install paramiko", file=sys.stderr)
    sys.exit(1)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
LOCAL_KB = ROOT / "backend" / "data" / "histology_kb"
FILES = ("meta.json", "chunks.jsonl", "embeddings.npy")
# Ma'lumotnoma rasmlar: yuzlab kichik fayl — bittalab yuborish sekin,
# shuning uchun bitta arxivga yig'ilib, serverda ochiladi.
ATLAS_DIR = "atlas"
ATLAS_TAR = "atlas.tar.gz"

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
# Parolsiz kalit (masalan ~/.ssh/imentor_deploy). Sudo uchun baribir parol kerak bo'lishi mumkin.
KEYFILE = os.path.expanduser(os.environ.get("AILAB_SSH_KEY", "").strip())
# Sudo paroli alohida bo'lsa (kalit bilan kirib, sudo parol so'rasa)
SUDO_PASSWORD = os.environ.get("AILAB_SUDO_PASSWORD", "").strip() or PASSWORD


def sudo_bash(client, script, timeout=600):
    if SUDO_PASSWORD:
        inner = f"echo {shlex.quote(SUDO_PASSWORD)} | sudo -S -p '' bash -lc {shlex.quote(script)}"
    else:
        inner = f"sudo -n bash -lc {shlex.quote(script)}"
    stdin, stdout, stderr = client.exec_command(inner, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, (out + ("\n" + err if err.strip() else "")).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.environ.get("AILAB_APP", "/opt/lab-fermi"))
    ap.add_argument("--service", default=os.environ.get("AILAB_SERVICE", "lab-fermi-gunicorn"))
    ap.add_argument("--kb-dir", default=str(LOCAL_KB))
    ap.add_argument("--no-restart", action="store_true")
    args = ap.parse_args()

    if not PASSWORD and not KEYFILE:
        print("AILAB_SSH_PASSWORD yoki AILAB_SSH_KEY kerak", file=sys.stderr)
        return 2

    kb = Path(args.kb_dir)
    missing = [f for f in FILES if not (kb / f).is_file()]
    if missing:
        print(f"Indeks topilmadi ({kb}): {', '.join(missing)}", file=sys.stderr)
        return 3

    total = sum((kb / f).stat().st_size for f in FILES)
    print(f"Indeks: {kb}  ({total / 1048576:.1f} MB)")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"SSH {USER}@{HOST}:{PORT}")
    client.connect(
        HOST,
        port=PORT,
        username=USER,
        password=PASSWORD or None,
        key_filename=KEYFILE or None,
        timeout=45,
        allow_agent=False,
        look_for_keys=False,
    )

    remote_kb = f"{args.app}/backend/data/histology_kb"
    staging = "/tmp/medlab_kb_upload"
    code, out = sudo_bash(client, f"mkdir -p {shlex.quote(staging)} && chmod 777 {shlex.quote(staging)}")
    if code != 0:
        print(out, file=sys.stderr)
        return 4

    # Atlas papkasi bo'lsa — arxivga yig'amiz
    atlas_src = kb / ATLAS_DIR
    atlas_tar = None
    if atlas_src.is_dir() and (atlas_src / "atlas.json").is_file():
        import tarfile
        import tempfile

        atlas_tar = Path(tempfile.gettempdir()) / ATLAS_TAR
        n = 0
        with tarfile.open(atlas_tar, "w:gz") as tf:
            for p in sorted(atlas_src.rglob("*")):
                if p.is_file():
                    tf.add(p, arcname=str(p.relative_to(atlas_src)).replace("\\", "/"))
                    n += 1
        print(f"atlas arxivi: {n} fayl, {atlas_tar.stat().st_size / 1048576:.1f} MB")

    sftp = client.open_sftp()
    t0 = time.time()
    if atlas_tar:
        print(f"yuborilmoqda {ATLAS_TAR} ({atlas_tar.stat().st_size / 1048576:.1f} MB)")
        sftp.put(str(atlas_tar), f"{staging}/{ATLAS_TAR}")
    for f in FILES:
        src = kb / f
        size = src.stat().st_size
        last = [0]

        def progress(done, tot, name=f, size=size):
            pct = int(done * 100 / max(1, tot))
            if pct >= last[0] + 10:
                last[0] = pct
                print(f"  {name}: {pct}%")

        print(f"yuborilmoqda {f} ({size / 1048576:.1f} MB)")
        sftp.put(str(src), f"{staging}/{f}", callback=progress)
    sftp.close()
    print(f"yuklandi {time.time() - t0:.0f}s")

    # Atomik almashtirish: avval yangi papkaga, keyin joyiga
    script = f"""
set -euo pipefail
mkdir -p {shlex.quote(remote_kb)}
for f in {' '.join(FILES)}; do
  mv -f {shlex.quote(staging)}/$f {shlex.quote(remote_kb)}/$f
done
if [ -f {shlex.quote(staging)}/{ATLAS_TAR} ]; then
  rm -rf {shlex.quote(remote_kb)}/{ATLAS_DIR}.new
  mkdir -p {shlex.quote(remote_kb)}/{ATLAS_DIR}.new
  tar -xzf {shlex.quote(staging)}/{ATLAS_TAR} -C {shlex.quote(remote_kb)}/{ATLAS_DIR}.new
  rm -rf {shlex.quote(remote_kb)}/{ATLAS_DIR}
  mv {shlex.quote(remote_kb)}/{ATLAS_DIR}.new {shlex.quote(remote_kb)}/{ATLAS_DIR}
  rm -f {shlex.quote(staging)}/{ATLAS_TAR}
  echo "atlas: $(find {shlex.quote(remote_kb)}/{ATLAS_DIR} -name '*.jpg' | wc -l) rasm"
fi
chown -R www-data:www-data {shlex.quote(remote_kb)}
chmod 640 {shlex.quote(remote_kb)}/*.json {shlex.quote(remote_kb)}/*.jsonl {shlex.quote(remote_kb)}/*.npy 2>/dev/null || true
find {shlex.quote(remote_kb)}/{ATLAS_DIR} -type d -exec chmod 750 {{}} + 2>/dev/null || true
find {shlex.quote(remote_kb)}/{ATLAS_DIR} -type f -exec chmod 640 {{}} + 2>/dev/null || true
rmdir {shlex.quote(staging)} 2>/dev/null || true
ls -la {shlex.quote(remote_kb)}
"""
    code, out = sudo_bash(client, script)
    print(out)
    if code != 0:
        return 5

    if not args.no_restart:
        code, out = sudo_bash(
            client,
            f"systemctl restart {shlex.quote(args.service)} && sleep 3 && "
            f"curl -fsS http://127.0.0.1:8011/api/health",
        )
        print(out)
        if code != 0:
            return 6

    client.close()
    print("OK — indeks serverda")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
