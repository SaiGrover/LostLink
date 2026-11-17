"""Claim workflow rules, in one place.

Views stay thin: they authenticate, parse forms and call these functions
inside ``transaction.atomic`` after locking the item and the claim (always
in that order, see ``lock_claim``). Every consequential change records an
audit entry with its previous and new state and notifies the people
involved. Nothing here trusts the UI: hidden buttons are a convenience, the
checks below are the rule.

Errors that the user should see are raised as ``ClaimError`` *before* any
write. Operations that must persist something even when they fail (a wrong
handover code increments the attempt counter) return a result object
instead of raising, so the transaction is not rolled back.
"""
from __future__ import annotations

import io
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from dashboard.models import ModerationDecision, record_decision
from items import lifecycle
from items.models import Item
from notifications.utils import notify_user

from .models import Claim, ClaimDispute, ClaimEvidence, ClaimRevision, HandoverSession

A = ModerationDecision.Action
OPEN = list(Claim.OPEN_STATUSES)


def setting(name, default):
    return getattr(settings, name, default)


class ClaimError(PermissionDenied):
    """A rule violation with a message safe to show to the user."""


# ---------------------------------------------------------------------------
# Locking and eligibility
# ---------------------------------------------------------------------------
def lock_claim(claim_pk):
    """Lock the claim's item, then the claim (consistent order avoids deadlocks)."""
    item_id = Claim.objects.values_list('item_id', flat=True).get(pk=claim_pk)
    item = Item.objects.select_for_update().get(pk=item_id)
    claim = Claim.objects.select_for_update().select_related('claimant', 'item__reporter').get(pk=claim_pk)
    claim.item = item
    return claim, item


def _is_party(claim, user):
    return user.id in (claim.claimant_id, claim.item.reporter_id)


def _require_independent_moderator(claim, user, what='decide this claim'):
    if not getattr(user, 'is_staff_role', False):
        raise ClaimError('Only a moderator can do this.')
    if user.id == claim.claimant_id:
        raise ClaimError(f'You submitted this claim, so another moderator has to {what}.')
    if user.id == claim.item.reporter_id:
        raise ClaimError(f'You reported this item, so another moderator has to {what}.')


def claim_eligibility(item, user):
    """What ``user`` may do about claiming ``item``, for buttons *and* views.

    Returns a dict: ``can_claim`` (start a new claim), ``state`` (a short
    machine key) and ``reason`` (shown when the action is unavailable), plus
    ``existing`` (the user's own claim thread, if any).
    """
    existing = None
    if user.is_authenticated:
        existing = item.claims.filter(claimant=user).first()
    def result(can, state, reason=''):
        return {'can_claim': can, 'state': state, 'reason': reason, 'existing': existing}

    if not item.is_active or item.status not in (Item.Status.APPROVED, Item.Status.CLAIMED):
        return result(False, 'closed', 'This report is no longer accepting claims.')
    if user.is_authenticated and item.reporter_id == user.id:
        return result(False, 'own', 'You reported this item.')
    reserved = item.reserved_claim
    if reserved is not None:
        if existing and existing.pk == reserved.pk:
            return result(False, 'reserved_for_you', 'Your claim is approved — the handover is pending.')
        return result(False, 'handover_pending',
                      'Ownership has already been verified for someone else; the handover is pending.')
    if existing is not None:
        if existing.status == Claim.Status.REJECTED and can_appeal(existing)[0]:
            return result(False, 'appealable', 'Your claim was rejected. You can request reconsideration.')
        return result(False, 'existing', 'You already have a claim on this item.')
    return result(True, 'open')


def refresh_item(item, actor=None):
    if hasattr(item, '_reserved_claim_cache'):
        del item._reserved_claim_cache
    return lifecycle.refresh_claim_state(item, actor=actor)


def _cancel_codes(claim, status=HandoverSession.Status.CANCELLED):
    return (claim.handover_sessions.filter(status=HandoverSession.Status.ACTIVE)
            .update(status=status))


