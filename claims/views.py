from django.contrib import messages
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.db import transaction

from accounts.decorators import moderator_required
from items.models import Item
from matching.models import Match
from notifications.utils import notify_user

from .forms import AnswerForm, ClaimForm, DecisionForm, QuestionForm
from .models import Claim, VerificationQuestion


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
    if item.claims.filter(status__in=['approved','returned']).exists():
        raise PermissionDenied('This item already has a verified claimant.')

    # Semantics differ by report type:
    #  * Found item  -> the viewer is the OWNER claiming it back ("this is mine")
    #  * Lost item   -> the viewer is the FINDER offering to return it ("I found this")
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
            # default contact from profile if left blank
            if not claim.contact_email:
                claim.contact_email = request.user.email
            if not claim.contact_phone:
                claim.contact_phone = request.user.phone
            claim.save()

            if item.status == Item.Status.APPROVED:
                item.status = Item.Status.CLAIMED
                item.save(update_fields=['status'])

            notify_user(item.reporter, kind='claim',
                        title=f'New response on “{item.title}”',
                        body=f'{request.user.display_name()} responded to your report.',
                        url=claim.get_absolute_url())
            messages.success(request, 'Submitted. Verification will follow shortly.')
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
        Claim.objects.select_related('item', 'claimant', 'item__reporter'), pk=pk)
    user = request.user
    is_claimant = claim.claimant_id == user.id
    is_finder = claim.item.reporter_id == user.id
    can_moderate = user.is_staff_role
    if not (is_claimant or is_finder or can_moderate):
        raise PermissionDenied('You cannot view this claim.')

    return render(request, 'claims/claim_detail.html', {
        'claim': claim,
        'questions': claim.questions.all(),
        'is_claimant': is_claimant,
        'is_finder': is_finder,
        'can_moderate': can_moderate,
        'assessment': build_ai_assessment(claim),
        'question_form': QuestionForm(),
        'answer_form': AnswerForm(),
        'decision_form': DecisionForm(),
    })


def build_ai_assessment(claim):
    """Give the moderator the AI context behind a claim (synopsis §4.11, §8).

    Surfaces (a) the best matching lost↔found confidence score + breakdown that
    connects the claimant to this item, and (b) a lightweight NLP check of how
    well the claimant's own statement aligns with the item's description.
    """
    from matching.engine import text_similarity

    item = claim.item
    # The strongest match linking THIS item to any report by the claimant.
    if item.is_found:
        match_qs = Match.objects.filter(
            found_item=item, lost_item__reporter=claim.claimant,
            engine_version=settings.MATCHING['VERSION'])
    else:
        match_qs = Match.objects.filter(
            lost_item=item, found_item__reporter=claim.claimant,
            engine_version=settings.MATCHING['VERSION'])
    match = claim.match or match_qs.order_by('-score').first()

    # NLP: does the claimant's statement read like the same object?
    statement_sim = text_similarity(claim.statement, [item.description])[0]

    return {
        'match': match,
        'statement_sim': round(statement_sim * 100),
        'statement_tone': ('success' if statement_sim >= 0.5
                           else 'warning' if statement_sim >= 0.25 else 'muted'),
    }


