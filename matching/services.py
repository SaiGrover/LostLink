"""Orchestration around the matching engine: run it for a report and persist
the resulting potential matches (synopsis Sections 4.9 and 4.14).

The service layer owns the guarantees the pure engine does not:

* **Eligibility** -- one rule, ``Item.objects.matchable()``, used for the
  report being matched (:func:`is_match_eligible`) and for its candidates
  (:func:`candidate_queryset`). Opposite report type only; never the same
  report, never the same reporter.
* **Idempotence** -- one ``Match`` row per (lost, found) pair, updated in
  place on every run and versioned by engine version, model manifest and
  taxonomy fingerprint. Re-running produces identical rows.
* **No stale actionable matches** -- a pair that falls below the minimum
  score, whose counterpart left the candidate pool, or whose report left
  circulation is *retired* (``status='retired'`` + reason), not left active.
  Retired rows stay available as history / claim evidence through
  ``Match.all_objects`` but are hidden from ``Match.objects``.
* **Notification hygiene** -- both reporters are told about a pair once,
  the first time it reaches the moderate or strong tier (``notified_at``).
  Re-runs, rebuilds, taxonomy recalculation and version changes never
  re-notify.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from items.models import Item

from . import engine
from .models import Match

logger = logging.getLogger('lostlink.matching')

SURFACED_TIERS = ('high', 'medium')


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------
def is_match_eligible(item: Item) -> bool:
    """Whether ``item`` takes part in matching (same rule as candidates)."""
    if item is None or item.pk is None:
        return False
    return Item.objects.matchable().filter(pk=item.pk).exists()


def candidate_queryset(item: Item):
    """Eligible, opposite-type reports by someone else that ``item`` could match."""
    return (Item.objects.matchable()
            .filter(report_type=item.opposite_type)
            .exclude(pk=item.pk)
            .exclude(reporter_id=item.reporter_id)
            .select_related('category', 'location', 'match_features'))


def retirement_reason(item: Item) -> str:
    """Why ``item`` no longer produces actionable matches."""
    R = Match.RetiredReason
    if item.status == Item.Status.RETURNED:
        return R.RETURNED
    if item.status == Item.Status.REJECTED:
        return R.REJECTED
    if not item.is_active:
        return R.WITHDRAWN
    if item.status == Item.Status.PENDING:
        return R.PENDING_REVIEW
    return R.INELIGIBLE


def _involving(item):
    return Q(lost_item=item) | Q(found_item=item)


def retire_matches(item: Item, reason: str) -> int:
    """Retire every active match involving ``item``; returns how many.

    Rows are kept (history, claim evidence) but disappear from
    ``Match.objects`` and therefore from every page and count.
    """
    now = timezone.now()
    return Match.objects.filter(_involving(item)).update(
        status=Match.Status.RETIRED, retired_reason=reason, retired_at=now, updated_at=now)


def clear_matches(item: Item) -> int:
    """Take ``item``'s matches out of circulation (public API, kept for callers).

    Used when a report's content changes or it leaves the matchable pool
    (withdrawn, rejected, returned, edited). Matches are retired with a
    reason derived from the report's state rather than deleted, so claim
    evidence survives; they no longer count as current matches.
    """
    return retire_matches(item, retirement_reason(item))


# ---------------------------------------------------------------------------
# Taxonomy snapshot (compatible categories / neighbouring zones)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Taxonomy:
    related_pairs: frozenset
    adjacent_pairs: frozenset
    version: str


def taxonomy_snapshot() -> Taxonomy:
    """Current category/location relations plus a short fingerprint (2 queries)."""
    from items.models import Category, Location
    related = frozenset(frozenset(p) for p in Category.related.through.objects
                        .values_list('from_category_id', 'to_category_id') if p[0] != p[1])
    adjacent = frozenset(frozenset(p) for p in Location.adjacent.through.objects
                         .values_list('from_location_id', 'to_location_id') if p[0] != p[1])
    payload = repr((sorted(tuple(sorted(p)) for p in related),
                    sorted(tuple(sorted(p)) for p in adjacent)))
    return Taxonomy(related, adjacent, hashlib.sha256(payload.encode()).hexdigest()[:12])


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _lost_found(item: Item, candidate: Item):
    return (item, candidate) if item.is_lost else (candidate, item)


def evidence_for(item: Item) -> engine.Evidence:
    """Build the engine's evidence record from a report and its cached features."""
    from .features import fingerprint
    from .models import ItemFeatures
    try:
        features = item.match_features
    except ItemFeatures.DoesNotExist:
        features = None
    if features is None:
        # Never prepared (legacy rows, direct score_pair calls): use whatever
        # vectors the report carries; text falls back to the lexical release.
        text_vector, text_model, image_vector, image_model, image_state = (
            None, '', item.image_vector or None, '', '')
    elif features.fingerprint and features.fingerprint == fingerprint(item):
        text_vector, text_model = item.text_vector, features.text_model
        image_vector, image_model, image_state = (item.image_vector or None, features.image_model,
                                                  features.image_state)
    else:
        # Content or configuration changed since the features were computed:
        # stale vectors are never compared.
        text_vector, text_model, image_vector, image_model, image_state = None, '', None, '', 'pending'
    return engine.Evidence(
        report_type=item.report_type, title=item.title, description=item.description,
        category=item.category_id, location=item.location_id,
        event_date=item.event_date, event_time=item.event_time,
        brand=item.brand, color=item.color,
        text_vector=text_vector, text_model=text_model,
        image_vector=image_vector, image_model=image_model,
        has_photo=bool(item.image), image_state=image_state, key=item.pk)


