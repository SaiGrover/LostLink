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

    engine_version = models.CharField(max_length=20, default='v1.0.0')
    configuration = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-score']
        unique_together = ('lost_item', 'found_item')
        verbose_name_plural = 'matches'

    def __str__(self):
        return f'{self.lost_item_id}↔{self.found_item_id} ({self.score:.0%})'

    @property
    def percent(self):
        return round(self.score * 100)

    @property
    def tier_tone(self):
        return {'high': 'success', 'medium': 'warning', 'low': 'muted'}[self.tier]

    def breakdown(self):
        """Ordered component rows for the match-explanation UI."""
        weights = self.configuration.get('weights', {'text':.3,'image':.3,'location':.15,'category':.1,'date':.1,'attribute':.05})
        return [
            ('Text description', self.text_score, weights['text']),
            ('Image / visual', self.image_score, weights['image']),
            ('Location', self.location_score, weights['location']),
            ('Category', self.category_score, weights['category']),
            ('Date proximity', self.date_score, weights['date']),
            ('Brand / colour', self.attribute_score, weights['attribute']),
        ]

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
