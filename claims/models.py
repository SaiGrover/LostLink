from django.conf import settings
from django.db import models
from django.urls import reverse

from items.models import Item
from matching.models import Match


class Claim(models.Model):
    """A request to recover an item, gated by verification (synopsis Section 4.12).

    A high/medium-confidence match never transfers an item automatically. The
    claimant submits a claim, answers private verification questions only the
    genuine owner could answer, and a moderator makes the final call — the
    consequential decision stays human (Section 8).
    """

    class Status(models.TextChoices):
        SUBMITTED = 'submitted', 'Submitted'
        QUESTIONS = 'questions', 'Awaiting answers'
        ANSWERED = 'answered', 'Answered — under review'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        RETURNED = 'returned', 'Returned'
        DISPUTED = 'disputed', 'Disputed — moderator review'

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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Database-level guarantee that a physical item is only ever
            # assigned to one verified claimant, even under concurrency.
            models.UniqueConstraint(
                fields=['item'],
                condition=models.Q(status__in=['approved', 'returned']),
                name='one_verified_claim_per_item'),
        ]

    OPEN_STATUSES = ('submitted', 'questions', 'answered', 'disputed')

    def __str__(self):
        return f'Claim #{self.pk} on {self.item.title} by {self.claimant}'

    def get_absolute_url(self):
        return reverse('claims:detail', args=[self.pk])

    @property
    def status_tone(self):
        return {
            self.Status.SUBMITTED: 'info',
            self.Status.QUESTIONS: 'warning',
            self.Status.ANSWERED: 'accent',
            self.Status.APPROVED: 'success',
            self.Status.REJECTED: 'danger',
            self.Status.RETURNED: 'success',
            self.Status.DISPUTED: 'danger',
        }.get(self.status, 'muted')

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_finalised(self):
        return self.status in (self.Status.REJECTED, self.Status.RETURNED)

    @property
    def role_label(self):
        """What the claimant is, relative to the report type."""
        return 'Owner' if self.item.is_found else 'Finder'

    def timeline(self):
        """Ordered recovery lifecycle steps with their state for the UI.

        States: ``done``, ``current``, ``pending``, ``blocked`` (rejected /
        disputed branches).
        """
        s = self.status
        reached = {'submitted': 0, 'questions': 1, 'answered': 2, 'approved': 3,
                   'returned': 4, 'rejected': 2, 'disputed': 2}[s]
        branch = s in (self.Status.REJECTED, self.Status.DISPUTED)
        has_questions = self.questions.exists()
        labels = [
            ('submitted', 'Claim submitted', 'Statement and contact details recorded.'),
            ('questions', 'Verification questions',
             'Private questions asked by the finder or a moderator.' if has_questions
             else 'Waiting for the finder or a moderator to ask questions.'),
            ('answered', 'Moderator review', 'Answers compared against the report and match evidence.'),
            ('approved', 'Approved · handover', 'Contact details shared; arrange a safe handover.'),
            ('returned', 'Returned', 'Physical handover confirmed by a moderator.'),
        ]
        steps = []
        for index, (key, title, detail) in enumerate(labels):
            if branch and index == 2:
                if s == self.Status.REJECTED:
                    steps.append({'key': 'rejected', 'title': 'Claim rejected', 'state': 'blocked',
                                  'detail': self.decision_note or 'The claim could not be verified.'})
                else:
                    steps.append({'key': 'disputed', 'title': 'Disputed · moderator review',
                                  'state': 'blocked',
                                  'detail': self.decision_note or 'A concern was raised about this claim.'})
                continue
            if s == self.Status.RETURNED or index < reached:
                state = 'done'
            elif index == reached:
                state = 'current'
            else:
                state = 'pending'
            steps.append({'key': key, 'title': title, 'state': state, 'detail': detail})
        return steps

    @property
    def all_answered(self):
        qs = self.questions.all()
        return qs.exists() and all(q.answer for q in qs)


class VerificationQuestion(models.Model):
    """A private question the genuine owner should be able to answer."""
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE,
                              related_name='questions')
    asked_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                 on_delete=models.SET_NULL, null=True)
    text = models.CharField(max_length=240)
    answer = models.TextField(blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return self.text
