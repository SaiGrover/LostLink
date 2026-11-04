import json
from django.core.management.base import BaseCommand, CommandError
from matching.research import validate_dataset


class Command(BaseCommand):
    help = 'Validate whether a pilot dataset is eligible for an academic accuracy claim.'

    def add_arguments(self, parser):
        parser.add_argument('dataset')
        parser.add_argument('--allow-small', action='store_true', help='Check schema only; never marks data research-ready.')

    def handle(self, dataset, allow_small=False, **options):
        summary = validate_dataset(dataset, allow_small)
        self.stdout.write(json.dumps(summary, indent=2))
        if not summary['checks_passed']:
            raise CommandError('Dataset failed research-readiness checks.')
        self.stdout.write(self.style.SUCCESS('Dataset checks passed; human review remains required.'))
