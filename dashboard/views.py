"""Moderator workspace and administrator analytics / exports.

All statistics here are live Django ORM aggregations over real records; nothing
is hard-coded. Exports never include private verification answers or contact
fields (synopsis Section 8, data minimisation).
"""
import csv
import datetime
from collections import Counter, defaultdict
from io import BytesIO
from statistics import mean, median

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required, moderator_required
from accounts.models import User
from claims.models import Claim, ClaimDispute
from items.forms import CategoryForm, LocationForm
from items import lifecycle
from items.models import Category, Item, Location, ReportFlag
from matching.features import manifest
from matching.models import Match, ScoreSnapshot
from matching.services import clear_matches, run_matching
from notifications.utils import notify_user

from .models import ModerationDecision, record_decision

OPEN_CLAIMS = [Claim.Status.SUBMITTED, Claim.Status.QUESTIONS, Claim.Status.ANSWERED]


def _current_matches():
    """Live potential matches: current engine version, both reports still
    active and public. Pairs whose report was returned, withdrawn or removed
    stay stored as claim evidence but are not counted as open matches."""
    live = [Item.Status.APPROVED, Item.Status.CLAIMED]
    return Match.objects.filter(engine_version=settings.MATCHING['VERSION'],
                                lost_item__is_active=True, lost_item__status__in=live,
                                found_item__is_active=True, found_item__status__in=live)


def _safe_next(request, default):
    from django.utils.http import url_has_allowed_host_and_scheme
    target = request.POST.get('next', '')
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return target
    return default


# ---------------------------------------------------------------------------
# Moderator — review queue
# ---------------------------------------------------------------------------
@moderator_required
def moderation(request):
    # Independent review: a moderator's own reports are not actionable for
    # them (another moderator decides), so they are listed separately.
    all_pending = (Item.objects.filter(status=Item.Status.PENDING, is_active=True)
                   .select_related('category', 'location', 'reporter', 'duplicate_of')
                   .order_by('created_at'))
    pending_items = all_pending.exclude(reporter=request.user)
    own_pending = all_pending.filter(reporter=request.user)
    pending_page = Paginator(pending_items, 20).get_page(request.GET.get('page'))
    open_claims = (Claim.objects.filter(status__in=OPEN_CLAIMS)
                   .select_related('item', 'claimant').order_by('updated_at'))
    disputes = (ClaimDispute.objects.filter(status=ClaimDispute.Status.OPEN)
                .select_related('claim', 'claim__item', 'claim__claimant', 'raised_by')
                .order_by('created_at'))
    handovers = (Claim.objects.filter(status=Claim.Status.APPROVED)
                 .select_related('item', 'claimant').order_by('updated_at'))
    flagged = (Item.objects.annotate(flag_count=Count('flags', filter=Q(flags__resolved_at__isnull=True)))
               .filter(flag_count__gt=0).select_related('category', 'reporter').order_by('-flag_count'))
    flag_reasons = defaultdict(list)
    for flag in (ReportFlag.objects.filter(resolved_at__isnull=True)
                 .select_related('reporter').order_by('-created_at')):
        flag_reasons[flag.item_id].append(flag)
    flagged = list(flagged)
    for flagged_item in flagged:
        flagged_item.open_flags = flag_reasons.get(flagged_item.pk, [])
    duplicates = pending_items.filter(duplicate_of__isnull=False)
    history = ModerationDecision.objects.select_related('actor', 'item', 'claim')[:10]
    return render(request, 'dashboard/moderation.html', {
        'pending_items': pending_page, 'page_obj': pending_page,
        'pending_count': pending_items.count(),
        'own_pending': own_pending,
        'open_claims': open_claims, 'claim_count': open_claims.count(),
        'disputes': disputes, 'dispute_count': disputes.count(),
        'handovers': handovers, 'handover_count': handovers.count(),
        'flagged': flagged, 'flag_count': len(flagged),
        'duplicate_count': duplicates.count(),
        'history': history,
    })


