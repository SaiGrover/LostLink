from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from claims.models import Claim
from matching.models import Match
from matching.services import clear_matches, run_matching

from .forms import FlagForm, ItemForm
from .models import Category, Item, Location

PUBLIC_STATUSES = (Item.Status.APPROVED, Item.Status.CLAIMED)


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
def home(request):
    if request.user.is_authenticated:
        return redirect('items:dashboard')
    public = (Item.objects.filter(is_active=True, status__in=PUBLIC_STATUSES)
              .select_related('category', 'location'))
    with_photos = list(public.exclude(image='').exclude(image__isnull=True)[:12])
    # The strongest current match between two public reports with photos:
    # a real, engine-computed example for the landing page's matching story.
    featured = (_current_matches().exclude(lost_item__image='').exclude(found_item__image='')
                .select_related('lost_item', 'found_item', 'lost_item__category',
                                'found_item__category', 'lost_item__location', 'found_item__location')
                .order_by('-score').first())
    return render(request, 'items/home.html', {
        'categories': Category.objects.all()[:10],
        'weights': settings.MATCHING['WEIGHTS'],
        'weight_rows': [(k, label, settings.MATCHING['WEIGHTS'][k]) for k, label in (
            ('text', 'Description'), ('image', 'Photograph'), ('location', 'Campus zone'),
            ('category', 'Category'), ('date', 'Date'), ('attribute', 'Brand & colour'))],
        'hanging': with_photos[:3],
        'strip': with_photos[:10],
        'featured': featured,
        'counts': {
            'reports': public.count(),
            'matches': _current_matches().filter(tier__in=['high', 'medium']).count(),
            'returned': Item.objects.filter(status=Item.Status.RETURNED).count(),
            'zones': Location.objects.count(),
        },
    })


def _current_matches():
    return Match.objects.filter(engine_version=settings.MATCHING['VERSION'],
                                score__gte=settings.MATCHING['MIN_SCORE'],
                                lost_item__is_active=True, found_item__is_active=True,
                                lost_item__status__in=PUBLIC_STATUSES,
                                found_item__status__in=PUBLIC_STATUSES)


# ---------------------------------------------------------------------------
# Authenticated
# ---------------------------------------------------------------------------
@login_required
def dashboard(request):
    user = request.user
    my_items = user.items.select_related('category', 'location')
    my_ids = set(my_items.values_list('id', flat=True))

    top_matches = (_current_matches()
                   .filter(tier__in=['high', 'medium'])
                   .filter(Q(lost_item__in=my_ids) | Q(found_item__in=my_ids))
                   .select_related('lost_item', 'found_item',
                                   'lost_item__category', 'found_item__category',
                                   'lost_item__location', 'found_item__location')
                   .order_by('-score')[:8])
    rows = []
    for m in top_matches:
        mine = m.lost_item if m.lost_item_id in my_ids else m.found_item
        rows.append({'match': m, 'mine': mine, 'other': m.counterpart_for(mine)})

    # Claims I made, plus claims other people made on my reports.
    my_claims = user.claims.select_related('item', 'item__category')
    incoming = (Claim.objects.filter(item__reporter=user)
                .select_related('item', 'claimant').order_by('-updated_at'))
    active_cases = (my_claims.filter(status__in=Claim.OPEN_STATUSES + ('approved',))
                    .order_by('-updated_at'))

    context = {
        'my_items': my_items[:8],
        'my_item_count': my_items.count(),
        'my_lost': my_items.filter(report_type=Item.Type.LOST).count(),
        'my_found': my_items.filter(report_type=Item.Type.FOUND).count(),
        'my_pending': my_items.filter(status=Item.Status.PENDING, is_active=True).count(),
        'my_returned': my_items.filter(status=Item.Status.RETURNED).count(),
        'match_count': (_current_matches().filter(tier__in=['high', 'medium'])
                        .filter(Q(lost_item__in=my_ids) | Q(found_item__in=my_ids)).count()),
        'my_claims': my_claims[:5],
        'active_cases': active_cases[:5],
        'incoming_claims': incoming.filter(status__in=Claim.OPEN_STATUSES + ('approved',))[:5],
        'open_claims': active_cases.count(),
        'awaiting_answers': my_claims.filter(status='questions', questions__answer='').distinct()[:5],
        'top_matches': rows,
        'recent_notes': user.notifications.select_related('item')[:5],
    }
    return render(request, 'items/dashboard.html', context)


