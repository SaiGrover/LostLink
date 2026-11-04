import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Analyse the manual-browse versus AJAX discovery study defined in docs/STUDY_PROTOCOL.md.'

    def add_arguments(self, parser):
        parser.add_argument('responses')
        parser.add_argument('--output', default='evaluation/studies/discovery-results.json')

    def handle(self, responses, output, **options):
        source = Path(responses)
        with source.open(newline='', encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        required = {'participant_id', 'task_id', 'mode', 'success', 'seconds'}
        if not rows or not required.issubset(rows[0]):
            raise CommandError(f'CSV must contain: {", ".join(sorted(required))}.')

        grouped = defaultdict(list)
        participant_modes = defaultdict(set)
        seen = set()
        for row in rows:
            mode = row['mode'].strip().lower()
            if mode not in {'manual', 'ajax'}:
                raise CommandError('mode must be manual or ajax.')
            try:
                success = int(row['success'])
                seconds = float(row['seconds'])
            except ValueError as exc:
                raise CommandError('success must be 0/1 and seconds must be numeric.') from exc
            if success not in (0, 1) or not math.isfinite(seconds) or seconds <= 0:
                raise CommandError('success must be 0/1 and seconds must be finite and positive.')
            participant = row['participant_id'].strip()
            task = row['task_id'].strip()
            if not participant or not task:
                raise CommandError('participant_id and task_id must not be blank.')
            key = (participant, mode, task)
            if key in seen:
                raise CommandError('Duplicate participant/mode/task observation.')
            seen.add(key)
            grouped[mode].append({'success': success, 'seconds': seconds})
            participant_modes[participant].add(mode)

        incomplete = sorted(pid for pid, modes in participant_modes.items()
                            if modes != {'manual', 'ajax'})
        if incomplete:
            raise CommandError('Every participant must complete both modes: ' + ', '.join(incomplete[:10]))

        result = {'source': str(source), 'participants': len(participant_modes), 'modes': {}}
        for mode in ('manual', 'ajax'):
            observations = grouped[mode]
            times = [row['seconds'] for row in observations]
            result['modes'][mode] = {
                'observations': len(observations),
                'success_rate': sum(row['success'] for row in observations) / len(observations),
                'mean_seconds': mean(times),
                'median_seconds': median(times),
            }
        manual = result['modes']['manual']['median_seconds']
        ajax = result['modes']['ajax']['median_seconds']
        result['median_time_reduction_percent'] = ((manual - ajax) / manual * 100) if manual else None
        result['limitations'] = [
            'Descriptive results only; report participant sampling and counterbalancing.',
            'Do not claim causality without an adequately powered and controlled study.',
        ]
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(str(target)))