@moderator_required
@require_POST
@transaction.atomic
def review_report(request, pk, decision):
    item = get_object_or_404(Item.objects.select_for_update(), pk=pk)
    note = request.POST.get('note', '').strip()[:280]
    next_url = _safe_next(request, 'dashboard:moderation')
    if item.reporter_id == request.user.id:
        # Independent moderation: nobody approves or rejects their own report,
        # whichever page or endpoint the request comes from.
        messages.error(request, 'You reported this item, so another moderator has to review it.')
        return redirect(next_url)
    if item.status != Item.Status.PENDING or not item.is_active:
        messages.error(request, 'Only active reports awaiting review can be moderated here.')
        return redirect(next_url)
    if decision == 'approve':
        lifecycle.transition(item, Item.Status.APPROVED, actor=request.user,
                             action=ModerationDecision.Action.REPORT_APPROVED, note=note, rematch=False)
        matches = run_matching(item)
        notify_user(item.reporter, kind='report',
                    title=f'Report approved — “{item.title}”',
                    body='Your report is now visible in search and matching.' +
                         (f' {len(matches)} candidate match(es) were found.' if matches else ''),
                    url=item.get_absolute_url(), item=item, dedupe=True)
        messages.success(request, f'Approved “{item.title}”.')
    elif decision == 'reject':
        lifecycle.transition(item, Item.Status.REJECTED, actor=request.user,
                             action=ModerationDecision.Action.REPORT_REJECTED, note=note)
        notify_user(item.reporter, kind='report',
                    title=f'Report rejected — “{item.title}”',
                    body=note or 'A moderator rejected this report. You can edit and resubmit it.',
                    url=item.get_absolute_url(), item=item, dedupe=True)
        messages.info(request, f'Rejected “{item.title}”.')
    else:
        messages.error(request, 'Unknown decision.')
    return redirect(next_url)


@moderator_required
@require_POST
@transaction.atomic
def resolve_flag(request, pk, decision):
    """Resolve every open flag on a report: dismiss them, or remove the report."""
    item = get_object_or_404(Item.objects.select_for_update(), pk=pk)
    note = request.POST.get('note', '').strip()[:280]
    if item.reporter_id == request.user.id:
        messages.error(request, 'You reported this item, so another moderator has to review its flags.')
        return redirect('dashboard:moderation')
    open_flags = item.flags.filter(resolved_at__isnull=True)
    if not open_flags.exists():
        messages.info(request, 'No open flags on this report.')
        return redirect('dashboard:moderation')
    if decision == 'reject':
        from claims.services import close_claims
        if item.status in lifecycle.TERMINAL:
            messages.info(request, 'This report is already closed.')
            return redirect('dashboard:moderation')
        open_flags.update(resolved_at=timezone.now(), resolved_by=request.user)
        # Claims on a removed report could never be decided; close them and
        # tell each claimant, instead of leaving them stuck in the queue.
        close_claims(item, request.user, 'This report was removed by a moderator, so the claim is closed.')
        lifecycle.transition(item, Item.Status.REMOVED, actor=request.user,
                             action=ModerationDecision.Action.REPORT_REMOVED,
                             note=note or 'Removed after user flags.')
        notify_user(item.reporter, kind='report', title=f'Report removed — “{item.title}”',
                    body=note or 'A moderator removed this report after reviewing user flags.',
                    url=item.get_absolute_url(), item=item)
        messages.info(request, f'Removed “{item.title}” and closed its flags.')
    elif decision == 'dismiss':
        messages.success(request, f'Flags on “{item.title}” dismissed.')
    else:
        messages.error(request, 'Unknown decision.')
        return redirect('dashboard:moderation')
    open_flags.update(resolved_at=timezone.now(), resolved_by=request.user)
    record_decision(request.user, ModerationDecision.Action.FLAG_RESOLVED, item=item,
                    note=f'{decision}: {note}'.strip(': '))
    return redirect('dashboard:moderation')


