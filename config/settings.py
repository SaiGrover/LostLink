"""
Django settings for the LostLink project.

LostLink — An AI-Enabled Full-Stack Platform for Intelligent
Lost-and-Found Item Matching and Recovery.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Secret key: read from the environment in production; a dev fallback keeps
# local setup friction-free. NEVER ship the fallback to a real deployment.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-thbjt0gvec5$yn1-!@2*9%m)24im5o2bmxwhly35%l4p=b)kwq')

# Debug is on only when explicitly enabled (defaults to on for local dev).
DEBUG = os.environ.get('DJANGO_DEBUG', 'true').lower() != 'false'

if not DEBUG and not os.environ.get('DJANGO_SECRET_KEY'):
    raise RuntimeError('DJANGO_SECRET_KEY is required when DJANGO_DEBUG=false.')
if not DEBUG and not os.environ.get('DATABASE_URL'):
    raise RuntimeError('DATABASE_URL is required when DJANGO_DEBUG=false.')

ALLOWED_HOSTS = os.environ.get(
    'DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # LostLink apps
    'accounts',
    'items',
    'matching',
    'claims',
    'notifications',
    'dashboard',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
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
                'notifications.context_processors.notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': Path(os.environ.get('LOSTLINK_SQLITE_PATH', BASE_DIR / 'db.sqlite3')),
    }
}


# Password validation
if os.environ.get('DATABASE_URL'):
    from urllib.parse import urlparse, unquote, parse_qs
    db_url = urlparse(os.environ['DATABASE_URL'])
    if db_url.scheme not in ('postgres', 'postgresql'):
        raise ValueError('DATABASE_URL must use PostgreSQL.')
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': unquote(db_url.path.lstrip('/')),
        'USER': unquote(db_url.username or ''),
        'PASSWORD': unquote(db_url.password or ''),
        'HOST': db_url.hostname, 'PORT': db_url.port or 5432,
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'sslmode': parse_qs(db_url.query).get('sslmode', ['prefer'])[0]},
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
# The hashed-manifest storage needs `collectstatic` to have run. It is only
# switched on for explicit production runs (DJANGO_DEBUG=false in the
# environment); development servers and the test runner (which forces
# DEBUG=False internally) use plain compressed storage so templates render
# without a manifest.
_PRODUCTION_STATIC = os.environ.get('DJANGO_DEBUG', 'true').lower() == 'false'
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': ('whitenoise.storage.CompressedManifestStaticFilesStorage'
                    if _PRODUCTION_STATIC else
                    'whitenoise.storage.CompressedStaticFilesStorage'),
    },
}

# Media files (user-uploaded item photographs)
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.environ.get('LOSTLINK_MEDIA_ROOT', BASE_DIR / 'media'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom user model with Student / Moderator / Administrator roles
AUTH_USER_MODEL = 'accounts.User'

# Authentication redirects
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'items:dashboard'
LOGOUT_REDIRECT_URL = 'items:home'

# Development default; the accounts block at the end of this file selects
# the real backend from the environment.
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# --- Security hardening (active in production, i.e. when DEBUG is off) -------
# Session/CSRF cookies are always http-only + same-site; the stricter HTTPS-only
# behaviours switch on automatically once DEBUG is disabled.
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # allow JS to read the token for AJAX POSTs
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
# Cap request body size to blunt oversized-upload abuse (~10 MB).
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    CSRF_TRUSTED_ORIGINS = [
        o for o in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if o]

# Messages -> Bootstrap-free tag mapping used by the custom toast component.
from django.contrib.messages import constants as messages  # noqa: E402
MESSAGE_TAGS = {
    messages.DEBUG: 'muted',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'danger',
}

# ---------------------------------------------------------------------------
# LostLink matching-engine configuration (Section 4.9 of the synopsis).
# Hybrid weighted score = sum(component_weight * component_similarity).
# Weights are experimentally reasonable defaults, retunable against the pilot
# dataset (see matching/engine.py and the ``retune`` management command hook).
# ---------------------------------------------------------------------------
MATCHING = {
    'WEIGHTS': {
        'text': 0.30,
        'image': 0.30,
        'location': 0.15,
        'category': 0.10,
        'date': 0.10,
        'attribute': 0.05,
    },
    # Confidence tiers -> system behaviour (Section 4.9).
    'TIER_HIGH': 0.85,
    'TIER_MEDIUM': 0.70,
    # Matches below this are not stored/surfaced at all.
    'MIN_SCORE': 0.40,
    # Date-decay: similarity halves roughly every N days between reports.
    'DATE_HALF_LIFE_DAYS': 10,
    # A found report dated more than this many days *before* the lost report
    # is physically impossible and is never stored as a match. One day of
    # tolerance absorbs approximate dates around midnight.
    'TEMPORAL_TOLERANCE_DAYS': 1,
    # Model/weight version tag for reproducibility (Section 4.17).
    'VERSION': 'v1.0.0',
}

# Traceable application logs (synopsis Section 4.17 operational monitoring).
# Inference failures are logged under ``lostlink.matching`` and never abort
# a request; ``LOSTLINK_LOG_LEVEL`` raises or lowers verbosity.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'standard'},
    },
    'loggers': {
        'lostlink': {'handlers': ['console'],
                     'level': os.environ.get('LOSTLINK_LOG_LEVEL', 'INFO'), 'propagate': False},
        # 4xx responses (including deliberate 403s from role checks) are not
        # errors; only 5xx server errors are logged.
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
    },
}

import json
MATCHING_PROFILE = json.loads((BASE_DIR / 'matching' / 'profile.json').read_text())
MATCHING['WEIGHTS'] = MATCHING_PROFILE['weights']
MATCHING['VERSION'] = MATCHING_PROFILE['version']
MATCHING['TIER_MEDIUM'] = MATCHING_PROFILE['medium']
MATCHING['TIER_HIGH'] = MATCHING_PROFILE['high']
# Chance-level image similarity per vision model (see matching/profile.json).
MATCHING['IMAGE_CHANCE'] = MATCHING_PROFILE.get('image_chance', {})

# --- Claims: ownership reservation, appeals, evidence, OTP handover ---
HANDOVER_CODE_TTL_MINUTES = int(os.environ.get('LOSTLINK_HANDOVER_CODE_TTL_MINUTES', 10))
HANDOVER_CODE_MAX_ATTEMPTS = int(os.environ.get('LOSTLINK_HANDOVER_CODE_MAX_ATTEMPTS', 5))
HANDOVER_CODES_PER_HOUR = int(os.environ.get('LOSTLINK_HANDOVER_CODES_PER_HOUR', 5))
HANDOVER_VERIFY_PER_10_MIN = int(os.environ.get('LOSTLINK_HANDOVER_VERIFY_PER_10_MIN', 10))
CLAIM_MAX_APPEALS = int(os.environ.get('LOSTLINK_CLAIM_MAX_APPEALS', 2))
CLAIM_APPEALS_PER_DAY = int(os.environ.get('LOSTLINK_CLAIM_APPEALS_PER_DAY', 3))
CLAIM_EVIDENCE_MAX_FILES = int(os.environ.get('LOSTLINK_CLAIM_EVIDENCE_MAX_FILES', 5))
CLAIM_EVIDENCE_MAX_BYTES = int(os.environ.get('LOSTLINK_CLAIM_EVIDENCE_MAX_BYTES', 5 * 1024 * 1024))
CLAIM_EVIDENCE_RETENTION_DAYS = int(os.environ.get('LOSTLINK_CLAIM_EVIDENCE_RETENTION_DAYS', 180))

# --- Production hardening, media and uploads ---
# Everything below refines the settings above; production-only checks and the
# media/upload policy live here so they are reviewed in one place.


def _env_list(name, default=''):
    return [value.strip() for value in os.environ.get(name, default).split(',') if value.strip()]


def _env_flag(name, default):
    return os.environ.get(name, 'true' if default else 'false').strip().lower() in ('1', 'true', 'yes', 'on')


# Hosting platforms set RENDER; there DEBUG must be chosen explicitly, so a
# forgotten DJANGO_DEBUG can never publish debug pages with tracebacks.
if DEBUG and os.environ.get('RENDER') and os.environ.get('DJANGO_DEBUG', '').lower() != 'true':
    raise RuntimeError('Set DJANGO_DEBUG=false on Render (or DJANGO_DEBUG=true to run a debug build on purpose).')

ALLOWED_HOSTS = _env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')

# Optional SQLite location for local runs and the first-run smoke test
# (scripts/smoke_first_run.py); ignored whenever DATABASE_URL is set.
if os.environ.get('LOSTLINK_SQLITE_PATH') and not os.environ.get('DATABASE_URL'):
    DATABASES['default']['NAME'] = Path(os.environ['LOSTLINK_SQLITE_PATH'])
if DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql':
    DATABASES['default']['CONN_HEALTH_CHECKS'] = True

if not DEBUG:
    if len(SECRET_KEY) < 50 or SECRET_KEY.startswith('django-insecure') or len(set(SECRET_KEY)) < 5:
        raise RuntimeError('DJANGO_SECRET_KEY must be a private random value of at least 50 characters.')
    if not os.environ.get('DJANGO_ALLOWED_HOSTS') or '*' in ALLOWED_HOSTS:
        raise RuntimeError('Set DJANGO_ALLOWED_HOSTS to the exact public hostname(s); "*" is not allowed.')
    CSRF_TRUSTED_ORIGINS = _env_list('DJANGO_CSRF_TRUSTED_ORIGINS')
    if any(not origin.startswith('https://') for origin in CSRF_TRUSTED_ORIGINS):
        raise RuntimeError('DJANGO_CSRF_TRUSTED_ORIGINS entries must be https:// origins.')
    # TLS terminates at the platform proxy, which sets X-Forwarded-Proto.
    SECURE_SSL_REDIRECT = _env_flag('DJANGO_SECURE_SSL_REDIRECT', True)
    SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SECONDS', SECURE_HSTS_SECONDS))
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Uploads must live outside the static tree, so WhiteNoise can never serve
    # one directly; every upload goes through the media view (items/media.py).
    _media = Path(MEDIA_ROOT).resolve()
    for _static in [Path(STATIC_ROOT).resolve(), *(Path(d).resolve() for d in STATICFILES_DIRS)]:
        if _media == _static or _static in _media.parents or _media in _static.parents:
            raise RuntimeError('LOSTLINK_MEDIA_ROOT must not overlap the static file directories.')

SESSION_COOKIE_AGE = int(os.environ.get('DJANGO_SESSION_COOKIE_AGE', 60 * 60 * 24 * 14))
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

# Uploaded photographs are validated, stripped of metadata and re-encoded by
# items/media.py; these bound what is accepted before any pixel is decoded.
LOSTLINK_UPLOAD_MAX_BYTES = int(os.environ.get('LOSTLINK_UPLOAD_MAX_MB', '5')) * 1024 * 1024
LOSTLINK_UPLOAD_MAX_PIXELS = int(os.environ.get('LOSTLINK_UPLOAD_MAX_PIXELS', '40000000'))
LOSTLINK_UPLOAD_MAX_EDGE = int(os.environ.get('LOSTLINK_UPLOAD_MAX_EDGE', '2048'))
FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750

# Security events (bad Host headers, suspicious file operations) and warnings
# from any logger reach the console in production too. Log messages carry
# record IDs only, never contact details or private answers.
LOGGING['loggers']['django.security'] = {'handlers': ['console'], 'level': 'WARNING', 'propagate': False}
LOGGING['root'] = {'handlers': ['console'], 'level': 'WARNING'}


# --- Accounts: email verification, password reset, email backend ---
# All values come from the environment; nothing secret is stored here.
def _env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None or value.strip() == '':
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


# Email delivery. Precedence: an explicit DJANGO_EMAIL_BACKEND; SMTP when
# DJANGO_EMAIL_HOST is set; the console in development; otherwise (DEBUG off
# and nothing configured) a backend that drops mail and logs an ERROR for
# each message, reported by ``manage.py check`` as accounts.W001. Production
# never silently prints one-time links to the console.
EMAIL_HOST = os.environ.get('DJANGO_EMAIL_HOST', '').strip()
EMAIL_PORT = int(os.environ.get('DJANGO_EMAIL_PORT') or 587)
EMAIL_HOST_USER = os.environ.get('DJANGO_EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('DJANGO_EMAIL_HOST_PASSWORD', '')
EMAIL_USE_SSL = _env_flag('DJANGO_EMAIL_USE_SSL', False)
EMAIL_USE_TLS = _env_flag('DJANGO_EMAIL_USE_TLS', not EMAIL_USE_SSL)
EMAIL_TIMEOUT = int(os.environ.get('DJANGO_EMAIL_TIMEOUT') or 10)
if os.environ.get('DJANGO_EMAIL_BACKEND', '').strip():
    EMAIL_BACKEND = os.environ['DJANGO_EMAIL_BACKEND'].strip()
elif EMAIL_HOST:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
elif DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
else:
    EMAIL_BACKEND = 'accounts.mail.UnconfiguredEmailBackend'
DEFAULT_FROM_EMAIL = os.environ.get('DJANGO_DEFAULT_FROM_EMAIL', '').strip() or 'LostLink <no-reply@localhost>'
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Absolute base URL for links in emails, e.g. https://lostlink.example.edu.
# When empty the current request's (ALLOWED_HOSTS-validated) host is used.
LOSTLINK_PUBLIC_URL = os.environ.get('LOSTLINK_PUBLIC_URL', '').strip().rstrip('/')

# Campus identity: comma-separated institutional email domains allowed to
# register (subdomains included). Empty = any domain, still email-verified.
LOSTLINK_ALLOWED_EMAIL_DOMAINS = [
    d.strip().lower().lstrip('@') for d in
    os.environ.get('LOSTLINK_ALLOWED_EMAIL_DOMAINS', '').split(',') if d.strip()]

# Link lifetimes (seconds). Verification links: 3 days. Reset links: 1 hour.
LOSTLINK_EMAIL_VERIFICATION_MAX_AGE = int(
    os.environ.get('LOSTLINK_EMAIL_VERIFICATION_MAX_AGE') or 3 * 24 * 3600)
PASSWORD_RESET_TIMEOUT = int(os.environ.get('LOSTLINK_PASSWORD_RESET_TIMEOUT') or 3600)

# Per-endpoint rate limits as {name: (hits, window_seconds)}; any entry of
# accounts.policy.DEFAULT_RATE_LIMITS can be overridden here.
LOSTLINK_AUTH_RATE_LIMITS = {}

# --- Review follow-ups: production identity checks, proxy trust, shared cache ---
# Decided when settings load (the test runner later forces DEBUG off, so checks
# must not read DEBUG itself).
LOSTLINK_PRODUCTION = not DEBUG

# Campus-only access: in production an empty LOSTLINK_ALLOWED_EMAIL_DOMAINS is
# a configuration error (accounts.E001), because email verification alone
# proves control of an inbox, not campus affiliation. An open deployment (for
# example a public demo) must opt out explicitly.
LOSTLINK_REQUIRE_CAMPUS_DOMAIN = os.environ.get(
    'LOSTLINK_REQUIRE_CAMPUS_DOMAIN', 'true').strip().lower() in ('1', 'true', 'yes', 'on')

# How many reverse proxies in front of Django append to X-Forwarded-For.
# 0 (default): the header is ignored and the connection address is used, so a
# client cannot pick its own rate-limit bucket. On Render (one proxy that
# appends the real client address) set 1 after confirming the header format.
LOSTLINK_TRUSTED_PROXY_COUNT = int(os.environ.get('LOSTLINK_TRUSTED_PROXY_COUNT', 0) or 0)

# Rate-limit counters and other cache data. Local memory is per process, so
# with several workers or instances each would keep its own quota: production
# should use Redis (REDIS_URL) or the database cache
# (LOSTLINK_CACHE=database, then `python manage.py createcachetable`).
if os.environ.get('REDIS_URL'):
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache',
                          'LOCATION': os.environ['REDIS_URL']}}
elif os.environ.get('LOSTLINK_CACHE', '').strip().lower() == 'database':
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
                          'LOCATION': 'lostlink_cache'}}

# --- Matching: stable text model, chronology, evidence coverage ---
# Text similarity uses a versioned, fixed-vocabulary lexical release
# (matching/text_model/<release>.json, built by `manage.py build_text_model`),
# so a pair's score never depends on which other reports exist. Changing the
# release, chronology or coverage rules changes scoring semantics: bump the
# engine version in matching/profile.json and run `manage.py rebuild_matches`.
MATCHING['TEXT_MODEL_RELEASE'] = MATCHING_PROFILE.get('text_release', 'lexical-v1')
# Graded chronology (matching/chronology.py): found-before-lost is penalised
# and flagged, never silently excluded. Heuristic, documented thresholds.
MATCHING['CHRONOLOGY'] = {
    'HALF_LIFE_DAYS': MATCHING['DATE_HALF_LIFE_DAYS'],
    'APPROXIMATE_DAYS': MATCHING['TEMPORAL_TOLERANCE_DAYS'],
    'TIME_TOLERANCE_HOURS': 3,
    'CONTRADICTORY_DAYS': 7,
    'PENALTY': {'plausible': 1.0, 'approximate': 0.9, 'suspicious': 0.5, 'contradictory': 0.0},
    'TIER_CAP': {'suspicious': 'medium', 'contradictory': 'low'},
}
# Evidence coverage = share of the base weight compared on both reports.
# Below STRONG_MIN a pair is shown as moderate at most; below MODERATE_MIN as
# weak. Heuristic display rules, not calibrated statistics.
MATCHING['EVIDENCE_COVERAGE'] = {'STRONG_MIN': 0.70, 'MODERATE_MIN': 0.50}
# Feature extraction attempts before a report waits for `manage.py retry_features`.
MATCHING['FEATURE_MAX_ATTEMPTS'] = 3
