from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

# Demo hisob (faqat ishlab chiqarishda o'chiring yoki parolni o'zgartiring)
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "MedLabDemo2026!"


class Command(BaseCommand):
    help = (
        f"Demo foydalanuvchi yaratadi yoki parolini yangilaydi: "
        f"login={DEMO_USERNAME} parol={DEMO_PASSWORD}"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="DEBUG=False bo'lsa ham yaratish (ishlab chiqarishda tavsiya etilmaydi)",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            self.stderr.write(
                self.style.ERROR(
                    "DEBUG=False: demo hisob xavfli. DJANGO_DEBUG=1 yoki --force qo'shing."
                )
            )
            return
        u, created = User.objects.get_or_create(
            username=DEMO_USERNAME,
            defaults={"email": "", "is_active": True},
        )
        u.set_password(DEMO_PASSWORD)
        u.is_active = True
        u.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"OK — login: {DEMO_USERNAME}  parol: {DEMO_PASSWORD}"
            )
        )
