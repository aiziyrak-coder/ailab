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
    patient_name = models.CharField(max_length=120, blank=True, default="")
    sample_id = models.CharField(max_length=40, blank=True, default="", db_index=True)
    age = models.CharField(max_length=8, blank=True, default="")
    sex = models.CharField(max_length=16, blank=True, default="")
    ward = models.CharField(max_length=80, blank=True, default="")
    specimen_site = models.CharField(max_length=80, blank=True, default="")
    clinical_note = models.CharField(max_length=200, blank=True, default="")
    region = models.CharField(max_length=40, blank=True, default="")
    locality = models.CharField(max_length=80, blank=True, default="")
    clinic = models.CharField(max_length=8, blank=True, default="")
    facility_type = models.CharField(max_length=8, blank=True, default="")
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
    def create_pending(
        cls,
        user,
        lab_type,
        source,
        job_id="",
        img_count=0,
        status="tahlil_qilinmoqda",
        patient_name="",
        sample_id="",
        age="",
        sex="",
        ward="",
        specimen_site="",
        clinical_note="",
        region="",
        locality="",
        clinic="",
        facility_type="",
    ):
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
                        lab_type=lab_type or "histology",
                        source=source or "upload",
                        status=status or "tahlil_qilinmoqda",
                        job_id=job_id or "",
                        img_count=max(0, min(int(img_count or 0), 32767)),
                        patient_name=(patient_name or "")[:120],
                        sample_id=(sample_id or "")[:40],
                        age=(age or "")[:8],
                        sex=(sex or "")[:16],
                        ward=(ward or "")[:80],
                        specimen_site=(specimen_site or "")[:80],
                        clinical_note=(clinical_note or "")[:200],
                        region=(region or "")[:40],
                        locality=(locality or "")[:80],
                        clinic=(clinic or "")[:8],
                        facility_type=(facility_type or "")[:8],
                    )
                    rec.save()
                    return rec
            except IntegrityError as e:
                last_err = e
                continue
        raise last_err or IntegrityError("Tahlil ID yaratilmadi")