# ---------------------------------------------------------------------------
# Starting, deciding, withdrawing
# ---------------------------------------------------------------------------
def start_claim(item, user, *, statement, contact_phone='', contact_email='', match=None):
    """Create a claim on a locked ``item`` after re-checking eligibility."""
    elig = claim_eligibility(item, user)
    if not elig['can_claim']:
        raise ClaimError(elig['reason'])
    try:
        with transaction.atomic():
            claim = Claim.objects.create(
                item=item, claimant=user, match=match, statement=statement,
                contact_phone=contact_phone or getattr(user, 'phone', ''),
                contact_email=contact_email or user.email)
    except IntegrityError:
        raise ClaimError('You already have a claim on this item.')
    ClaimRevision.objects.create(claim=claim, number=1, kind=ClaimRevision.Kind.ORIGINAL,
                                 statement=statement, created_by=user)
    refresh_item(item, actor=user)
    verb = 'claimed' if item.is_found else 'offered to return'
    notify_user(item.reporter, kind='claim', title=f'Someone {verb} “{item.title}”',
                body=f'{user.display_name()} responded. Ask private verification questions before '
                     'anything changes hands.',
                url=claim.get_absolute_url(), item=item)
    return claim


APPROVAL_BLOCKERS = {
    'check_statement': {'inconsistent'},
    'check_private': {'mismatch'},
    'check_answers': {'incorrect'},
    'check_evidence': {'contradicts'},
}
POSITIVE_EVIDENCE = {
    'check_private': {'matches', 'partial'},
    'check_answers': {'correct', 'partial'},
    'check_evidence': {'supports'},
}


def checklist_problems(checklist):
    """Why an approval checklist does not support approval (empty if it does)."""
    problems = []
    for key, bad in APPROVAL_BLOCKERS.items():
        if checklist.get(key) in bad:
            problems.append(f'{key.replace("check_", "").replace("_", " ").title()} contradicts the claim.')
    if not any(checklist.get(k) in good for k, good in POSITIVE_EVIDENCE.items()):
        problems.append('At least one independent check (private details, answers or evidence) '
                        'must support the claim.')
    return problems


def approve_claim(claim, item, actor, *, note, checklist):
    _require_independent_moderator(claim, actor)
    if not claim.is_open:
        raise ClaimError('This claim has already been decided.')
    if claim.has_open_dispute:
        raise ClaimError('Resolve the open dispute on this claim before deciding it.')
    if not item.is_active or item.status not in (Item.Status.APPROVED, Item.Status.CLAIMED):
        raise ClaimError('This report is no longer available for claims.')
    if not claim.all_answered:
        raise ClaimError('Complete the private verification questions before approval.')
    problems = checklist_problems(checklist)
    if problems:
        raise ClaimError(' '.join(problems))
    if item.claims.filter(status__in=Claim.VERIFIED_STATUSES).exclude(pk=claim.pk).exists():
        raise ClaimError('Another claim on this item has already been verified.')

    previous = claim.status
    claim.status = Claim.Status.APPROVED
    claim.reviewed_by = actor
    claim.decision_note = note[:280]
    try:
        with transaction.atomic():
            claim.save()
    except IntegrityError:
        raise ClaimError('Another claim on this item was verified at the same time.')
    record_decision(actor, A.CLAIM_APPROVED, item=item, claim=claim, note=note,
                    from_state=previous, to_state=claim.status, metadata={'checklist': checklist})

    for other in item.claims.exclude(pk=claim.pk).filter(status__in=OPEN).select_related('claimant'):
        other_prev = other.status
        other.status = Claim.Status.REJECTED
        other.reviewed_by = actor
        other.decision_note = ('Ownership was verified for another claimant. If that decision changes '
                               'you can request reconsideration.')
        other.save(update_fields=['status', 'reviewed_by', 'decision_note', 'updated_at'])
        _cancel_codes(other)
        record_decision(actor, A.CLAIM_REJECTED, item=item, claim=other, note=other.decision_note,
                        from_state=other_prev, to_state=other.status)
        notify_user(other.claimant, kind='claim', title=f'Claim closed — “{item.title}”',
                    body=other.decision_note, url=other.get_absolute_url(), item=item)

    refresh_item(item, actor=actor)
    # Reserved: no new claims and no new match alerts for this report.
    from matching.services import clear_matches
    clear_matches(item)

    recipient, provider = claim.recipient, claim.provider
    notify_user(recipient, kind='claim', title=f'Approved — handover pending for “{item.title}”',
                body='Contact details are shared. When you meet, create your one-time handover code '
                     'on the claim page and share it only after you have received and checked the item.',
                url=claim.get_absolute_url(), item=item)
    notify_user(provider, kind='claim', title=f'Approved — handover pending for “{item.title}”',
                body='Contact details are shared. After you hand the item over, ask the owner for their '
                     'one-time code and enter it on the claim page.',
                url=claim.get_absolute_url(), item=item)
    return claim


