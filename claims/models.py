"""Claims, ownership reservation, disputes, appeals, evidence and handover.

The claim workflow keeps four concerns apart:

* **Verification status** (``Claim.status``): submitted → questions →
  answered → approved / rejected, plus withdrawn by the claimant and
  returned once the physical handover is complete.
* **Ownership reservation**: an ``approved`` claim *is* the reservation. It
  is unique per item (database constraint) and survives disputes; only an
  explicit, audited moderator decision (revocation) or the claimant's own
  withdrawal releases it.
* **Disputes** (``ClaimDispute``): raised alongside a claim without changing
  its status. An open dispute suspends handover; it never frees the item.
* **Physical handover** (``HandoverSession``): a recipient-held one-time code
  (OTP). The person handing the item over enters it; only a valid code, or
  a documented moderator exception, marks the claim returned.

Claim approved ≠ item returned.
"""
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from items.models import Item
from matching.models import Match


class Claim(models.Model):
    """A request to recover an item, gated by human verification."""

    class Status(models.TextChoices):
        SUBMITTED = 'submitted', 'Submitted'
        QUESTIONS = 'questions', 'Awaiting answers'
        ANSWERED = 'answered', 'Answered — under review'
        APPROVED = 'approved', 'Approved — handover pending'
        REJECTED = 'rejected', 'Rejected'
        WITHDRAWN = 'withdrawn', 'Withdrawn'
        RETURNED = 'returned', 'Returned'

    class HandoverMethod(models.TextChoices):
        OTP = 'otp', 'Recipient one-time code'
        EXCEPTION = 'exception', 'Moderator exception (documented)'

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='claims')
    claimant = models.ForeignKey(settings.AUTH_USER_MODEL,
                                 on_delete=models.CASCADE, related_name='claims')
    match = models.ForeignKey(Match, on_delete=models.SET_NULL, null=True,
                              blank=True, related_name='claims')

    statement = models.TextField(
        help_text='Explain why this item is yours (proof of ownership).')
    # Contact details so the two parties can coordinate the handover once a
    # claim is approved (revealed only after approval).
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)

    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.SUBMITTED)

    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='reviewed_claims')
    decision_note = models.CharField(max_length=280, blank=True)
    # Number of reconsideration requests used after a rejection.
    appeal_count = models.PositiveSmallIntegerField(default=0)

    handover_method = models.CharField(max_length=10, choices=HandoverMethod.choices, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)

    # The claimant's own report of the same physical item (their lost report
    # when claiming a found item, or their found report when offering to
    # return a lost one). A matching suggestion is never enough: this is set
    # only by an explicit, audited confirmation (the reviewing moderator at
    # approval, or the claimant after the handover). Only a confirmed link
    # closes that second report.
    linked_report = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='confirmed_links')
    linked_report_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                                   null=True, blank=True, related_name='+')
    linked_report_confirmed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Database-level guarantee that a physical item is only ever
            # reserved for / returned to one verified claimant, even under
            # concurrency. Disputes do not change the status, so they never
            # release this reservation.
            models.UniqueConstraint(
                fields=['item'],
                condition=models.Q(status__in=['approved', 'returned']),
                name='one_verified_claim_per_item'),
        ]

    OPEN_STATUSES = ('submitted', 'questions', 'answered')
    RESERVED_STATUSES = ('approved',)
    VERIFIED_STATUSES = ('approved', 'returned')

    def __str__(self):
        return f'Claim #{self.pk} on {self.item.title} by {self.claimant}'

    def get_absolute_url(self):
        return reverse('claims:detail', args=[self.pk])

    @property
    def status_tone(self):
        if self.has_open_dispute and self.status not in ('rejected', 'withdrawn', 'returned'):
            return 'danger'
        return {
            self.Status.SUBMITTED: 'info',
            self.Status.QUESTIONS: 'warning',
            self.Status.ANSWERED: 'accent',
            self.Status.APPROVED: 'success',
            self.Status.REJECTED: 'danger',
            self.Status.WITHDRAWN: 'muted',
            self.Status.RETURNED: 'success',
        }.get(self.status, 'muted')

    @property
    def status_label(self):
        label = self.get_status_display()
        if self.has_open_dispute and self.status not in ('rejected', 'withdrawn', 'returned'):
            return f'{label} · disputed'
        return label

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_reserved(self):
        return self.status == self.Status.APPROVED

    @property
    def is_finalised(self):
        return self.status in (self.Status.REJECTED, self.Status.RETURNED, self.Status.WITHDRAWN)

    @property
    def open_dispute(self):
        if self.pk is None:
            return None
        return self.disputes.filter(status=ClaimDispute.Status.OPEN).first()

    @property
    def has_open_dispute(self):
        return self.pk is not None and self.open_dispute is not None

    @property
    def role_label(self):
        """What the claimant is, relative to the report type."""
        return 'Owner' if self.item.is_found else 'Finder'

    @property
    def recipient(self):
        """The person who physically receives the item: always the owner.

        For a found report the claimant is the owner; for a lost report the
        reporter is the owner and the claimant (a finder) hands it over.
        """
        return self.claimant if self.item.is_found else self.item.reporter

    @property
    def provider(self):
        """The person who physically hands the item over."""
        return self.item.reporter if self.item.is_found else self.claimant

    def timeline(self):
        """Ordered recovery lifecycle steps with their state for the UI.

        States: ``done``, ``current``, ``pending``, ``blocked``.
        """
        s = self.status
        reached = {'submitted': 0, 'questions': 1, 'answered': 2, 'approved': 4,
                   'returned': 5, 'rejected': 2, 'withdrawn': 2}[s]
        branch = s in (self.Status.REJECTED, self.Status.WITHDRAWN)
        has_questions = self.questions.exists()
        labels = [
            ('submitted', 'Claim submitted', 'Statement and contact details recorded.'),
            ('questions', 'Verification questions',
             'Private questions asked by the reporter or a moderator.' if has_questions
             else 'Waiting for the reporter or a moderator to ask questions.'),
            ('answered', 'Moderator review', 'An independent moderator checks the evidence checklist.'),
            ('approved', 'Approved · item reserved', 'Contact details shared; the item is reserved for this claimant.'),
            ('handover', 'Handover with one-time code',
             'The owner shares their code only after receiving and checking the item.'),
            ('returned', 'Returned', 'Handover confirmed.'),
        ]
        steps = []
        for index, (key, title, detail) in enumerate(labels):
            if branch and index == 2:
                if s == self.Status.REJECTED:
                    steps.append({'key': 'rejected', 'title': 'Claim rejected', 'state': 'blocked',
                                  'detail': self.decision_note or 'The claim could not be verified.'})
                else:
                    steps.append({'key': 'withdrawn', 'title': 'Claim withdrawn', 'state': 'blocked',
                                  'detail': 'The claimant withdrew this claim.'})
                continue
            if s == self.Status.RETURNED or index < reached:
                state = 'done'
            elif index == reached or (s == self.Status.APPROVED and index == 4):
                state = 'current'
            else:
                state = 'pending'
            if key == 'returned' and s == self.Status.RETURNED and self.handover_method == 'exception':
                detail = 'Recorded by a moderator exception with documented evidence.'
            elif key == 'returned' and s == self.Status.RETURNED:
                detail = 'Confirmed with the owner\'s one-time code.'
            if key == 'handover' and self.has_open_dispute and s == self.Status.APPROVED:
                state, detail = 'blocked', 'Paused while a moderator reviews a dispute.'
            steps.append({'key': key, 'title': title, 'state': state, 'detail': detail})
        return steps

    @property
    def all_answered(self):
        qs = self.questions.all()
        return qs.exists() and all(q.answer for q in qs)


