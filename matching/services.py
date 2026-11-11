"""Orchestration around the matching engine: run it for a report and persist
the resulting potential matches (synopsis Sections 4.9 and 4.14).

The service layer owns three guarantees the engine itself does not:

* **Eligibility** -- only active, moderator-approved (or claim-in-progress)
  reports of the opposite type, by a different reporter, are candidates.
* **Idempotence** -- one ``Match`` row per (lost, found) pair, updated in
  place on every run, versioned by engine version and model manifest; pairs
  that fall below the minimum score or become temporally impossible are
  removed.
* **Notification hygiene** -- a reporter is notified once per surfaced
  (medium/high) pair, when it first appears or when a rescore lifts it out
  of the low tier. Repeated runs never re-notify.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from items.models import Item

from . import engine
from .models import Match

logger = logging.getLogger('lostlink.matching')

SURFACED_TIERS = ('high', 'medium')


def candidate_queryset(item: Item):
    """Active, reviewed, opposite-type reports this item could match against."""
    return (Item.objects
            .filter(report_type=item.opposite_type, is_active=True)
            .filter(status__in=[Item.Status.APPROVED, Item.Status.CLAIMED])
            .exclude(reporter=item.reporter)
            .select_related('category', 'location'))


def _lost_found(item: Item, candidate: Item):
    return (item, candidate) if item.is_lost else (candidate, item)


def score_pair(item: Item, candidate: Item, text_sim: float | None = None,
               adjacent_ids=None, related_ids=None) -> dict:
    """Full component breakdown for one lost<->found pair.

    ``text_sim`` may be supplied by a batched TF-IDF pass; otherwise it is
    computed for this pair alone. Missing components follow the engine's
    documented missing-data policy.
    """
    lost, found = _lost_found(item, candidate)
    if text_sim is None:
        if item.text_vector and candidate.text_vector:
            text_sim = engine.vector_similarity(item.text_vector, candidate.text_vector)
        else:
            text_sim = engine.text_similarity(item.description, [candidate.description])[0]

    missing: list[str] = []
    has_image_pair = bool(item.image_vector) and bool(candidate.image_vector)
    if has_image_pair:
        image = engine.calibrated_image_similarity(item.image_vector, candidate.image_vector)
    else:
        image, missing = 0.0, missing + ['image']

    if adjacent_ids is None:
        adjacent_ids = set(item.location.adjacent.values_list('id', flat=True)) \
            if item.location_id else set()
    if related_ids is None:
        related_ids = set(item.category.related.values_list('id', flat=True)) \
            if item.category_id else set()

    attribute = engine.attribute_score(item.brand, item.color, candidate.brand, candidate.color)
    if attribute is None:
        attribute, missing = 0.0, missing + ['attribute']

    components = {
        'text': text_sim,
        'image': image,
        'location': engine.location_score(item.location_id, candidate.location_id, adjacent_ids),
        'category': engine.category_score(item.category_id, candidate.category_id, related_ids),
        'date': engine.date_score(lost.event_date, found.event_date),
        'attribute': attribute,
    }
    score = engine.combine(components, missing=missing)
    return {'components': components, 'score': score, 'missing': missing,
            'tier': engine.confidence_tier(score),
            'plausible': engine.is_temporally_plausible(lost.event_date, found.event_date)}


# Kept for older callers (tests, scripts).
def compute_pair_scores(item: Item, candidate: Item) -> dict:
    from .features import prepare
    prepare(item)
    prepare(candidate)
    return score_pair(item, candidate)


@transaction.atomic
def run_matching(item: Item, notify: bool = True) -> list[Match]:
    """Score ``item`` against every eligible candidate and upsert Match rows.

    Returns the current matches at or above the minimum score, best first.
    """
    from .features import manifest, prepare

    if item.status not in (Item.Status.APPROVED, Item.Status.CLAIMED) or not item.is_active:
        return []

    min_score = settings.MATCHING['MIN_SCORE']
    version = settings.MATCHING['VERSION']
    prepare(item)
    candidates = list(candidate_queryset(item))
    for candidate in candidates:
        prepare(candidate)

    # One TF-IDF fit over the whole candidate pool (see engine.text_similarity).
    if item.text_vector:
        text_sims = [engine.vector_similarity(item.text_vector, c.text_vector)
                     if c.text_vector else None for c in candidates]
    else:
        text_sims = engine.text_similarity(item.description, [c.description for c in candidates])

    adjacent_ids = set(item.location.adjacent.values_list('id', flat=True))
    related_ids = set(item.category.related.values_list('id', flat=True))
    configuration = manifest()

    if item.is_lost:
        previous = {m.found_item_id: m for m in Match.objects.filter(lost_item=item)}
    else:
        previous = {m.lost_item_id: m for m in Match.objects.filter(found_item=item)}

    current: list[Match] = []
    newly_surfaced: list[Match] = []
    keep_ids: set[int] = set()

    for candidate, text_sim in zip(candidates, text_sims):
        result = score_pair(item, candidate, text_sim, adjacent_ids, related_ids)
        old = previous.get(candidate.pk)
        if not result['plausible'] or result['score'] < min_score:
            if old is not None:
                old.delete()
            continue

        lost, found = _lost_found(item, candidate)
        comp = result['components']
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
                'missing_components': result['missing'],
                'engine_version': version,
                'configuration': configuration,
            },
        )
        keep_ids.add(match.pk)
        current.append(match)
        was_surfaced = old is not None and old.tier in SURFACED_TIERS \
            and old.engine_version == version
        if match.tier in SURFACED_TIERS and (is_new or not was_surfaced):
            newly_surfaced.append(match)

    current.sort(key=lambda m: m.score, reverse=True)
    if notify and newly_surfaced:
        _notify_matches(item, newly_surfaced)
    return current


def _notify_matches(item: Item, matches: list[Match]):
    """Notify both reporters about newly surfaced medium/high matches.

    Each counterpart receives one notification per pair; the triggering
    reporter receives one summary notification for the batch.
    """
    from notifications.utils import notify_user

    tier_rank = {'high': 3, 'medium': 2, 'low': 1}
    best = max(matches, key=lambda m: (tier_rank.get(m.tier, 0), m.score))
    counterpart = best.counterpart_for(item)

    for match in matches:
        other = match.counterpart_for(item)
        label = 'Likely match' if match.tier == 'high' else 'Potential match'
        notify_user(other.reporter, kind='match',
                    title=f'{label} for “{other.title}”',
                    body=(f'“{item.title}” scored {match.percent}% similarity. '
                          'Similarity is not proof of ownership; verification is required.'),
                    url=other.get_absolute_url(), item=item, dedupe=True)

    count = len(matches)
    notify_user(
        item.reporter, kind='match',
        title=(f'{count} potential match{"es" if count != 1 else ""} for “{item.title}”'),
        body=f'Top candidate “{counterpart.title}” at {best.percent}% similarity.',
        url=item.get_absolute_url(), item=counterpart, dedupe=True)


def clear_matches(item: Item) -> int:
    """Remove every stored match involving ``item`` (used when its content
    changes or it leaves the matchable pool)."""
    qs = Match.objects.filter(lost_item=item) if item.is_lost else Match.objects.filter(found_item=item)
    count, _ = qs.delete()
    return count
