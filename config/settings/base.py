"""Shared settings; secrets come exclusively from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env", override=False)

SECRET_KEY = os.environ.get("SECRET_KEY", "")
DEBUG = False
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")]
INSTALLED_APPS = [
    "config.apps.PlatformAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.accounts",
    "apps.dashboard",
    "apps.companies",
    "apps.campaigns",
    "apps.crm",
    "apps.audit",
    "apps.jobs",
    "apps.prospecting",
    "apps.scoring",
    "apps.proposals",
    "apps.compliance",
    "apps.outreach",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
LANGUAGE_CODE = "es"
TIME_ZONE = os.getenv("DEFAULT_TIMEZONE", "Europe/Madrid")
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_000_000
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_000_000
FILE_UPLOAD_HANDLERS = [
    "apps.proposals.uploads.BoundedAssetUploadHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}
# Real email requires both this environment opt-in and the workspace opt-in.
OUTREACH_MODE = os.getenv("OUTREACH_MODE", "simulation").strip().lower()
MAX_EMAILS_PER_DAY = int(os.getenv("MAX_EMAILS_PER_DAY", "25"))
MAX_WHATSAPP_PER_DAY = int(os.getenv("MAX_WHATSAPP_PER_DAY", "10"))
CONTACT_COOLDOWN_DAYS = int(os.getenv("CONTACT_COOLDOWN_DAYS", "14"))
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "mock").strip().lower()
WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "mock").strip().lower()
AI_PROVIDER = "mock"
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WHATSAPP_WABA_ID = os.getenv("WHATSAPP_WABA_ID", "").strip()
WHATSAPP_GRAPH_API_VERSION = os.getenv("WHATSAPP_GRAPH_API_VERSION", "").strip()
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_WEBHOOK_ENABLED = os.getenv("WHATSAPP_WEBHOOK_ENABLED", "false").lower() == "true"
WHATSAPP_HTTP_TIMEOUT = float(os.getenv("WHATSAPP_HTTP_TIMEOUT", "15"))
OUTREACH_SMTP_HOST = os.getenv("OUTREACH_SMTP_HOST", "").strip()
OUTREACH_SMTP_PORT = int(os.getenv("OUTREACH_SMTP_PORT", "587"))
OUTREACH_SMTP_USERNAME = os.getenv("OUTREACH_SMTP_USERNAME", "").strip()
OUTREACH_SMTP_PASSWORD = os.getenv("OUTREACH_SMTP_PASSWORD", "")
OUTREACH_SMTP_SENDER = os.getenv("OUTREACH_SMTP_SENDER", "").strip()
OUTREACH_SMTP_SECURITY = os.getenv("OUTREACH_SMTP_SECURITY", "starttls").strip().lower()
OUTREACH_SMTP_TIMEOUT = float(os.getenv("OUTREACH_SMTP_TIMEOUT", "15"))
PLACES_PROVIDER = os.getenv("PLACES_PROVIDER", "mock").strip().lower()
GOOGLE_PLACES_ENABLED = os.getenv("GOOGLE_PLACES_ENABLED", "false").lower() == "true"
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
PLACES_CACHE_SECONDS = int(os.getenv("PLACES_CACHE_SECONDS", "300"))
JOB_RETRY_BASE_SECONDS = 5
WEBSITE_PROVIDER = os.getenv("WEBSITE_PROVIDER", "mock").strip().lower()
WEB_CRAWLER_ENABLED = os.getenv("WEB_CRAWLER_ENABLED", "false").lower() == "true"
WEB_CRAWLER_USER_AGENT = os.getenv("WEB_CRAWLER_USER_AGENT", "AgencyCRM-Bot/1.0")
WEB_CRAWLER_TIMEOUT_SECONDS = float(os.getenv("WEB_CRAWLER_TIMEOUT_SECONDS", "10"))
WEB_CRAWLER_TOTAL_SECONDS = float(os.getenv("WEB_CRAWLER_TOTAL_SECONDS", "60"))
WEB_CRAWLER_MAX_BYTES = int(os.getenv("WEB_CRAWLER_MAX_BYTES", "2000000"))
WEB_CRAWLER_MAX_PAGES_PER_DOMAIN = int(os.getenv("WEB_CRAWLER_MAX_PAGES_PER_DOMAIN", "8"))
WEB_CRAWLER_DELAY_SECONDS = float(os.getenv("WEB_CRAWLER_DELAY_SECONDS", "1"))
JOB_STALE_AFTER_SECONDS = 900
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"webhook_query": {"()": "common.logging.WebhookQueryFilter"}},
    "formatters": {"standard": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "filters": ["webhook_query"],
            "propagate": False,
        },
        "httpx": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "httpcore": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
