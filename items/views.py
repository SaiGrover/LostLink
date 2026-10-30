from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.conf import settings

from matching.engine import extract_image_vector
from matching.models import Match
from matching.services import run_matching

from .forms import ItemForm
from .models import Category, Item, Location


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
def home(request):
    if request.user.is_authenticated:
        return redirect('items:dashboard')
    return render(request, 'items/home.html')


# ---------------------------------------------------------------------------
# Authenticated
# ---------------------------------------------------------------------------
@login_required
def dashboard(request):
    user = request.user
    my_items = user.items.all()
    context = {
        'my_items': my_items[:5],
        'my_item_count': my_items.count(),
        'my_lost': my_items.filter(report_type=Item.Type.LOST).count(),
        'my_found': my_items.filter(report_type=Item.Type.FOUND).count(),
        'my_returned': my_items.filter(status=Item.Status.RETURNED).count(),
        'my_claims': user.claims.all()[:5],
        'open_claims': user.claims.exclude(
            status__in=['approved', 'rejected']).count(),
    }
    # Surface the user's strongest current potential matches.
    my_ids = set(my_items.values_list('id', flat=True))
    top_matches = (Match.objects
                   .filter(engine_version=settings.MATCHING['VERSION'], score__gte=settings.MATCHING['MIN_SCORE'])
                   .filter(lost_item__is_active=True, found_item__is_active=True,
                           lost_item__status__in=['approved', 'claimed'],
                           found_item__status__in=['approved', 'claimed'], tier__in=['high', 'medium'])
                   .filter(Q(lost_item__in=my_ids) | Q(found_item__in=my_ids))
                   .select_related('lost_item', 'found_item',
                                   'lost_item__category', 'found_item__category')
                   .order_by('-score')[:4])
    rows = []
    for m in top_matches:
        mine = m.lost_item if m.lost_item_id in my_ids else m.found_item
        other = m.counterpart_for(mine)
        rows.append({'match': m, 'mine': mine, 'other': other})
    context['top_matches'] = rows
    return render(request, 'items/dashboard.html', context)


@login_required
def report_item(request):
    initial = {}
    rtype = request.GET.get('type')
    if rtype in (Item.Type.LOST, Item.Type.FOUND):
        initial['report_type'] = rtype
    initial.setdefault('event_date', timezone.now().date())

    if request.method == 'POST':
        form = ItemForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save(commit=False)
            item.reporter = request.user
            from matching.engine import text_similarity
            recent = list(Item.objects.filter(reporter=request.user, report_type=item.report_type,
                category=item.category, is_active=True).order_by('-created_at')[:50])
            if recent:
                similarities = text_similarity(item.description, [r.description for r in recent])
                best = max(range(len(recent)), key=lambda i:similarities[i])
                if similarities[best] >= .85:
                    item.duplicate_of = recent[best]
            item.save()
            # Compute the visual descriptor once, at submission time (Sec 4.4).
            from matching.features import prepare
            prepare(item)
            # Invoke the matching engine for the new report (Section 4.9).
            matches = run_matching(item)
            if matches:
                surfaced = [m for m in matches if m.tier in ('high', 'medium')]
                if surfaced:
                    messages.success(
                        request,
                        f'Report submitted. We found {len(surfaced)} potential '
                        f'match(es) — see them below.')
                else:
                    messages.success(request,
                                     'Report submitted and sent for review.')
            else:
                messages.success(request,
                                 'Report submitted and sent for review.')
            return redirect(item.get_absolute_url())
    else:
        form = ItemForm(initial=initial)
    return render(request, 'items/report_form.html', {'form': form})


@login_required
def item_detail(request, pk):
    item = get_object_or_404(
        Item.objects.select_related('category', 'location', 'reporter'), pk=pk)
    is_owner = item.reporter_id == request.user.id
    can_review = request.user.is_staff_role
    is_participant = item.claims.filter(claimant=request.user).exists()
    if (not item.is_active or item.status not in (Item.Status.APPROVED, Item.Status.CLAIMED)) and not (is_owner or can_review or is_participant):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('This report is not publicly available.')

    # Potential matches for this item, best first.
    if item.is_lost:
        matches = item.matches_as_lost.select_related('found_item').filter(engine_version=settings.MATCHING['VERSION'],score__gte=settings.MATCHING['MIN_SCORE'],found_item__is_active=True, found_item__status__in=['approved', 'claimed'])
    else:
        matches = item.matches_as_found.select_related('lost_item').filter(engine_version=settings.MATCHING['VERSION'],score__gte=settings.MATCHING['MIN_SCORE'],lost_item__is_active=True, lost_item__status__in=['approved', 'claimed'])
    match_rows = [{'match': m, 'other': m.counterpart_for(item)} for m in matches] if item.is_matchable else []

    existing_claim = item.claims.filter(claimant=request.user).first()

    return render(request, 'items/item_detail.html', {
        'item': item,
        'is_owner': is_owner,
        'can_review': can_review,
        'match_rows': match_rows,
        'existing_claim': existing_claim,
    })


