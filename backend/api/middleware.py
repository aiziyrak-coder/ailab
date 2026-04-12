"""Xavfsizlik sarlavhalari, 413 JSON va maxfiy API uchun kesh boshqaruvi."""

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.shortcuts import redirect


class ApiHostUiRedirectMiddleware:
    """
    So'rov API domenida (ailabapi) bo'lsa, HTML sahifalarini UI domeniga (ailab) yo'naltiradi.
    Foydalanuvchi xato bilan api.../login ochmasin.
    """

    _UI_PATHS = frozenset({"/", "/login", "/register"})

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ui = getattr(settings, "MEDLAB_PUBLIC_UI_BASE", "") or ""
        api_h = getattr(settings, "MEDLAB_API_HOSTNAME", "") or ""
        if not ui or not api_h:
            return self.get_response(request)
        host = (request.get_host() or "").split(":")[0].lower()
        if host != api_h:
            return self.get_response(request)
        path = request.path or "/"
        if path not in self._UI_PATHS:
            return self.get_response(request)
        target = ui.rstrip("/") + path
        qs = request.META.get("QUERY_STRING", "")
        if qs:
            target += "?" + qs
        return redirect(target)


class RequestBodyLimitJsonMiddleware:
    """Juda katta yuklamada Django HTML o'rniga JSON 413 qaytaradi."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except RequestDataTooBig:
            return JsonResponse(
                {
                    "success": False,
                    "message": "So'rov tanasi juda katta (413).",
                },
                status=413,
            )


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.setdefault("Permissions-Policy", "camera=(self), microphone=()")
        p = (request.path or "").rstrip("/") or "/"
        if p.startswith("/api"):
            response.setdefault("Cache-Control", "no-store, private")
        if p == "/video_feed":
            response.setdefault("Cache-Control", "no-cache, no-store, must-revalidate")
            response.setdefault("Pragma", "no-cache")
        return response