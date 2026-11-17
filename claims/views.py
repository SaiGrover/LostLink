"""Claim verification and recovery workflow.

    Match → Claim submitted → Private questions / evidence → Independent
    moderator review (checklist) → Approved: item reserved, handover pending
    → Owner's one-time code entered by the person handing over → Returned

Two use cases share the model but keep distinct copy and permissions:

* a **found** report is *claimed* by its owner ("this is mine");
* a **lost** report receives a *return offer* from a finder ("I found this").

The owner always holds the handover code. Rules live in ``claims.services``;
these views authenticate, lock rows, call the service and render.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import moderator_required, verified_required
from items.models import Item
from matching.models import Match

from . import services
from .forms import (AnswerForm, AppealForm, ClaimForm, DecisionForm, DisputeResolutionForm,
                    EvidenceForm, ExceptionHandoverForm, HandoverCodeForm, QuestionForm, RejectForm)
from .models import Claim, ClaimEvidence, VerificationQuestion
from .services import ClaimError, close_claims  # noqa: F401  (re-exported for older imports)

OPEN = list(Claim.OPEN_STATUSES)


def _fail(request, claim, error):
    messages.error(request, str(error))
    return redirect(claim.get_absolute_url())


@login_required
@verified_required
def start_claim(request, item_id):
    item = get_object_or_404(Item, pk=item_id)
    user = request.user
    if not item.is_public and not (item.reporter_id == user.id or user.is_staff_role):
        raise Http404('Report not found.')
    elig = services.claim_eligibility(item, user)
    if not elig['can_claim']:
        # A link to an unavailable action explains why instead of failing.
        if elig['existing'] is not None:
            if elig['state'] == 'appealable':
                messages.info(request, elig['reason'])
            return redirect(elig['existing'].get_absolute_url())
        messages.warning(request, elig['reason'])
        return redirect(item.get_absolute_url())

    match_id = request.GET.get('match')
    if match_id and not match_id.isdecimal():
        raise PermissionDenied('Invalid match reference.')
    match = get_object_or_404(Match, pk=match_id) if match_id else None
    if match and not ((match.found_item_id == item.id and match.lost_item.reporter_id == user.id)
                      or (match.lost_item_id == item.id and match.found_item.reporter_id == user.id)):
        raise PermissionDenied('This match does not belong to your report.')

    form = ClaimForm(request.POST or None, found=item.is_found,
                     initial={'contact_email': request.user.email, 'contact_phone': request.user.phone})
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                locked = Item.objects.select_for_update().get(pk=item.pk)
                claim = services.start_claim(
                    locked, request.user, statement=form.cleaned_data['statement'],
                    contact_phone=form.cleaned_data['contact_phone'],
                    contact_email=form.cleaned_data['contact_email'], match=match)
        except ClaimError:
            raise
        messages.success(request, 'Submitted. Private verification follows before any decision.')
        return redirect(claim.get_absolute_url())
    return render(request, 'claims/start_claim.html',
                  {'form': form, 'item': item, 'match': match, 'is_found': item.is_found})


@login_required
def claim_detail(request, pk):
    claim = get_object_or_404(
        Claim.objects.select_related('item', 'claimant', 'item__reporter', 'item__category',
                                     'item__location', 'reviewed_by'), pk=pk)
    user = request.user
    item = claim.item
    is_claimant = claim.claimant_id == user.id
    is_finder = item.reporter_id == user.id
    can_moderate = user.is_staff_role
    if not (is_claimant or is_finder or can_moderate):
        raise PermissionDenied('You cannot view this claim.')
    independent = can_moderate and not (is_claimant or is_finder)

    questions = list(claim.questions.select_related('asked_by'))
    for q in questions:
        # The expected answer is private to whoever asked and to moderators.
        q.show_expected = bool(q.expected_answer) and (can_moderate or q.asked_by_id == user.id)
    session = services.active_session(claim) if claim.status == Claim.Status.APPROVED else None
    blocker = services.handover_blocker(claim, item) if claim.status == Claim.Status.APPROVED else ''
    appeal_ok, appeal_reason = services.can_appeal(claim) if claim.status == 'rejected' else (False, '')
    suggested = None
    if (claim.status in (Claim.Status.ANSWERED, Claim.Status.QUESTIONS, Claim.Status.SUBMITTED)
            and can_moderate) or (claim.status == Claim.Status.RETURNED and is_claimant):
        suggested = services.suggested_counterpart(claim)
        if suggested is not None and not (suggested.is_active and suggested.status in ('approved', 'claimed')):
            suggested = None
    return render(request, 'claims/claim_detail.html', {
        'claim': claim,
        'questions': questions,
        'unanswered': sum(1 for q in questions if not q.answer),
        'is_claimant': is_claimant,
        'is_finder': is_finder,
        'can_moderate': can_moderate,
        'independent': independent,
        'is_recipient': claim.recipient.id == user.id,
        'is_provider': claim.provider.id == user.id,
        'contacts_visible': claim.status in Claim.VERIFIED_STATUSES,
        'assessment': build_ai_assessment(claim) if (can_moderate or is_finder) else None,
        'private_details': item.private_details if (can_moderate or is_finder) else '',
        'question_form': QuestionForm(),
        'answer_form': AnswerForm(),
        'decision_form': DecisionForm(),
        'appeal_form': AppealForm(initial={'statement': claim.statement}),
        'evidence_form': EvidenceForm(),
        'dispute_form': DisputeResolutionForm(),
        'code_form': HandoverCodeForm(),
        'exception_form': ExceptionHandoverForm(),
        'evidence': list(claim.evidence.all()) if services.can_view_evidence(claim, user) else [],
        'revisions': list(claim.revisions.all()) if (is_claimant or can_moderate) else [],
        'open_dispute': claim.open_dispute,
        'session': session,
        'handover_blocker': blocker,
        'sessions': list(claim.handover_sessions.all()[:6]) if can_moderate else [],
        'appeal_ok': appeal_ok,
        'appeal_reason': appeal_reason,
        'timeline': claim.timeline(),
        'decisions': claim.moderation_decisions.select_related('actor') if can_moderate else [],
        'max_appeals': getattr(settings, 'CLAIM_MAX_APPEALS', 2),
        'suggested_report': suggested,
    })


def build_ai_assessment(claim):
    """Matching context for the reviewer: the best current match between the
    claimant's own report and this item, plus statement-vs-description text
    similarity. Decision support only — never an approval signal."""
    from matching.engine import text_similarity

    item = claim.item
    if item.is_found:
        match_qs = Match.all_objects.filter(found_item=item, lost_item__reporter=claim.claimant,
                                        engine_version=settings.MATCHING['VERSION'])
    else:
        match_qs = Match.all_objects.filter(lost_item=item, found_item__reporter=claim.claimant,
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


def _locked(pk):
    try:
        return services.lock_claim(pk)
    except Claim.DoesNotExist:
        raise Http404('Claim not found.')


@login_required
@verified_required
@require_POST
@transaction.atomic
def add_question(request, pk):
    claim, item = _locked(pk)
    if not claim.is_open:
        raise ClaimError('This claim is closed.')
    if not (request.user.is_staff_role or item.reporter_id == request.user.id):
        raise ClaimError('Only the reporter or a moderator can ask questions.')
    if request.user.id == claim.claimant_id:
        raise ClaimError('You cannot question your own claim.')
    form = QuestionForm(request.POST)
    if not form.is_valid():
        return _fail(request, claim, 'Write a question before adding it.')
    q = form.save(commit=False)
    q.claim = claim
    q.asked_by = request.user
    q.save()
    if claim.status in (Claim.Status.SUBMITTED, Claim.Status.ANSWERED):
        claim.status = Claim.Status.QUESTIONS
        claim.save(update_fields=['status', 'updated_at'])
    from notifications.utils import notify_user
    notify_user(claim.claimant, kind='claim', title='Verification question added',
                body=f'Please answer to verify “{item.title}”.',
                url=claim.get_absolute_url(), item=item, dedupe=True)
    messages.success(request, 'Verification question added.')
    return redirect(claim.get_absolute_url())


@login_required
@verified_required
@require_POST
@transaction.atomic
def answer_question(request, pk, qid):
    claim, item = _locked(pk)
    if not claim.is_open:
        raise ClaimError('This claim is closed.')
    if claim.claimant_id != request.user.id:
        raise ClaimError('Only the claimant can answer.')
    question = get_object_or_404(VerificationQuestion.objects.select_for_update(), pk=qid, claim=claim)
    if question.answer:
        raise ClaimError('This question has already been answered.')
    form = AnswerForm(request.POST)
    if not form.is_valid():
        return _fail(request, claim, 'Write an answer before submitting.')
    from django.utils import timezone
    from notifications.utils import notify_user
    question.answer = form.cleaned_data['answer']
    question.answered_at = timezone.now()
    question.save(update_fields=['answer', 'answered_at'])
    if claim.all_answered:
        claim.status = Claim.Status.ANSWERED
        claim.save(update_fields=['status', 'updated_at'])
        notify_user(item.reporter, kind='claim', title='Verification answers submitted',
                    body=f'“{item.title}” is ready for moderator review.',
                    url=claim.get_absolute_url(), item=item, dedupe=True)
    messages.success(request, 'Answer saved.')
    return redirect(claim.get_absolute_url())


@moderator_required
@require_POST
@transaction.atomic
def decide_claim(request, pk, decision):
    claim, item = _locked(pk)
    if decision not in ('approve', 'reject'):
        raise ClaimError('Unknown decision.')
    # Authority and state first, so a stale or forged request is refused outright.
    services._require_independent_moderator(claim, request.user)
    if not claim.is_open:
        raise ClaimError('This claim has already been decided.')
    try:
        if decision == 'approve':
            form = DecisionForm(request.POST)
            if not form.is_valid():
                return _fail(request, claim, 'Complete every checklist item and give a reason before approving.')
            services.approve_claim(claim, item, request.user, note=form.cleaned_data['note'],
                                   checklist=form.checklist(),
                                   confirm_linked_report=form.cleaned_data['confirm_linked_report'])
            messages.success(request, 'Claim approved. The item is reserved; the handover is confirmed '
                                      'with the owner\'s one-time code.')
        else:
            form = RejectForm(request.POST)
            if not form.is_valid():
                return _fail(request, claim, 'Give the claimant a reason for the rejection.')
            checklist = DecisionForm(request.POST)
            services.reject_claim(claim, item, request.user, note=form.cleaned_data['note'],
                                  checklist=checklist.checklist() if checklist.is_valid() else {})
            messages.info(request, 'Claim rejected.')
    except ClaimError:
        raise
    return redirect(claim.get_absolute_url())


@login_required
@require_POST
@transaction.atomic
def withdraw_claim(request, pk):
    claim, item = _locked(pk)
    try:
        services.withdraw_claim(claim, item, request.user, note=request.POST.get('note', '')[:280])
    except ClaimError:
        raise
    messages.info(request, 'Your claim has been withdrawn.')
    return redirect(claim.get_absolute_url())


@login_required
@verified_required
@require_POST
@transaction.atomic
def appeal_claim(request, pk):
    claim, item = _locked(pk)
    form = AppealForm(request.POST)
    if not form.is_valid():
        return _fail(request, claim, 'Explain your request in at least 20 characters.')
    try:
        services.appeal_claim(claim, item, request.user, statement=form.cleaned_data['statement'],
                              additional_details=form.cleaned_data['additional_details'],
                              files=request.FILES.getlist('evidence'))
    except ClaimError:
        raise
    messages.success(request, 'Reconsideration requested. A moderator will review the new information.')
    return redirect(claim.get_absolute_url())


@login_required
@verified_required
@require_POST
@transaction.atomic
def add_evidence(request, pk):
    claim, item = _locked(pk)
    form = EvidenceForm(request.POST)
    try:
        count = services.add_evidence(claim, item, request.user, files=request.FILES.getlist('evidence'),
                                      note=form.data.get('note', ''))
    except ClaimError:
        raise
    messages.success(request, f'{count} photo{"s" if count != 1 else ""} added privately to your claim.')
    return redirect(claim.get_absolute_url())


@login_required
def evidence_file(request, pk, eid):
    """Serve private evidence to the claimant and moderators only."""
    claim = get_object_or_404(Claim, pk=pk)
    evidence = get_object_or_404(ClaimEvidence, pk=eid, claim=claim)
    if not services.can_view_evidence(claim, request.user):
        raise PermissionDenied('This evidence is private.')
    if not evidence.file:
        raise Http404('Evidence not found.')
    response = FileResponse(evidence.file.open('rb'), content_type='image/jpeg')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Disposition'] = f'inline; filename="claim-{claim.pk}-evidence-{evidence.pk}.jpg"'
    return response


@login_required
@verified_required
@require_POST
@transaction.atomic
def dispute_claim(request, pk):
    claim, item = _locked(pk)
    try:
        services.raise_dispute(claim, item, request.user, reason=request.POST.get('reason', ''))
    except ClaimError:
        raise
    messages.success(request, 'Dispute sent to the moderator review queue.')
    return redirect(claim.get_absolute_url())


@moderator_required
@require_POST
@transaction.atomic
def resolve_dispute(request, pk):
    claim, item = _locked(pk)
    services._require_independent_moderator(claim, request.user, what='resolve this dispute')
    if not claim.has_open_dispute:
        raise ClaimError('There is no open dispute on this claim.')
    form = DisputeResolutionForm(request.POST)
    if not form.is_valid():
        return _fail(request, claim, 'Choose an outcome and record the reason (at least 10 characters).')
    try:
        services.resolve_dispute(claim, item, request.user, outcome=form.cleaned_data['outcome'],
                                 note=form.cleaned_data['note'])
    except ClaimError:
        raise
    messages.success(request, 'Dispute resolved.')
    return redirect(claim.get_absolute_url())


@login_required
@verified_required
@require_POST
@transaction.atomic
def issue_code(request, pk):
    """Create the recipient's one-time handover code and show it once."""
    claim, item = _locked(pk)
    try:
        session, code = services.issue_handover_code(claim, item, request.user)
    except ClaimError:
        raise
    response = render(request, 'claims/handover_code.html', {
        'claim': claim, 'item': item, 'code': f'{code[:3]} {code[3:]}', 'session': session})
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@verified_required
@require_POST
@transaction.atomic
def verify_code(request, pk):
    claim, item = _locked(pk)
    form = HandoverCodeForm(request.POST)
    if not form.is_valid():
        return _fail(request, claim, 'Enter the 6-digit code.')
    try:
        result = services.verify_handover_code(claim, item, request.user, form.cleaned_data['code'])
    except ClaimError:
        raise
    (messages.success if result.ok else messages.error)(request, result.message)
    return redirect(claim.get_absolute_url())


