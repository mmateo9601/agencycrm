"""Local SQLite configuration. HTTP cookies are limited to local development."""

from .base import *

DEBUG = os.getenv("DEBUG", "True").lower() in {"true", "1", "yes"}
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / os.getenv("DATABASE_NAME", "db.sqlite3"),
        "OPTIONS": {"timeout": 20},
    }
}
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
