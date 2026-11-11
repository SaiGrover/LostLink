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
        'NAME': BASE_DIR / 'db.sqlite3',
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

# In development we print emails to the console (email is a future extension).
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
