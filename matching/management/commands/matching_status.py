"""Read-only health report for stored matches and per-report features.

Shows active vs retired matches (by reason), engine versions and
configuration drift, pairs whose stored taxonomy relation no longer holds,
coverage / tier distribution and feature-processing states. Nothing is
changed; run ``rebuild_matches`` or ``retry_features`` to act on it.
"""
import json
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand

from matching.features import manifest
from matching.models import ItemFeatures, Match
from matching.services import taxonomy_snapshot


class Command(BaseCommand):
    help = 'Summarise match lifecycle, versions, taxonomy staleness and feature states.'

    def handle(self, **options):
        version = settings.MATCHING['VERSION']
        config = manifest()
        taxonomy = taxonomy_snapshot()
        active = list(Match.objects.select_related('lost_item', 'found_item'))
        stale_taxonomy = 0
        for match in active:
            info = (match.details or {}).get('components', {})
            lost, found = match.lost_item, match.found_item
            related = frozenset((lost.category_id, found.category_id)) in taxonomy.related_pairs
            adjacent = frozenset((lost.location_id, found.location_id)) in taxonomy.adjacent_pairs
            if ('category' in info and info['category'].get('related', False) != related) or \
                    ('location' in info and info['location'].get('adjacent', False) != adjacent):
                stale_taxonomy += 1
        report = {
            'engine_version': version,
            'taxonomy_version': taxonomy.version,
            'active_matches': len(active),
            'active_by_version': dict(Counter(m.engine_version for m in active)),
            'active_with_other_configuration': sum(1 for m in active if m.configuration != config),
            'active_with_stale_taxonomy_relation': stale_taxonomy,
            'active_by_tier': dict(Counter(m.tier for m in active)),
            'active_capped': sum(1 for m in active if (m.details or {}).get('caps')),
            'active_chronology_warnings': sum(1 for m in active if m.chronology_warning),
            'mean_coverage': round(sum(m.evidence_coverage for m in active) / len(active), 3) if active else None,
            'retired_by_reason': dict(Counter(Match.all_objects.filter(status=Match.Status.RETIRED)
                                              .values_list('retired_reason', flat=True))),
            'features': {
                'text': dict(Counter(ItemFeatures.objects.values_list('text_state', flat=True))),
                'image': dict(Counter(ItemFeatures.objects.values_list('image_state', flat=True))),
                'image_models': dict(Counter(ItemFeatures.objects.exclude(image_model='')
                                             .values_list('image_model', flat=True))),
            },
        }
        self.stdout.write(json.dumps(report, indent=2))
