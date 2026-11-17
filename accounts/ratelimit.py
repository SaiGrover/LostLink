"""Small cache-backed rate limiter shared by sign-in, registration, email
verification, password reset and handover OTP endpoints.

It uses Django's configured cache (local memory by default, a shared cache
such as Redis or the database cache in multi-process production). Counting
is fixed-window: ``limit`` hits per ``window`` seconds per key.

    from accounts.ratelimit import is_limited, client_ip

    if is_limited(f'login:{client_ip(request)}', limit=10, window=300):
        ...  # refuse politely

Keys must never contain secrets (passwords, OTPs, tokens); use identifiers
such as an IP address, a user id or a normalised email address.
"""
import hashlib

from django.core.cache import cache


def _key(name: str) -> str:
    return 'rl:' + hashlib.sha256(name.encode('utf-8')).hexdigest()


def is_limited(name: str, limit: int, window: int, increment: bool = True) -> bool:
    """Record one hit for ``name`` and report whether it exceeds ``limit``.

    With ``increment=False`` the current count is checked without recording a
    hit (useful to refuse early before doing any work).
    """
    key = _key(name)
    if not increment:
        return (cache.get(key) or 0) >= limit
    if cache.add(key, 1, timeout=window):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:  # expired between add() and incr()
            cache.set(key, 1, timeout=window)
            count = 1
    return count > limit


def reset(name: str) -> None:
    """Forget the counter for ``name`` (e.g. after a successful sign-in)."""
    cache.delete(_key(name))


def client_ip(request) -> str:
    """The client address used for IP rate limits.

    ``X-Forwarded-For`` can be written by the client, so its *first* value is
    never trusted. With ``LOSTLINK_TRUSTED_PROXY_COUNT = n`` (the number of
    reverse proxies in front of Django that each append the address they saw)
    the client is the n-th entry from the right, which only those proxies
    write. With 0 (the default) the header is ignored and the connection
    address (``REMOTE_ADDR``) is used.
    """
    from django.conf import settings

    remote = request.META.get('REMOTE_ADDR', '') or 'unknown'
    trusted = getattr(settings, 'LOSTLINK_TRUSTED_PROXY_COUNT', 0) or 0
    if trusted <= 0:
        return remote
    hops = [h.strip() for h in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if h.strip()]
    if len(hops) < trusted:
        return remote
    return hops[-trusted]