def reject_claim(claim, item, actor, *, note, checklist=None):
    _require_independent_moderator(claim, actor)
    if not claim.is_open:
        raise ClaimError('This claim has already been decided.')
    if not note.strip():
        raise ClaimError('Give the claimant a reason for the rejection.')
    previous = claim.status
    claim.status = Claim.Status.REJECTED
    claim.reviewed_by = actor
    claim.decision_note = note[:280]
    claim.save(update_fields=['status', 'reviewed_by', 'decision_note', 'updated_at'])
    record_decision(actor, A.CLAIM_REJECTED, item=item, claim=claim, note=note,
                    from_state=previous, to_state=claim.status, metadata={'checklist': checklist or {}})
    dispute = claim.disputes.filter(status=ClaimDispute.Status.OPEN).first()
    if dispute is not None:
        # Rejecting the claim settles a concern raised about it.
        dispute.status = ClaimDispute.Status.RESOLVED
        dispute.outcome = ClaimDispute.Outcome.REJECTED
        dispute.resolved_by = actor
        dispute.resolution_note = note[:500]
        dispute.resolved_at = timezone.now()
        dispute.save()
        record_decision(actor, A.DISPUTE_RESOLVED, item=item, claim=claim, note=note,
                        metadata={'dispute': dispute.pk, 'outcome': dispute.outcome})
    refresh_item(item, actor=actor)
    notify_user(claim.claimant, kind='claim', title=f'Claim rejected — “{item.title}”',
                body=note[:280], url=claim.get_absolute_url(), item=item)
    return claim


def withdraw_claim(claim, item, user, *, note=''):
    if user.id != claim.claimant_id:
        raise ClaimError('Only the claimant can withdraw this claim.')
    if claim.status not in OPEN + [Claim.Status.APPROVED]:
        raise ClaimError('This claim can no longer be withdrawn.')
    was_reserved = claim.status == Claim.Status.APPROVED
    previous = claim.status
    claim.status = Claim.Status.WITHDRAWN
    claim.save(update_fields=['status', 'updated_at'])
    _cancel_codes(claim)
    claim.disputes.filter(status=ClaimDispute.Status.OPEN).update(
        status=ClaimDispute.Status.RESOLVED, outcome=ClaimDispute.Outcome.DISMISSED,
        resolution_note='Closed because the claimant withdrew the claim.', resolved_at=timezone.now())
    record_decision(user, A.CLAIM_WITHDRAWN, item=item, claim=claim, note=note,
                    from_state=previous, to_state=claim.status)
    refresh_item(item, actor=user)
    if was_reserved and item.is_matchable:
        from matching.services import run_matching
        run_matching(item)
    notify_user(item.reporter, kind='claim', title=f'Claim withdrawn — “{item.title}”',
                body='The person who responded withdrew their claim.' +
                     (' The item is available to other claimants again.' if was_reserved else ''),
                url=item.get_absolute_url(), item=item)
    return claim


def close_claims(item, actor, note, statuses=None):
    """Close every unfinished claim on ``item`` (the report left circulation).

    Includes a reserved (approved) claim: its handover codes are cancelled.
    Returns the number of claims closed.
    """
    statuses = statuses or OPEN + [Claim.Status.APPROVED]
    closed = 0
    for claim in item.claims.filter(status__in=statuses).select_related('claimant'):
        previous = claim.status
        claim.status = Claim.Status.REJECTED
        claim.reviewed_by = actor
        claim.decision_note = note[:280]
        claim.save(update_fields=['status', 'reviewed_by', 'decision_note', 'updated_at'])
        _cancel_codes(claim)
        record_decision(actor, A.CLAIM_REJECTED, item=item, claim=claim, note=note,
                        from_state=previous, to_state=claim.status)
        notify_user(claim.claimant, kind='claim', title=f'Claim closed — “{item.title}”',
                    body=note, url=claim.get_absolute_url(), item=item)
        closed += 1
    return closed


