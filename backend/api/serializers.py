"""
So'rov/javob sxemalari (hujjatlashtirish va keyinchalik validatsiya uchun).
"""
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from lab_core.engine import _normalize_lab_type


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, write_only=True, style={"input_type": "password"})


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
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

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Parollar mos kelmaydi."}
            )
        return attrs

    def create(self, validated_data):
        validated_data = validated_data.copy()
        validated_data.pop("password_confirm", None)
        password = validated_data.pop("password")
        email = (validated_data.pop("email", "") or "").strip()
        username = validated_data.pop("username").strip()
        return User.objects.create_user(
            username=username, email=email, password=password
        )


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
