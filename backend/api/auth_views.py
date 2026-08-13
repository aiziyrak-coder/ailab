"""Ro'yxatdan o'tish, kirish, chiqish (Django sessiya + DRF)."""
import logging

from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UserProfile
from .serializers import LoginSerializer, RegisterSerializer
from .template_mixins import MedlabPublicTemplateMixin
from .throttling import AuthThrottle

auth_log = logging.getLogger("medlab.auth")


def _profile_fields_for_response(user):
    """Daftar identifikatsiyasi uchun profil maydonlari (bo‘sh bo‘lsa ham qoidaga mos)."""
    hudud = clinic_no = visit_type = ""
    try:
        p = user.profile
        hudud = p.hudud_code or ""
        clinic_no = p.clinic_no or ""
        visit_type = p.visit_type or ""
    except UserProfile.DoesNotExist:
        pass
    return hudud, clinic_no, visit_type


def _user_auth_payload(user):
    hudud, clinic_no, visit_type = _profile_fields_for_response(user)
    return {
        "username": user.username,
        "email": user.email or "",
        "hudud_code": hudud,
        "clinic_no": clinic_no,
        "visit_type": visit_type,
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class LoginPageView(MedlabPublicTemplateMixin, TemplateView):
    template_name = "login.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("/")
        return super().get(request, *args, **kwargs)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class RegisterPageView(MedlabPublicTemplateMixin, TemplateView):
    template_name = "register.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("/")
        return super().get(request, *args, **kwargs)


class LoginApiView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        ser = LoginSerializer(data=request.data)
        if not ser.is_valid():
            return Response(
                {"success": False, "message": "Ma'lumotlar noto‘g‘ri", "errors": ser.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        username = ser.validated_data["username"]
        password = ser.validated_data["password"]
        user = authenticate(
            request._request, username=username, password=password
        )
        if user is None:
            auth_log.warning("login_fail username=%s", username)
            return Response(
                {
                    "success": False,
                    "message": "Login yoki parol noto‘g‘ri",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.is_active:
            return Response(
                {"success": False, "message": "Hisob o‘chirilgan"},
                status=status.HTTP_403_FORBIDDEN,
            )
        login(request._request, user)
        auth_log.info("login_ok user=%s", user.username)
        return Response(
            {
                "success": True,
                "message": "Xush kelibsiz",
                "user": _user_auth_payload(user),
            }
        )


class RegisterApiView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        if not ser.is_valid():
            return Response(
                {
                    "success": False,
                    "message": "Ro'yxatdan o'tish ma'lumotlari noto‘g‘ri",
                    "errors": ser.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = ser.save()
        login(request._request, user)
        auth_log.info("register_ok user=%s", user.username)
        return Response(
            {
                "success": True,
                "message": "Hisob yaratildi",
                "user": _user_auth_payload(user),
            },
            status=status.HTTP_201_CREATED,
        )


class LogoutApiView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        name = getattr(request.user, "username", "")
        logout(request._request)
        auth_log.info("logout user=%s", name)
        return Response({"success": True, "message": "Chiqildi"})


class MeApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        u = request.user
        return Response(
            {
                "success": True,
                "user": _user_auth_payload(u),
            }
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class AuthCheckView(View):
    """Sessiya tekshiruvi — to'g'ridan-to'g'ri Django (DRF autentifikatsiyasiz)."""

    def get(self, request):
        u = request.user
        if u.is_authenticated:
            return JsonResponse({"authenticated": True, "username": u.username})
        return JsonResponse({"authenticated": False, "username": None})