def score_pair(item: Item, candidate: Item, text_sim: float | None = None,
               adjacent_ids=None, related_ids=None, taxonomy: Taxonomy | None = None) -> dict:
    """Full component breakdown for one lost<->found pair.

    ``adjacent_ids`` / ``related_ids`` (relative to ``item``) are accepted for
    backward compatibility; otherwise the current taxonomy snapshot is used.
    ``text_sim`` is ignored: text similarity is always computed pairwise from
    the two reports so it cannot depend on how candidates were batched.
    """
    lost, found = _lost_found(item, candidate)
    if taxonomy is not None:
        related, adjacent = taxonomy.related_pairs, taxonomy.adjacent_pairs
    else:
        if related_ids is None:
            related_ids = set(item.category.related.values_list('id', flat=True)) \
                if item.category_id else set()
        if adjacent_ids is None:
            adjacent_ids = set(item.location.adjacent.values_list('id', flat=True)) \
                if item.location_id else set()
        related = frozenset(frozenset((item.category_id, r)) for r in related_ids)
        adjacent = frozenset(frozenset((item.location_id, a)) for a in adjacent_ids)
    return engine.score_evidence(evidence_for(lost), evidence_for(found),
                                 related_pairs=related, adjacent_pairs=adjacent)


# Kept for older callers (tests, scripts).
def compute_pair_scores(item: Item, candidate: Item) -> dict:
    from .features import prepare
    prepare(item)
    prepare(candidate)
    return score_pair(item, candidate)


def _match_fields(result: dict, configuration: dict, taxonomy: Taxonomy) -> dict:
    comp = result['components']
    return {
        'score': result['score'],
        'tier': result['tier'],
        'text_score': comp['text'] or 0.0,
        'image_score': comp['image'] or 0.0,
        'location_score': comp['location'] or 0.0,
        'category_score': comp['category'] or 0.0,
        'date_score': comp['date'] or 0.0,
        'attribute_score': comp['attribute'] or 0.0,
        'missing_components': result['missing'],
        'evidence_coverage': result['coverage'],
        'details': result['details'],
        'engine_version': settings.MATCHING['VERSION'],
        'configuration': configuration,
        'taxonomy_version': taxonomy.version,
        'status': Match.Status.ACTIVE,
        'retired_reason': '',
        'retired_at': None,
    }


