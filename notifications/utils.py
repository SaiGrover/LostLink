from .models import Notification


def notify_user(user, *, kind='system', title='', body='', url=''):
    """Create an in-app notification for ``user``."""
    if user is None:
        return None
    return Notification.objects.create(
        user=user, kind=kind, title=title, body=body[:280], url=url)