@login_required
def add_question(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    if not claim.is_open:
        raise PermissionDenied('This claim is closed.')
    if not (request.user.is_staff_role or claim.item.reporter_id == request.user.id):
        raise PermissionDenied('Only the finder or a moderator can ask questions.')
    if request.method == 'POST':
        form = QuestionForm(request.POST)
        if form.is_valid():
            q = form.save(commit=False)
            q.claim = claim
            q.asked_by = request.user
            q.save()
            if claim.status == Claim.Status.SUBMITTED:
                claim.status = Claim.Status.QUESTIONS
                claim.save(update_fields=['status'])
            notify_user(claim.claimant, kind='claim',
                        title='Verification question added',
                        body=f'Please answer to verify “{claim.item.title}”.',
                        url=claim.get_absolute_url())
            messages.success(request, 'Verification question added.')
    return redirect(claim.get_absolute_url())


@login_required
def answer_question(request, pk, qid):
    claim = get_object_or_404(Claim, pk=pk)
    if not claim.is_open:
        raise PermissionDenied('This claim is closed.')
    question = get_object_or_404(VerificationQuestion, pk=qid, claim=claim)
    if claim.claimant_id != request.user.id:
        raise PermissionDenied('Only the claimant can answer.')
    if request.method == 'POST':
        form = AnswerForm(request.POST)
        if form.is_valid():
            question.answer = form.cleaned_data['answer']
            question.answered_at = timezone.now()
            question.save(update_fields=['answer', 'answered_at'])
            if claim.all_answered:
                claim.status = Claim.Status.ANSWERED
                claim.save(update_fields=['status'])
                notify_user(claim.item.reporter, kind='claim',
                            title='Claim answers submitted',
                            body=f'“{claim.item.title}” is ready for review.',
                            url=claim.get_absolute_url())
            messages.success(request, 'Answer saved.')
    return redirect(claim.get_absolute_url())


@moderator_required
@transaction.atomic
def decide_claim(request, pk, decision):
    claim = get_object_or_404(Claim.objects.select_for_update(), pk=pk)
    item = Item.objects.select_for_update().get(pk=claim.item_id)
    if not item.is_active or item.status not in ('approved', 'claimed'):
        raise PermissionDenied('This report is no longer available for claims.')
    if not claim.is_open or decision not in ('approve', 'reject'):
        raise PermissionDenied('This decision is not available.')
    if request.method != 'POST':
        return redirect(claim.get_absolute_url())
    form = DecisionForm(request.POST)
    note = form.cleaned_data.get('note', '') if form.is_valid() else ''
    claim.reviewed_by = request.user
    claim.decision_note = note

    if decision == 'approve':
        if not claim.is_open or not claim.all_answered:
            messages.error(request, 'Complete the private verification questions before approval.')
            return redirect(claim.get_absolute_url())
        claim.status = Claim.Status.APPROVED
        item = claim.item
        item.status = Item.Status.CLAIMED
        item.is_active = True
        item.save(update_fields=['status', 'is_active'])
        # Reject any other open claims on the same item.
        item.claims.exclude(pk=claim.pk).filter(
            status__in=[Claim.Status.SUBMITTED, Claim.Status.QUESTIONS,
                        Claim.Status.ANSWERED]).update(
            status=Claim.Status.REJECTED,
            decision_note='Item was returned to another verified claimant.')
        notify_user(claim.claimant, kind='claim',
                    title=f'Claim approved — “{item.title}”',
                    body='Arrange collection. A moderator will confirm the completed handover.',
                    url=claim.get_absolute_url())
        messages.success(request, 'Claim approved. Confirm the handover once collection is complete.')
    else:
        claim.status = Claim.Status.REJECTED
        # If no other open claim, return item to the pool.
        item = claim.item
        if item.status == Item.Status.CLAIMED and not item.claims.exclude(
                pk=claim.pk).filter(status__in=[
                    Claim.Status.SUBMITTED, Claim.Status.QUESTIONS,
                    Claim.Status.ANSWERED]).exists():
            item.status = Item.Status.APPROVED
            item.save(update_fields=['status'])
        notify_user(claim.claimant, kind='claim',
                    title=f'Claim rejected — “{claim.item.title}”',
                    body=note or 'The claim could not be verified.',
                    url=claim.get_absolute_url())
        messages.info(request, 'Claim rejected.')

    claim.save()
    return redirect(claim.get_absolute_url())


@login_required
def my_claims(request):
    claims = request.user.claims.select_related('item')
    return render(request, 'claims/my_claims.html', {'claims': claims})


@moderator_required
@transaction.atomic
def confirm_return(request, pk):
    claim = get_object_or_404(Claim.objects.select_for_update(), pk=pk)
    if request.method != 'POST' or claim.status != Claim.Status.APPROVED:
        raise PermissionDenied('Only an approved claim can be marked returned.')
    item = Item.objects.select_for_update().get(pk=claim.item_id)
    claim.status = Claim.Status.RETURNED
    claim.save(update_fields=['status','updated_at'])
    item.status = Item.Status.RETURNED
    item.is_active = False
    item.save(update_fields=['status','is_active'])
    for user in (claim.claimant,item.reporter):
        notify_user(user,kind='claim',title='Handover completed',body=item.title,url=claim.get_absolute_url())
    messages.success(request,'Handover confirmed. This item is now recorded as returned.')
    return redirect(claim.get_absolute_url())


@login_required
@transaction.atomic
def dispute_claim(request, pk):
    claim = get_object_or_404(Claim.objects.select_for_update(), pk=pk)
    if request.user not in (claim.claimant,claim.item.reporter) and not request.user.is_staff_role:
        raise PermissionDenied('You cannot dispute this claim.')
    if request.method != 'POST' or claim.status in ('returned','rejected'):
        raise PermissionDenied('This claim cannot be disputed.')
    reason = request.POST.get('reason','').strip()
    if not reason:
        messages.error(request,'Explain the concern so a moderator can review it.')
        return redirect(claim.get_absolute_url())
    claim.status = Claim.Status.DISPUTED
    claim.decision_note = reason[:280]
    claim.save(update_fields=['status','decision_note','updated_at'])
    messages.success(request,'Dispute sent to the moderator review queue.')
    return redirect(claim.get_absolute_url())