@transaction.atomic
def run_matching(item: Item, notify: bool = True, *, candidates=None,
                 taxonomy: Taxonomy | None = None, configuration: dict | None = None) -> list[Match]:
    """Score ``item`` against every eligible candidate and upsert Match rows.

    Returns the active matches at or above the minimum score, best first.
    ``candidates`` / ``taxonomy`` / ``configuration`` let bulk callers
    (``rebuild_matches``) share one prepared candidate list, one taxonomy
    snapshot and one manifest across many reports.
    """
    from .features import manifest, prepare

    if not is_match_eligible(item):
        retire_matches(item, retirement_reason(item))
        return []

    min_score = settings.MATCHING['MIN_SCORE']
    prepare(item)
    if candidates is None:
        candidates = list(candidate_queryset(item))
        for candidate in candidates:
            prepare(candidate)
    else:
        candidates = [c for c in candidates if c.report_type == item.opposite_type
                      and c.pk != item.pk and c.reporter_id != item.reporter_id]
    taxonomy = taxonomy or taxonomy_snapshot()
    configuration = configuration or manifest()
    item_evidence = evidence_for(item)

    side = 'found_item_id' if item.is_lost else 'lost_item_id'
    previous = {getattr(m, side): m for m in Match.all_objects.filter(_involving(item))}

    current: list[Match] = []
    newly_surfaced: list[Match] = []
    seen: set[int] = set()
    now = timezone.now()

    for candidate in candidates:
        seen.add(candidate.pk)
        cand_evidence = evidence_for(candidate)
        lost_ev, found_ev = (item_evidence, cand_evidence) if item.is_lost else (cand_evidence, item_evidence)
        result = engine.score_evidence(lost_ev, found_ev, related_pairs=taxonomy.related_pairs,
                                       adjacent_pairs=taxonomy.adjacent_pairs)
        old = previous.get(candidate.pk)
        if result['score'] < min_score:
            if old is not None and (old.status == Match.Status.ACTIVE
                                    or old.retired_reason != Match.RetiredReason.BELOW_THRESHOLD):
                # Keep the row as history, but record the latest reason.
                Match.all_objects.filter(pk=old.pk).update(
                    status=Match.Status.RETIRED, retired_reason=Match.RetiredReason.BELOW_THRESHOLD,
                    retired_at=now if old.status == Match.Status.ACTIVE else old.retired_at,
                    updated_at=now)
            continue

        lost, found = _lost_found(item, candidate)
        fields = _match_fields(result, configuration, taxonomy)
        if old is None:
            match = Match.all_objects.create(lost_item=lost, found_item=found, **fields)
        else:
            match = old
            changed = [name for name, value in fields.items() if getattr(match, name) != value]
            if changed:  # identical reruns write nothing (idempotent, no per-row queries)
                for name in changed:
                    setattr(match, name, fields[name])
                match.save()
        match.lost_item, match.found_item = lost, found
        current.append(match)
        if match.tier in SURFACED_TIERS and match.notified_at is None:
            newly_surfaced.append(match)

    # Counterparts that are no longer candidates (withdrawn, returned,
    # rejected, reassigned to the same reporter, ...) must not stay active.
    stale = [m.pk for other, m in previous.items()
             if other not in seen and m.status == Match.Status.ACTIVE]
    if stale:
        Match.all_objects.filter(pk__in=stale).update(
            status=Match.Status.RETIRED, retired_reason=Match.RetiredReason.CANDIDATE_INELIGIBLE,
            retired_at=now, updated_at=now)

    current.sort(key=lambda m: m.score, reverse=True)
    if notify and newly_surfaced:
        _notify_matches(item, newly_surfaced)
        Match.all_objects.filter(pk__in=[m.pk for m in newly_surfaced]).update(notified_at=now)
        for match in newly_surfaced:
            match.notified_at = now
    return current


def _notify_matches(item: Item, matches: list[Match]):
    """Notify both reporters about newly surfaced moderate/strong pairs.

    Each counterpart receives one notification per pair; the triggering
    reporter receives one summary notification for the batch.
    """
    from notifications.utils import notify_user

    tier_rank = {'high': 3, 'medium': 2, 'low': 1}
    best = max(matches, key=lambda m: (tier_rank.get(m.tier, 0), m.score))
    counterpart = best.counterpart_for(item)

    for match in matches:
        other = match.counterpart_for(item)
        label = 'Strong similarity' if match.tier == 'high' else 'Moderate similarity'
        notify_user(other.reporter, kind='match',
                    title=f'{label}: a report resembles “{other.title}”',
                    body=(f'“{item.title}” has a similarity score of {match.percent}/100 '
                          f'(evidence coverage {match.coverage_percent}%), a weighted comparison, not a '
                          'probability. Similarity is not proof of ownership; verification is required.'),
                    url=other.get_absolute_url(), item=item, dedupe=True)

    count = len(matches)
    notify_user(
        item.reporter, kind='match',
        title=(f'{count} similar report{"s" if count != 1 else ""} for “{item.title}”'),
        body=(f'Most similar: “{counterpart.title}”, similarity score {best.percent}/100. '
              'A moderator verifies ownership before anything is handed over.'),
        url=item.get_absolute_url(), item=counterpart, dedupe=True)


