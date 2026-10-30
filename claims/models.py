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
        }.get(self.status, 'muted')

    @property
    def is_open(self):
        return self.status not in (self.Status.APPROVED, self.Status.REJECTED, self.Status.RETURNED)

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
