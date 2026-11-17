"""Create the default report categories and campus zones.

Usage:  python manage.py seed_taxonomy [--if-empty]

Safe on a production database: idempotent, never edits or deletes existing
rows, creates no accounts, passwords or reports, and is independent of the
demo data. See items/taxonomy.py for the exact rules.
"""
from django.core.management.base import BaseCommand

from items.models import Category, Location
from items.taxonomy import ensure_default_taxonomy


class Command(BaseCommand):
    help = 'Create the default categories and campus zones (idempotent, keeps customisations).'

    def add_arguments(self, parser):
        parser.add_argument('--if-empty', action='store_true',
                            help='Seed each vocabulary only if it has no rows yet '
                                 '(use in deploy hooks so deleted defaults stay deleted).')

    def handle(self, *args, **options):
        summary = ensure_default_taxonomy(only_if_empty=options['if_empty'])
        for kind, label in (('categories', 'categories'), ('locations', 'campus zones')):
            if summary[f'{kind}_skipped']:
                self.stdout.write(f'{label.capitalize()}: already configured, left unchanged (--if-empty).')
            elif summary[f'{kind}_created']:
                self.stdout.write(f'Created {len(summary[f"{kind}_created"])} {label}: '
                                  + ', '.join(summary[f'{kind}_created']))
            else:
                self.stdout.write(f'{label.capitalize()}: all defaults already present.')
        if summary['relations_added']:
            self.stdout.write(f'Added {summary["relations_added"]} related-category / adjacent-zone links.')
        self.stdout.write(self.style.SUCCESS(
            f'Taxonomy ready: {Category.objects.count()} categories, {Location.objects.count()} campus zones.'))