# ---------------------------------------------------------------------------
# Reconsideration (appeal) after a rejection
# ---------------------------------------------------------------------------
def can_appeal(claim):
    """(allowed, reason) for a reconsideration request on a rejected claim."""
    if claim.status != Claim.Status.REJECTED:
        return False, 'Only a rejected claim can be reconsidered.'
    limit = setting('CLAIM_MAX_APPEALS', 2)
    if claim.appeal_count >= limit:
        return False, f'This claim has used all {limit} reconsideration requests.'
    item = claim.item
    if not item.is_active or item.status not in (Item.Status.APPROVED, Item.Status.CLAIMED):
        return False, 'The report is closed, so the claim cannot be reopened.'
    if item.claims.filter(status__in=Claim.VERIFIED_STATUSES).exclude(pk=claim.pk).exists():
        return False, ('Ownership is currently verified for another claimant. If a moderator revokes '
                       'that decision you will be able to request reconsideration.')
    return True, ''


def appeal_claim(claim, item, user, *, statement, additional_details='', files=()):
    from accounts.ratelimit import is_limited

    if user.id != claim.claimant_id:
        raise ClaimError('Only the claimant can request reconsideration.')
    allowed, reason = can_appeal(claim)
    if not allowed:
        raise ClaimError(reason)
    if is_limited(f'claim-appeal:{user.id}', limit=setting('CLAIM_APPEALS_PER_DAY', 3), window=86400):
        raise ClaimError('Too many reconsideration requests today. Try again tomorrow.')
    statement = statement.strip()
    if len(statement) < 20:
        raise ClaimError('Explain in a little more detail (at least 20 characters).')
    files = list(files)
    prepared = [prepare_evidence_file(f) for f in files]

    if not claim.revisions.exists():  # claims created before revision history existed
        ClaimRevision.objects.create(claim=claim, number=1, kind=ClaimRevision.Kind.ORIGINAL,
                                     statement=claim.statement, created_by=claim.claimant)
    number = (claim.revisions.order_by('-number').values_list('number', flat=True).first() or 0) + 1
    revision = ClaimRevision.objects.create(
        claim=claim, number=number, kind=ClaimRevision.Kind.APPEAL, statement=statement,
        additional_details=additional_details.strip()[:2000], responds_to_decision=claim.decision_note,
        created_by=user)
    for content in prepared:
        ClaimEvidence.objects.create(claim=claim, revision=revision, uploaded_by=user,
                                     file=content, note='Submitted with a reconsideration request')

    previous = claim.status
    claim.statement = statement
    claim.appeal_count += 1
    questions = list(claim.questions.all())
    if questions and all(q.answer for q in questions):
        claim.status = Claim.Status.ANSWERED
    elif questions:
        claim.status = Claim.Status.QUESTIONS
    else:
        claim.status = Claim.Status.SUBMITTED
    claim.reviewed_by = None
    claim.decision_note = ''
    claim.save(update_fields=['statement', 'appeal_count', 'status', 'reviewed_by', 'decision_note',
                              'updated_at'])
    record_decision(user, A.CLAIM_REOPENED, item=item, claim=claim,
                    note=f'Reconsideration request {claim.appeal_count}.',
                    from_state=previous, to_state=claim.status,
                    metadata={'revision': number, 'evidence_files': len(prepared)})
    refresh_item(item, actor=user)
    notify_user(item.reporter, kind='claim', title=f'Claim reopened — “{item.title}”',
                body='The claimant asked for reconsideration with more information. A moderator will review it.',
                url=claim.get_absolute_url(), item=item)
    return claim


# ---------------------------------------------------------------------------
# Private evidence
# ---------------------------------------------------------------------------
EVIDENCE_TYPES = {'JPEG', 'PNG', 'WEBP'}


