"""
So'rov/javob sxemalari (hujjatlashtirish va keyinchalik validatsiya uchun).
"""
import re

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from api.models import AnalysisRecord, UserProfile
from lab_core.engine import _normalize_lab_type

LAB_LABELS = {
    "hematology": "Gematologiya",
    "urine": "Siydik tahlili",
    "coprology": "Koprologiya",
    "spermogram": "Sperma tahlili",
    "smear": "Mazok",
    "csf": "Likvor (OMS)",
    "lymph": "Limfa suyuqligi",
    "le_cell": "LE-hujayra",
    "prostata_sok": "Prostata SOK",
    "myelogram": "Miyelogramma",
    "blood_parasites": "Qon parazitlari",
    "afb_microscopy": "KOCH / AFB",
    "mycology": "Mikologiya",
    "dermatology": "Dermatologiya",
    "derm_microscopy": "Teri qirindisi",
    "effusion_cytology": "Effuziya sitologiyasi",
    "histology": "Gistologiya",
}


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, write_only=True, style={"input_type": "password"})


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    hudud_code = serializers.CharField(
        max_length=16,
        min_length=2,
        trim_whitespace=True,
        help_text="Daftar identifikatsiyasi: hudud kodi (2–16 lotin harf yoki raqam).",
    )
    clinic_no = serializers.CharField(
        max_length=3,
        trim_whitespace=True,
        help_text="Poliklinika raqami (1–3 raqam).",
    )
    visit_type = serializers.CharField(
        max_length=3,
        min_length=2,
        required=False,
        default="OP",
        trim_whitespace=True,
        help_text="Tur qisqartmasi (masalan OP — oylaviy poliklinika).",
    )
    password = serializers.CharField(
        write_only=True, min_length=8, max_length=128, style={"input_type": "password"}
    )
    password_confirm = serializers.CharField(
        write_only=True, max_length=128, style={"input_type": "password"}
    )

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages)) from e
        return value

    def validate_username(self, value):
        v = value.strip()
        if User.objects.filter(username__iexact=v).exists():
            raise serializers.ValidationError("Bu login allaqachon band.")
        return v

    def validate_hudud_code(self, value):
        raw = (value or "").strip().upper()
        cleaned = re.sub(r"[^A-Z0-9]", "", raw)
        if len(cleaned) < 2 or len(cleaned) > 16:
            raise serializers.ValidationError(
                "Hudud kodi 2 dan 16 tagacha lotin harf yoki raqamdan iborat bo‘lishi kerak."
            )
        return cleaned

    def validate_clinic_no(self, value):
        digits = re.sub(r"\D", "", (value or "").strip())
        if not digits or len(digits) > 3:
            raise serializers.ValidationError(
                "Poliklinika raqami 1 dan 3 tagacha raqam bo‘lishi kerak."
            )
        return digits

    def validate_visit_type(self, value):
        raw = (value or "OP").strip().upper()
        letters = re.sub(r"[^A-Z]", "", raw)
        if len(letters) < 2 or len(letters) > 3:
            raise serializers.ValidationError(
                "Tur kodi 2 yoki 3 ta lotin harfidan iborat bo‘lishi kerak (masalan OP)."
            )
        return letters

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Parollar mos kelmaydi."}
            )
        return attrs

    def create(self, validated_data):
        validated_data = validated_data.copy()
        validated_data.pop("password_confirm", None)
        hudud_code = validated_data.pop("hudud_code")
        clinic_no = validated_data.pop("clinic_no")
        visit_type = validated_data.pop("visit_type")
        password = validated_data.pop("password")
        email = (validated_data.pop("email", "") or "").strip()
        username = validated_data.pop("username").strip()
        user = User.objects.create_user(
            username=username, email=email, password=password
        )
        UserProfile.objects.create(
            user=user,
            hudud_code=hudud_code,
            clinic_no=clinic_no,
            visit_type=visit_type,
        )
        return user


class MicroscopeStateSerializer(serializers.Serializer):
    ocular = serializers.CharField(required=False, allow_blank=True, max_length=500)
    objective = serializers.CharField(required=False, allow_blank=True, max_length=500)
    total_label = serializers.CharField(required=False, allow_blank=True, max_length=500)
    condenser = serializers.CharField(required=False, allow_blank=True, max_length=500)
    illumination = serializers.CharField(required=False, allow_blank=True, max_length=500)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class AnalyzeJsonSerializer(serializers.Serializer):
    lab_type = serializers.CharField(required=False, default="hematology", max_length=48)
    prompt = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=6000)
    source = serializers.ChoiceField(choices=("camera", "upload"), default="upload")
    microscope = MicroscopeStateSerializer(required=False)
    patient_name = serializers.CharField(required=False, allow_blank=True, max_length=120, default="")
    sample_id = serializers.CharField(required=False, allow_blank=True, max_length=40, default="")
    age = serializers.CharField(required=False, allow_blank=True, max_length=8, default="")
    sex = serializers.CharField(required=False, allow_blank=True, max_length=16, default="")
    ward = serializers.CharField(required=False, allow_blank=True, max_length=80, default="")
    specimen_site = serializers.CharField(required=False, allow_blank=True, max_length=80, default="")
    clinical_note = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")
    region = serializers.CharField(required=False, allow_blank=True, max_length=40, default="")
    locality = serializers.CharField(required=False, allow_blank=True, max_length=80, default="")
    clinic = serializers.CharField(required=False, allow_blank=True, max_length=8, default="")
    facility_type = serializers.CharField(required=False, allow_blank=True, max_length=8, default="")
    priority = serializers.CharField(required=False, allow_blank=True, max_length=16, default="")

    def validate_lab_type(self, value):
        return _normalize_lab_type(value)


class StartCameraSerializer(serializers.Serializer):
    index = serializers.IntegerField(min_value=0, max_value=32, default=0)


class AnalysisRecordSerializer(serializers.ModelSerializer):
    lab_label = serializers.SerializerMethodField()
    created_label = serializers.SerializerMethodField()
    preview = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisRecord
        fields = (
            "public_id",
            "lab_type",
            "lab_label",
            "source",
            "status",
            "text",
            "img_count",
            "patient_name",
            "sample_id",
            "age",
            "sex",
            "ward",
            "specimen_site",
            "clinical_note",
            "region",
            "locality",
            "clinic",
            "facility_type",
            "created_at",
            "created_label",
            "preview",
        )

    def get_lab_label(self, obj):
        return LAB_LABELS.get(obj.lab_type, obj.lab_type)

    def get_created_label(self, obj):
        dt = timezone.localtime(obj.created_at)
        return dt.strftime("%d.%m.%Y %H:%M")

    def get_preview(self, obj):
        raw = getattr(obj, "_preview_src", None)
        if raw is None:
            raw = obj.text or ""
        raw = re.sub(r"[|#*`]+", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        if len(raw) > 140:
            return raw[:140] + "…"
        return raw


class AnalysisListSerializer(AnalysisRecordSerializer):
    class Meta(AnalysisRecordSerializer.Meta):
        fields = tuple(f for f in AnalysisRecordSerializer.Meta.fields if f != "text")


class AnalysisSearchSerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=64, default="")
    lab_type = serializers.CharField(required=False, allow_blank=True, max_length=48, default="")