@moderator_required
@require_POST
@transaction.atomic
def confirm_return(request, pk):
    """Exceptional, documented moderator completion of a handover.

    The normal path is the owner's one-time code. This requires a reason and
    written evidence, an independent moderator, and is recorded as an
    exception — distinguishable from a code-confirmed handover.
    """
    claim, item = _locked(pk)
    services._require_independent_moderator(claim, request.user, what='record this handover')
    blocker = services.handover_blocker(claim, item)
    if blocker:
        raise ClaimError(blocker)
    form = ExceptionHandoverForm(request.POST)
    if not form.is_valid():
        return _fail(request, claim, 'Choose a reason and document the evidence (at least 20 characters).')
    try:
        services.exception_handover(claim, item, request.user, reason=form.cleaned_data['reason'],
                                    note=form.cleaned_data['note'])
    except ClaimError:
        raise
    messages.success(request, 'Handover recorded as a documented exception. The item is returned.')
    return redirect(claim.get_absolute_url())


@login_required
@verified_required
@require_POST
@transaction.atomic
def close_own_report(request, pk, item_id):
    """The claimant confirms their own open report was this same item."""
    claim, item = _locked(pk)
    report = get_object_or_404(Item, pk=item_id)
    services.close_own_report_as_same_item(claim, item, request.user, report)
    messages.success(request, f'“{report.title}” is closed as returned with this claim.')
    return redirect(claim.get_absolute_url())


@login_required
def my_claims(request):
    qs = request.user.claims.select_related('item', 'item__category', 'item__location')
    status = request.GET.get('status', '')
    if status == 'open':
        qs = qs.filter(status__in=OPEN)
    elif status == 'approved':
        qs = qs.filter(status__in=Claim.VERIFIED_STATUSES)
    elif status == 'closed':
        qs = qs.filter(status__in=['rejected', 'returned', 'withdrawn'])
    page = Paginator(qs, 12).get_page(request.GET.get('page'))
    incoming = (Claim.objects.filter(item__reporter=request.user)
                .select_related('item', 'claimant').order_by('-updated_at')[:12])
    return render(request, 'claims/my_claims.html', {
        'claims': page, 'page_obj': page, 'sel_status': status, 'incoming': incoming,
    })


# Kept for imports elsewhere (e.g. tests referencing the old helper name).
_claimants_own_report = services.counterpart_report
