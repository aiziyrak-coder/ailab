"""
So'rov/javob sxemalari (hujjatlashtirish va keyinchalik validatsiya uchun).
"""
import re

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from lab_core.engine import _normalize_lab_type

from .models import UserProfile


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, write_only=True, style={"input_type": "password"})


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    hudud_code = serializers.CharField(
        max_length=3,
        min_length=2,
        trim_whitespace=True,
        help_text="Daftar identifikatsiyasi: hudud kodi (2–3 lotin harfi).",
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
        letters = re.sub(r"[^A-Z]", "", raw)
        if len(letters) < 2 or len(letters) > 3:
            raise serializers.ValidationError(
                "Hudud kodi 2 yoki 3 ta lotin harfidan iborat bo‘lishi kerak."
            )
        return letters

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

    def validate_lab_type(self, value):
        return _normalize_lab_type(value)


class StartCameraSerializer(serializers.Serializer):
    index = serializers.IntegerField(min_value=0, max_value=16, default=0)
