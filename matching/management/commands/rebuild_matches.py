from django.core.management.base import BaseCommand
from items.models import Item
from matching.models import Match
from matching.services import run_matching
from matching.features import manifest


class Command(BaseCommand):
    help = 'Recompute image features and matches after changing model configuration.'

    def handle(self, **options):
        for item in Item.objects.all():
            from matching.features import prepare
            prepare(item)
        for item in Item.objects.filter(report_type='lost', is_active=True, status__in=['approved','claimed']):
            run_matching(item, notify=False)
        count = Match.objects.filter(configuration=manifest()).count()
        self.stdout.write(self.style.SUCCESS(f'Rebuilt {count} current-version matches.'))
