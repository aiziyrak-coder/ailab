"""Xavfsizlik sarlavhalari, 413 JSON va maxfiy API uchun kesh boshqaruvi."""

from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse


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