@login_required
def report_item(request):
    initial = {}
    rtype = request.GET.get('type')
    if rtype in (Item.Type.LOST, Item.Type.FOUND):
        initial['report_type'] = rtype
    initial.setdefault('event_date', timezone.localdate())

    if request.method == 'POST':
        form = ItemForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save(commit=False)
            item.reporter = request.user
            # Flag near-duplicate reports by the same user for moderator review
            # rather than silently merging them (synopsis Section 4.2).
            from matching.engine import text_similarity
            recent = list(Item.objects.filter(
                reporter=request.user, report_type=item.report_type,
                category=item.category, is_active=True).order_by('-created_at')[:50])
            if recent:
                sims = text_similarity(item.description, [r.description for r in recent])
                best = max(range(len(recent)), key=lambda i: sims[i])
                if sims[best] >= .85:
                    item.duplicate_of = recent[best]
            item.save()
            from matching.features import prepare
            prepare(item)  # features once at submission time (Section 4.4)
            messages.success(request, 'Report submitted. A moderator will review it before it '
                                      'appears in search and matching.')
            return redirect(item.get_absolute_url())
    else:
        form = ItemForm(initial=initial)
    return render(request, 'items/report_form.html', {'form': form})


@login_required
def item_detail(request, pk):
    item = get_object_or_404(
        Item.objects.select_related('category', 'location', 'reporter'), pk=pk)
    user = request.user
    is_owner = item.reporter_id == user.id
    can_review = user.is_staff_role
    is_participant = item.claims.filter(claimant=user).exists()
    if not item.is_public and not (is_owner or can_review or is_participant):
        raise PermissionDenied('This report is not publicly available.')

    if item.is_lost:
        matches = (item.matches_as_lost.select_related('found_item', 'found_item__category',
                                                       'found_item__location')
                   .filter(engine_version=settings.MATCHING['VERSION'],
                           score__gte=settings.MATCHING['MIN_SCORE'],
                           found_item__is_active=True, found_item__status__in=PUBLIC_STATUSES))
    else:
        matches = (item.matches_as_found.select_related('lost_item', 'lost_item__category',
                                                        'lost_item__location')
                   .filter(engine_version=settings.MATCHING['VERSION'],
                           score__gte=settings.MATCHING['MIN_SCORE'],
                           lost_item__is_active=True, lost_item__status__in=PUBLIC_STATUSES))
    match_rows = [{'match': m, 'other': m.counterpart_for(item)}
                  for m in matches] if item.is_matchable else []
    if not (is_owner or can_review):
        # Other users only see matches surfaced for discovery (medium/high);
        # low-tier candidates stay search-only (synopsis Section 4.9).
        match_rows = [r for r in match_rows if r['match'].tier != 'low']
    if is_owner and item.is_active:
        from django.urls import reverse
        for row in match_rows:
            if row['other'].is_active:
                row['action_url'] = f"{reverse('claims:start', args=[row['other'].pk])}?match={row['match'].pk}"
                row['action_label'] = 'This is mine — start a claim' if item.is_lost else 'Offer to return it'

    existing_claim = item.claims.filter(claimant=user).first()
    claims = item.claims.select_related('claimant') if (is_owner or can_review) else []
    my_flag = item.flags.filter(reporter=user, resolved_at__isnull=True).first()

    photo_credit = None
    if item.is_demo_photo:
        import json
        from django.utils.html import strip_tags
        credit_file = settings.BASE_DIR / 'docs/DEMO_IMAGE_CREDITS.json'
        if credit_file.exists():
            rows = json.loads(credit_file.read_text(encoding='utf-8'))
            credit = next((row for row in rows if row.get('item_title') == item.title), None)
            if credit:
                photo_credit = {'source': credit['source'], 'artist': strip_tags(credit['artist']),
                                'license': credit['license'], 'license_url': credit['license_url']}

    return render(request, 'items/item_detail.html', {
        'item': item,
        'is_owner': is_owner,
        'can_review': can_review,
        'match_rows': match_rows,
        'existing_claim': existing_claim,
        'claims': claims,
        'photo_credit': photo_credit,
        'flag_form': FlagForm(),
        'my_flag': my_flag,
        'open_flags': item.flags.filter(resolved_at__isnull=True).select_related('reporter')
        if can_review else [],
        'decisions': item.moderation_decisions.select_related('actor')[:6] if can_review else [],
        'can_flag': (not is_owner and item.is_public and my_flag is None),
    })


