"""Central report lifecycle: the only place that changes ``Item.status``.

    pending ──▶ approved ⇄ claimed ──▶ returned   (approved ──▶ returned when the
                                                  counterpart report is handed over)
       │  ╲        │  ╲       │
       │   ▶ rejected ─▶ pending (edited and resubmitted)
       ▼        ▼          ▼
    withdrawn (reporter)   removed (moderator)

Terminal states (returned, withdrawn, removed) also set ``is_active=False``
so the report leaves search, matching and claim intake. Every transition is
validated against ``TRANSITIONS``, applied to a row the caller has locked
(``select_for_update``) inside a transaction, recorded in the audit trail
with its previous and new state, and propagated to matching.
"""
from django.core.exceptions import PermissionDenied

from .models import Item

S = Item.Status

TRANSITIONS = {
    S.PENDING: {S.APPROVED, S.REJECTED, S.WITHDRAWN, S.REMOVED},
    # approved → returned: resolved through the counterpart report's handover.
    S.APPROVED: {S.CLAIMED, S.PENDING, S.WITHDRAWN, S.REMOVED, S.RETURNED},
    S.REJECTED: {S.PENDING, S.WITHDRAWN, S.REMOVED},
    S.CLAIMED: {S.APPROVED, S.RETURNED, S.REMOVED, S.PENDING},
    S.RETURNED: set(),
    S.WITHDRAWN: set(),
    S.REMOVED: set(),
}
TERMINAL = {S.RETURNED, S.WITHDRAWN, S.REMOVED}
MATCHING_STATES = {S.APPROVED, S.CLAIMED}


class InvalidTransition(PermissionDenied):
    """Raised for a status change the lifecycle does not allow."""


def can_transition(item, new_status):
    return new_status == item.status or new_status in TRANSITIONS.get(item.status, set())


def transition(item, new_status, *, actor=None, action=None, note='', metadata=None,
               rematch=True, audit=True):
    """Move ``item`` to ``new_status`` and apply the consequences.

    ``item`` must be a row locked by the caller. Returns the previous status.
    Leaving the matching states retires the report's matches; entering them
    re-runs matching (unless ``rematch`` is false, e.g. when the caller runs
    it itself after further changes).
    """
    from dashboard.models import ModerationDecision, record_decision
    from matching.services import clear_matches, run_matching

    previous = item.status
    if new_status != previous and new_status not in TRANSITIONS.get(previous, set()):
        raise InvalidTransition(f'A report cannot move from {item.get_status_display().lower()} '
                                f'to {Item.Status(new_status).label.lower()}.')
    if not item.is_active and previous in TERMINAL:
        raise InvalidTransition('This report is closed.')

    item.status = new_status
    fields = ['status', 'updated_at']
    if new_status in TERMINAL:
        item.is_active = False
        fields.append('is_active')
    item.save(update_fields=fields)
    if hasattr(item, '_reserved_claim_cache'):
        del item._reserved_claim_cache

    if new_status in TERMINAL:
        # A closed report needs no flag review.
        from django.utils import timezone
        item.flags.filter(resolved_at__isnull=True).update(resolved_at=timezone.now(),
                                                          resolved_by=actor)
    if previous in MATCHING_STATES and new_status not in MATCHING_STATES:
        clear_matches(item)
    elif rematch and new_status in MATCHING_STATES and previous not in MATCHING_STATES:
        run_matching(item)

    if audit and action:
        record_decision(actor, action, item=item, note=note,
                        from_state=previous, to_state=new_status, metadata=metadata)
    elif audit and new_status != previous:
        record_decision(actor, ModerationDecision.Action.REPORT_STATUS_CHANGED, item=item,
                        note=note, from_state=previous, to_state=new_status, metadata=metadata)
    return previous


def refresh_claim_state(item, *, actor=None, note=''):
    """Recompute approved ⇄ claimed from the item's claims after a claim changes.

    ``claimed`` means at least one open or reserved claim exists.
    """
    if item.status not in MATCHING_STATES or not item.is_active:
        return item.status
    from claims.models import Claim
    busy = item.claims.filter(status__in=Claim.OPEN_STATUSES + Claim.RESERVED_STATUSES).exists()
    wanted = S.CLAIMED if busy else S.APPROVED
    if wanted != item.status:
        transition(item, wanted, actor=actor, note=note, rematch=False, audit=False)
    return item.status
