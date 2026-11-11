"""Role-based access control decorators (synopsis Section 3.2, Section 8).

Each consequential capability is gated on the user's role rather than a single
undifferentiated ``is_authenticated`` check.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


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
