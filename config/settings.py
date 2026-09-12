from pathlib import Path
from decouple import config, Csv
from django.core.exceptions import ImproperlyConfigured
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Core ──────────────────────────────────────────────────────────────────────
import os
import sys

SECRET_KEY = config('SECRET_KEY', default='django-insecure-local-development-key-not-for-production')
DEBUG = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1,testserver', cast=Csv())

# The default above exists only so local dev works without a .env file.
# Refuse to boot in production rather than silently signing sessions and
# password-reset tokens with a key that is public in the GitHub repo.
if not DEBUG and SECRET_KEY.startswith('django-insecure-'):
    raise ImproperlyConfigured(
        'SECRET_KEY is unset in production. Set a real SECRET_KEY environment variable.'
    )

# Railway auto-injects these — add them automatically
for _var in ('RAILWAY_PUBLIC_DOMAIN', 'RAILWAY_PRIVATE_DOMAIN'):
    _val = os.environ.get(_var)
    if _val and _val not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_val)

# ── Apps ──────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third party
    'rest_framework',
    'corsheaders',
    'anymail',
    # Local
    'associations',
    'members',
    'tournaments',
    'matches',
    'notifications',
    'payments',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # serves static files in prod
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'members.middleware.TwoFactorMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'notifications.context_processors.unread_count',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Database ──────────────────────────────────────────────────────────────────
DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
    )
}

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# Tests create many Users; the default PBKDF2 hasher's deliberate slowness
# (the whole point of it in production) makes the suite take minutes for
# no benefit in a throwaway test DB — swap to a fast hasher during `test` only.
if 'test' in sys.argv:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# ── i18n ──────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Harare'
USE_I18N = True
USE_TZ = True

# ── Static & Media ────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = DEBUG

# ── Email ─────────────────────────────────────────────────────────────────────
#
# SMTP DOES NOT WORK ON RAILWAY. This was measured from inside the running
# container, not assumed: outbound TCP to smtp.gmail.com times out on 587, 465,
# 25 AND 2525, while HTTPS to port 443 returns 200. Railway blocks outbound
# SMTP wholesale to stop its platform being used for spam. No combination of
# host, port, TLS setting or app password will change that — Django's SMTP
# backend simply cannot open a socket.
#
# So production sends over an HTTPS email API instead, via django-anymail.
# Nothing in notifications/email.py changes: it builds ordinary
# EmailMultiAlternatives objects and anymail swaps in underneath as a normal
# Django email backend.
#
#   local dev   -> console backend (default below), prints to stdout
#   production  -> EMAIL_BACKEND=anymail.backends.brevo.EmailBackend
#                  plus BREVO_API_KEY
#
# The SMTP settings are kept so a future host that permits SMTP needs only an
# environment change, not a code change.
EMAIL_BACKEND = config(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='ChessHub <noreply@bcachesshub.co.zw>')

# Brevo was chosen over Resend deliberately. Resend is the nicer product but
# will only deliver to arbitrary recipients once a DOMAIN is verified, and
# bcachesshub.co.zw is not owned yet. Brevo verifies a single SENDER EMAIL
# ADDRESS, so it can mail real members today from a personal Gmail, on 300
# emails/day free. Revisit if the domain is ever purchased.
ANYMAIL = {
    'BREVO_API_KEY': config('BREVO_API_KEY', default=''),
}

# Refuse to boot in production configured to send mail it cannot send. A
# silently dead notification system is exactly the failure this project already
# had for months: every announcement, challenge and password reset was written
# to stdout and thrown away, and nothing anywhere reported a problem.
if not DEBUG and EMAIL_BACKEND.endswith('smtp.EmailBackend'):
    raise ImproperlyConfigured(
        'SMTP is blocked on Railway (ports 25/465/587/2525 all time out). '
        'Set EMAIL_BACKEND=anymail.backends.brevo.EmailBackend and BREVO_API_KEY.'
    )

# ── Site / Paynow ─────────────────────────────────────────────────────────────
SITE_BASE_URL = config('SITE_BASE_URL', default='http://127.0.0.1:8000')
PAYNOW_INTEGRATION_ID = config('PAYNOW_INTEGRATION_ID', default='SANDBOX_ID')
PAYNOW_INTEGRATION_KEY = config('PAYNOW_INTEGRATION_KEY', default='SANDBOX_KEY')
# Defaults to DEBUG, NOT to True. Sandbox mode lets a payment be marked paid
# without paying, so the safe default in production is off. It previously
# defaulted to True and the Railway variable was never set, which left the
# sandbox approval endpoint live on the public site.
PAYNOW_SANDBOX = config('PAYNOW_SANDBOX', default=DEBUG, cast=bool)

if not DEBUG and PAYNOW_SANDBOX:
    raise ImproperlyConfigured(
        'PAYNOW_SANDBOX is enabled in production. Sandbox mode can mark '
        'payments completed without payment. Unset it or set it to False.'
    )

# ── Production security ───────────────────────────────────────────────────────
if not DEBUG:
    # Railway terminates SSL at its edge — don't redirect internally
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
