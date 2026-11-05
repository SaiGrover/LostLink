from django.db import models

from items.models import Item


class Match(models.Model):
    """A potential lost↔found match produced by the hybrid engine.

    The component score breakdown is stored so the UI can show *why* two reports
    were paired rather than a single opaque percentage (synopsis Section 4.11).
    """

    class Tier(models.TextChoices):
        HIGH = 'high', 'High confidence'
        MEDIUM = 'medium', 'Medium confidence'
        LOW = 'low', 'Low confidence'

    lost_item = models.ForeignKey(Item, on_delete=models.CASCADE,
                                  related_name='matches_as_lost')
    found_item = models.ForeignKey(Item, on_delete=models.CASCADE,
                                   related_name='matches_as_found')

    score = models.FloatField()
    tier = models.CharField(max_length=6, choices=Tier.choices)

    # Component breakdown (each in [0, 1]).
    text_score = models.FloatField(default=0)
    image_score = models.FloatField(default=0)
    location_score = models.FloatField(default=0)
    category_score = models.FloatField(default=0)
    date_score = models.FloatField(default=0)
    attribute_score = models.FloatField(default=0)

    # Components that had no evidence on one or both reports and were
    # therefore dropped from the weighted sum (missing-data policy).
    missing_components = models.JSONField(default=list, blank=True)

    engine_version = models.CharField(max_length=20, default='v1.0.0')
    configuration = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-score']
        unique_together = ('lost_item', 'found_item')
        verbose_name_plural = 'matches'
        indexes = [models.Index(fields=['engine_version', 'tier'])]

    def __str__(self):
        return f'{self.lost_item_id}↔{self.found_item_id} ({self.score:.0%})'

    @property
    def percent(self):
        return round(self.score * 100)

    @property
    def tier_tone(self):
        return {'high': 'success', 'medium': 'warning', 'low': 'muted'}[self.tier]

    @property
    def model_label(self):
        """Human-readable model summary for the explanation UI."""
        text = {'tfidf': 'TF-IDF', 'sbert': 'Sentence-BERT'}.get(self.configuration.get('text'), '—')
        image = {'resnet': 'ResNet-50', 'clip': 'CLIP ViT-B/32',
                 'histogram': 'colour histogram (baseline)'}.get(self.configuration.get('image'), '—')
        return f'{text} text · {image} image'

    @property
    def effective_weights(self):
        from .engine import effective_weights
        base = self.configuration.get('weights') or {
            'text': .3, 'image': .3, 'location': .15, 'category': .1, 'date': .1, 'attribute': .05}
        return effective_weights(self.missing_components or (), base)

    def breakdown(self):
        """Ordered component rows for the match-explanation UI.

        Each row: ``{key, label, score, weight, effective_weight,
        contribution, missing}``. ``contribution`` is the component's share of
        the final score (effective weight x score) so the UI can show exactly
        how the parts add up to the total.
        """
        base = self.configuration.get('weights') or {
            'text': .3, 'image': .3, 'location': .15, 'category': .1, 'date': .1, 'attribute': .05}
        effective = self.effective_weights
        missing = set(self.missing_components or ())
        labels = [
            ('text', 'Text description', self.text_score),
            ('image', 'Photograph', self.image_score),
            ('location', 'Campus location', self.location_score),
            ('category', 'Category', self.category_score),
            ('date', 'Date proximity', self.date_score),
            ('attribute', 'Brand / colour', self.attribute_score),
        ]
        rows = []
        for key, label, score in labels:
            is_missing = key in missing
            weight = effective.get(key, 0.0)
            rows.append({
                'key': key, 'label': label,
                'score': 0.0 if is_missing else float(score),
                'percent': 0 if is_missing else round(float(score) * 100),
                'weight': base.get(key, 0.0),
                'effective_weight': weight,
                'effective_percent': round(weight * 100),
                'contribution': 0.0 if is_missing else weight * float(score),
                'contribution_percent': 0 if is_missing else round(weight * float(score) * 100),
                'contribution_width': '0.00' if is_missing else f'{weight * float(score) * 100:.2f}',
                'effective_width': f'{weight * 100:.2f}',
                'missing': is_missing,
                'missing_reason': {
                    'image': 'No photograph on one or both reports',
                    'attribute': 'Brand and colour not given on both reports',
                }.get(key, 'No evidence on one or both reports') if is_missing else '',
            })
        return rows

    def counterpart_for(self, item):
        """Return the *other* item in this match relative to ``item``."""
        return self.found_item if item == self.lost_item else self.lost_item


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
