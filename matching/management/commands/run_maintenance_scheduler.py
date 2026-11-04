import time
import math

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections


class Command(BaseCommand):
    help = 'Run score monitoring and confirmed-pair export on a host-managed interval.'

    def add_arguments(self, parser):
        parser.add_argument('--interval-hours', type=float, default=24.0)
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--export-output', default='evaluation/production/confirmed-pairs.json')

    def run_cycle(self, export_output):
        call_command('monitor_matching')
        try:
            call_command('export_confirmed_pairs', output=export_output)
        except CommandError as exc:
            self.stdout.write(self.style.WARNING(str(exc)))

    def handle(self, interval_hours, once=False, export_output=None, **options):
        if not math.isfinite(interval_hours) or interval_hours <= 0:
            raise CommandError('--interval-hours must be finite and positive.')
        self.run_cycle(export_output)
        if once:
            return
        self.stdout.write(
            self.style.SUCCESS(f'Maintenance scheduler active every {interval_hours:g} hours.'))
        try:
            while True:
                time.sleep(interval_hours * 3600)
                close_old_connections()
                self.run_cycle(export_output)
        except KeyboardInterrupt:
            self.stdout.write('Maintenance scheduler stopped.')