@moderator_required
@require_POST
def photo_privacy(request, pk, decision):
    """Moderator ruling on a report photo: ``restrict`` shows everyone except
    the reporter, staff and a verified owner a blurred preview; ``public``
    lifts that (items/media.py). The ruling is recorded on the report."""
    item = get_object_or_404(Item, pk=pk)
    next_url = _safe_next(request, 'dashboard:moderation')
    if decision not in ('restrict', 'public') or not item.image:
        messages.error(request, 'Unknown decision, or the report has no photo.')
        return redirect(next_url)
    item.photo_sensitive = decision == 'restrict'
    item.photo_sensitivity_source = Item.PhotoSensitivitySource.MODERATOR
    item.photo_reviewed_by = request.user
    item.photo_reviewed_at = timezone.now()
    item.save(update_fields=['photo_sensitive', 'photo_sensitivity_source',
                             'photo_reviewed_by', 'photo_reviewed_at'])
    messages.success(request, f'Photo on “{item.title}” is now '
                              f'{"shown blurred to other people" if item.photo_sensitive else "visible to everyone who can see the report"}.')
    return redirect(next_url)


@moderator_required
def decision_history(request):
    qs = ModerationDecision.objects.select_related('actor', 'item', 'claim', 'subject_user')
    action = request.GET.get('action', '')
    if action in dict(ModerationDecision.Action.choices):
        qs = qs.filter(action=action)
    page = Paginator(qs, 30).get_page(request.GET.get('page'))
    return render(request, 'dashboard/history.html', {
        'decisions': page, 'page_obj': page, 'sel_action': action,
        'actions': ModerationDecision.Action.choices,
    })


# ---------------------------------------------------------------------------
# Administrator — analytics dashboard (synopsis Sections 4.3, 4.11, 7.4)
# ---------------------------------------------------------------------------
def _safe_date(value):
    """A date from YYYY-MM-DD, or None for anything malformed or impossible
    (``parse_date`` raises on well-formed but invalid dates like 2026-02-30)."""
    try:
        return parse_date((value or '').strip())
    except ValueError:
        return None


def _period(request):
    """Optional ``from`` / ``to`` dates (inclusive) from the query string."""
    start = _safe_date(request.GET.get('from'))
    end = _safe_date(request.GET.get('to'))
    if start and end and start > end:
        start, end = end, start
    return start, end


def _apply_period(qs, field, start, end):
    if start:
        qs = qs.filter(**{f'{field}__date__gte': start})
    if end:
        qs = qs.filter(**{f'{field}__date__lte': end})
    return qs


def _weekly_trends(items, returned_claims, weeks=12):
    today = timezone.localdate()
    first = today - datetime.timedelta(days=today.weekday() + 7 * (weeks - 1))
    buckets = [first + datetime.timedelta(days=7 * i) for i in range(weeks)]
    lost, found, returned = Counter(), Counter(), Counter()
    for created, rtype in items.filter(created_at__date__gte=first).values_list('created_at', 'report_type'):
        day = timezone.localtime(created).date()
        week = day - datetime.timedelta(days=day.weekday())
        (lost if rtype == Item.Type.LOST else found)[week] += 1
    # The handover time (returned_at) is the recovery date; older rows fall back to updated_at.
    for returned_at, updated in returned_claims.filter(updated_at__date__gte=first)             .values_list('returned_at', 'updated_at'):
        day = timezone.localtime(returned_at or updated).date()
        returned[day - datetime.timedelta(days=day.weekday())] += 1
    rows = [{'week': w, 'label': w.strftime('%d %b'), 'lost': lost[w], 'found': found[w],
             'returned': returned[w]} for w in buckets]
    peak = max((r['lost'] + r['found'] for r in rows), default=0) or 1
    for r in rows:
        r['total'] = r['lost'] + r['found']
        r['pct'] = round(r['total'] * 100 / peak)
        r['lost_pct'] = round(r['lost'] * 100 / peak)
        r['found_pct'] = round(r['found'] * 100 / peak)
    return rows