@login_required
@require_POST
def flag_item(request, pk):
    item = get_object_or_404(Item, pk=pk)
    if item.reporter_id == request.user.id or not item.is_public:
        raise PermissionDenied('You cannot flag this report.')
    if item.flags.filter(reporter=request.user, resolved_at__isnull=True).exists():
        messages.info(request, 'You have already flagged this report; a moderator will review it.')
        return redirect(item.get_absolute_url())
    form = FlagForm(request.POST)
    if form.is_valid():
        flag = form.save(commit=False)
        flag.item = item
        flag.reporter = request.user
        flag.save()
        messages.success(request, 'Thanks — the report has been flagged for moderator review.')
    else:
        messages.error(request, 'Choose a reason for the flag.')
    return redirect(item.get_absolute_url())


@login_required
def my_items(request):
    qs = request.user.items.select_related('category', 'location')
    rtype = request.GET.get('type', '')
    status = request.GET.get('status', '')
    if rtype in (Item.Type.LOST, Item.Type.FOUND):
        qs = qs.filter(report_type=rtype)
    if status == 'active':
        qs = qs.filter(is_active=True).exclude(status=Item.Status.RETURNED)
    elif status == 'returned':
        qs = qs.filter(status=Item.Status.RETURNED)
    elif status == 'withdrawn':
        qs = qs.filter(is_active=False).exclude(status=Item.Status.RETURNED)
    page = Paginator(qs, 12).get_page(request.GET.get('page'))
    return render(request, 'items/my_items.html', {
        'items': page, 'page_obj': page, 'sel_type': rtype, 'sel_status': status,
        'total': qs.count(),
    })


@login_required
@require_POST
def withdraw_item(request, pk):
    item = get_object_or_404(Item, pk=pk, reporter=request.user)
    if item.claims.exclude(status='rejected').exists():
        messages.error(request, 'This report has an active or completed claim. '
                                'Contact a moderator before withdrawing it.')
        return redirect(item.get_absolute_url())
    item.is_active = False
    item.save(update_fields=['is_active'])
    clear_matches(item)
    # A withdrawn report is no longer public, so its open flags need no review.
    item.flags.filter(resolved_at__isnull=True).update(resolved_at=timezone.now())
    messages.info(request, f'“{item.title}” has been withdrawn.')
    return redirect('items:mine')


# ---------------------------------------------------------------------------
# Search (server-rendered page + AJAX endpoint, synopsis Section 4.13)
# ---------------------------------------------------------------------------
SORT_OPTIONS = {
    'newest': ('-created_at',),
    'oldest': ('created_at',),
    'event_desc': ('-event_date', '-created_at'),
    'event_asc': ('event_date', 'created_at'),
    'title': ('title',),
}
PAGE_SIZE = 24


def _clean_date(value):
    try:
        return parse_date((value or '').strip())
    except ValueError:
        return None


def search_filters(request):
    """Validated search parameters from the query string."""
    g = request.GET
    rtype = g.get('type', '').strip()
    status = g.get('status', '').strip()
    sort = g.get('sort', '').strip()
    return {
        'q': g.get('q', '').strip()[:120],
        'type': rtype if rtype in (Item.Type.LOST, Item.Type.FOUND) else '',
        'category': g.get('category', '').strip()[:70],
        'location': g.get('location', '').strip()[:90],
        'status': status if status in ('approved', 'claimed') else '',
        'date_from': _clean_date(g.get('date_from')),
        'date_to': _clean_date(g.get('date_to')),
        'sort': sort if sort in SORT_OPTIONS else 'newest',
    }


def _search_queryset(request, filters=None):
    f = filters or search_filters(request)
    qs = (Item.objects.filter(is_active=True, status__in=PUBLIC_STATUSES)
          .select_related('category', 'location', 'reporter'))
    if f['q']:
        q = f['q']
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) |
                       Q(brand__icontains=q) | Q(color__icontains=q))
    if f['type']:
        qs = qs.filter(report_type=f['type'])
    if f['category']:
        qs = qs.filter(category__slug=f['category'])
    if f['location']:
        qs = qs.filter(location__slug=f['location'])
    if f['status']:
        qs = qs.filter(status=f['status'])
    if f['date_from']:
        qs = qs.filter(event_date__gte=f['date_from'])
    if f['date_to']:
        qs = qs.filter(event_date__lte=f['date_to'])
    return qs.order_by(*SORT_OPTIONS[f['sort']])


