import json
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Validate whether a pilot dataset is eligible for an academic accuracy claim.'

    def add_arguments(self, parser):
        parser.add_argument('dataset')
        parser.add_argument('--allow-small', action='store_true')

    def handle(self, dataset, allow_small=False, **options):
        path = Path(dataset)
        payload = json.loads(path.read_text(encoding='utf-8'))
        pairs = payload.get('pairs', [])
        errors = []

        if not pairs:
            errors.append('Dataset contains no pairs.')
        if not allow_small and not 150 <= len(pairs) <= 200:
            errors.append('The synopsis requires 150–200 labelled pairs.')

        split_labels = {}
        identities = {}
        for index, row in enumerate(pairs, 1):
            split = row.get('split')
            label = row.get('label')
            split_labels.setdefault(split, set()).add(label)
            for side in ('query', 'candidate'):
                record = row.get(side, {})
                identity = record.get('id')
                if identity:
                    identities.setdefault(identity, set()).add(split)
            review = row.get('review', {})
            if review.get('label_source') != 'manual':
                errors.append(f'Pair {index} is not manually labelled.')
            if review.get('status') != 'approved':
                errors.append(f'Pair {index} lacks approved review status.')
            if not review.get('reviewer'):
                errors.append(f'Pair {index} has no reviewer identifier.')
            if label == 1 and not review.get('independent_photos'):
                errors.append(f'Positive pair {index} does not use independently captured photos.')

        for split, labels in split_labels.items():
            if labels != {0, 1}:
                errors.append(f'Split {split!r} must contain positive and negative labels.')
        leaked = sorted(identity for identity, splits in identities.items() if len(splits) > 1)
        if leaked:
            errors.append(f'{len(leaked)} identities leak across validation/test splits.')

        summary = {
            'dataset': str(path),
            'pairs': len(pairs),
            'splits': Counter(row.get('split') for row in pairs),
            'research_ready': not errors,
            'errors': errors[:25],
            'additional_errors': max(0, len(errors) - 25),
        }
        self.stdout.write(json.dumps(summary, indent=2, default=dict))
        if errors:
            raise CommandError(
                'Dataset is suitable for engineering checks only; it is not eligible for an academic accuracy claim.')
        self.stdout.write(self.style.SUCCESS('Dataset passes research-readiness checks.'))
