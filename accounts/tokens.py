"""Signed, expiring, single-use email verification tokens.

A token is ``django.core.signing.dumps({'u': pk, 'f': fingerprint})`` with a
timestamp, so it

* cannot be forged without ``SECRET_KEY``;
* expires after ``LOSTLINK_EMAIL_VERIFICATION_MAX_AGE`` seconds;
* is single-use: the fingerprint hashes the account's current email address
  and ``email_verified_at``. Verifying the account (or changing its email)
  changes the fingerprint, so every earlier link stops working.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.utils.crypto import constant_time_compare, salted_hmac

SALT = 'lostlink.accounts.email-verification'

OK = 'ok'
EXPIRED = 'expired'
INVALID = 'invalid'


def max_age():
    return int(getattr(settings, 'LOSTLINK_EMAIL_VERIFICATION_MAX_AGE', 3 * 24 * 3600))


def _fingerprint(user):
    verified = user.email_verified_at.isoformat() if user.email_verified_at else ''
    value = f'{user.pk}|{(user.email or "").strip().lower()}|{verified}'
    return salted_hmac(SALT, value, algorithm='sha256').hexdigest()[:40]


def make_token(user):
    return signing.dumps({'u': user.pk, 'f': _fingerprint(user)}, salt=SALT)


def check_token(token):
    """Return ``(status, user)``; ``user`` is set only when status is OK."""
    try:
        payload = signing.loads(token, salt=SALT, max_age=max_age())
    except signing.SignatureExpired:
        return EXPIRED, None
    except (signing.BadSignature, ValueError, TypeError):
        return INVALID, None
    if not isinstance(payload, dict):
        return INVALID, None
    User = get_user_model()
    try:
        user = User.objects.get(pk=payload.get('u'), is_active=True)
    except (User.DoesNotExist, ValueError, TypeError):
        return INVALID, None
    if not constant_time_compare(str(payload.get('f', '')), _fingerprint(user)):
        return INVALID, None
    return OK, user