def analytics_context(request):
    start, end = _period(request)
    all_items = Item.objects.all()
    items = _apply_period(all_items, 'created_at', start, end)
    claims = _apply_period(Claim.objects.all(), 'created_at', start, end)
    current = _current_matches()
    matches = _apply_period(current, 'created_at', start, end)
    total = items.count()

    by_category = (items.values('category__name', 'category__icon')
                   .annotate(n=Count('id')).order_by('-n')[:8])
    by_location = items.values('location__name').annotate(n=Count('id')).order_by('-n')[:8]
    lost_by_category = (items.filter(report_type=Item.Type.LOST)
                        .values('category__name', 'category__icon').annotate(n=Count('id')).order_by('-n')[:6])
    by_status = {row['status']: row['n'] for row in items.values('status').annotate(n=Count('id'))}
    claim_status = {row['status']: row['n'] for row in claims.values('status').annotate(n=Count('id'))}
    tier_counts = {row['tier']: row['n'] for row in matches.values('tier').annotate(n=Count('id'))}

    scores = list(matches.values_list('score', flat=True))
    bins = [0] * 10
    for s in scores:
        bins[min(9, int(s * 10))] += 1
    bin_peak = max(bins) or 1
    histogram = [{'label': f'{i * 10}–{i * 10 + 10}%', 'n': n, 'pct': round(n * 100 / bin_peak),
                  'tier': 'high' if i * 10 >= settings.MATCHING['TIER_HIGH'] * 100
                  else 'medium' if i * 10 >= settings.MATCHING['TIER_MEDIUM'] * 100 - 1e-9 else 'low'}
                 for i, n in enumerate(bins)]

    # Ongoing EDA promised in synopsis Section 4.3: descriptive, not accuracy.
    observations = list(matches.values_list('lost_item__event_date', 'found_item__event_date', 'text_score'))
    day_gaps = [abs((l - f).days) for l, f, _ in observations]
    text_scores = [t for _, _, t in observations]
    gap_ranges = [('Same day', lambda v: v == 0), ('1–3 days', lambda v: 1 <= v <= 3),
                  ('4–7 days', lambda v: 4 <= v <= 7), ('8–14 days', lambda v: 8 <= v <= 14),
                  ('15+ days', lambda v: v >= 15)]
    date_gap_distribution = [{'label': label, 'n': sum(1 for v in day_gaps if p(v))} for label, p in gap_ranges]
    gap_max = max((r['n'] for r in date_gap_distribution), default=1) or 1
    for r in date_gap_distribution:
        r['pct'] = round(r['n'] * 100 / gap_max)

    with_photos = items.exclude(image='').exclude(image__isnull=True).count()
    cat_max = max([c['n'] for c in by_category], default=1) or 1
    loc_max = max([l['n'] for l in by_location], default=1) or 1
    lost_cat_max = max([c['n'] for c in lost_by_category], default=1) or 1
    returned_claims = Claim.objects.filter(status=Claim.Status.RETURNED)
    lost_total = items.filter(report_type=Item.Type.LOST).count()
    found_total = items.filter(report_type=Item.Type.FOUND).count()
    returned_total = items.filter(status=Item.Status.RETURNED).count()
    resolved_base = items.filter(is_active=True).count() + returned_total
    recovery_rate = round(returned_total * 100 / resolved_base) if resolved_base else 0

    return {
        'period': {'from': start.isoformat() if start else '', 'to': end.isoformat() if end else '',
                   'active': bool(start or end)},
        'kpis': {
            'users': User.objects.count(),
            'reports': total,
            'lost': lost_total, 'found': found_total,
            'pending': items.filter(status=Item.Status.PENDING, is_active=True).count(),
            'active_reports': items.filter(is_active=True).count(),
            'matches': matches.count(),
            'high': tier_counts.get('high', 0), 'medium': tier_counts.get('medium', 0),
            'low': tier_counts.get('low', 0),
            'claims': claims.count(),
            'claims_pending': sum(claim_status.get(s, 0) for s in ('submitted', 'questions', 'answered')),
            'claims_approved': claim_status.get('approved', 0) + claim_status.get('returned', 0),
            'claims_rejected': claim_status.get('rejected', 0),
            'claims_withdrawn': claim_status.get('withdrawn', 0),
            'handovers_pending': claim_status.get('approved', 0),
            'disputes_open': ClaimDispute.objects.filter(status=ClaimDispute.Status.OPEN).count(),
            'recoveries': returned_claims.count(),
            'recoveries_by_code': returned_claims.filter(handover_method='otp').count(),
            'recoveries_by_exception': returned_claims.filter(handover_method='exception').count(),
            'returned': returned_total,
            'recovery_rate': recovery_rate,
            'flags_open': ReportFlag.objects.filter(resolved_at__isnull=True).count(),
        },
        'by_category': [dict(c, pct=round(c['n'] * 100 / cat_max)) for c in by_category],
        'lost_by_category': [dict(c, pct=round(c['n'] * 100 / lost_cat_max)) for c in lost_by_category],
        'by_location': [dict(l, pct=round(l['n'] * 100 / loc_max)) for l in by_location],
        'by_status': [{'status': s, 'label': dict(Item.Status.choices)[s], 'n': n}
                      for s, n in by_status.items()],
        'claim_status': [{'status': s, 'label': dict(Claim.Status.choices)[s], 'n': n,
                          'pct': round(n * 100 / (claims.count() or 1))}
                         for s, n in sorted(claim_status.items(), key=lambda kv: -kv[1])],
        'tier_dist': [{'tier': t, 'n': tier_counts.get(t, 0),
                       'pct': round(tier_counts.get(t, 0) * 100 / (len(scores) or 1))}
                      for t in ('high', 'medium', 'low')],
        'histogram': histogram,
        'avg_match': matches.aggregate(a=Avg('score'))['a'] or 0,
        'eda': {
            'photo_rate': round(with_photos * 100 / total) if total else 0,
            'reports_with_photos': with_photos,
            'median_day_gap': round(median(day_gaps), 1) if day_gaps else None,
            'mean_day_gap': round(mean(day_gaps), 1) if day_gaps else None,
            'mean_text_similarity': round(mean(text_scores) * 100) if text_scores else None,
            'wording_variation': round((1 - mean(text_scores)) * 100) if text_scores else None,
            'sample_size': len(observations),
        },
        'date_gap_distribution': date_gap_distribution,
        'trends': _weekly_trends(all_items, returned_claims),
        'lost_total': lost_total, 'found_total': found_total,
        'snapshots': ScoreSnapshot.objects.all()[:12],
        'manifest': manifest(),
        'thresholds': {'high': settings.MATCHING['TIER_HIGH'], 'medium': settings.MATCHING['TIER_MEDIUM']},
    }


