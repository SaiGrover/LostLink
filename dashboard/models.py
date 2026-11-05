from django.conf import settings
from django.db import models


class ModerationDecision(models.Model):
    """Append-only audit trail of consequential moderation and admin actions.

    Every report approval/rejection, claim decision, handover confirmation,
    dispute, flag resolution and role change is recorded with the acting
    user, the affected record and an optional note (synopsis Section 8:
    "a record of moderation decisions").
    """

    class Action(models.TextChoices):
        REPORT_APPROVED = 'report_approved', 'Report approved'
        REPORT_REJECTED = 'report_rejected', 'Report rejected'
        CLAIM_APPROVED = 'claim_approved', 'Claim approved'
        CLAIM_REJECTED = 'claim_rejected', 'Claim rejected'
        HANDOVER_CONFIRMED = 'handover_confirmed', 'Handover confirmed'
        DISPUTE_RAISED = 'dispute_raised', 'Dispute raised'
        FLAG_RESOLVED = 'flag_resolved', 'Flag resolved'
        ROLE_CHANGED = 'role_changed', 'Role changed'
        USER_STATUS_CHANGED = 'user_status_changed', 'User activated / deactivated'

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                              related_name='moderation_decisions')
    action = models.CharField(max_length=24, choices=Action.choices)
    item = models.ForeignKey('items.Item', null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='moderation_decisions')
    claim = models.ForeignKey('claims.Claim', null=True, blank=True, on_delete=models.SET_NULL,
                              related_name='moderation_decisions')
    subject_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    note = models.CharField(max_length=280, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['action', 'created_at'])]

    def __str__(self):
        return f'{self.get_action_display()} by {self.actor_id} at {self.created_at:%Y-%m-%d %H:%M}'

    @property
    def tone(self):
        return {
            'report_approved': 'success', 'claim_approved': 'success', 'handover_confirmed': 'success',
            'report_rejected': 'danger', 'claim_rejected': 'danger', 'dispute_raised': 'warning',
        }.get(self.action, 'muted')


def record_decision(actor, action, *, item=None, claim=None, subject_user=None, note=''):
    """Convenience wrapper so views stay short."""
    return ModerationDecision.objects.create(
        actor=actor, action=action, item=item, claim=claim,
        subject_user=subject_user, note=(note or '')[:280])
