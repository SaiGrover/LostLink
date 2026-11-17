"""Outgoing account emails (verification links, duplicate-registration notices).

Sending never raises into the request: a failed delivery is logged under
``lostlink.accounts`` and the caller shows the same response either way, so
nothing about the account's existence leaks through errors.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from . import tokens

logger = logging.getLogger('lostlink.accounts')


def site_base_url(request):
    """Absolute ``scheme://host`` used in emailed links.

    ``LOSTLINK_PUBLIC_URL`` wins when set (recommended in production so links
    never depend on the request's Host header); otherwise the current request
    is used (its host is already validated against ``ALLOWED_HOSTS``).
    """
    configured = (getattr(settings, 'LOSTLINK_PUBLIC_URL', '') or '').strip().rstrip('/')
    if configured:
        return configured
    return request.build_absolute_uri('/').rstrip('/')


def _send(subject_template, body_template, context, to_email):
    subject = ''.join(render_to_string(subject_template, context).splitlines()).strip()
    body = render_to_string(body_template, context)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)
        return True
    except Exception:  # SMTP down, bad credentials, ...: never break the page
        logger.exception('Failed to send account email (%s) to user %s',
                         body_template, context.get('user').pk if context.get('user') else '?')
        return False


def send_verification_email(request, user):
    if not user.email:
        return False
    base = site_base_url(request)
    context = {
        'user': user,
        'verify_url': base + reverse('accounts:verify_email', args=[tokens.make_token(user)]),
        'hours': max(1, tokens.max_age() // 3600),
        'base_url': base,
    }
    return _send('accounts/emails/verification_subject.txt',
                 'accounts/emails/verification_email.txt', context, user.email)


def send_duplicate_registration_notice(request, user):
    """Tell the owner of an existing address that someone tried to register
    with it. Sent instead of an error so the sign-up form never reveals which
    addresses have accounts."""
    if not user.email:
        return False
    base = site_base_url(request)
    context = {
        'user': user,
        'login_url': base + reverse('accounts:login'),
        'reset_url': base + reverse('accounts:password_reset'),
        'base_url': base,
    }
    return _send('accounts/emails/duplicate_registration_subject.txt',
                 'accounts/emails/duplicate_registration_email.txt', context, user.email)
