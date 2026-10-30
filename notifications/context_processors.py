def notifications(request):
    """Expose the unread-notification count to every template."""
    if not request.user.is_authenticated:
        return {'unread_notifications': 0, 'recent_notifications': []}
    qs = request.user.notifications.all()
    return {
        'unread_notifications': qs.filter(is_read=False).count(),
        'recent_notifications': qs[:6],
    }
