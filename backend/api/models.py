from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """Foydalanuvchi profili — daftar identifikatsiyasi (hudud, poliklinika, tur)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    hudud_code = models.CharField(
        max_length=3,
        blank=True,
        db_index=True,
        help_text="Hudud qisqartmasi (2–3 lotin harfi, masalan FSH, FT, BT).",
    )
    clinic_no = models.CharField(
        max_length=3,
        default="7",
        help_text="Poliklinika raqami (1–3 raqam), masalan 7.",
    )
    visit_type = models.CharField(
        max_length=3,
        default="OP",
        help_text="Muassasa turi qisqartmasi (2–3 harf), masalan OP — oylaviy poliklinika.",
    )

    class Meta:
        verbose_name = "User profile"
        verbose_name_plural = "User profiles"

    def __str__(self):
        return f"{self.user_id}: {self.hudud_code or '-'} / {self.clinic_no} / {self.visit_type}"