@login_required
def my_items(request):
    items = request.user.items.select_related('category', 'location')
    return render(request, 'items/my_items.html', {'items': items})


@login_required
def withdraw_item(request, pk):
    item = get_object_or_404(Item, pk=pk, reporter=request.user)
    if request.method == 'POST':
        if item.claims.exclude(status='rejected').exists():
            messages.error(request,'This report has an active or completed claim. Contact a moderator before withdrawing it.')
            return redirect(item.get_absolute_url())
        item.is_active = False
        item.save(update_fields=['is_active'])
        messages.info(request, f'“{item.title}” has been withdrawn.')
    return redirect('items:mine')


# ---------------------------------------------------------------------------
# Search (server-rendered page + AJAX endpoint, synopsis Section 4.13)
# ---------------------------------------------------------------------------
def _search_queryset(request):
    qs = (Item.objects.filter(is_active=True)
          .filter(status__in=[Item.Status.APPROVED, Item.Status.CLAIMED])
          .select_related('category', 'location', 'reporter'))
    q = request.GET.get('q', '').strip()
    rtype = request.GET.get('type', '').strip()
    category = request.GET.get('category', '').strip()
    location = request.GET.get('location', '').strip()

    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) |
                       Q(brand__icontains=q) | Q(color__icontains=q))
    if rtype in (Item.Type.LOST, Item.Type.FOUND):
        qs = qs.filter(report_type=rtype)
    if category:
        qs = qs.filter(category__slug=category)
    if location:
        qs = qs.filter(location__slug=location)
    from django.utils.dateparse import parse_date
    for parameter, lookup in [('date_from', 'event_date__gte'), ('date_to', 'event_date__lte')]:
        try:
            value = parse_date(request.GET.get(parameter, ''))
        except ValueError:
            value = None
        if value:
            qs = qs.filter(**{lookup: value})
    return qs


def search(request):
    qs = _search_queryset(request)
    page = Paginator(qs, 24).get_page(request.GET.get('page'))
    context = {
        'items': page,
        'page_obj': page,
        'total': qs.count(),
        'categories': Category.objects.all(),
        'locations': Location.objects.all(),
        'q': request.GET.get('q', ''),
        'sel_type': request.GET.get('type', ''),
        'sel_category': request.GET.get('category', ''),
        'sel_location': request.GET.get('location', ''),
    }
    return render(request, 'items/search.html', context)


def browse(request, report_type):
    """Dedicated Lost / Found browse page (a locked-type live-search view)."""
    # Force the type filter so this page only ever shows lost OR found.
    request.GET = request.GET.copy()
    request.GET['type'] = report_type
    qs = _search_queryset(request)
    page = Paginator(qs, 24).get_page(request.GET.get('page'))
    is_lost = report_type == Item.Type.LOST
    context = {
        'items': page,
        'page_obj': page,
        'total': qs.count(),
        'categories': Category.objects.all(),
        'locations': Location.objects.all(),
        'q': request.GET.get('q', ''),
        'locked_type': report_type,
        'is_lost': is_lost,
        'sel_category': request.GET.get('category', ''),
        'sel_location': request.GET.get('location', ''),
        'page_kind': 'lost' if is_lost else 'found',
    }
    return render(request, 'items/browse.html', context)


def search_api(request):
    """JSON/HTML fragment endpoint powering live AJAX search (Section 4.13)."""
    qs = _search_queryset(request)
    total = qs.count()
    items = Paginator(qs, 24).get_page(request.GET.get('page'))
    html = render_to_string('items/partials/result_grid.html',
                            {'items': items, 'page_obj': items}, request=request)
    return JsonResponse({'html': html, 'total': total})


@login_required
def edit_item(request, pk):
    item = get_object_or_404(Item, pk=pk, reporter=request.user, is_active=True)
    if item.claims.exclude(status='rejected').exists():
        messages.error(request,'A report with an ongoing claim cannot be edited. Contact a moderator.')
        return redirect(item.get_absolute_url())
    form = ItemForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        item.status = Item.Status.PENDING
        item.feature_version = ''
        item.save()
        from matching.features import prepare
        prepare(item)
        messages.success(request,'Changes saved and sent for moderator review.')
        return redirect(item.get_absolute_url())
    return render(request,'items/report_form.html',{'form':form,'editing':True})


def serve_media(request, path):
    from django.http import FileResponse, Http404
    from django.core.exceptions import PermissionDenied
    item = get_object_or_404(Item, image=path)
    public = item.is_active and item.status in ('approved','claimed')
    user = request.user
    allowed = user.is_authenticated and (user.is_staff_role or item.reporter_id==user.id or item.claims.filter(claimant=user).exists())
    if not (public or allowed):
        raise PermissionDenied('This image is awaiting review or is no longer public.')
    try:
        response = FileResponse(item.image.open('rb'))
    except (FileNotFoundError, OSError):
        raise Http404('Image unavailable')
    response['Cache-Control'] = 'private, no-store'
    return response
