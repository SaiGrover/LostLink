from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Custom user with a campus role.

    LostLink is a role-based platform (synopsis Section 1.2): a Student reports
    and searches, a Moderator reviews and verifies, an Administrator oversees
    the whole system. Role-based access control is enforced via the helper
    predicates below and the decorators in ``accounts.decorators``.
    """

    class Role(models.TextChoices):
        STUDENT = 'student', 'Student'
        MODERATOR = 'moderator', 'Moderator'
        ADMIN = 'admin', 'Administrator'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STUDENT,
    )
    enrolment_no = models.CharField(max_length=30, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    avatar_color = models.CharField(max_length=7, default='#E8623C')

    # --- campus identity ---------------------------------------------------
    class VerificationMethod(models.TextChoices):
        EMAIL = 'email', 'Email link'
        PASSWORD_RESET = 'password_reset', 'Password reset link'
        ADMIN = 'admin', 'Confirmed by an administrator'
        LEGACY = 'legacy', 'Existing account (before verification)'

    # When the account's current email address was proven to belong to its
    # owner. Cleared whenever the email address changes. Pre-existing accounts
    # were marked by migration 0003 with method ``legacy``.
    email_verified_at = models.DateTimeField(null=True, blank=True)
    email_verification_method = models.CharField(
        max_length=20, choices=VerificationMethod.choices, blank=True)

    # --- role predicates -------------------------------------------------
    @property
    def is_student(self):
        return self.role == self.Role.STUDENT

    @property
    def is_moderator(self):
        return self.role == self.Role.MODERATOR

    @property
    def is_administrator(self):
        # A Django superuser is always treated as an administrator too.
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_staff_role(self):
        """Moderator or Administrator — i.e. anyone with review powers."""
        return self.is_moderator or self.is_administrator

    @property
    def is_email_verified(self):
        return self.email_verified_at is not None

    @property
    def is_campus_verified(self):
        """May this account perform campus-restricted actions (reporting,
        editing reports, flagging, claiming)?

        Self-registered accounts must prove ownership of their email address.
        Staff accounts (moderators, administrators, Django staff and
        superusers) are provisioned by an administrator, who vouches for them,
        so they are never locked out.
        """
        if not self.is_active:
            return False
        if self.is_staff or self.is_staff_role:
            return True
        return self.email_verified_at is not None

    def mark_email_verified(self, method, save=True):
        self.email_verified_at = timezone.now()
        self.email_verification_method = method
        if save:
            self.save(update_fields=['email_verified_at', 'email_verification_method'])

    def clear_email_verification(self):
        """Forget the proof of ownership (the email address changed)."""
        self.email_verified_at = None
        self.email_verification_method = ''

    @property
    def initials(self):
        first = (self.first_name or self.username or '?')[:1]
        last = (self.last_name or '')[:1]
        return (first + last).upper()

    def display_name(self):
        full = self.get_full_name()
        return full or self.username

    def __str__(self):
        return f'{self.display_name()} ({self.get_role_display()})'
