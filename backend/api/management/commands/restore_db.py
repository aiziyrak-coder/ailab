"""Zaxiradan tiklash — SQLite fayl nusxasi. Productionda ehtiyot bo'ling."""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "SQLite zaxirasini tiklaydi (--yes majburiy)."

    def add_arguments(self, parser):
        parser.add_argument("backup_file", help="db_YYYYMMDD_HHMMSS.sqlite3 yo'li")
        parser.add_argument("--yes", action="store_true", help="Tasdiqlash")

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("Tasdiqlash uchun --yes qo'shing. Joriy DB o'zgaradi.")
        engine = settings.DATABASES["default"]["ENGINE"]
        if "sqlite" not in engine:
            raise CommandError("restore_db hozircha faqat SQLite. PostgreSQL: psql < dump.sql")
        src = Path(options["backup_file"])
        if not src.is_file():
            raise CommandError(f"Fayl yo'q: {src}")
        dest = Path(settings.DATABASES["default"]["NAME"])
        connection.close()
        shutil.copy2(src, dest)
        self.stdout.write(self.style.SUCCESS(f"Tiklendi: {src} → {dest}"))
