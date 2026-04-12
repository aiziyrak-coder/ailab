"""Ro'yxatdan o'tish, kirish, chiqish (Django sessiya + DRF)."""
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

from .serializers import LoginSerializer, RegisterSerializer
from .template_mixins import MedlabPublicTemplateMixin
from .throttling import AuthThrottle


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
        return Response(
            {
                "success": True,
                "message": "Xush kelibsiz",
                "user": {"username": user.username, "email": user.email or ""},
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
        return Response(
            {
                "success": True,
                "message": "Hisob yaratildi",
                "user": {"username": user.username, "email": user.email or ""},
            },
            status=status.HTTP_201_CREATED,
        )


class LogoutApiView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request._request)
        return Response({"success": True, "message": "Chiqildi"})


class MeApiView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        u = request.user
        return Response(
            {
                "success": True,
                "user": {"username": u.username, "email": getattr(u, "email", "") or ""},
            }
        )


class AuthCheckView(View):
    """Sessiya tekshiruvi — to'g'ridan-to'g'ri Django (DRF autentifikatsiyasiz)."""

    def get(self, request):
        u = request.user
        if u.is_authenticated:
            return JsonResponse({"authenticated": True, "username": u.username})
        return JsonResponse({"authenticated": False, "username": None})
