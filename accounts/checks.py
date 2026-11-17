from django.conf import settings
from django.core.checks import Error, Warning, register

from .policy import allowed_email_domains


def _production():
    return getattr(settings, 'LOSTLINK_PRODUCTION', not settings.DEBUG)


@register()
def campus_domain_configured(app_configs, **kwargs):
    """Campus-only sign-up must be configured in production (or opted out)."""
    if (_production() and getattr(settings, 'LOSTLINK_REQUIRE_CAMPUS_DOMAIN', True)
            and not allowed_email_domains()):
        return [Error(
            'LOSTLINK_ALLOWED_EMAIL_DOMAINS is empty, so anyone with any email address can '
            'register: email verification proves control of an inbox, not campus affiliation.',
            hint='Set LOSTLINK_ALLOWED_EMAIL_DOMAINS to the institution domain(s), e.g. '
                 '"college.edu". For a deliberately open deployment set '
                 'LOSTLINK_REQUIRE_CAMPUS_DOMAIN=false.',
            id='accounts.E001')]
    return []


@register()
def shared_cache_for_rate_limits(app_configs, **kwargs):
    backend = settings.CACHES.get('default', {}).get('BACKEND', '')
    if _production() and backend.endswith('locmem.LocMemCache'):
        return [Warning(
            'Rate limits use a per-process local-memory cache: with several workers or '
            'instances each keeps its own quota.',
            hint='Set REDIS_URL, or LOSTLINK_CACHE=database and run createcachetable.',
            id='accounts.W003')]
    if _production() and getattr(settings, 'LOSTLINK_TRUSTED_PROXY_COUNT', 0) == 0:
        return [Warning(
            'X-Forwarded-For is ignored (LOSTLINK_TRUSTED_PROXY_COUNT=0), so behind a proxy all '
            'clients may share one IP rate-limit bucket.',
            hint='After confirming how the hosting proxy builds X-Forwarded-For, set '
                 'LOSTLINK_TRUSTED_PROXY_COUNT to the number of proxies that append to it.',
            id='accounts.W004')]
    return []


@register()
def email_delivery_configured(app_configs, **kwargs):
    backend = getattr(settings, 'EMAIL_BACKEND', '')
    if backend == 'accounts.mail.UnconfiguredEmailBackend':
        return [Warning(
            'Email delivery is not configured: verification and password-reset '
            'emails will be dropped (and logged as errors).',
            hint='Set DJANGO_EMAIL_HOST, DJANGO_EMAIL_PORT, DJANGO_EMAIL_HOST_USER, '
                 'DJANGO_EMAIL_HOST_PASSWORD and DJANGO_DEFAULT_FROM_EMAIL.',
            id='accounts.W001')]
    if _production() and backend == 'django.core.mail.backends.console.EmailBackend':
        return [Warning(
            'The console email backend is in use with DEBUG off: one-time links '
            'will be written to the server log.',
            hint='Configure SMTP with DJANGO_EMAIL_HOST and related settings.',
            id='accounts.W002')]
    return []
