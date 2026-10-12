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
WEBSITE_PROVIDER = "mock"
WEB_CRAWLER_ENABLED = False
MAX_EMAILS_PER_DAY = 25
MAX_WHATSAPP_PER_DAY = 10
CONTACT_COOLDOWN_DAYS = 14
OUTREACH_SMTP_HOST = OUTREACH_SMTP_USERNAME = OUTREACH_SMTP_PASSWORD = ""
OUTREACH_SMTP_SENDER = ""