@admin_required
def analytics(request):
    return render(request, 'dashboard/analytics.html', analytics_context(request))


# ---------------------------------------------------------------------------
# Administrator — users, categories and locations
# ---------------------------------------------------------------------------
@admin_required
def manage_users(request):
    if request.method == 'POST':
        user_id = request.POST.get('user_id', '')
        if not user_id.isdecimal():
            messages.error(request, 'Choose a person to update.')
            return redirect('dashboard:users')
        user = get_object_or_404(User, pk=user_id)
        new_role = request.POST.get('role')
        if user.pk == request.user.pk or user.is_superuser:
            messages.error(request, 'Your own role and superuser roles cannot be changed here.')
            return redirect('dashboard:users')
        if new_role in dict(User.Role.choices) and new_role != user.role:
            old = user.get_role_display()
            user.role = new_role
            user.save(update_fields=['role'])
            record_decision(request.user, ModerationDecision.Action.ROLE_CHANGED, subject_user=user,
                            note=f'{old} → {user.get_role_display()}')
            notify_user(user, kind='system', title='Your role was updated',
                        body=f'You are now a {user.get_role_display()} on LostLink.',
                        url='/dashboard/')
            messages.success(request, f'{user.display_name()} is now {user.get_role_display()}.')
        return redirect('dashboard:users')

    qs = User.objects.annotate(report_count=Count('items', distinct=True),
                               claim_count=Count('claims', distinct=True))
    q = request.GET.get('q', '').strip()
    role = request.GET.get('role', '')
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) |
                       Q(last_name__icontains=q) | Q(email__icontains=q) | Q(enrolment_no__icontains=q))
    if role in dict(User.Role.choices):
        qs = qs.filter(role=role)
    page = Paginator(qs.order_by('role', 'username'), 25).get_page(request.GET.get('page'))
    counts = {row['role']: row['n'] for row in User.objects.values('role').annotate(n=Count('id'))}
    return render(request, 'dashboard/users.html', {
        'users': page, 'page_obj': page, 'roles': User.Role.choices, 'q': q, 'sel_role': role,
        'counts': counts, 'total': User.objects.count(),
    })


