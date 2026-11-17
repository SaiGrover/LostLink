from django.conf import settings
from django.db import models


class ModerationDecision(models.Model):
    """Append-only audit trail of consequential state changes.

    Every report transition, claim decision, appeal, dispute, handover code
    event, flag resolution and role change is recorded with the actor, the
    affected record, the previous and new state, a reason and structured
    metadata (e.g. the evidence checklist). Rows cannot be edited or deleted
    through the ORM's normal save/delete paths; plaintext secrets (handover
    codes, passwords) are never written here.
    """

    class Action(models.TextChoices):
        REPORT_APPROVED = 'report_approved', 'Report approved'
        REPORT_REJECTED = 'report_rejected', 'Report rejected'
        REPORT_WITHDRAWN = 'report_withdrawn', 'Report withdrawn'
        REPORT_REMOVED = 'report_removed', 'Report removed'
        REPORT_RESUBMITTED = 'report_resubmitted', 'Report edited and resubmitted'
        REPORT_STATUS_CHANGED = 'report_status_changed', 'Report status changed'
        CLAIM_APPROVED = 'claim_approved', 'Claim approved'
        CLAIM_REJECTED = 'claim_rejected', 'Claim rejected'
        CLAIM_WITHDRAWN = 'claim_withdrawn', 'Claim withdrawn'
        CLAIM_REOPENED = 'claim_reopened', 'Claim reconsideration requested'
        CLAIM_REVOKED = 'claim_revoked', 'Claim approval revoked'
        EVIDENCE_ADDED = 'evidence_added', 'Ownership evidence added'
        HANDOVER_CODE_ISSUED = 'handover_code_issued', 'Handover code issued'
        HANDOVER_CODE_FAILED = 'handover_code_failed', 'Handover code attempt failed'
        HANDOVER_SUSPENDED = 'handover_suspended', 'Handover suspended'
        HANDOVER_CONFIRMED = 'handover_confirmed', 'Handover confirmed (code)'
        HANDOVER_EXCEPTION = 'handover_exception', 'Handover recorded by exception'
        DISPUTE_RAISED = 'dispute_raised', 'Dispute raised'
        DISPUTE_RESOLVED = 'dispute_resolved', 'Dispute resolved'
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
    from_state = models.CharField(max_length=24, blank=True)
    to_state = models.CharField(max_length=24, blank=True)
    note = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['action', 'created_at'])]

    def __str__(self):
        return f'{self.get_action_display()} by {self.actor_id} at {self.created_at:%Y-%m-%d %H:%M}'

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ValueError('Audit records are append-only.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Audit records are append-only.')

    @property
    def tone(self):
        return {
            'report_approved': 'success', 'claim_approved': 'success', 'handover_confirmed': 'success',
            'handover_exception': 'warning', 'report_rejected': 'danger', 'claim_rejected': 'danger',
            'claim_revoked': 'danger', 'report_removed': 'danger', 'dispute_raised': 'warning',
            'handover_code_failed': 'warning', 'handover_suspended': 'warning',
        }.get(self.action, 'muted')


def record_decision(actor, action, *, item=None, claim=None, subject_user=None, note='',
                    from_state='', to_state='', metadata=None):
    """Append one audit record. Never pass secrets in ``note``/``metadata``."""
    return ModerationDecision.objects.create(
        actor=actor, action=action, item=item, claim=claim, subject_user=subject_user,
        note=(note or '')[:500], from_state=from_state or '', to_state=to_state or '',
        metadata=metadata or {})