# ---------------------------------------------------------------------------
# Bulk maintenance
# ---------------------------------------------------------------------------
def reconcile_matches() -> dict:
    """Retire active matches that are no longer valid, whatever the cause.

    Covers reports that left the matchable pool, same-reporter or
    same-report pairs, and rows from another engine version. Idempotent.
    Returns ``{reason: count}``.
    """
    now = timezone.now()
    R = Match.RetiredReason
    counts = {}
    matchable = Item.objects.matchable().values('pk')
    active = Match.objects.all()
    for match in (active.exclude(lost_item__in=matchable) | active.exclude(found_item__in=matchable)) \
            .select_related('lost_item', 'found_item').distinct():
        lost, found = match.lost_item, match.found_item
        culprit = lost if not is_match_eligible(lost) else found
        reason = retirement_reason(culprit)
        Match.all_objects.filter(pk=match.pk).update(status=Match.Status.RETIRED,
                                                     retired_reason=reason, retired_at=now)
        counts[reason] = counts.get(reason, 0) + 1
    from django.db.models import F
    bad = Match.objects.filter(Q(lost_item__reporter=F('found_item__reporter')) |
                               Q(lost_item=F('found_item')) |
                               ~Q(lost_item__report_type=Item.Type.LOST) |
                               ~Q(found_item__report_type=Item.Type.FOUND))
    n = bad.update(status=Match.Status.RETIRED, retired_reason=R.CANDIDATE_INELIGIBLE, retired_at=now)
    if n:
        counts[R.CANDIDATE_INELIGIBLE] = counts.get(R.CANDIDATE_INELIGIBLE, 0) + n
    n = Match.objects.exclude(engine_version=settings.MATCHING['VERSION']).update(
        status=Match.Status.RETIRED, retired_reason=R.ENGINE_VERSION, retired_at=now)
    if n:
        counts[R.ENGINE_VERSION] = n
    return counts


def rebuild_all(notify: bool = False, stdout=None) -> dict:
    """Recompute features and every eligible pair, then retire leftovers.

    Used by ``rebuild_matches`` after a model / weight / release / taxonomy
    change. Idempotent: a second run rewrites identical scores, retires
    nothing new and sends no notifications.
    """
    from .features import manifest, prepare
    eligible = list(Item.objects.matchable().select_related('category', 'location', 'match_features'))
    failed = 0
    for item in eligible:
        if not prepare(item):
            failed += 1
    taxonomy = taxonomy_snapshot()
    configuration = manifest()
    found_items = [i for i in eligible if i.is_found]
    pairs = 0
    for item in eligible:
        if item.is_lost:
            pairs += len(run_matching(item, notify=notify, candidates=found_items,
                                      taxonomy=taxonomy, configuration=configuration))
    retired = reconcile_matches()
    return {'reports': len(eligible), 'feature_failures': failed, 'active_matches': pairs,
            'retired': retired, 'taxonomy_version': taxonomy.version}


def recalculate_for_taxonomy(category_ids=(), location_ids=(), notify: bool = True) -> int:
    """Rescore every pair whose category or location relation may have changed.

    A pair's category (location) component depends only on whether its two
    categories (zones) are related. A relation edit between A and B can only
    change pairs whose lost *and* found reports both sit in {A, B, ...}, so
    re-running matching for the affected *lost* reports covers every
    affected pair exactly once. Returns the number of lost reports rescored.
    """
    category_ids, location_ids = set(category_ids or ()), set(location_ids or ())
    if not category_ids and not location_ids:
        return 0
    from .features import manifest
    taxonomy = taxonomy_snapshot()
    configuration = manifest()
    lost = (Item.objects.matchable().filter(report_type=Item.Type.LOST)
            .filter(Q(category_id__in=category_ids) | Q(location_id__in=location_ids))
            .select_related('category', 'location', 'match_features'))
    count = 0
    for item in lost:
        run_matching(item, notify=notify, taxonomy=taxonomy, configuration=configuration)
        count += 1
    logger.info('Taxonomy change: rescored %s lost report(s) under taxonomy %s', count, taxonomy.version)
    return count