def _search_context(request, locked_type=None):
    if locked_type:
        request.GET = request.GET.copy()
        request.GET['type'] = locked_type
    f = search_filters(request)
    qs = _search_queryset(request, f)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get('page'))
    query_without_page = request.GET.copy()
    query_without_page.pop('page', None)
    active_filters = sum(1 for k in ('q', 'category', 'location', 'status', 'date_from', 'date_to')
                         if f[k]) + (1 if f['type'] and not locked_type else 0)
    return {
        'items': page, 'page_obj': page, 'total': page.paginator.count,
        'categories': Category.objects.all(), 'locations': Location.objects.all(),
        'filters': f, 'q': f['q'], 'sel_type': f['type'], 'sel_category': f['category'],
        'sel_location': f['location'], 'sel_status': f['status'], 'sel_sort': f['sort'],
        'date_from': f['date_from'].isoformat() if f['date_from'] else '',
        'date_to': f['date_to'].isoformat() if f['date_to'] else '',
        'sort_options': [('newest', 'Newest first'), ('oldest', 'Oldest first'),
                         ('event_desc', 'Most recent event'), ('event_asc', 'Earliest event'),
                         ('title', 'Title A–Z')],
        'active_filters': active_filters,
        'manual_mode': request.GET.get('study') == 'manual',
        'querystring': query_without_page.urlencode(),
    }


def search(request):
    context = _search_context(request)
    context['page_kind'] = 'all'
    return render(request, 'items/search.html', context)


def browse(request, report_type):
    """Dedicated Lost / Found browse page (a locked-type live-search view)."""
    context = _search_context(request, locked_type=report_type)
    is_lost = report_type == Item.Type.LOST
    context.update({'locked_type': report_type, 'is_lost': is_lost,
                    'page_kind': 'lost' if is_lost else 'found'})
    return render(request, 'items/browse.html', context)


def search_api(request):
    """HTML-fragment + JSON endpoint powering live AJAX search (Section 4.13).

    Returns the rendered result grid, the total count and pagination state so
    the client never needs to reload the page.
    """
    f = search_filters(request)
    qs = _search_queryset(request, f)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get('page'))
    query_without_page = request.GET.copy()
    query_without_page.pop('page', None)
    html = render_to_string('items/partials/result_grid.html',
                            {'items': page, 'page_obj': page,
                             'querystring': query_without_page.urlencode(),
                             'manual_mode': False}, request=request)
    return JsonResponse({
        'html': html, 'total': page.paginator.count,
        'page': page.number, 'pages': page.paginator.num_pages,
        'filters': {k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in f.items()},
    })


@login_required
def edit_item(request, pk):
    item = get_object_or_404(Item, pk=pk, reporter=request.user, is_active=True)
    if item.claims.exclude(status='rejected').exists():
        messages.error(request, 'A report with an ongoing claim cannot be edited. Contact a moderator.')
        return redirect(item.get_absolute_url())
    form = ItemForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.status = Item.Status.PENDING
        item.feature_version = ''
        item.save()
        clear_matches(item)  # content changed: stale scores must not survive
        from matching.features import prepare
        prepare(item)
        messages.success(request, 'Changes saved and sent for moderator review.')
        return redirect(item.get_absolute_url())
    return render(request, 'items/report_form.html', {'form': form, 'editing': True, 'item': item})


def serve_media(request, path):
    """Protected media: item photographs are only served to users allowed to
    see the report (public once approved; otherwise owner, participants and
    staff). Never exposed through a static file URL."""
    from django.http import FileResponse, Http404
    item = get_object_or_404(Item, image=path)
    user = request.user
    allowed = user.is_authenticated and (
        user.is_staff_role or item.reporter_id == user.id or
        item.claims.filter(claimant=user).exists())
    if not (item.is_public or allowed):
        raise PermissionDenied('This image is awaiting review or is no longer public.')
    try:
        response = FileResponse(item.image.open('rb'))
    except (FileNotFoundError, OSError):
        raise Http404('Image unavailable')
    response['Cache-Control'] = 'private, no-store'
    return response