def prepare_evidence_file(upload):
    """Validate an uploaded evidence photo and re-encode it as a clean JPEG.

    Re-encoding drops EXIF/GPS and any appended payload; the original bytes
    are never stored.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    max_bytes = setting('CLAIM_EVIDENCE_MAX_BYTES', 5 * 1024 * 1024)
    if upload.size > max_bytes:
        raise ClaimError(f'Each evidence photo must be under {max_bytes // (1024 * 1024)} MB.')
    try:
        image = Image.open(upload)
        image.verify()
        upload.seek(0)
        image = Image.open(upload)
        if image.format not in EVIDENCE_TYPES:
            raise ClaimError('Evidence must be a JPEG, PNG or WebP photo.')
        if image.width * image.height > setting('CLAIM_EVIDENCE_MAX_PIXELS', 40_000_000):
            raise ClaimError('That photo is too large. Please upload a smaller one.')
        image = ImageOps.exif_transpose(image).convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ClaimError('That file is not a readable photo.')
    image.thumbnail((2400, 2400))
    out = io.BytesIO()
    image.save(out, format='JPEG', quality=85)
    return ContentFile(out.getvalue(), name='evidence.jpg')


def add_evidence(claim, item, user, *, files, note=''):
    if user.id != claim.claimant_id:
        raise ClaimError('Only the claimant can add ownership evidence.')
    if not claim.is_open:
        raise ClaimError('Evidence can be added while the claim is under review.')
    files = list(files)
    if not files:
        raise ClaimError('Choose a photo to upload.')
    limit = setting('CLAIM_EVIDENCE_MAX_FILES', 5)
    if claim.evidence.count() + len(files) > limit:
        raise ClaimError(f'A claim can hold at most {limit} evidence photos.')
    prepared = [prepare_evidence_file(f) for f in files]
    for content in prepared:
        ClaimEvidence.objects.create(claim=claim, uploaded_by=user, file=content, note=note[:280])
    record_decision(user, A.EVIDENCE_ADDED, item=item, claim=claim,
                    note=f'{len(prepared)} evidence photo(s) added.', metadata={'count': len(prepared)})
    return len(prepared)


def can_view_evidence(claim, user):
    """Evidence is private to the claimant and moderators."""
    return user.is_authenticated and (user.id == claim.claimant_id or getattr(user, 'is_staff_role', False))


def purge_expired_evidence(now=None):
    """Delete evidence files of claims finalised longer ago than the retention period."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=setting('CLAIM_EVIDENCE_RETENTION_DAYS', 180))
    purged = 0
    qs = (ClaimEvidence.objects.filter(purged_at__isnull=True, claim__updated_at__lt=cutoff,
                                       claim__status__in=['rejected', 'returned', 'withdrawn'])
          .exclude(file=''))
    for evidence in qs:
        evidence.file.delete(save=False)
        evidence.purged_at = now
        evidence.save(update_fields=['file', 'purged_at'])
        purged += 1
    return purged


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------
def raise_dispute(claim, item, user, *, reason):
    if not (_is_party(claim, user) or getattr(user, 'is_staff_role', False)):
        raise ClaimError('You cannot dispute this claim.')
    if claim.status in (Claim.Status.RETURNED, Claim.Status.WITHDRAWN):
        raise ClaimError('This claim is closed. Contact a moderator about a completed handover.')
    if claim.status == Claim.Status.REJECTED:
        raise ClaimError('This claim is rejected. The claimant can request reconsideration instead.')
    reason = reason.strip()
    if not reason:
        raise ClaimError('Explain the concern so a moderator can review it.')
    if claim.has_open_dispute:
        raise ClaimError('A dispute on this claim is already under review.')
    try:
        with transaction.atomic():
            dispute = ClaimDispute.objects.create(claim=claim, raised_by=user, reason=reason[:500])
    except IntegrityError:
        raise ClaimError('A dispute on this claim is already under review.')
    suspended = _cancel_codes(claim, status=HandoverSession.Status.SUSPENDED)
    record_decision(user, A.DISPUTE_RAISED, item=item, claim=claim, note=reason,
                    from_state=claim.status, to_state=claim.status,
                    metadata={'dispute': dispute.pk, 'handover_codes_suspended': suspended})
    if claim.status == Claim.Status.APPROVED:
        record_decision(user, A.HANDOVER_SUSPENDED, item=item, claim=claim,
                        note='Handover paused while the dispute is reviewed.')
    for other in {claim.claimant, item.reporter} - {user}:
        notify_user(other, kind='claim', title=f'Dispute raised — “{item.title}”',
                    body='A concern was raised about this claim. A moderator will review it; '
                         'the handover is paused meanwhile.' if claim.status == Claim.Status.APPROVED
                    else 'A concern was raised about this claim. A moderator will review it.',
                    url=claim.get_absolute_url(), item=item)
    return dispute


