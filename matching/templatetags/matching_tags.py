"""Template helpers for match explanations and moderator diagnostics.

Kept in the matching app so pages owned by other apps only need
``{% load matching_tags %}`` and one tag, not new view context.
"""
from django import template
from django.conf import settings

register = template.Library()

TIER_THRESHOLDS_NOTE = ('Strong above {high}, moderate {medium}–{high}, weak below {medium}: heuristic '
                        'display thresholds on a weighted similarity score, not probabilities.')


@register.simple_tag
def tier_thresholds_note():
    return TIER_THRESHOLDS_NOTE.format(high=round(settings.MATCHING['TIER_HIGH'] * 100),
                                       medium=round(settings.MATCHING['TIER_MEDIUM'] * 100))


@register.simple_tag
def feature_status(item):
    """Feature-processing state of a report (moderator diagnostics), or None."""
    from matching.models import ItemFeatures
    if item is None or not getattr(item, 'pk', None):
        return None
    features = ItemFeatures.objects.filter(item_id=item.pk).first()
    if features is None:
        return {'pending': True, 'has_photo': bool(item.image)}
    return {
        'pending': False, 'has_photo': bool(item.image),
        'text_state': features.get_text_state_display(), 'text_model': features.text_model or '—',
        'image_state': features.get_image_state_display(), 'image_state_key': features.image_state,
        'image_model': features.image_model_label if features.image_model else '—',
        'image_model_detail': features.image_model_detail,
        'attempts': features.attempts, 'last_error': features.last_error,
        'last_attempt_at': features.last_attempt_at, 'needs_retry': features.needs_retry,
    }


@register.simple_tag
def match_history(item, limit=10):
    """Retired (no longer actionable) matches of a report, newest first."""
    from django.db.models import Q

    from matching.models import Match
    if item is None or not getattr(item, 'pk', None):
        return []
    rows = (Match.all_objects.filter(Q(lost_item_id=item.pk) | Q(found_item_id=item.pk))
            .filter(status=Match.Status.RETIRED)
            .select_related('lost_item', 'found_item').order_by('-retired_at')[:limit])
    return [{'match': m, 'other': m.found_item if m.lost_item_id == item.pk else m.lost_item}
            for m in rows]
