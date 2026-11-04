"""Orchestration around the matching engine: run it for a report and persist
the resulting potential matches (synopsis Section 4.9, Section 4.14)."""
from __future__ import annotations

from django.conf import settings
from django.db import transaction

from items.models import Item

from . import engine
from .models import Match


def compute_pair_scores(item: Item, candidate: Item) -> dict:
    """Compute the full component breakdown for one lost↔found pair."""
    from .features import prepare
    prepare(item)
    prepare(candidate)
    text = engine.image_similarity(item.text_vector, candidate.text_vector) if item.text_vector and candidate.text_vector else engine.text_similarity(item.description, [candidate.description])[0]
    has_image_pair = bool(item.image_vector) and bool(candidate.image_vector)
    image = engine.image_similarity(item.image_vector, candidate.image_vector) \
        if has_image_pair else 0.0

    adjacent_ids = set(item.location.adjacent.values_list('id', flat=True)) \
        if item.location_id else set()

    components = {
        'text': text,
        'image': image,
        'location': engine.location_score(item.location_id, candidate.location_id,
                                          adjacent_ids),
        'category': engine.category_score(item.category_id, candidate.category_id),
        'date': engine.date_score(item.event_date, candidate.event_date),
        'attribute': engine.attribute_score(item.brand, item.color,
                                            candidate.brand, candidate.color),
    }
    score = engine.combine(components, has_image_pair)
    return {'components': components, 'score': score,
            'tier': engine.confidence_tier(score)}


def candidate_queryset(item: Item):
    """Active, opposite-type reports this item could match against."""
    return (Item.objects
            .filter(report_type=item.opposite_type, is_active=True)
            .filter(status__in=[Item.Status.APPROVED, Item.Status.CLAIMED])
            .exclude(reporter=item.reporter)
            .select_related('category', 'location'))


@transaction.atomic
def run_matching(item: Item, notify: bool = True) -> list[Match]:
    """Score ``item`` against every candidate and upsert Match rows.

    Returns the created/updated matches at or above the minimum score, ordered
    by descending confidence. Optionally emits in-app notifications for matches
    above the low-confidence threshold (Section 4.14).
    """
    min_score = settings.MATCHING['MIN_SCORE']
    if item.status not in (Item.Status.APPROVED, Item.Status.CLAIMED) or not item.is_active:
        return []
    version = settings.MATCHING['VERSION']
    created: list[Match] = []
    fresh = []

    for candidate in candidate_queryset(item):
        result = compute_pair_scores(item, candidate)
        if result['score'] < min_score:
            continue

        lost = item if item.is_lost else candidate
        found = candidate if item.is_lost else item
        comp = result['components']

        from .features import manifest
        match, is_new = Match.objects.update_or_create(
            lost_item=lost, found_item=found,
            defaults={
                'score': result['score'],
                'tier': result['tier'],
                'text_score': comp['text'],
                'image_score': comp['image'],
                'location_score': comp['location'],
                'category_score': comp['category'],
                'date_score': comp['date'],
                'attribute_score': comp['attribute'],
                'engine_version': version,
                'configuration': manifest(),
            },
        )
        created.append(match)
        if is_new:
            fresh.append(match)

    created.sort(key=lambda m: m.score, reverse=True)

    if notify and fresh:
        _notify_matches(item, fresh)
    return created


def _notify_matches(item: Item, matches: list[Match]):
    """Notify the reporter (and counterpart) about fresh potential matches."""
    from notifications.utils import notify_user

    tier_rank = {'high': 3, 'medium': 2, 'low': 1}
    best = max(matches, key=lambda m: tier_rank.get(m.tier, 0))
    # Only surface medium/high proactively; low is search-only (Section 4.9).
    surfaced = [m for m in matches if m.tier in ('high', 'medium')]
    if not surfaced:
        return

    counterpart = best.counterpart_for(item)
    for match in surfaced:
        other = match.counterpart_for(item)
        notify_user(other.reporter, kind='match', title=f'Potential match for {other.title}',
                    body=f'{item.title}: {match.percent}% similarity. Human verification required.',
                    url=other.get_absolute_url(), item=item)
    notify_user(
        item.reporter,
        kind='match',
        title=f'{len(surfaced)} potential match(es) for “{item.title}”',
        body=f'Top match “{counterpart.title}” at {best.percent}% similarity.',
        url=item.get_absolute_url(),
        item=counterpart,
    )