def resolve_dispute(claim, item, actor, *, outcome, note):
    _require_independent_moderator(claim, actor, what='resolve this dispute')
    dispute = claim.disputes.select_for_update().filter(status=ClaimDispute.Status.OPEN).first()
    if dispute is None:
        raise ClaimError('There is no open dispute on this claim.')
    if dispute.raised_by_id == actor.id:
        raise ClaimError('You raised this dispute, so another moderator has to resolve it.')
    note = note.strip()
    if len(note) < 10:
        raise ClaimError('Record the reason for the resolution (at least 10 characters).')
    O = ClaimDispute.Outcome
    if outcome == O.REVOKED and claim.status != Claim.Status.APPROVED:
        raise ClaimError('Only an approved claim can have its approval revoked.')
    if outcome == O.REJECTED and not claim.is_open:
        raise ClaimError('Only a claim under review can be rejected here.')
    if outcome not in O.values:
        raise ClaimError('Unknown outcome.')

    dispute.status = ClaimDispute.Status.RESOLVED
    dispute.outcome = outcome
    dispute.resolved_by = actor
    dispute.resolution_note = note[:500]
    dispute.resolved_at = timezone.now()
    dispute.save()
    previous = claim.status
    # A code suspended by the dispute is never revived: the recipient issues a new one.
    claim.handover_sessions.filter(status=HandoverSession.Status.SUSPENDED).update(
        status=HandoverSession.Status.CANCELLED)

    if outcome in (O.REVOKED, O.REJECTED):
        claim.status = Claim.Status.REJECTED
        claim.reviewed_by = actor
        claim.decision_note = note[:280]
        claim.save(update_fields=['status', 'reviewed_by', 'decision_note', 'updated_at'])
        _cancel_codes(claim)
        record_decision(actor, A.CLAIM_REVOKED if outcome == O.REVOKED else A.CLAIM_REJECTED,
                        item=item, claim=claim, note=note, from_state=previous, to_state=claim.status,
                        metadata={'dispute': dispute.pk})
        refresh_item(item, actor=actor)
        if outcome == O.REVOKED and item.is_matchable:
            from matching.services import run_matching
            run_matching(item)
    record_decision(actor, A.DISPUTE_RESOLVED, item=item, claim=claim, note=note,
                    from_state=previous, to_state=claim.status,
                    metadata={'dispute': dispute.pk, 'outcome': outcome})
    body = {
        O.DISMISSED: 'A moderator reviewed the concern and the claim continues.'
                     + (' Create a new handover code when you meet.' if claim.status == 'approved' else ''),
        O.REVOKED: 'A moderator revoked the approval after reviewing the dispute. The item is no longer '
                   'reserved for this claimant.',
        O.REJECTED: 'A moderator rejected the claim after reviewing the dispute.',
    }[outcome]
    for user in (claim.claimant, item.reporter):
        notify_user(user, kind='claim', title=f'Dispute resolved — “{item.title}”',
                    body=body, url=claim.get_absolute_url(), item=item)
    return dispute


# ---------------------------------------------------------------------------
# Handover with a recipient-held one-time code
# ---------------------------------------------------------------------------
def _hash_code(salt, code):
    return salted_hmac('lostlink.claims.handover', f'{salt}:{code}', algorithm='sha256').hexdigest()


def handover_blocker(claim, item):
    """Why the handover cannot proceed right now (``''`` if it can)."""
    if claim.status != Claim.Status.APPROVED:
        return 'The claim must be approved before the handover.'
    if claim.has_open_dispute:
        return 'The handover is paused while a moderator reviews a dispute.'
    if not item.is_active:
        return 'This report is closed.'
    return ''


def active_session(claim):
    session = claim.handover_sessions.filter(status=HandoverSession.Status.ACTIVE).first()
    if session and session.is_expired:
        session.status = HandoverSession.Status.EXPIRED
        session.save(update_fields=['status'])
        return None
    return session