class VerificationQuestion(models.Model):
    """A private question the genuine owner should be able to answer.

    ``expected_answer`` is what the person asking knows to be true (for
    example the finder looking at the item). It is visible only to the asker
    and moderators, never to the claimant, and is a guide for the human
    reviewer — answers are never auto-approved by comparison.
    """
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE,
                              related_name='questions')
    asked_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                 on_delete=models.SET_NULL, null=True)
    text = models.CharField(max_length=240)
    expected_answer = models.CharField(max_length=240, blank=True)
    answer = models.TextField(blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return self.text


class ClaimDispute(models.Model):
    """A concern raised about a claim. Never changes the claim status itself."""

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        RESOLVED = 'resolved', 'Resolved'

    class Outcome(models.TextChoices):
        DISMISSED = 'dismissed', 'Dismissed — claim continues'
        REVOKED = 'revoked', 'Upheld — approval revoked'
        REJECTED = 'rejected', 'Upheld — claim rejected'

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name='disputes')
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                  related_name='raised_disputes')
    reason = models.CharField(max_length=500)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    outcome = models.CharField(max_length=10, choices=Outcome.choices, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='resolved_disputes')
    resolution_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['claim'], condition=models.Q(status='open'),
                                    name='one_open_dispute_per_claim'),
        ]

    def __str__(self):
        return f'Dispute on claim #{self.claim_id} ({self.status})'


