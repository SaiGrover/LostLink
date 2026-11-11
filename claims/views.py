"""Claim verification and recovery workflow (synopsis Section 4.12).

    Potential match -> Claim submitted -> Verification questions -> Moderator
    review -> Approved / Rejected -> Physical handover -> Returned

Two use cases share the model but are kept distinct in copy and permissions:

* a **found** report is *claimed* by its owner ("this is mine");
* a **lost** report receives a *return offer* from a finder ("I found this").

AI never approves anything here. Scores are shown to moderators as context
only; every state change below is a human action with an audit record.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import moderator_required
from dashboard.models import ModerationDecision, record_decision
from items.models import Item
from matching.models import Match
from matching.services import clear_matches
from notifications.utils import notify_user

from .forms import AnswerForm, ClaimForm, DecisionForm, QuestionForm
from .models import Claim, VerificationQuestion

OPEN = list(Claim.OPEN_STATUSES)


def close_claims(item, actor, note, statuses=None):
    """Reject every unfinished claim on ``item`` and tell each claimant why.

    Used when a report leaves the platform (removed by a moderator) or is
    resolved another way, so no claim is left open on a report nobody can act
    on any more. Returns the number of claims closed.
    """
    statuses = statuses or OPEN + [Claim.Status.APPROVED]
    closed = 0
    for claim in item.claims.filter(status__in=statuses).select_related('claimant'):
        claim.status = Claim.Status.REJECTED
        claim.reviewed_by = actor
        claim.decision_note = note[:280]
        claim.save(update_fields=['status', 'reviewed_by', 'decision_note', 'updated_at'])
        record_decision(actor, ModerationDecision.Action.CLAIM_REJECTED, item=item, claim=claim, note=note)
        notify_user(claim.claimant, kind='claim', title=f'Claim closed — “{item.title}”',
                    body=note, url=claim.get_absolute_url(), item=item)
        closed += 1
    return closed


def _claimants_own_report(claim):
    """The claimant's own report of the same item, if it can be identified.

    A claim started from a match names it directly; otherwise the claimant's
    report is used only when exactly one current match links it to this item,
    so an unrelated report is never closed by guesswork.
    """
    item = claim.item
    if claim.match_id:
        match = claim.match
    else:
        if item.is_found:
            candidates = Match.objects.filter(found_item=item, lost_item__reporter=claim.claimant,
                                              engine_version=settings.MATCHING['VERSION'])
        else:
            candidates = Match.objects.filter(lost_item=item, found_item__reporter=claim.claimant,
                                              engine_version=settings.MATCHING['VERSION'])
        candidates = list(candidates[:2])
        match = candidates[0] if len(candidates) == 1 else None
    if match is None:
        return None
    own = match.lost_item if item.is_found else match.found_item
    if own.reporter_id != claim.claimant_id or own.pk == item.pk:
        return None
    return own


@login_required
@transaction.atomic
def start_claim(request, item_id):
    item = get_object_or_404(Item.objects.select_for_update(), pk=item_id, is_active=True,
                             status__in=[Item.Status.APPROVED, Item.Status.CLAIMED])
    if item.reporter_id == request.user.id:
        messages.warning(request, 'You cannot respond to an item you reported.')
        return redirect(item.get_absolute_url())

    existing = item.claims.filter(claimant=request.user).first()
    if existing:
        return redirect(existing.get_absolute_url())
    if item.claims.filter(status__in=['approved', 'returned']).exists():
        raise PermissionDenied('This item already has a verified claimant.')

    is_found = item.is_found

    match_id = request.GET.get('match')
    if match_id and not match_id.isdecimal():
        raise PermissionDenied('Invalid match reference.')
    match = get_object_or_404(Match, pk=match_id) if match_id else None
    if match and not ((match.found_item_id == item.id and match.lost_item.reporter_id == request.user.id)
                      or (match.lost_item_id == item.id and match.found_item.reporter_id == request.user.id)):
        raise PermissionDenied('This match does not belong to your report.')

    if request.method == 'POST':
        form = ClaimForm(request.POST, found=is_found)
        if form.is_valid():
            claim = form.save(commit=False)
            claim.item = item
            claim.claimant = request.user
            claim.match = match
            if not claim.contact_email:
                claim.contact_email = request.user.email
            if not claim.contact_phone:
                claim.contact_phone = request.user.phone
            claim.save()

            if item.status == Item.Status.APPROVED:
                item.status = Item.Status.CLAIMED
                item.save(update_fields=['status'])

            verb = 'claimed' if is_found else 'offered to return'
            notify_user(item.reporter, kind='claim',
                        title=f'Someone {verb} “{item.title}”',
                        body=f'{request.user.display_name()} responded. Ask verification questions '
                             'to confirm before anything changes hands.',
                        url=claim.get_absolute_url(), item=item)
            messages.success(request, 'Submitted. Verification questions will follow before any decision.')
            return redirect(claim.get_absolute_url())
    else:
        form = ClaimForm(found=is_found,
                         initial={'contact_email': request.user.email,
                                  'contact_phone': request.user.phone})
    return render(request, 'claims/start_claim.html',
                  {'form': form, 'item': item, 'match': match, 'is_found': is_found})


@login_required
def claim_detail(request, pk):
    claim = get_object_or_404(
        Claim.objects.select_related('item', 'claimant', 'item__reporter', 'item__category',
                                     'item__location', 'reviewed_by'), pk=pk)
    user = request.user
    is_claimant = claim.claimant_id == user.id
    is_finder = claim.item.reporter_id == user.id
    can_moderate = user.is_staff_role
    if not (is_claimant or is_finder or can_moderate):
        raise PermissionDenied('You cannot view this claim.')

    questions = list(claim.questions.select_related('asked_by'))
    # Private answers are only visible to the claimant who wrote them, the
    # reporter who must verify them, and moderators. Nobody else reaches
    # this view, but the template still only renders answers for these roles.
    return render(request, 'claims/claim_detail.html', {
        'claim': claim,
        'questions': questions,
        'unanswered': sum(1 for q in questions if not q.answer),
        'is_claimant': is_claimant,
        'is_finder': is_finder,
        'can_moderate': can_moderate,
        'contacts_visible': claim.status in ('approved', 'returned'),
        'assessment': build_ai_assessment(claim) if (can_moderate or is_finder) else None,
        'question_form': QuestionForm(),
        'answer_form': AnswerForm(),
        'decision_form': DecisionForm(),
        'timeline': claim.timeline(),
        'decisions': claim.moderation_decisions.select_related('actor') if can_moderate else [],
    })


def build_ai_assessment(claim):
    """AI context for a moderator (synopsis Sections 4.11 and 8): the best
    stored match linking the claimant's own report to this item, plus a
    lightweight statement-vs-description similarity. Decision support only."""
    from matching.engine import text_similarity

    item = claim.item
    if item.is_found:
        match_qs = Match.objects.filter(found_item=item, lost_item__reporter=claim.claimant,
                                        engine_version=settings.MATCHING['VERSION'])
    else:
        match_qs = Match.objects.filter(lost_item=item, found_item__reporter=claim.claimant,
                                        engine_version=settings.MATCHING['VERSION'])
    match = claim.match if (claim.match and claim.match.engine_version == settings.MATCHING['VERSION']) \
        else match_qs.order_by('-score').first()
    statement_sim = text_similarity(claim.statement, [item.description])[0]
    return {
        'match': match,
        'statement_sim': round(statement_sim * 100),
        'statement_tone': ('success' if statement_sim >= 0.5
                           else 'warning' if statement_sim >= 0.25 else 'muted'),
    }


@login_required
@require_POST
def add_question(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    if not claim.is_open:
        raise PermissionDenied('This claim is closed.')
    if not (request.user.is_staff_role or claim.item.reporter_id == request.user.id):
        raise PermissionDenied('Only the reporter or a moderator can ask questions.')
    form = QuestionForm(request.POST)
    if form.is_valid():
        q = form.save(commit=False)
        q.claim = claim
        q.asked_by = request.user
        q.save()
        if claim.status in (Claim.Status.SUBMITTED, Claim.Status.ANSWERED):
            claim.status = Claim.Status.QUESTIONS
            claim.save(update_fields=['status', 'updated_at'])
        notify_user(claim.claimant, kind='claim',
                    title='Verification question added',
                    body=f'Please answer to verify “{claim.item.title}”.',
                    url=claim.get_absolute_url(), item=claim.item)
        messages.success(request, 'Verification question added.')
    else:
        messages.error(request, 'Write a question before adding it.')
    return redirect(claim.get_absolute_url())


@login_required
@require_POST
def answer_question(request, pk, qid):
    claim = get_object_or_404(Claim, pk=pk)
    if not claim.is_open:
        raise PermissionDenied('This claim is closed.')
    question = get_object_or_404(VerificationQuestion, pk=qid, claim=claim)
    if claim.claimant_id != request.user.id:
        raise PermissionDenied('Only the claimant can answer.')
    if question.answer:
        raise PermissionDenied('This question has already been answered.')
    form = AnswerForm(request.POST)
    if form.is_valid():
        question.answer = form.cleaned_data['answer']
        question.answered_at = timezone.now()
        question.save(update_fields=['answer', 'answered_at'])
        if claim.all_answered and claim.status != Claim.Status.DISPUTED:
            claim.status = Claim.Status.ANSWERED
            claim.save(update_fields=['status', 'updated_at'])
            notify_user(claim.item.reporter, kind='claim',
                        title='Verification answers submitted',
                        body=f'“{claim.item.title}” is ready for moderator review.',
                        url=claim.get_absolute_url(), item=claim.item)
        messages.success(request, 'Answer saved.')
    else:
        messages.error(request, 'Write an answer before submitting.')
    return redirect(claim.get_absolute_url())


@moderator_required
@require_POST
@transaction.atomic
def decide_claim(request, pk, decision):
    claim = get_object_or_404(Claim.objects.select_for_update(), pk=pk)
    item = Item.objects.select_for_update().get(pk=claim.item_id)
    if decision not in ('approve', 'reject'):
        raise PermissionDenied('Unknown decision.')
    if not claim.is_open:
        raise PermissionDenied('This claim has already been decided.')
    if not item.is_active or item.status not in ('approved', 'claimed'):
        raise PermissionDenied('This report is no longer available for claims.')
    if claim.claimant_id == request.user.id:
        raise PermissionDenied('You cannot decide your own claim.')
    if item.reporter_id == request.user.id:
        # The reporter is a party to the claim; an independent moderator decides.
        raise PermissionDenied('You reported this item, so another moderator has to decide the claim.')

    form = DecisionForm(request.POST)
    note = form.cleaned_data.get('note', '') if form.is_valid() else ''
    claim.reviewed_by = request.user
    claim.decision_note = note

    if decision == 'approve':
        if not claim.all_answered:
            messages.error(request, 'Complete the private verification questions before approval.')
            return redirect(claim.get_absolute_url())
        if item.claims.filter(status__in=['approved', 'returned']).exclude(pk=claim.pk).exists():
            raise PermissionDenied('Another claim on this item has already been verified.')
        claim.status = Claim.Status.APPROVED
        try:
            with transaction.atomic():
                claim.save()
        except IntegrityError:
            raise PermissionDenied('Another claim on this item was verified concurrently.')
        item.status = Item.Status.CLAIMED
        item.is_active = True
        item.save(update_fields=['status', 'is_active'])
        # Any other open claim on the same item loses; its claimant is told why.
        for other in item.claims.exclude(pk=claim.pk).filter(status__in=OPEN):
            other.status = Claim.Status.REJECTED
            other.reviewed_by = request.user
            other.decision_note = 'The item was verified as belonging to another claimant.'
            other.save(update_fields=['status', 'reviewed_by', 'decision_note', 'updated_at'])
            notify_user(other.claimant, kind='claim',
                        title=f'Claim closed — “{item.title}”',
                        body=other.decision_note, url=other.get_absolute_url(), item=item)
        record_decision(request.user, ModerationDecision.Action.CLAIM_APPROVED,
                        item=item, claim=claim, note=note)
        for user in (claim.claimant, item.reporter):
            notify_user(user, kind='claim',
                        title=f'Claim approved — “{item.title}”',
                        body='Contact details are now shared. Arrange the handover; a moderator '
                             'confirms completion afterwards.',
                        url=claim.get_absolute_url(), item=item)
        messages.success(request, 'Claim approved. Confirm the handover once collection is complete.')
    else:
        claim.status = Claim.Status.REJECTED
        claim.save()
        if item.status == Item.Status.CLAIMED and not item.claims.exclude(pk=claim.pk) \
                .filter(status__in=OPEN + ['approved']).exists():
            item.status = Item.Status.APPROVED
            item.save(update_fields=['status'])
        record_decision(request.user, ModerationDecision.Action.CLAIM_REJECTED,
                        item=item, claim=claim, note=note)
        notify_user(claim.claimant, kind='claim',
                    title=f'Claim rejected — “{item.title}”',
                    body=note or 'The claim could not be verified.',
                    url=claim.get_absolute_url(), item=item)
        messages.info(request, 'Claim rejected.')
    return redirect(claim.get_absolute_url())


@login_required
def my_claims(request):
    qs = request.user.claims.select_related('item', 'item__category', 'item__location')
    status = request.GET.get('status', '')
    if status == 'open':
        qs = qs.filter(status__in=OPEN)
    elif status == 'approved':
        qs = qs.filter(status__in=['approved', 'returned'])
    elif status == 'closed':
        qs = qs.filter(status__in=['rejected', 'returned'])
    page = Paginator(qs, 12).get_page(request.GET.get('page'))
    incoming = (Claim.objects.filter(item__reporter=request.user)
                .select_related('item', 'claimant').order_by('-updated_at')[:12])
    return render(request, 'claims/my_claims.html', {
        'claims': page, 'page_obj': page, 'sel_status': status, 'incoming': incoming,
    })


@moderator_required
@require_POST
@transaction.atomic
def confirm_return(request, pk):
    claim = get_object_or_404(Claim.objects.select_for_update(), pk=pk)
    if claim.status != Claim.Status.APPROVED:
        raise PermissionDenied('Only an approved claim can be marked returned.')
    item = Item.objects.select_for_update().get(pk=claim.item_id)
    note = request.POST.get('note', '').strip()[:280]
    claim.status = Claim.Status.RETURNED
    if not claim.reviewed_by_id:
        claim.reviewed_by = request.user
    claim.save(update_fields=['status', 'reviewed_by', 'updated_at'])
    item.status = Item.Status.RETURNED
    item.is_active = False
    item.save(update_fields=['status', 'is_active'])
    record_decision(request.user, ModerationDecision.Action.HANDOVER_CONFIRMED,
                    item=item, claim=claim, note=note)

    # The claimant's own report of the same item (their lost report when a
    # found item is returned, or their found report when they returned a lost
    # one) is resolved too, so it stops appearing in search and matching.
    own = _claimants_own_report(claim)
    if own is not None and own.is_active and own.status != Item.Status.RETURNED:
        own = Item.objects.select_for_update().get(pk=own.pk)
        own.status = Item.Status.RETURNED
        own.is_active = False
        own.save(update_fields=['status', 'is_active'])
        clear_matches(own)
        close_claims(own, request.user,
                     'This item has been returned through another report, so this claim is closed.')
        record_decision(request.user, ModerationDecision.Action.HANDOVER_CONFIRMED,
                        item=own, claim=claim, note=f'Resolved with report #{item.pk}.')
    for user in (claim.claimant, item.reporter):
        notify_user(user, kind='claim', title=f'Handover completed — “{item.title}”',
                    body='The item is recorded as returned. Thank you for using LostLink.',
                    url=claim.get_absolute_url(), item=item)
    messages.success(request, 'Handover confirmed. This item is now recorded as returned.')
    return redirect(claim.get_absolute_url())


@login_required
@require_POST
@transaction.atomic
def dispute_claim(request, pk):
    claim = get_object_or_404(Claim.objects.select_for_update(), pk=pk)
    user = request.user
    if user.id not in (claim.claimant_id, claim.item.reporter_id) and not user.is_staff_role:
        raise PermissionDenied('You cannot dispute this claim.')
    if claim.is_finalised:
        raise PermissionDenied('This claim cannot be disputed.')
    reason = request.POST.get('reason', '').strip()
    if not reason:
        messages.error(request, 'Explain the concern so a moderator can review it.')
        return redirect(claim.get_absolute_url())
    claim.status = Claim.Status.DISPUTED
    claim.decision_note = reason[:280]
    claim.save(update_fields=['status', 'decision_note', 'updated_at'])
    record_decision(user, ModerationDecision.Action.DISPUTE_RAISED,
                    item=claim.item, claim=claim, note=reason)
    other = claim.item.reporter if user.id == claim.claimant_id else claim.claimant
    notify_user(other, kind='claim', title=f'Dispute raised — “{claim.item.title}”',
                body='A concern was raised about this claim; a moderator will review it.',
                url=claim.get_absolute_url(), item=claim.item)
    messages.success(request, 'Dispute sent to the moderator review queue.')
    return redirect(claim.get_absolute_url())