def issue_handover_code(claim, item, user):
    """Create a fresh code for the recipient. Returns ``(session, code)``.

    The plaintext code exists only in this return value (shown once on the
    recipient's screen); the database stores a salted HMAC.
    """
    from accounts.ratelimit import is_limited

    if user.id != claim.recipient.id:
        raise ClaimError('Only the person receiving the item can create the handover code.')
    blocker = handover_blocker(claim, item)
    if blocker:
        raise ClaimError(blocker)
    if is_limited(f'handover-issue:{claim.pk}', limit=setting('HANDOVER_CODES_PER_HOUR', 5), window=3600):
        raise ClaimError('Too many codes were created in the last hour. Try again later.')
    now = timezone.now()
    claim.handover_sessions.filter(status=HandoverSession.Status.ACTIVE).update(
        status=HandoverSession.Status.SUPERSEDED)
    generation = claim.handover_sessions.count() + 1
    code = f'{secrets.randbelow(10 ** 6):06d}'
    salt = secrets.token_hex(16)
    session = HandoverSession.objects.create(
        claim=claim, recipient=claim.recipient, provider=claim.provider, salt=salt,
        otp_hash=_hash_code(salt, code), generation_number=generation,
        expires_at=now + timedelta(minutes=setting('HANDOVER_CODE_TTL_MINUTES', 10)))
    record_decision(user, A.HANDOVER_CODE_ISSUED, item=item, claim=claim,
                    note=f'Handover code #{generation} created.',
                    metadata={'session': session.pk, 'generation': generation,
                              'expires_at': session.expires_at.isoformat()})
    notify_user(claim.provider, kind='claim', title=f'Handover code ready — “{item.title}”',
                body='The owner has a one-time code. Hand the item over, then ask them for the code '
                     'and enter it on the claim page.',
                url=claim.get_absolute_url(), item=item, dedupe=True)
    return session, code


@dataclass
class VerifyResult:
    ok: bool
    message: str


def verify_handover_code(claim, item, user, code):
    """Check a code entered by the person handing the item over.

    Returns a ``VerifyResult`` (never raises after writing, so failed
    attempts are persisted). Raises ``ClaimError`` only for checks made
    before any write.
    """
    from accounts.ratelimit import is_limited

    if user.id != claim.provider.id:
        raise ClaimError('Only the person handing the item over can enter the code.')
    if is_limited(f'handover-verify:{user.id}', limit=setting('HANDOVER_VERIFY_PER_10_MIN', 10), window=600):
        raise ClaimError('Too many attempts. Wait a few minutes and try again.')
    blocker = handover_blocker(claim, item)
    if blocker:
        raise ClaimError(blocker)
    session = (claim.handover_sessions.select_for_update()
               .filter(status=HandoverSession.Status.ACTIVE).first())
    if session is None:
        raise ClaimError('There is no active code. Ask the owner to create one on their claim page.')
    now = timezone.now()
    if session.is_expired:
        session.status = HandoverSession.Status.EXPIRED
        session.save(update_fields=['status'])
        return VerifyResult(False, 'That code has expired. Ask the owner to create a new one.')
    code = (code or '').strip().replace(' ', '')
    if not (len(code) == 6 and code.isdigit()) or not constant_time_compare(
            _hash_code(session.salt, code), session.otp_hash):
        session.failed_attempts += 1
        session.last_attempt_at = now
        max_attempts = setting('HANDOVER_CODE_MAX_ATTEMPTS', 5)
        locked = session.failed_attempts >= max_attempts
        if locked:
            session.status = HandoverSession.Status.LOCKED
        session.save(update_fields=['failed_attempts', 'last_attempt_at', 'status'])
        record_decision(user, A.HANDOVER_CODE_FAILED, item=item, claim=claim,
                        note='Incorrect handover code entered.',
                        metadata={'session': session.pk, 'attempts': session.failed_attempts,
                                  'locked': locked})
        if locked:
            notify_user(claim.recipient, kind='claim', title=f'Handover code locked — “{item.title}”',
                        body='Too many incorrect attempts. Create a new code when you are with the other person.',
                        url=claim.get_absolute_url(), item=item)
            return VerifyResult(False, 'Too many incorrect attempts. The code is locked; the owner '
                                       'has to create a new one.')
        left = max_attempts - session.failed_attempts
        return VerifyResult(False, f'That code is not correct. {left} attempt{"s" if left != 1 else ""} left.')

    session.status = HandoverSession.Status.VERIFIED
    session.verified_at = now
    session.last_attempt_at = now
    session.save(update_fields=['status', 'verified_at', 'last_attempt_at'])
    complete_handover(claim, item, user, method=Claim.HandoverMethod.OTP,
                      note='Handover confirmed with the owner\'s one-time code.',
                      metadata={'session': session.pk, 'generation': session.generation_number})
    return VerifyResult(True, 'Handover confirmed. The item is recorded as returned.')