class ClaimRevision(models.Model):
    """Immutable history of a claim's statement: the original and each appeal."""

    class Kind(models.TextChoices):
        ORIGINAL = 'original', 'Original statement'
        APPEAL = 'appeal', 'Reconsideration request'

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name='revisions')
    number = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    statement = models.TextField()
    additional_details = models.TextField(blank=True)
    # The rejection this revision answers (empty for the original).
    responds_to_decision = models.CharField(max_length=280, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['number']
        constraints = [models.UniqueConstraint(fields=['claim', 'number'], name='unique_claim_revision')]

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop('force_history_write', False):
            raise ValueError('Claim revisions are immutable.')
        super().save(*args, **kwargs)


def evidence_upload_to(instance, filename):
    import uuid
    return f'claim_evidence/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.jpg'


class ClaimEvidence(models.Model):
    """Private supporting evidence (e.g. a photo of a receipt or a hidden mark).

    Files are re-encoded on upload (metadata stripped), stored outside the
    public media policy and served only to the claimant and moderators.
    Records are append-only; the file is purged after the retention period
    once the claim is finalised (``purge_claim_evidence``).
    """
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name='evidence')
    revision = models.ForeignKey(ClaimRevision, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='evidence')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    file = models.FileField(upload_to=evidence_upload_to, blank=True)
    note = models.CharField(max_length=280, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Evidence #{self.pk} for claim #{self.claim_id}'


class HandoverSession(models.Model):
    """One OTP challenge for the physical handover of an approved claim.

    Only a salted HMAC of the code is stored. At most one ``active`` session
    exists per claim (database constraint); issuing a new code supersedes the
    previous one.
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        VERIFIED = 'verified', 'Verified'
        EXPIRED = 'expired', 'Expired'
        SUPERSEDED = 'superseded', 'Replaced by a newer code'
        LOCKED = 'locked', 'Locked after failed attempts'
        SUSPENDED = 'suspended', 'Suspended (dispute)'
        CANCELLED = 'cancelled', 'Cancelled'

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name='handover_sessions')
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    provider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    salt = models.CharField(max_length=32)
    otp_hash = models.CharField(max_length=128)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    generation_number = models.PositiveSmallIntegerField(default=1)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['claim'], condition=models.Q(status='active'),
                                    name='one_active_handover_code_per_claim'),
        ]

    def __str__(self):
        return f'Handover code #{self.generation_number} for claim #{self.claim_id} ({self.status})'

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at
