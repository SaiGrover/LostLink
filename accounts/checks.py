from django.conf import settings
from django.core.checks import Warning, register


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
    if not settings.DEBUG and backend == 'django.core.mail.backends.console.EmailBackend':
        return [Warning(
            'The console email backend is in use with DEBUG off: one-time links '
            'will be written to the server log.',
            hint='Configure SMTP with DJANGO_EMAIL_HOST and related settings.',
            id='accounts.W002')]
    return []