EXCEPTION_REASONS = {
    'desk_collection': 'Collected at the campus lost-and-found desk with ID check',
    'code_unavailable': 'Both parties present; the code could not be used',
    'party_unavailable': 'Owner confirmed receipt but cannot use their account',
}


def exception_handover(claim, item, actor, *, reason, note):
    """Documented moderator completion when the code flow cannot be used."""
    _require_independent_moderator(claim, actor, what='record this handover')
    blocker = handover_blocker(claim, item)
    if blocker:
        raise ClaimError(blocker)
    if reason not in EXCEPTION_REASONS:
        raise ClaimError('Choose why the normal handover code could not be used.')
    note = note.strip()
    if len(note) < 20:
        raise ClaimError('Document the evidence for this exception (at least 20 characters): who was '
                         'present, where, and how ownership and receipt were confirmed.')
    _cancel_codes(claim)
    complete_handover(claim, item, actor, method=Claim.HandoverMethod.EXCEPTION, note=note,
                      metadata={'reason': reason, 'reason_text': EXCEPTION_REASONS[reason]})


def complete_handover(claim, item, actor, *, method, note, metadata=None):
    """Mark the claim returned and close the report (and the claimant's own
    counterpart report). Caller holds the locks and has verified authority."""
    from matching.services import clear_matches

    # Identify the claimant's own report while the linking match still exists
    # (closing the item below retires its matches).
    own = counterpart_report(claim)
    previous = claim.status
    claim.status = Claim.Status.RETURNED
    claim.handover_method = method
    claim.returned_at = timezone.now()
    if not claim.reviewed_by_id:
        claim.reviewed_by = actor
    claim.save(update_fields=['status', 'handover_method', 'returned_at', 'reviewed_by', 'updated_at'])
    action = A.HANDOVER_CONFIRMED if method == Claim.HandoverMethod.OTP else A.HANDOVER_EXCEPTION
    record_decision(actor, action, item=item, claim=claim, note=note,
                    from_state=previous, to_state=claim.status, metadata=metadata or {})
    lifecycle.transition(item, Item.Status.RETURNED, actor=actor, note=f'Claim #{claim.pk} returned.',
                         audit=True, action=A.REPORT_STATUS_CHANGED)
    close_claims(item, actor, 'This item has been returned to its owner, so this claim is closed.')

    if own is not None and own.is_active and own.status in lifecycle.MATCHING_STATES:
        own = Item.objects.select_for_update().get(pk=own.pk)
        lifecycle.transition(own, Item.Status.RETURNED, actor=actor, action=A.REPORT_STATUS_CHANGED,
                             note=f'Resolved together with report #{item.pk}.')
        clear_matches(own)
        close_claims(own, actor, 'This item has been returned through another report, so this claim is closed.')
    for user in (claim.claimant, item.reporter):
        notify_user(user, kind='claim', title=f'Handover completed — “{item.title}”',
                    body='The item is recorded as returned. Thank you for using LostLink.',
                    url=claim.get_absolute_url(), item=item)


def counterpart_report(claim):
    """The claimant's own report of the same item, if it can be identified.

    A claim started from a match names it directly; otherwise the claimant's
    report is used only when exactly one current match links it to this item,
    so an unrelated report is never closed by guesswork.
    """
    from matching.models import Match

    item = claim.item
    if claim.match_id:
        match = claim.match
    else:
        # Retired rows count too: approving the claim retires the item's
        # matches, but they still identify the claimant's own report.
        if item.is_found:
            candidates = Match.all_objects.filter(found_item=item, lost_item__reporter=claim.claimant)
        else:
            candidates = Match.all_objects.filter(lost_item=item, found_item__reporter=claim.claimant)
        candidates = list(candidates.filter(engine_version=settings.MATCHING['VERSION'])[:2])
        match = candidates[0] if len(candidates) == 1 else None
    if match is None:
        return None
    own = match.lost_item if item.is_found else match.found_item
    if own.reporter_id != claim.claimant_id or own.pk == item.pk:
        return None
    return own
