"""Recompute features and matches after a model, weight, release, chronology
or taxonomy change (any change to scoring semantics).

* Features are recomputed only where their fingerprint changed.
* Every eligible pair is rescored with the current engine version; rows are
  updated in place (one row per pair), so claim references stay valid.
* Rows that are no longer valid -- older engine version, ineligible report,
  same-reporter pair, below the minimum score -- are retired, not deleted.
* Idempotent: running it twice gives identical scores and retires nothing
  new. Notifications are off by default; with ``--notify`` each pair is still
  notified at most once (``Match.notified_at``).
"""
import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from matching.models import Match
from matching.services import rebuild_all


class Command(BaseCommand):
    help = 'Recompute features and matches with the current engine version; retire stale rows.'

    def add_arguments(self, parser):
        parser.add_argument('--notify', action='store_true',
                            help='Notify reporters about pairs that surface for the first time.')

    def handle(self, notify=False, **options):
        started = time.monotonic()
        before = {
            'active': Match.objects.count(),
            'active_other_version': Match.objects.exclude(engine_version=settings.MATCHING['VERSION']).count(),
        }
        summary = rebuild_all(notify=notify)
        version = settings.MATCHING['VERSION']
        current = Match.objects.filter(engine_version=version)
        summary.update({
            'engine_version': version,
            'before': before,
            'tiers': {t: current.filter(tier=t).count() for t in ('high', 'medium', 'low')},
            'retired_total': Match.all_objects.filter(status=Match.Status.RETIRED).count(),
            'seconds': round(time.monotonic() - started, 2),
        })
        if options.get('verbosity', 1) > 1:
            self.stdout.write(json.dumps(summary, indent=2))
        self.stdout.write(self.style.SUCCESS(
            f'Rebuilt {current.count()} current-version matches ({version}); '
            f'retired now: {summary["retired"] or "none"}.'))
