"""
Django settings for mpesa-pay.

Every environment-specific value comes from environment variables (or a
`.env` file in the project root). See `.env.example` for the full list.
"""

import sys
from pathlib import Path
from urllib.parse import urlparse

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
DEBUG = env.bool("DEBUG", default=False)

SECRET_KEY = env("SECRET_KEY", default="") or ("dev-insecure-change-me" if DEBUG else "")
if not SECRET_KEY:
    raise ImproperlyConfigured("Set SECRET_KEY (required when DEBUG=0).")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "axes",
    "payments",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must be last: locks out admin logins after repeated failures.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "payments.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --------------------------------------------------------------------------
# Database: SQLite unless DATABASE_URL is set (Docker sets it to Postgres).
# --------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "admin:login"
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]

# --------------------------------------------------------------------------
# I18N / time. Daraja timestamps must be in Kenyan time.
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static files (served by WhiteNoise, no separate web server needed)
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# --------------------------------------------------------------------------
# Production hardening (only when DEBUG is off)
# --------------------------------------------------------------------------
if not DEBUG:
    SESSION_COOKIE_SECURE = env.bool("SECURE_COOKIES", default=True)
    CSRF_COOKIE_SECURE = env.bool("SECURE_COOKIES", default=True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_CONTENT_TYPE_NOSNIFF = True

# --------------------------------------------------------------------------
# Logging: every Daraja request/response is logged by payments.daraja
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "payments": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
if "test" in sys.argv:  # keep test output readable
    for _logger in LOGGING["loggers"].values():
        _logger["level"] = "CRITICAL"
    LOGGING["root"]["level"] = "CRITICAL"

# --------------------------------------------------------------------------
# Site
# --------------------------------------------------------------------------
SITE_NAME = env("SITE_NAME", default="QuickPay")

# --------------------------------------------------------------------------
# M-Pesa / Daraja
# --------------------------------------------------------------------------
# simulate   - no Safaricom calls at all; you click "simulate" buttons to
#              finish payments. Lets anyone run the project with zero setup.
# sandbox    - Safaricom sandbox (sandbox.safaricom.co.ke).
# production - live payments (api.safaricom.co.ke).
MPESA_MODE = env("MPESA_MODE", default="simulate")
MPESA_CONSUMER_KEY = env("MPESA_CONSUMER_KEY", default="")
MPESA_CONSUMER_SECRET = env("MPESA_CONSUMER_SECRET", default="")
MPESA_SHORTCODE = env("MPESA_SHORTCODE", default="174379")
MPESA_PASSKEY = env("MPESA_PASSKEY", default="")
# CustomerPayBillOnline (paybill) or CustomerBuyGoodsOnline (till)
MPESA_TRANSACTION_TYPE = env("MPESA_TRANSACTION_TYPE", default="CustomerPayBillOnline")
# For tills, PartyB is the till number while BusinessShortCode is the store
# number. For paybills they are the same, so this defaults to the shortcode.
MPESA_PARTY_B = env("MPESA_PARTY_B", default="") or MPESA_SHORTCODE
MPESA_ACCOUNT_REFERENCE = env("MPESA_ACCOUNT_REFERENCE", default=SITE_NAME)[:12]
MPESA_TRANSACTION_DESC = env("MPESA_TRANSACTION_DESC", default="Payment")[:13]
# Public HTTPS base URL Safaricom can reach, e.g. your ngrok domain.
MPESA_CALLBACK_BASE_URL = env("MPESA_CALLBACK_BASE_URL", default="").rstrip("/")
# Random secret embedded in the callback path so outsiders can't forge callbacks.
MPESA_CALLBACK_SECRET = env("MPESA_CALLBACK_SECRET", default="") or "dev-callback-secret"
MPESA_TIMEOUT_SECONDS = env.int("MPESA_TIMEOUT_SECONDS", default=15)

# Amount limits in whole shillings. M-Pesa's own per-transaction cap is
# KES 250,000; set a lower ceiling if that suits your use case.
PAY_MIN_AMOUNT = env.int("PAY_MIN_AMOUNT", default=1)
PAY_MAX_AMOUNT = env.int("PAY_MAX_AMOUNT", default=250_000)

# Abuse protection for the public pay endpoint.
PAY_RATE_LIMIT_PER_PHONE = env.int("PAY_RATE_LIMIT_PER_PHONE", default=3)
PAY_RATE_LIMIT_PER_IP = env.int("PAY_RATE_LIMIT_PER_IP", default=10)
PAY_RATE_LIMIT_WINDOW_MINUTES = env.int("PAY_RATE_LIMIT_WINDOW_MINUTES", default=5)
# Pending payments older than this with no answer from Safaricom are marked TIMEOUT.
PAY_PENDING_EXPIRY_SECONDS = env.int("PAY_PENDING_EXPIRY_SECONDS", default=180)
# Set True only when running behind a proxy you control (Render, Railway,
# nginx); otherwise clients could fake their IP to dodge rate limits.
USE_X_FORWARDED_FOR = env.bool("USE_X_FORWARDED_FOR", default=False)

# --------------------------------------------------------------------------
# Safety checks: fail loudly at startup instead of misbehaving with money.
# --------------------------------------------------------------------------
if MPESA_MODE not in {"simulate", "sandbox", "production"}:
    raise ImproperlyConfigured("MPESA_MODE must be one of: simulate, sandbox, production")

if MPESA_MODE == "simulate" and not DEBUG and not env.bool("ALLOW_SIMULATE", default=False):
    # Simulate mode lets anyone mark a payment as paid. Never expose it publicly.
    raise ImproperlyConfigured("MPESA_MODE=simulate is only allowed with DEBUG=1. Use sandbox or production.")

if MPESA_MODE != "simulate":
    missing = [
        name
        for name in (
            "MPESA_CONSUMER_KEY",
            "MPESA_CONSUMER_SECRET",
            "MPESA_PASSKEY",
            "MPESA_CALLBACK_BASE_URL",
        )
        if not globals()[name]
    ]
    if missing:
        raise ImproperlyConfigured(f"MPESA_MODE={MPESA_MODE} needs: {', '.join(missing)}")
    if MPESA_CALLBACK_SECRET == "dev-callback-secret" or len(MPESA_CALLBACK_SECRET) < 16:
        raise ImproperlyConfigured(
            "Set MPESA_CALLBACK_SECRET to a long random string "
            '(python -c "import secrets; print(secrets.token_urlsafe(32))").'
        )

# The public callback host (e.g. your ngrok domain) must be an allowed host.
if MPESA_CALLBACK_BASE_URL:
    _callback_host = urlparse(MPESA_CALLBACK_BASE_URL).hostname
    if _callback_host and _callback_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_callback_host)
        CSRF_TRUSTED_ORIGINS.append(f"https://{_callback_host}")
