"""Email backend used when production has no email delivery configured.

Selected by ``config/settings.py`` when ``DJANGO_DEBUG=false`` and neither
``DJANGO_EMAIL_HOST`` nor ``DJANGO_EMAIL_BACKEND`` is set. Rather than
printing verification and password-reset links to the server console (where
anyone with log access could use them) or crashing the request, every message
is dropped and an ERROR is logged. ``manage.py check`` also reports it
(``accounts.W001``).
"""
import logging

from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger('lostlink.accounts')


class UnconfiguredEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        messages = list(email_messages or [])
        for message in messages:
            # Log the subject and recipient count only; never the body (it
            # holds one-time links) or the addresses.
            logger.error(
                'Email NOT sent: email delivery is not configured '
                '(set DJANGO_EMAIL_HOST and related settings). Subject=%r, recipients=%d',
                message.subject, len(message.recipients()))
        return 0
