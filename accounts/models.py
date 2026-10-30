from django.contrib.auth.models import AbstractUser
from django.db import models


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
    def initials(self):
        first = (self.first_name or self.username or '?')[:1]
        last = (self.last_name or '')[:1]
        return (first + last).upper()

    def display_name(self):
        full = self.get_full_name()
        return full or self.username

    def __str__(self):
        return f'{self.display_name()} ({self.get_role_display()})'
