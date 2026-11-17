"""Role-based access control decorators (synopsis Section 3.2, Section 8).

Each consequential capability is gated on the user's role rather than a single
undifferentiated ``is_authenticated`` check.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect


def _check(request, predicate):
    user = request.user
    if not user.is_authenticated:
        return False, redirect_to_login(request.get_full_path())
    if not predicate(user):
        raise PermissionDenied('You do not have access to this area.')
    return True, None


def role_required(*roles):
    """Allow the view only for users whose ``role`` is in ``roles``.

    Superusers and administrators always pass.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                messages.info(request, 'Please sign in to continue.')
                return redirect_to_login(request.get_full_path())
            allowed = user.is_administrator or user.role in roles
            if not allowed:
                raise PermissionDenied('You do not have access to this area.')
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def moderator_required(view_func):
    """Moderators and administrators only."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            messages.info(request, 'Please sign in to continue.')
            return redirect_to_login(request.get_full_path())
        if not user.is_staff_role:
            raise PermissionDenied('Moderator access required.')
        return view_func(request, *args, **kwargs)
    return _wrapped


def admin_required(view_func):
    """Administrators (or Django superusers) only."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            messages.info(request, 'Please sign in to continue.')
            return redirect_to_login(request.get_full_path())
        if not user.is_administrator:
            raise PermissionDenied('Administrator access required.')
        return view_func(request, *args, **kwargs)
    return _wrapped


UNVERIFIED_MESSAGE = ('Confirm your email address first: reporting, editing and flagging '
                      'reports and claiming items are open to verified campus accounts only.')


def _wants_json(request):
    accept = request.headers.get('Accept', '')
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or ('application/json' in accept and 'text/html' not in accept))


def verified_required(view_func):
    """Campus-restricted actions: signed in *and* campus verified.

    * Anonymous visitors are sent to the sign-in page (like ``login_required``).
    * Signed-in accounts whose ``is_campus_verified`` is false get a warning
      and are redirected to the "confirm your email" page (JSON/AJAX callers
      get a 403 JSON body instead). Nothing in the wrapped view runs, for GET
      and POST alike, so this is a server-side guard.
    * Staff (moderators, administrators, Django staff/superusers) always pass;
      see ``User.is_campus_verified``.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            messages.info(request, 'Please sign in to continue.')
            return redirect_to_login(request.get_full_path())
        if not user.is_campus_verified:
            if _wants_json(request):
                return JsonResponse({'error': 'verification_required',
                                     'detail': UNVERIFIED_MESSAGE}, status=403)
            messages.warning(request, UNVERIFIED_MESSAGE)
            return redirect('accounts:verification_pending')
        return view_func(request, *args, **kwargs)
    return _wrapped
