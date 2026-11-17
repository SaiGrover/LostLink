"""Delete private ownership-evidence files past the retention period.

Usage:  python manage.py purge_claim_evidence

Files of claims that were finalised (rejected, withdrawn or returned) more
than ``CLAIM_EVIDENCE_RETENTION_DAYS`` ago are deleted; the evidence record
stays (with ``purged_at`` set) so the audit trail remains complete.
Idempotent; safe to run daily from a scheduler.
"""
from django.core.management.base import BaseCommand

from claims.services import purge_expired_evidence


class Command(BaseCommand):
    help = 'Delete ownership-evidence files past the retention period.'

    def handle(self, *args, **options):
        count = purge_expired_evidence()
        self.stdout.write(self.style.SUCCESS(f'Purged {count} evidence file(s).'))
