"""Apply the legacy-account policy of migration 0003 again.

Useful after restoring users from a fixture or snapshot made before email
verification existed (``loaddata`` leaves ``email_verified_at`` empty, which
would leave those accounts unverified).

    python manage.py mark_accounts_verified --dry-run
    python manage.py mark_accounts_verified --username saanvi --username aditya
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User


class Command(BaseCommand):
    help = 'Mark currently unverified accounts as verified with the "legacy" marker.'

    def add_arguments(self, parser):
        parser.add_argument('--username', action='append', default=[],
                            help='Limit to these usernames (repeatable).')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        qs = User.objects.filter(email_verified_at__isnull=True)
        if options['username']:
            qs = qs.filter(username__in=options['username'])
        count = qs.count()
        if options['dry_run']:
            self.stdout.write(f'{count} account(s) would be marked verified (legacy).')
            return
        qs.update(email_verified_at=timezone.now(),
                  email_verification_method=User.VerificationMethod.LEGACY)
        self.stdout.write(self.style.SUCCESS(f'{count} account(s) marked verified (legacy).'))
