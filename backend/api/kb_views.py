"""Bilimlar bazasi — klinika kutubxonasi (alohida parol bilan ochiladi).

Bo'lim ikki qavatli himoyada: avval platformaga kirish (sessiya), so'ng
bo'lim paroli. Parol `KB_ACCESS_PASSWORD` muhit o'zgaruvchisidan olinadi.
"""
import hmac
import logging
import os

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from lab_core import histology_kb as kb

from .template_mixins import MedlabPublicTemplateMixin
from .throttling import AuthThrottle

log = logging.getLogger("medlab")

SESSION_KEY = "kb_unlocked"
DEFAULT_PASSWORD = "19980912"


def kb_password():
    value = os.environ.get("KB_ACCESS_PASSWORD")
    if value is None:
        value = getattr(settings, "KB_ACCESS_PASSWORD", "") or ""
    value = value.strip()
    return value or DEFAULT_PASSWORD


def is_unlocked(request):
    return bool(request.session.get(SESSION_KEY))


class _KbLocked(Response):
    def __init__(self):
        super().__init__(
            {"ok": False, "locked": True, "error": "Bo'lim qulflangan"},
            status=status.HTTP_403_FORBIDDEN,
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class KnowledgeBasePageView(MedlabPublicTemplateMixin, LoginRequiredMixin, TemplateView):
    """/bilimlar — kutubxona sahifasi (qulf JS tomonda tekshiriladi)."""

    template_name = "knowledge.html"
    login_url = "/login"


class KbUnlockView(APIView):
    """Bo'lim parolini tekshirish."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        given = (request.data.get("password") or "").strip()
        if not given:
            return Response(
                {"ok": False, "error": "Parolni kiriting"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not hmac.compare_digest(given, kb_password()):
            log.warning("kb: noto'g'ri parol user=%s", request.user.username)
            return Response(
                {"ok": False, "error": "Parol noto'g'ri"},
                status=status.HTTP_403_FORBIDDEN,
            )
        request.session[SESSION_KEY] = True
        request.session.modified = True
        return Response({"ok": True})


class KbLockView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        request.session.pop(SESSION_KEY, None)
        request.session.modified = True
        return Response({"ok": True})


class KbStatusView(APIView):
    """Qulf holati — sahifa ochilganda so'raladi (parolsiz)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        st = kb.index_stats()
        return Response(
            {
                "ok": True,
                "unlocked": is_unlocked(request),
                "ready": bool(st.get("ready")),
                "chunks": st.get("chunks", 0),
                "clinic_chunks": st.get("clinic_chunks", 0),
            }
        )


class KbBooksView(APIView):
    """Kutubxonadagi kitoblar ro'yxati."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not is_unlocked(request):
            return _KbLocked()
        data = kb.library_overview()
        return Response({"ok": True, **data})


class KbSearchView(APIView):
    """Kutubxona bo'ylab semantik qidiruv."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not is_unlocked(request):
            return _KbLocked()
        query = (request.data.get("q") or "").strip()
        if len(query) < 3:
            return Response(
                {"ok": False, "error": "Kamida 3 ta belgi yozing"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        source = (request.data.get("source") or "").strip() or None
        if source and source not in kb.SOURCES:
            source = None
        scope = (request.data.get("scope") or "").strip()
        try:
            limit = int(request.data.get("limit") or 12)
        except (TypeError, ValueError):
            limit = 12
        hits = kb.search_library(
            query,
            k=limit,
            source=source,
            clinic_only=(scope == "clinic" and not source),
        )
        return Response({"ok": True, "query": query, "count": len(hits), "hits": hits})
