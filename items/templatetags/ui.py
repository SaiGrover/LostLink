"""Small presentation helpers shared by every LostLink template.

``{% icon 'search' %}`` renders a symbol from the inline sprite in
``templates/partials/icons.svg`` so all icons share one stroke style.
"""
from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def icon(name, cls=''):
    return format_html(
        '<svg class="icon {}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>',
        cls, name)


@register.filter
def report_id(pk):
    """Human-facing report reference, e.g. ``LL-0042``."""
    try:
        return f'LL-{int(pk):04d}'
    except (TypeError, ValueError):
        return ''


@register.filter
def claim_id(pk):
    try:
        return f'CL-{int(pk):04d}'
    except (TypeError, ValueError):
        return ''


@register.filter
def percent_of(value, total):
    """Integer percentage of ``value`` in ``total`` (0 when total is 0)."""
    try:
        total = float(total)
        return round(float(value) * 100 / total) if total else 0
    except (TypeError, ValueError):
        return 0


@register.filter
def times100(value):
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return 0


@register.simple_tag(takes_context=True)
def query_with(context, **kwargs):
    """Current query string with some parameters replaced (``None`` removes)."""
    params = context['request'].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f'?{encoded}' if encoded else '?'


@register.simple_tag(takes_context=True)
def qs_without(context, key='page'):
    """Current query string (no leading ``?``) without ``key``; for pagination."""
    params = context['request'].GET.copy()
    params.pop(key, None)
    return params.urlencode()
