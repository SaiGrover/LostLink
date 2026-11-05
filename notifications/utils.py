from datetime import timedelta

from django.utils import timezone

from .models import Notification


def notify_user(user, *, kind='system', title='', body='', url='', item=None, dedupe=False):
    """Create an in-app notification for ``user``.

    With ``dedupe=True`` an identical unread notification (same user, kind,
    title and link) created in the last 24 hours is reused instead of
    creating a second one, so repeated matching runs never spam a reporter.
    """
    if user is None:
        return None
    if dedupe:
        existing = Notification.objects.filter(
            user=user, kind=kind, title=title[:160], url=url, is_read=False,
            created_at__gte=timezone.now() - timedelta(hours=24)).first()
        if existing:
            return existing
    return Notification.objects.create(
        user=user, kind=kind, title=title[:160], body=body[:280], url=url, item=item)
