from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST


@login_required
def inbox(request):
    notes = request.user.notifications.select_related('item', 'item__category')
    return render(request, 'notifications/inbox.html', {'notes': notes})


@login_required
def open_note(request, pk):
    note = get_object_or_404(request.user.notifications, pk=pk)
    note.is_read = True
    note.save(update_fields=['is_read'])
    return redirect(note.url or 'notifications:inbox')


@login_required
@require_POST
def mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect('notifications:inbox')