@admin_required
@require_POST
def user_status(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk or user.is_superuser:
        messages.error(request, 'You cannot deactivate yourself or a superuser.')
        return redirect('dashboard:users')
    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])
    record_decision(request.user, ModerationDecision.Action.USER_STATUS_CHANGED, subject_user=user,
                    note='activated' if user.is_active else 'deactivated')
    messages.success(request, f'{user.display_name()} {"activated" if user.is_active else "deactivated"}.')
    return redirect('dashboard:users')


def _unique_slug(model, name, exclude_pk=None):
    base = slugify(name) or 'item'
    slug, n = base, 2
    while model.objects.filter(slug=slug).exclude(pk=exclude_pk).exists():
        slug, n = f'{base}-{n}', n + 1
    return slug


@admin_required
def taxonomy(request):
    """Manage the controlled vocabularies used for metadata matching."""
    category_form, location_form = CategoryForm(), LocationForm()
    if request.method == 'POST':
        kind = request.POST.get('kind')
        if kind == 'category':
            category_form = CategoryForm(request.POST)
            if category_form.is_valid():
                cat = category_form.save(commit=False)
                cat.slug = _unique_slug(Category, cat.name)
                cat.save()
                category_form.save_m2m()
                messages.success(request, f'Category “{cat.name}” added.')
                return redirect('dashboard:taxonomy')
        elif kind == 'location':
            location_form = LocationForm(request.POST)
            if location_form.is_valid():
                loc = location_form.save(commit=False)
                loc.slug = _unique_slug(Location, loc.name)
                loc.save()
                location_form.save_m2m()
                messages.success(request, f'Location “{loc.name}” added.')
                return redirect('dashboard:taxonomy')
    return render(request, 'dashboard/taxonomy.html', {
        'categories': Category.objects.annotate(n=Count('items')).prefetch_related('related'),
        'locations': Location.objects.annotate(n=Count('items')).prefetch_related('adjacent'),
        'category_form': category_form, 'location_form': location_form,
    })


