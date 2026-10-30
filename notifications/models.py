from django.conf import settings
from django.db import models


class Notification(models.Model):
    """In-app notification (synopsis Section 4.14 — email is a future extension)."""

    class Kind(models.TextChoices):
        MATCH = 'match', 'Potential match'
        CLAIM = 'claim', 'Claim update'
        REPORT = 'report', 'Report update'
        SYSTEM = 'system', 'System'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='notifications')
    kind = models.CharField(max_length=10, choices=Kind.choices,
                            default=Kind.SYSTEM)
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=280, blank=True)
    url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_kind_display()} → {self.user_id}: {self.title}'

    @property
    def icon(self):
        return {'match': '🎯', 'claim': '🔑', 'report': '📋',
                'system': '🔔'}.get(self.kind, '🔔')
