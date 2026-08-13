from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

LAB_USERNAME = "12345"
LAB_PASSWORD = "1234512345"


class Command(BaseCommand):
    help = "Asosiy foydalanuvchini yaratadi yoki parolini yangilaydi; demo hisobni o‘chiradi."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ishlab chiqarishda ham yangilash",
        )

    def handle(self, *args, **options):
        deleted, _ = User.objects.filter(username="demo").delete()
        if deleted:
            self.stdout.write("demo hisob o‘chirildi")
        u, created = User.objects.get_or_create(
            username=LAB_USERNAME,
            defaults={"email": "", "is_active": True},
        )
        u.set_password(LAB_PASSWORD)
        u.is_active = True
        u.is_staff = False
        u.save()
        self.stdout.write(self.style.SUCCESS(f"OK — asosiy login: {LAB_USERNAME}"))
