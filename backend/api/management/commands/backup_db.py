"""SQLite yoki PostgreSQL zaxira nusxasi (backend/backups/)."""
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Ma'lumotlar bazasining zaxira nusxasini yaratadi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default="",
            help="Zaxira papkasi (standart: backend/backups)",
        )

    def handle(self, *args, **options):
        dest_dir = Path(options["dir"] or os.environ.get("BACKUP_DIR") or (settings.BASE_DIR / "backups"))
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        engine = settings.DATABASES["default"]["ENGINE"]

        if "sqlite" in engine:
            import sqlite3

            dest = dest_dir / f"db_{stamp}.sqlite3"
            connection.ensure_connection()
            src_conn = connection.connection
            dst_conn = sqlite3.connect(str(dest))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        elif "postgresql" in engine or "postgres" in engine:
            dest = dest_dir / f"db_{stamp}.sql"
            url = os.environ.get("DATABASE_URL", "").strip()
            if not url:
                self.stderr.write("DATABASE_URL kerak (PostgreSQL dump).")
                return
            subprocess.run(
                ["pg_dump", "--no-owner", "--no-acl", "-f", str(dest), url],
                check=True,
            )
        else:
            self.stderr.write(f"Noma'lum DB: {engine}")
            return

        keep = 14
        try:
            keep = max(1, int(os.environ.get("BACKUP_KEEP_DAYS", "14")))
        except ValueError:
            keep = 14
        cutoff = datetime.now() - timedelta(days=keep)
        for p in dest_dir.glob("db_*"):
            try:
                if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
                    p.unlink()
            except OSError:
                pass

        self.stdout.write(self.style.SUCCESS(f"OK {dest}"))
