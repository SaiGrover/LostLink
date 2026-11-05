from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST


def _wants_json(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
        'application/json' in request.headers.get('Accept', '')


@login_required
def inbox(request):
    qs = request.user.notifications.select_related('item', 'item__category')
    kind = request.GET.get('kind', '')
    if request.GET.get('unread') == '1':
        qs = qs.filter(is_read=False)
    if kind in ('match', 'claim', 'report', 'system'):
        qs = qs.filter(kind=kind)
    page = Paginator(qs, 20).get_page(request.GET.get('page'))
    return render(request, 'notifications/inbox.html', {
        'notes': page, 'page_obj': page,
        'unread_only': request.GET.get('unread') == '1', 'sel_kind': kind,
        'unread_total': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
def open_note(request, pk):
    note = get_object_or_404(request.user.notifications, pk=pk)
    if not note.is_read:
        note.is_read = True
        note.save(update_fields=['is_read'])
    return redirect(note.url or 'notifications:inbox')


@login_required
@require_POST
def mark_read(request, pk):
    """Mark a single notification read (or unread with ``unread=1``)."""
    note = get_object_or_404(request.user.notifications, pk=pk)
    note.is_read = request.POST.get('unread') != '1'
    note.save(update_fields=['is_read'])
    if _wants_json(request):
        return JsonResponse({'id': note.pk, 'is_read': note.is_read,
                             'unread': request.user.notifications.filter(is_read=False).count()})
    return redirect('notifications:inbox')


@login_required
@require_POST
def mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    if _wants_json(request):
        return JsonResponse({'unread': 0})
    return redirect('notifications:inbox')


@login_required
@require_GET
def unread_count(request):
    """Lightweight poll endpoint for the live badge."""
    return JsonResponse({'unread': request.user.notifications.filter(is_read=False).count()})