@admin_required
def taxonomy_edit(request, kind, pk):
    model, form_class = {'category': (Category, CategoryForm),
                         'location': (Location, LocationForm)}.get(kind, (None, None))
    if model is None:
        messages.error(request, 'Unknown vocabulary.')
        return redirect('dashboard:taxonomy')
    obj = get_object_or_404(model, pk=pk)
    form = form_class(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.slug = _unique_slug(model, obj.name, exclude_pk=obj.pk)
        obj.save()
        form.save_m2m()
        messages.success(request, f'“{obj.name}” updated.')
        return redirect('dashboard:taxonomy')
    return render(request, 'dashboard/taxonomy_edit.html', {
        'form': form, 'kind': kind, 'obj': obj, 'usage': obj.items.count(),
    })


@admin_required
@require_POST
def taxonomy_delete(request, kind, pk):
    model = {'category': Category, 'location': Location}.get(kind)
    if model is None:
        messages.error(request, 'Unknown vocabulary.')
        return redirect('dashboard:taxonomy')
    obj = get_object_or_404(model, pk=pk)
    try:
        obj.delete()
        messages.success(request, f'“{obj.name}” deleted.')
    except ProtectedError:
        messages.error(request, f'“{obj.name}” is used by {obj.items.count()} report(s) and cannot be deleted.')
    return redirect('dashboard:taxonomy')


# ---------------------------------------------------------------------------
# Exports (synopsis Section 7.4 — CSV export + PDF report)
# ---------------------------------------------------------------------------
_CSV_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r', '\n')


def _csv_safe(value):
    """Neutralise spreadsheet formula injection (OWASP): a text cell that a
    spreadsheet could evaluate is prefixed with an apostrophe. Leading
    whitespace is ignored when deciding, because spreadsheets ignore it too."""
    if isinstance(value, str) and value and (value.startswith(_CSV_FORMULA_PREFIXES)
                                             or value.lstrip().startswith(_CSV_FORMULA_PREFIXES)):
        return "'" + value
    return value


@admin_required
def export_csv(request):
    """Report dataset as CSV. Private answers, contact fields and usernames
    are never included; the reporter is identified by numeric ID only."""
    start, end = _period(request)
    qs = _apply_period(Item.objects.all(), 'created_at', start, end)
    rtype = request.GET.get('type', '')
    status = request.GET.get('status', '')
    if rtype in (Item.Type.LOST, Item.Type.FOUND):
        qs = qs.filter(report_type=rtype)
    if status in dict(Item.Status.choices):
        qs = qs.filter(status=status)
    qs = (qs.select_related('category', 'location')
          .annotate(n_matches=Count('matches_as_lost', filter=Q(matches_as_lost__engine_version=settings.MATCHING['VERSION']), distinct=True) +
                    Count('matches_as_found', filter=Q(matches_as_found__engine_version=settings.MATCHING['VERSION']), distinct=True),
                    n_claims=Count('claims', distinct=True))
          .order_by('id'))
    suffix = f'_{start or "start"}_{end or "today"}' if (start or end) else ''
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="lostlink_reports{suffix}.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID', 'Type', 'Title', 'Category', 'Location', 'Event date', 'Approximate time',
                     'Status', 'Active', 'Brand', 'Color', 'Has photo', 'Sensitive photo', 'Reporter ID',
                     'Potential matches', 'Claims', 'Created', 'Updated'])
    for it in qs.iterator():
        row = [it.id, it.get_report_type_display(), it.title, it.category.name, it.location.name,
               it.event_date.isoformat(), it.event_time.strftime('%H:%M') if it.event_time else '',
               it.get_status_display(), 'yes' if it.is_active else 'no', it.brand, it.color,
               'yes' if it.image else 'no', 'yes' if it.image and it.photo_sensitive else 'no',
               it.reporter_id, it.n_matches, it.n_claims,
               timezone.localtime(it.created_at).strftime('%Y-%m-%d %H:%M'),
               timezone.localtime(it.updated_at).strftime('%Y-%m-%d %H:%M')]
        writer.writerow([_csv_safe(v) for v in row])
    return response


