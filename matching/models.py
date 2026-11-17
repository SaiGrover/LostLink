from django.db import models

from items.models import Item


class CurrentMatchManager(models.Manager):
    """Default manager: only *active* (actionable) matches.

    Retired rows are kept as history and claim evidence but never surface
    through ``Match.objects`` or the reverse relations
    (``item.matches_as_lost`` / ``item.matches_as_found``). Use
    ``Match.all_objects`` for history; ``claim.match`` still resolves a
    retired row because forward relations use the base manager.
    """

    def get_queryset(self):
        return super().get_queryset().filter(status=Match.Status.ACTIVE)


class Match(models.Model):
    """A potential lost↔found pair produced by the hybrid engine.

    ``score`` is a weighted similarity / compatibility score in [0, 1], not
    a probability. The component breakdown, the missing components, evidence
    coverage and an explanation payload (``details``) are stored so the UI can
    show *why* two reports were paired (synopsis Section 4.11).
    """

    class Tier(models.TextChoices):
        # Labels read naturally in "<label> similarity" (see tier_label).
        HIGH = 'high', 'Strong'
        MEDIUM = 'medium', 'Moderate'
        LOW = 'low', 'Weak'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        RETIRED = 'retired', 'Retired'

    class RetiredReason(models.TextChoices):
        WITHDRAWN = 'withdrawn', 'A report was withdrawn'
        REJECTED = 'rejected', 'A report was rejected or removed'
        RETURNED = 'returned', 'A report was returned / resolved'
        PENDING_REVIEW = 'pending_review', 'A report was edited and awaits review'
        INELIGIBLE = 'ineligible', 'A report is no longer eligible for matching'
        CANDIDATE_INELIGIBLE = 'candidate_ineligible', 'The other report is no longer a candidate'
        BELOW_THRESHOLD = 'below_threshold', 'Rescored below the minimum similarity'
        ENGINE_VERSION = 'engine_version', 'Superseded by a new engine version'

    lost_item = models.ForeignKey(Item, on_delete=models.CASCADE,
                                  related_name='matches_as_lost')
    found_item = models.ForeignKey(Item, on_delete=models.CASCADE,
                                   related_name='matches_as_found')

    score = models.FloatField()
    tier = models.CharField(max_length=6, choices=Tier.choices)

    # Component breakdown (each in [0, 1]; 0 when the component is missing).
    text_score = models.FloatField(default=0)
    image_score = models.FloatField(default=0)
    location_score = models.FloatField(default=0)
    category_score = models.FloatField(default=0)
    date_score = models.FloatField(default=0)
    attribute_score = models.FloatField(default=0)

    # Components that had no evidence on one or both reports and were
    # therefore dropped from the weighted sum (missing-data policy).
    missing_components = models.JSONField(default=list, blank=True)
    # Share of the base weight that was backed by evidence on both reports.
    evidence_coverage = models.FloatField(default=1.0)
    # Explanation payload from engine.score_evidence: per-component status
    # (agree / partial / low / conflict / missing + reason), chronology,
    # tier caps and warnings.
    details = models.JSONField(default=dict, blank=True)

    engine_version = models.CharField(max_length=20, default='v1.0.0')
    configuration = models.JSONField(default=dict, blank=True)
    taxonomy_version = models.CharField(max_length=16, blank=True, default='')

    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    retired_reason = models.CharField(max_length=24, choices=RetiredReason.choices, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    # Set when both reporters were told about this pair; never re-notified.
    notified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CurrentMatchManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-score']
        unique_together = ('lost_item', 'found_item')
        verbose_name_plural = 'matches'
        base_manager_name = 'all_objects'
        indexes = [models.Index(fields=['engine_version', 'tier']),
                   models.Index(fields=['status', 'engine_version'])]

    def __str__(self):
        return f'{self.lost_item_id}↔{self.found_item_id} ({self.score:.0%})'

    # --- presentation ------------------------------------------------------
    @property
    def percent(self):
        return round(self.score * 100)

    @property
    def tier_tone(self):
        return {'high': 'success', 'medium': 'warning', 'low': 'muted'}[self.tier]

    @property
    def tier_label(self):
        return f'{self.get_tier_display()} similarity'

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    @property
    def coverage_percent(self):
        return round((self.evidence_coverage if self.evidence_coverage is not None else 1.0) * 100)

    @property
    def tier_caps(self):
        """Caps that lowered the threshold tier, with readable explanations."""
        out = []
        for cap in (self.details or {}).get('caps', []):
            label = dict(self.Tier.choices).get(cap.get('tier'), cap.get('tier')) + ' similarity'
            if cap.get('reason') == 'coverage':
                text = (f'Shown as {label.lower()} at most: only {self.coverage_percent}% of the '
                        'evidence could be compared on both reports.')
            else:
                text = f'Shown as {label.lower()} at most because the dates or times conflict.'
            out.append({'reason': cap.get('reason'), 'tier': cap.get('tier'), 'text': text})
        return out

    @property
    def chronology_status(self):
        return ((self.details or {}).get('chronology') or {}).get('status', '')

    @property
    def chronology_warning(self):
        chrono = (self.details or {}).get('chronology') or {}
        return chrono.get('warning', '') if chrono.get('status') in (
            'suspicious', 'contradictory') else ''

    @property
    def warnings(self):
        return list((self.details or {}).get('warnings', []))

    @property
    def model_label(self):
        """Human-readable model summary for the explanation UI."""
        config = self.configuration or {}
        if config.get('text') == 'sbert':
            text = 'Sentence-BERT'
        elif config.get('text') == 'tfidf':
            text = f"fixed-vocabulary lexical model {config.get('text_release', '')}".strip()
        else:
            text = '—'
        image = {'resnet': 'ResNet-50', 'clip': 'CLIP ViT-B/32',
                 'histogram': 'colour histogram (baseline)'}.get(config.get('image'), '—')
        return f'{text} text · {image} image'

    def _base_weights(self):
        return (self.configuration or {}).get('weights') or {
            'text': .3, 'image': .3, 'location': .15, 'category': .1, 'date': .1, 'attribute': .05}

    @property
    def effective_weights(self):
        from .engine import effective_weights
        return effective_weights(self.missing_components or (), self._base_weights())

    MISSING_REASONS = {
        'no_photo_lost': 'No photo on the lost report',
        'no_photo_found': 'No photo on the found report',
        'no_photo_both': 'Neither report has a photo',
        'photo_not_processed': 'A photo could not be compared',
        'photo_pending': 'A photo is still being processed',
        'incompatible_models': 'Photos were processed by different models',
        'no_attributes': 'Brand and colour not given on both reports',
        'no_text': 'No descriptive text to compare',
        'no_date': 'A date is missing',
        'no_location': 'A location is missing',
        'no_category': 'A category is missing',
        'disabled': 'Not used in this evaluation run',
    }
    STATUS_LABELS = {'agree': 'Agrees', 'partial': 'Partly agrees', 'low': 'Little resemblance',
                     'conflict': 'Contradicts', 'missing': 'Not compared'}

    def breakdown(self):
        """Ordered component rows for the match-explanation UI.

        Each row: ``{key, label, score, percent, weight, base_percent,
        effective_weight, effective_percent, contribution,
        contribution_percent, missing, missing_reason, status, status_label,
        note}``. ``contribution`` is effective weight x similarity, so the
        rows add up to the total score.
        """
        base = self._base_weights()
        effective = self.effective_weights
        missing = set(self.missing_components or ())
        info = (self.details or {}).get('components', {})
        labels = [
            ('text', 'Title and description', self.text_score),
            ('image', 'Photograph', self.image_score),
            ('location', 'Campus location', self.location_score),
            ('category', 'Category', self.category_score),
            ('date', 'Date and time', self.date_score),
            ('attribute', 'Brand / colour', self.attribute_score),
        ]
        rows = []
        for key, label, score in labels:
            is_missing = key in missing
            weight = effective.get(key, 0.0)
            row_info = info.get(key, {})
            status = 'missing' if is_missing else row_info.get('status', '')
            reason = row_info.get('reason', '')
            rows.append({
                'key': key, 'label': label,
                'score': 0.0 if is_missing else float(score),
                'percent': 0 if is_missing else round(float(score) * 100),
                'weight': base.get(key, 0.0),
                'base_percent': round(base.get(key, 0.0) * 100),
                'effective_weight': weight,
                'effective_percent': round(weight * 100),
                'contribution': 0.0 if is_missing else weight * float(score),
                'contribution_percent': 0 if is_missing else round(weight * float(score) * 100),
                'contribution_width': '0.00' if is_missing else f'{weight * float(score) * 100:.2f}',
                'effective_width': f'{weight * 100:.2f}',
                'missing': is_missing,
                'missing_reason': (self.MISSING_REASONS.get(reason)
                                   or {'image': 'No photograph on one or both reports',
                                       'attribute': 'Brand and colour not given on both reports',
                                       }.get(key, 'No evidence on one or both reports')) if is_missing else '',
                'status': status,
                'status_label': self.STATUS_LABELS.get(status, ''),
                'note': self._row_note(key, row_info) if not is_missing else '',
            })
        return rows

    @staticmethod
    def _row_note(key, info):
        if key == 'date':
            chrono = info.get('chronology')
            if chrono in ('approximate', 'suspicious', 'contradictory'):
                return {'approximate': 'found slightly before lost (approximate)',
                        'suspicious': 'found before lost: check dates',
                        'contradictory': 'found well before lost: dates contradict'}[chrono]
            return 'times compared' if info.get('time_compared') else ''
        if key == 'attribute':
            parts = []
            for name, value in (('brand', info.get('brand')), ('colour', info.get('colour'))):
                if value:
                    parts.append(f"{name} {'matches' if value == 'full' else 'partly matches' if value == 'partial' else 'differs'}")
            return ', '.join(parts)
        if key == 'location' and info.get('adjacent'):
            return 'neighbouring zone'
        if key == 'category' and info.get('related'):
            return 'compatible category'
        if key == 'text' and info.get('shared_terms'):
            return 'shared: ' + ', '.join(info['shared_terms'][:4])
        return ''

    @property
    def missing_rows(self):
        return [r for r in self.breakdown() if r['missing']]

    def counterpart_for(self, item):
        """Return the *other* item in this match relative to ``item``."""
        return self.found_item if item == self.lost_item else self.lost_item


class ItemFeatures(models.Model):
    """Per-report feature-processing state (one row per report).

    Records whether the text and photo features were produced, which model
    actually produced them, and diagnostics for moderators. An intentionally
    absent photo is ``missing``; a photo whose processing failed is
    ``failed`` (or ``retrying`` while automatic retries remain). Error text is
    only shown to staff; students see "photo not compared".
    """

    class State(models.TextChoices):
        SUCCESS = 'success', 'Processed'
        PENDING = 'pending', 'Waiting to be processed'
        MISSING = 'missing', 'Not supplied'
        FAILED = 'failed', 'Processing failed'
        RETRYING = 'retrying', 'Failed; will retry'

    item = models.OneToOneField(Item, on_delete=models.CASCADE, related_name='match_features')
    text_state = models.CharField(max_length=8, choices=State.choices, default=State.PENDING)
    text_model = models.CharField(max_length=120, blank=True)
    image_state = models.CharField(max_length=8, choices=State.choices, default=State.PENDING)
    # The model that actually produced the stored image vector:
    # 'resnet', 'clip' or 'histogram' (the lightweight baseline).
    image_model = models.CharField(max_length=20, blank=True)
    image_model_detail = models.CharField(max_length=120, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=200, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'item features'
        indexes = [models.Index(fields=['image_state']), models.Index(fields=['text_state'])]

    def __str__(self):
        return f'Features for report {self.item_id}: text {self.text_state}, image {self.image_state}'

    @property
    def image_model_label(self):
        from .engine import IMAGE_MODEL_LABELS
        return IMAGE_MODEL_LABELS.get(self.image_model, self.image_model or '—')

    @property
    def needs_retry(self):
        states = {self.text_state, self.image_state}
        return bool(states & {self.State.FAILED, self.State.RETRYING, self.State.PENDING})


class ScoreSnapshot(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    configuration = models.JSONField(default=dict)
    report_count = models.PositiveIntegerField(default=0)
    match_count = models.PositiveIntegerField(default=0)
    mean_score = models.FloatField(default=0)
    histogram = models.JSONField(default=list)
    tiers = models.JSONField(default=dict)

    class Meta:
        ordering = ['-created_at']
