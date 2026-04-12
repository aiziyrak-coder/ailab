"""API tezlik cheklovi (DoS / ZiyrakAi inference xarajatini yumshatish)."""
from rest_framework.throttling import SimpleRateThrottle


class _IdentThrottle(SimpleRateThrottle):
    """SimpleRateThrottle talab qiladi: get_cache_key."""

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class AnalyzeThrottle(_IdentThrottle):
    scope = "analyze"


class CameraThrottle(_IdentThrottle):
    scope = "camera"


class AuthThrottle(_IdentThrottle):
    """Login / register — bruteforce kamaytirish."""

    scope = "auth"
