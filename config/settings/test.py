"""Hermetic SQLite tests with temporary media and no external backends."""

import atexit
import tempfile

from .base import *

SECRET_KEY = "test-only-key-not-for-deployment"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
_media_directory = tempfile.TemporaryDirectory(prefix="agencycrm-tests-")
atexit.register(_media_directory.cleanup)
MEDIA_ROOT = Path(_media_directory.name)
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
OUTREACH_MODE = "simulation"
EMAIL_PROVIDER = WHATSAPP_PROVIDER = AI_PROVIDER = PLACES_PROVIDER = "mock"
GOOGLE_PLACES_ENABLED = False
GOOGLE_PLACES_API_KEY = ""
