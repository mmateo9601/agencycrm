"""HTTPS deployment template; configure hosts, TLS termination and static serving first."""

import os

from . import base

globals().update({name: getattr(base, name) for name in dir(base) if name.isupper()})
DEBUG = False
ALLOWED_HOSTS = base.ALLOWED_HOSTS
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": base.BASE_DIR / os.getenv("DATABASE_NAME", "db.sqlite3"),
        "OPTIONS": {"timeout": 20},
    }
}
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "3600"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = os.getenv("SECURE_HSTS_PRELOAD", "false").lower() == "true"
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": base.OUTREACH_SMTP_HOST or "127.0.0.1",
            "port": base.OUTREACH_SMTP_PORT,
            "username": base.OUTREACH_SMTP_USERNAME,
            "password": base.OUTREACH_SMTP_PASSWORD,
            "use_tls": base.OUTREACH_SMTP_SECURITY != "ssl",
            "use_ssl": base.OUTREACH_SMTP_SECURITY == "ssl",
            "timeout": base.OUTREACH_SMTP_TIMEOUT,
        },
    }
}
