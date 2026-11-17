"""Keep stored matches consistent with the data they were computed from.

Connected in ``MatchingConfig.ready``. Everything here lives in the matching
app so the dashboard, items and claims code need no knowledge of it.

* **Taxonomy edits** (``Category.related`` / ``Location.adjacent`` changed in
  the dashboard taxonomy pages, the admin or a shell) -> after the
  transaction commits, every pair whose category/zone relation may have
  changed is rescored (``services.recalculate_for_taxonomy``). Several m2m
  events inside one transaction (``set()`` issues a remove *and* an add) are
  coalesced into one recalculation.
* **Report changes** -> after the transaction commits, a report that
  stopped being eligible (withdrawn, rejected, returned, sent back to review,
  reserved by a claim rule in ``Item.objects.matchable()``) has its matches
  retired; a still eligible report whose matching-relevant content changed
  (e.g. an administrator edit of category, zone, dates, text, colour or
  photo) is rescored. Views that call ``clear_matches`` act immediately.
* **Claim changes** -> eligibility is re-checked for the claimed report, so a
  claim rule that reserves a report retires its matches, and a report that
  becomes eligible again is rescored.

Notifications triggered from here are exactly-once per pair
(``Match.notified_at``), so repeated or coalesced recalculations never spam.
"""
from __future__ import annotations

import logging
import threading
from functools import partial

from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_init, post_save
from django.utils import timezone

logger = logging.getLogger('lostlink.matching')

_MISSING = object()
TRACKED = ('report_type', 'reporter_id', 'title', 'description', 'category_id', 'location_id',
           'event_date', 'event_time', 'brand', 'color', 'image', 'status', 'is_active')
CONTENT = frozenset(TRACKED) - {'status', 'is_active'}
ELIGIBILITY_REASONS = ('withdrawn', 'rejected', 'returned', 'pending_review', 'ineligible',
                       'candidate_ineligible')

_state = threading.local()


def _snapshot(instance) -> dict:
    data = instance.__dict__
    snap = {}
    for name in TRACKED:
        value = data.get(name, _MISSING)  # never trigger a deferred-field query
        if name == 'image' and value is not _MISSING:
            value = str(getattr(value, 'name', value) or '')
        snap[name] = value
    return snap


def item_initialised(sender, instance, **kwargs):
    instance._matching_snapshot = _snapshot(instance)


def item_saved(sender, instance, created, update_fields=None, raw=False, **kwargs):
    before = getattr(instance, '_matching_snapshot', None)
    instance._matching_snapshot = _snapshot(instance)
    if created or raw or before is None:
        return
    after = instance._matching_snapshot
    names = TRACKED if update_fields is None else [n for n in TRACKED if n in update_fields
                                                   or n.removesuffix('_id') in update_fields]
    changed = {n for n in names
               if before.get(n, _MISSING) is not _MISSING and before[n] != after[n]}
    if not changed:
        return
    # Reconcile after the business transaction commits, so code later in the
    # same transaction (e.g. a handover looking up the pair it resolves)
    # still sees the matches it is acting on.
    transaction.on_commit(partial(reconcile_report, instance.pk,
                                  content_changed=bool(changed & CONTENT), since=timezone.now()))


def reconcile_report(pk, content_changed=False, since=None):
    """Bring report ``pk``'s matches in line with its current state.

    * not eligible any more -> retire its active matches (with the reason);
    * eligible and its matching-relevant content changed -> rescore;
    * eligible again after a state change -> revive (rescore) its retired
      matches, unless something already rescored it after ``since`` (e.g. the
      approval view ran matching itself) or there is nothing to revive.
    """
    from items.models import Item

    from . import services
    from .models import Match
    item = Item.objects.filter(pk=pk).first()
    if item is None:
        return
    try:
        if not services.is_match_eligible(item):
            reason = services.retirement_reason(item)
            retired = services.retire_matches(item, reason)
            if retired:
                logger.info('Retired %s match(es) of report %s (%s)', retired, pk, reason)
            return
        involving = services._involving(item)
        if not content_changed:
            if since is not None and Match.all_objects.filter(involving, updated_at__gte=since).exists():
                return
            if not Match.all_objects.filter(involving, retired_reason__in=ELIGIBILITY_REASONS).exists():
                return
        services.run_matching(item, notify=True)
    except Exception:  # never break the request that triggered the reconciliation
        logger.exception('Automatic match reconciliation failed for report %s', pk)


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------
def claim_changed(sender, instance, **kwargs):
    item_id = getattr(instance, 'item_id', None)
    if item_id:
        transaction.on_commit(partial(reconcile_report, item_id, since=timezone.now()))


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
def _pending():
    pending = getattr(_state, 'taxonomy', None)
    if pending is None:
        pending = _state.taxonomy = {'category': set(), 'location': set()}
    return pending


def _flush_taxonomy():
    pending = getattr(_state, 'taxonomy', None)
    _state.taxonomy = None
    if not pending or not (pending['category'] or pending['location']):
        return
    from . import services
    try:
        services.recalculate_for_taxonomy(pending['category'], pending['location'])
    except Exception:
        logger.exception('Recalculation after a taxonomy change failed; run rebuild_matches')


def _taxonomy_changed(kind, instance, action, pk_set, reverse=False, **kwargs):
    accessor = 'related' if kind == 'category' else 'adjacent'
    if action == 'pre_clear':
        instance._matching_cleared = set(getattr(instance, accessor).values_list('pk', flat=True))
        return
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return
    affected = {instance.pk}
    affected |= set(pk_set or ())
    if action == 'post_clear':
        affected |= getattr(instance, '_matching_cleared', set())
    _pending()[kind].update(affected)
    # One callback per event; the first to run flushes everything, the rest
    # find nothing pending. Rolled-back events are flushed harmlessly later.
    transaction.on_commit(_flush_taxonomy)


def connect():
    from claims.models import Claim
    from items.models import Category, Item, Location

    post_init.connect(item_initialised, sender=Item, dispatch_uid='matching_item_init')
    post_save.connect(item_saved, sender=Item, dispatch_uid='matching_item_saved')
    post_save.connect(claim_changed, sender=Claim, dispatch_uid='matching_claim_saved')
    post_delete.connect(claim_changed, sender=Claim, dispatch_uid='matching_claim_deleted')
    m2m_changed.connect(partial(_taxonomy_changed, 'category'), sender=Category.related.through,
                        dispatch_uid='matching_category_related', weak=False)
    m2m_changed.connect(partial(_taxonomy_changed, 'location'), sender=Location.adjacent.through,
                        dispatch_uid='matching_location_adjacent', weak=False)
