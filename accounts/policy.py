"""Campus identity policy: which email domains may register, plus the rate
limits applied to the authentication endpoints.

``LOSTLINK_ALLOWED_EMAIL_DOMAINS`` (settings, from the environment variable of
the same name) is a list of domains such as ``['college.edu']``. An address is
allowed when its domain equals an entry or is a subdomain of it
(``cs.college.edu`` matches ``college.edu``). An empty list allows any domain;
the address must still be verified by email.
"""
from django.conf import settings

DEFAULT_RATE_LIMITS = {
    # name: (hits, window in seconds)
    'login_ip': (50, 15 * 60),        # failed sign-ins per client IP
    'login_user': (5, 15 * 60),       # failed sign-ins per normalised username
    'register_ip': (20, 60 * 60),     # sign-up submissions per client IP
    'register_email': (5, 60 * 60),   # sign-ups naming one email address
    'resend_ip': (10, 60 * 60),       # verification resend requests per IP
    'resend_email': (3, 60 * 60),     # verification emails per address/account
    'reset_ip': (10, 60 * 60),        # password-reset requests per IP
    'reset_email': (3, 60 * 60),      # password-reset emails per address
}


def rate_limit(name):
    """``(limit, window)`` for ``name``; settings can override any entry."""
    overrides = getattr(settings, 'LOSTLINK_AUTH_RATE_LIMITS', None) or {}
    return overrides.get(name, DEFAULT_RATE_LIMITS[name])


def normalise_email(email):
    return (email or '').strip().lower()


def normalise_username(username):
    return (username or '').strip().lower()


def allowed_email_domains():
    raw = getattr(settings, 'LOSTLINK_ALLOWED_EMAIL_DOMAINS', None) or []
    if isinstance(raw, str):
        raw = raw.split(',')
    return [d.strip().lower().lstrip('@').lstrip('.') for d in raw if d and d.strip()]


def email_domain_allowed(email):
    domains = allowed_email_domains()
    if not domains:
        return True
    email = normalise_email(email)
    if '@' not in email:
        return False
    domain = email.rsplit('@', 1)[1]
    return any(domain == d or domain.endswith('.' + d) for d in domains)


def allowed_domains_display():
    return ', '.join('@' + d for d in allowed_email_domains())
