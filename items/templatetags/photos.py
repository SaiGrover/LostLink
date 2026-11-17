"""Template helpers for photo privacy.

The media view enforces the policy on every photo URL; these tags only let
templates *explain* it (a "blurred for privacy" note) and offer moderators the
sensitive-photo hint. See items/media.py.
"""
from django import template
from django.contrib.auth.models import AnonymousUser

from items import media

register = template.Library()


def _viewer(context):
    request = context.get('request')
    return getattr(request, 'user', None) or AnonymousUser()


@register.simple_tag(takes_context=True)
def photo_restricted(context, item):
    """True when this viewer receives the blurred preview of ``item``'s photo."""
    if not item or not getattr(item, 'image', None) or not item.photo_sensitive:
        return False
    return media.photo_variant(_viewer(context), item) == media.PREVIEW


@register.filter
def photo_privacy_hint(item):
    """Moderator hint: the report looks like it may show personal details but
    its photo is not restricted and no moderator has ruled on it yet."""
    return bool(item and item.image and not item.photo_sensitive and item.photo_reviewed_by_id is None
                and media.suggests_sensitive(item.title, item.description, item.category))
