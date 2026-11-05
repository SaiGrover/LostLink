from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Category(models.Model):
    """Controlled category vocabulary (synopsis Section 4.2).

    Metadata comparison is exact-match against a fixed list rather than free
    text, so category can act as a coarse but reliable matching filter.
    """
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=70, unique=True)
    icon = models.CharField(max_length=8, default='📦',
                            help_text='Emoji shown in the UI.')
    description = models.CharField(max_length=200, blank=True)
    # Compatible categories receive partial category credit (e.g. a wallet
    # reported under "Bags & Wallets" versus "Others"); identical categories
    # score 1.0, related 0.5, unrelated 0.0 (synopsis Section 4.7).
    related = models.ManyToManyField('self', blank=True, symmetrical=True)

    class Meta:
        verbose_name_plural = 'categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Location(models.Model):
    """Fixed campus location zones (synopsis Section 3.3 — zone-based only)."""
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=90, unique=True)
    # Adjacent zones receive partial location credit (synopsis Section 4.7).
    adjacent = models.ManyToManyField('self', blank=True, symmetrical=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Item(models.Model):
    """A lost or found item report — the platform's primary operational data."""

    class Type(models.TextChoices):
        LOST = 'lost', 'Lost'
        FOUND = 'found', 'Found'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending review'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        CLAIMED = 'claimed', 'Claim in progress'
        RETURNED = 'returned', 'Returned / Resolved'

    reporter = models.ForeignKey(settings.AUTH_USER_MODEL,
                                 on_delete=models.CASCADE, related_name='items')
    report_type = models.CharField(max_length=6, choices=Type.choices)
    title = models.CharField(max_length=120)
    description = models.TextField(
        help_text='Describe the item — colour, material, distinctive marks.')
    category = models.ForeignKey(Category, on_delete=models.PROTECT,
                                 related_name='items')
    location = models.ForeignKey(Location, on_delete=models.PROTECT,
                                 related_name='items')
    # Date the item was lost / found (not necessarily the report date).
    event_date = models.DateField(default=timezone.localdate)
    event_time = models.TimeField(
        null=True, blank=True,
        help_text='Approximate time the item was lost or found.')

    # Optional structured attributes (contribute a small matching signal).
    brand = models.CharField(max_length=60, blank=True)
    color = models.CharField(max_length=40, blank=True)

    image = models.ImageField(upload_to='items/%Y/%m/', blank=True, null=True)

    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.PENDING)
    is_active = models.BooleanField(
        default=True,
        help_text='Inactive once returned or withdrawn — excluded from matching.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Cached feature vectors for matching (computed at save time,
    # synopsis Section 4.4 — features computed once per report).
    text_vector = models.JSONField(null=True, blank=True)
    image_vector = models.JSONField(null=True, blank=True)
    feature_version = models.CharField(max_length=200, blank=True)
    duplicate_of = models.ForeignKey('self', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='possible_duplicates')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['report_type', 'status']),
            models.Index(fields=['category', 'location']),
        ]

    def __str__(self):
        return f'[{self.get_report_type_display()}] {self.title}'

    def get_absolute_url(self):
        return reverse('items:detail', args=[self.pk])

    @property
    def is_lost(self):
        return self.report_type == self.Type.LOST

    @property
    def is_found(self):
        return self.report_type == self.Type.FOUND

    @property
    def opposite_type(self):
        return self.Type.FOUND if self.is_lost else self.Type.LOST

    @property
    def status_tone(self):
        """Maps status to a UI colour token."""
        return {
            self.Status.PENDING: 'warning',
            self.Status.APPROVED: 'info',
            self.Status.REJECTED: 'danger',
            self.Status.CLAIMED: 'accent',
            self.Status.RETURNED: 'success',
        }.get(self.status, 'muted')

    @property
    def is_matchable(self):
        return self.is_active and self.status in (
            self.Status.APPROVED, self.Status.CLAIMED)

    @property
    def is_public(self):
        """Visible in discovery and eligible for matching."""
        return self.is_active and self.status in (self.Status.APPROVED, self.Status.CLAIMED)

    @property
    def is_demo_photo(self):
        return bool(self.image and self.image.name.rsplit('/', 1)[-1].startswith('demo-real-'))

    @property
    def open_flag_count(self):
        return self.flags.filter(resolved_at__isnull=True).count()


class ReportFlag(models.Model):
    """A user-raised concern about a report (suspicious, duplicate, wrong).

    Flags never change a report by themselves; they queue it for moderator
    attention. One open flag per user per report.
    """

    class Reason(models.TextChoices):
        DUPLICATE = 'duplicate', 'Duplicate of another report'
        SUSPICIOUS = 'suspicious', 'Suspicious or misleading'
        INAPPROPRIATE = 'inappropriate', 'Inappropriate content or photo'
        OTHER = 'other', 'Something else'

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='flags')
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name='raised_flags')
    reason = models.CharField(max_length=16, choices=Reason.choices, default=Reason.OTHER)
    note = models.CharField(max_length=280, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='resolved_flags')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['item', 'reporter'],
                                    condition=models.Q(resolved_at__isnull=True),
                                    name='one_open_flag_per_user_per_item'),
        ]

    def __str__(self):
        return f'Flag on {self.item_id} ({self.get_reason_display()})'
