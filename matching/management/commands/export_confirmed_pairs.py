import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from claims.models import Claim
from items.models import Item


def report_record(item):
    return {
        'id': f'report-{item.pk}',
        'physical_id': '',  # A reviewer must assign a stable real-object identity.
        'description': item.description,
        'category': item.category.slug,
        'location': item.location.slug,
        'date': item.event_date.isoformat(),
        'time': item.event_time.isoformat(timespec='minutes') if item.event_time else '',
        'brand': item.brand,
        'color': item.color,
        'image': str((settings.MEDIA_ROOT / item.image.name).resolve()) if item.image else '',
    }


class Command(BaseCommand):
    help = 'Export moderator-confirmed returned pairs for controlled future retuning.'

    def add_arguments(self, parser):
        parser.add_argument('--output', default='evaluation/production/confirmed-pairs.json')

    def handle(self, output, **options):
        claims = (Claim.objects.filter(status=Claim.Status.RETURNED, match__isnull=False)
                  .select_related('match__lost_item__category', 'match__lost_item__location',
                                  'match__found_item__category', 'match__found_item__location',
                                  'reviewed_by'))
        pairs = []
        skipped = 0
        exported = 0
        for claim in claims:
            lost = claim.match.lost_item
            found = claim.match.found_item
            if not lost.image or not found.image or not claim.reviewed_by_id:
                skipped += 1
                continue
            split = 'validation' if claim.pk % 5 else 'test'
            query = report_record(lost)
            positive = report_record(found)
            pairs.append({
                'query_id': query['id'], 'split': split, 'label': 1,
                'query': query, 'candidate': positive,
                'review': {
                    'label_source': 'moderator-confirmed handover',
                    'reviewer': f'user-{claim.reviewed_by_id}',
                    'status': 'needs-research-review',
                    'independent_photos': None,
                    'independent_descriptions': None,
                    'claim_id': claim.pk,
                },
            })
            exported += 1
            negative = (Item.objects.filter(
                report_type=Item.Type.FOUND, category=lost.category,
                status__in=[Item.Status.APPROVED, Item.Status.CLAIMED, Item.Status.RETURNED])
                .exclude(pk=found.pk).exclude(image='').filter(image__isnull=False)
                .select_related('category', 'location').order_by('pk').first())
            if negative:
                pairs.append({
                    'query_id': query['id'], 'split': split, 'label': 0,
                    'query': query, 'candidate': report_record(negative),
                    'review': {
                        'label_source': 'moderator-confirmed positive with sampled non-match',
                        'reviewer': f'user-{claim.reviewed_by_id}',
                        'status': 'needs-manual-negative-review',
                        'independent_photos': None,
                        'independent_descriptions': None,
                        'claim_id': claim.pk,
                    },
                })
        payload = {
            'provenance': 'Restricted review export from moderator-approved, completed handovers.',
            'limitations': [
                'Negative candidates require manual review before research use.',
                'Dedicated username, contact and verification-answer fields are excluded.',
                'Free-text descriptions and photographs may contain personal data; redact and obtain consent before sharing.',
                'Photo independence, description authorship and physical identities require research review.',
                'A minimum sample size and source-disjoint split remain required before retuning.',
            ],
            'confirmed_claims_exported': exported,
            'claims_skipped_without_images_or_reviewer': skipped,
            'pairs': pairs,
        }
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        if not pairs:
            raise CommandError(f'No eligible completed handovers yet. Empty audit export written to {target}.')
        self.stdout.write(self.style.SUCCESS(str(target)))