@admin_required
def export_pdf(request):
    """Periodic summary PDF (synopsis Section 7.4): monthly or custom range."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        messages.error(request, 'PDF export needs reportlab. Run: pip install reportlab')
        return redirect('dashboard:analytics')

    month = request.GET.get('month', '')
    if month:
        try:
            period = datetime.datetime.strptime(month, '%Y-%m')
        except ValueError:
            return HttpResponse('Use YYYY-MM for month.', status=400)
        start = period.date().replace(day=1)
        end = (start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        label = period.strftime('%B %Y')
    else:
        start, end = _period(request)
        if (request.GET.get('from') and not start) or (request.GET.get('to') and not end):
            return HttpResponse('Use YYYY-MM-DD for from/to.', status=400)
        label = (f'{start or "beginning"} to {end or "today"}' if (start or end) else 'All time')

    items = _apply_period(Item.objects.all(), 'created_at', start, end)
    claims = _apply_period(Claim.objects.all(), 'created_at', start, end)
    matches = _apply_period(_current_matches(), 'created_at', start, end)
    returned = _apply_period(Claim.objects.filter(status=Claim.Status.RETURNED), 'updated_at', start, end)

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title='LostLink Summary Report',
                            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    styles = getSampleStyleSheet()
    ink = colors.HexColor('#1B1F24')
    accent = colors.HexColor('#3A4A5C')
    line = colors.HexColor('#D8DCE2')
    stripe = colors.HexColor('#F3F5F7')
    small = ParagraphStyle('small', parent=styles['Normal'], fontSize=8.5, textColor=colors.HexColor('#5B6573'))
    story = [Paragraph('LostLink — Lost &amp; Found Summary Report', styles['Title']),
             Paragraph(f'Period: {label} · Generated {timezone.now():%d %B %Y, %H:%M}', styles['Normal']),
             Spacer(1, 6 * mm)]

    def table(rows, head_color, widths=(95 * mm, 55 * mm)):
        t = Table(rows, colWidths=list(widths))
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), head_color), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9.5),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [stripe, colors.white]),
            ('GRID', (0, 0), (-1, -1), 0.4, line), ('PADDING', (0, 0), (-1, -1), 5),
            ('ALIGN', (1, 1), (-1, -1), 'RIGHT')]))
        return t

    def section(title, rows, head_color=accent, empty='No data in this period.'):
        story.append(Paragraph(title, styles['Heading2']))
        if len(rows) == 1:
            rows = rows + [[empty, '']]
        story.append(table(rows, head_color))
        story.append(Spacer(1, 6 * mm))

    tier = {r['tier']: r['n'] for r in matches.values('tier').annotate(n=Count('id'))}
    cstat = {r['status']: r['n'] for r in claims.values('status').annotate(n=Count('id'))}
    section('Key figures', [
        ['Metric', 'Value'],
        ['Reports created', items.count()],
        ['Lost reports', items.filter(report_type=Item.Type.LOST).count()],
        ['Found reports', items.filter(report_type=Item.Type.FOUND).count()],
        ['Reports awaiting moderation', items.filter(status=Item.Status.PENDING, is_active=True).count()],
        ['Reports with a photograph', items.exclude(image='').exclude(image__isnull=True).count()],
        ['Potential matches (current engine)', matches.count()],
        ['  High / medium / low confidence', f"{tier.get('high', 0)} / {tier.get('medium', 0)} / {tier.get('low', 0)}"],
        ['Claims submitted', claims.count()],
        ['  Pending / approved / rejected',
         f"{sum(cstat.get(s, 0) for s in ('submitted', 'questions', 'answered'))} / "
         f"{cstat.get('approved', 0) + cstat.get('returned', 0)} / {cstat.get('rejected', 0)}"],
        ['Items returned (handover confirmed)', returned.count()],
        ['Registered users (all time)', User.objects.count()],
    ], ink)

    cat_rows = [['Category', 'Reports']] + [[c['category__name'], c['n']] for c in
                                            items.values('category__name').annotate(n=Count('id')).order_by('-n')[:8]]
    section('Most commonly reported categories', cat_rows)
    loc_rows = [['Location', 'Reports']] + [[l['location__name'], l['n']] for l in
                                            items.values('location__name').annotate(n=Count('id')).order_by('-n')[:8]]
    section('Campus locations with the most reports', loc_rows)

    story.append(Paragraph(
        'Match figures describe similarity scores produced by the configured engine '
        f"({manifest()['text']} text, {manifest()['image']} image, weights version {manifest()['version']}). "
        'They are not measurements of ownership or real-world accuracy. This report excludes '
        'personal contact details and private verification answers.', small))
    doc.build(story)
    buf.seek(0)
    resp = HttpResponse(buf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="lostlink_report_{(month or label).replace(" ", "_")}.pdf"'
    return resp
