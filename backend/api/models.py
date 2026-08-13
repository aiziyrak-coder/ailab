from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone


class UserProfile(models.Model):
    """Foydalanuvchi profili — daftar identifikatsiyasi (hudud, poliklinika, tur)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    hudud_code = models.CharField(
        max_length=16,
        blank=True,
        db_index=True,
        help_text="Daftar hudud kodi (2–16 lotin harf yoki raqam, masalan FSH, QOQ, FARMRG).",
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


class AnalysisRecord(models.Model):
    """Saqlangan tahlil — qidiruv uchun unikal ID (ML-YYMMDD-NNNN)."""

    public_id = models.CharField(max_length=24, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="analyses",
    )
    lab_type = models.CharField(max_length=48, db_index=True)
    source = models.CharField(max_length=16, default="upload")
    status = models.CharField(max_length=32, default="tahlil_qilinmoqda", db_index=True)
    text = models.TextField(blank=True)
    job_id = models.CharField(max_length=64, blank=True, db_index=True)
    img_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return self.public_id

    @classmethod
    def create_pending(cls, user, lab_type, source, job_id="", img_count=0, status="tahlil_qilinmoqda"):
        prefix = timezone.localtime().strftime("ML-%y%m%d-")
        last_err = None
        for _ in range(8):
            try:
                with transaction.atomic():
                    last = (
                        cls.objects.select_for_update()
                        .filter(public_id__startswith=prefix)
                        .order_by("-public_id")
                        .first()
                    )
                    n = 1
                    if last:
                        try:
                            n = int(str(last.public_id).rsplit("-", 1)[-1]) + 1
                        except ValueError:
                            n = 1
                    rec = cls(
                        public_id=f"{prefix}{n:04d}",
                        user=user,
                        lab_type=lab_type or "hematology",
                        source=source or "upload",
                        status=status or "tahlil_qilinmoqda",
                        job_id=job_id or "",
                        img_count=max(0, min(int(img_count or 0), 32767)),
                    )
                    rec.save()
                    return rec
            except IntegrityError as e:
                last_err = e
                continue
        raise last_err or IntegrityError("Tahlil ID yaratilmadi")
