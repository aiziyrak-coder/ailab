"""
MedLab AI — Django konfiguratsiyasi.
"""
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from django.core.exceptions import ImproperlyConfigured  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip() or os.environ.get(
    "SECRET_KEY", "dev-only-change-in-production"
)
if SECRET_KEY == "dev-only-change-in-production":
    import warnings

    warnings.warn(
        "DJANGO_SECRET_KEY o'rnatilmagan — ishlab chiqarishda .env da belgilang.",
        stacklevel=1,
    )

DEBUG = os.environ.get("DJANGO_DEBUG", "1").strip().lower() in ("1", "true", "yes")

# Django admin: prod da odatda o'chiq (DJANGO_ADMIN_ENABLED=1 bilan yoqish mumkin)
ADMIN_ENABLED = os.environ.get(
    "DJANGO_ADMIN_ENABLED", "1" if DEBUG else "0"
).strip().lower() in ("1", "true", "yes")

APPEND_SLASH = False

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
if DEBUG and "*" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.extend(["0.0.0.0", "[::1]", "testserver"])

# UI va API turli subdomain bo'lsa, shablonlarda window.__MEDLAB_API_BASE__ uchun (.env)
MEDLAB_PUBLIC_API_BASE = os.environ.get("MEDLAB_PUBLIC_API_BASE", "").strip()

_INSECURE_SECRET_MARKERS = ("dev-only", "changeme", "secret", "django-insecure")
if not DEBUG:
    sk = SECRET_KEY.lower()
    if len(SECRET_KEY) < 40 or any(m in sk for m in _INSECURE_SECRET_MARKERS):
        raise ImproperlyConfigured(
            "DEBUG=False: DJANGO_SECRET_KEY kamida 40 belgi va tasodifiy bo'lishi kerak "
            "(masalan: python -c \"import secrets; print(secrets.token_urlsafe(48))\")."
        )

# lab_core cheklovi bilan mos (yuklash hajmi)
from lab_core.engine import MAX_FILE_READ_BYTES  # noqa: E402

DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_FILE_READ_BYTES
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_FILE_READ_BYTES
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "api",
]

MIDDLEWARE = [
    "api.middleware.RequestBodyLimitJsonMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "api.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [FRONTEND_DIR],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

_database_url = os.environ.get("DATABASE_URL", "").strip()
if _database_url:
    import dj_database_url

    _db_max_age = os.environ.get("DB_CONN_MAX_AGE", "600").strip()
    try:
        _conn_max_age = max(0, int(_db_max_age))
    except ValueError:
        _conn_max_age = 600
    DATABASES = {
        "default": dj_database_url.parse(_database_url, conn_max_age=_conn_max_age),
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [FRONTEND_DIR / "static"] if (FRONTEND_DIR / "static").is_dir() else []
STATIC_ROOT = BASE_DIR / "staticfiles"

WHITENOISE_MAX_AGE = 60 if DEBUG else 3600

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

try:
    SESSION_COOKIE_AGE = max(300, int(os.environ.get("SESSION_COOKIE_AGE", str(60 * 60 * 8))))
except ValueError:
    SESSION_COOKIE_AGE = 60 * 60 * 8
SESSION_SAVE_EVERY_REQUEST = (
    os.environ.get("SESSION_SAVE_EVERY_REQUEST", "0").strip().lower() in ("1", "true", "yes")
)

# Masalan: .ziyrak.org — ailab.* va ailabapi.* bir xil cookie (faqat .env da)
_session_dom = os.environ.get("SESSION_COOKIE_DOMAIN", "").strip()
if _session_dom:
    SESSION_COOKIE_DOMAIN = _session_dom
_csrf_dom = os.environ.get("CSRF_COOKIE_DOMAIN", "").strip()
if _csrf_dom:
    CSRF_COOKIE_DOMAIN = _csrf_dom

# ailab.* ↔ ailabapi.* (bir xil sayt, turli host) — fetch + cookie
SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax").strip() or "Lax"
CSRF_COOKIE_SAMESITE = os.environ.get("CSRF_COOKIE_SAMESITE", "Lax").strip() or "Lax"

REST_FRAMEWORK = {
    "JSON_RENDERER_OPTIONS": {"ensure_ascii": False},
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "EXCEPTION_HANDLER": "api.exceptions.medlab_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "analyze": os.environ.get("THROTTLE_ANALYZE", "36/hour"),
        "camera": os.environ.get("THROTTLE_CAMERA", "180/hour"),
        "auth": os.environ.get("THROTTLE_AUTH", "45/hour"),
    },
}

# Alohida portdagi frontend (masalan Live Server)
# Eslatma: CORS_ALLOW_ALL_ORIGINS=True bilan brauzer cookie yubormaydi (spetsifikatsiya).
_cors = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
if _cors:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors.split(",") if o.strip()]
    CORS_ALLOW_ALL_ORIGINS = False
    CORS_ALLOW_CREDENTIALS = True
else:
    CORS_ALLOW_CREDENTIALS = False
    CORS_ALLOW_ALL_ORIGINS = DEBUG

if not DEBUG and not _cors:
    CORS_ALLOW_ALL_ORIGINS = False

_csrf_trusted = os.environ.get("CSRF_TRUSTED_ORIGINS", "").strip()
if _csrf_trusted:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_trusted.split(",") if o.strip()]

# Ishlab chiqarish (HTTPS ortida reverse proxy bo'lsa)
if not DEBUG:
    _sec_proxy = os.environ.get("SECURE_PROXY_SSL_HEADER", "").strip()
    if _sec_proxy:
        pair = [x.strip() for x in _sec_proxy.split(",", 1)]
        if len(pair) == 2:
            SECURE_PROXY_SSL_HEADER = (pair[0], pair[1])
    if os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        SECURE_SSL_REDIRECT = True
    if os.environ.get("SESSION_COOKIE_SECURE", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        SESSION_COOKIE_SECURE = True
        CSRF_COOKIE_SECURE = True
    _hsts = os.environ.get("SECURE_HSTS_SECONDS", "0").strip()
    try:
        SECURE_HSTS_SECONDS = max(0, int(_hsts))
    except ValueError:
        SECURE_HSTS_SECONDS = 0
    if SECURE_HSTS_SECONDS:
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "").strip().lower() in (
            "1",
            "true",
        )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "medlab": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "medlab",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
