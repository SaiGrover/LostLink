"""Retry feature extraction for reports whose processing failed or is pending.

    python manage.py retry_features                # failed / retrying / pending reports
    python manage.py retry_features --item 42      # one report, regardless of state
    python manage.py retry_features --no-rematch   # only recompute features

Attempts are reset, so a report whose retries were exhausted gets a fresh
set. Eligible reports whose features change are rescored; reporters are told
about a pair at most once (``Match.notified_at``).
"""
import json

from django.core.management.base import BaseCommand
from django.db.models import Q

from items.models import Item
from matching.features import features_for, prepare
from matching.models import ItemFeatures
from matching.services import is_match_eligible, run_matching

S = ItemFeatures.State


class Command(BaseCommand):
    help = 'Retry text/image feature extraction for failed, retrying or pending reports.'

    def add_arguments(self, parser):
        parser.add_argument('--item', type=int, action='append', default=[],
                            help='Report id to retry (repeatable).')
        parser.add_argument('--no-rematch', action='store_true')

    def handle(self, item, no_rematch=False, **options):
        if item:
            items = Item.objects.filter(pk__in=item)
        else:
            needs = Q(match_features__text_state__in=[S.FAILED, S.RETRYING, S.PENDING]) | \
                Q(match_features__image_state__in=[S.FAILED, S.RETRYING, S.PENDING]) | \
                Q(match_features__isnull=True)
            items = Item.objects.filter(needs).filter(is_active=True)
        summary = {'retried': 0, 'succeeded': 0, 'still_failing': [], 'rescored': 0}
        for report in items.select_related('match_features').order_by('pk'):
            features = features_for(report)
            features.attempts = 0
            features.save(update_fields=['attempts', 'updated_at'])
            summary['retried'] += 1
            if prepare(report, force=True):
                summary['succeeded'] += 1
            else:
                report.match_features.refresh_from_db()
                summary['still_failing'].append({'item': report.pk,
                                                 'text': report.match_features.text_state,
                                                 'image': report.match_features.image_state,
                                                 'error': report.match_features.last_error})
            if not no_rematch and is_match_eligible(report):
                run_matching(report, notify=True)
                summary['rescored'] += 1
        self.stdout.write(json.dumps(summary, indent=2))
