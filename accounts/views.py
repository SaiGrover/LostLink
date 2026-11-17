from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from . import tokens
from .emails import send_duplicate_registration_notice, send_verification_email, site_base_url
from .forms import LoginForm, ProfileForm, RegisterForm, ResendVerificationForm
from .policy import (allowed_domains_display, email_domain_allowed, normalise_email,
                     normalise_username, rate_limit)
from .ratelimit import client_ip, is_limited, reset

User = get_user_model()

TOO_MANY = 429


def _limited(key, name, increment=True):
    limit, window = rate_limit(name)
    return is_limited(key, limit, window, increment=increment)


# ---------------------------------------------------------------------------
# Registration and email verification
# ---------------------------------------------------------------------------
@sensitive_post_parameters('password1', 'password2')
@never_cache
def register(request):
    if request.user.is_authenticated:
        return redirect('items:dashboard')
    context = {'domains': allowed_domains_display()}
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if _limited(f'register:ip:{client_ip(request)}', 'register_ip'):
            context.update(form=form, rate_limited=True)
            return render(request, 'accounts/register.html', context, status=TOO_MANY)
        if form.is_valid():
            email = form.cleaned_data['email']
            if _limited(f'register:email:{email}', 'register_email'):
                # Counted for every attempt naming this address, whether or
                # not it has an account, so the refusal reveals nothing.
                context.update(form=form, rate_limited=True)
                return render(request, 'accounts/register.html', context, status=TOO_MANY)
            existing = form.existing_user
            if existing is not None:
                # Same outcome page as a new sign-up; hash the password anyway
                # so the response time does not give the duplicate away.
                make_password(form.cleaned_data['password1'])
                send_duplicate_registration_notice(request, existing)
            else:
                user = form.save()
                send_verification_email(request, user)
            return redirect('accounts:verification_sent')
    else:
        form = RegisterForm()
    context['form'] = form
    return render(request, 'accounts/register.html', context)


def verification_sent(request):
    """Shown after sign-up and after an anonymous resend request. Identical for
    new, duplicate and unknown addresses."""
    return render(request, 'accounts/verification_sent.html', {
        'hours': max(1, tokens.max_age() // 3600)})


@login_required
def verification_pending(request):
    """The signed-in account's verification state: pending or complete."""
    user = request.user
    return render(request, 'accounts/verification_pending.html', {
        'verified': user.is_campus_verified,
        'domain_ok': not user.email or email_domain_allowed(user.email),
        'domains': allowed_domains_display(),
        'hours': max(1, tokens.max_age() // 3600),
    })


@never_cache
@require_http_methods(['GET', 'POST'])
def verify_email(request, token):
    status, user = tokens.check_token(token)
    if status != tokens.OK:
        return render(request, 'accounts/verify_email.html', {'state': status}, status=400)
    if not email_domain_allowed(user.email):
        return render(request, 'accounts/verify_email.html', {
            'state': 'domain', 'domains': allowed_domains_display()}, status=400)
    if request.method == 'POST':
        # Confirm with a POST so link scanners that prefetch the URL do not
        # spend the token; the change itself is CSRF-protected.
        user.mark_email_verified(User.VerificationMethod.EMAIL)
        return redirect('accounts:verification_complete')
    return render(request, 'accounts/verify_email.html', {'state': 'confirm', 'account': user})


def verification_complete(request):
    return render(request, 'accounts/verification_complete.html')


@never_cache
@require_http_methods(['GET', 'POST'])
def resend_verification(request):
    user = request.user
    if user.is_authenticated:
        if request.method != 'POST' or user.is_campus_verified:
            return redirect('accounts:verification_pending')
        if _limited(f'resend:ip:{client_ip(request)}', 'resend_ip') or \
                _limited(f'resend:user:{user.pk}', 'resend_email'):
            messages.error(request, 'You have asked for several links already. '
                                    'Wait a while, then try again.')
        elif not user.email:
            messages.error(request, 'Add an email address to your profile first.')
        elif not email_domain_allowed(user.email):
            messages.error(request, 'Change your email to a campus address (%s) in your '
                                    'profile first.' % allowed_domains_display())
        elif send_verification_email(request, user):
            messages.success(request, 'We sent a new confirmation link to your email.')
        else:
            messages.error(request, 'We could not send the email just now. Try again later.')
        return redirect('accounts:verification_pending')

    form = ResendVerificationForm(request.POST or None)
    if request.method == 'POST':
        if _limited(f'resend:ip:{client_ip(request)}', 'resend_ip'):
            return render(request, 'accounts/resend_verification.html',
                          {'form': form, 'rate_limited': True}, status=TOO_MANY)
        if form.is_valid():
            email = normalise_email(form.cleaned_data['email'])
            # Per-address limit, silent: the response must look the same
            # whether or not the address has an account.
            if not _limited(f'resend:email:{email}', 'resend_email'):
                target = (User.objects.filter(email__iexact=email, is_active=True,
                                              email_verified_at__isnull=True)
                          .exclude(role__in=[User.Role.MODERATOR, User.Role.ADMIN]).first())
                if target and not target.is_campus_verified and email_domain_allowed(target.email):
                    send_verification_email(request, target)
            return redirect('accounts:verification_sent')
    return render(request, 'accounts/resend_verification.html', {'form': form})


# ---------------------------------------------------------------------------
# Sign in / sign out
# ---------------------------------------------------------------------------
class LostLinkLoginView(auth_views.LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def _keys(self):
        username = normalise_username(self.request.POST.get('username'))
        return f'login:ip:{client_ip(self.request)}', f'login:user:{username}'

    def post(self, request, *args, **kwargs):
        ip_key, user_key = self._keys()
        if _limited(ip_key, 'login_ip', increment=False) or \
                _limited(user_key, 'login_user', increment=False):
            form = self.get_form_class()(request, initial={
                'username': (request.POST.get('username') or '')[:150]})
            return self.render_to_response(
                self.get_context_data(form=form, rate_limited=True), status=TOO_MANY)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        ip_key, user_key = self._keys()
        _limited(ip_key, 'login_ip')
        _limited(user_key, 'login_user')
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['show_demo'] = settings.DEBUG
        return context

    def form_valid(self, form):
        reset(self._keys()[1])
        user = form.get_user()
        messages.success(self.request, f'Signed in as {user.display_name()}.')
        if not user.is_campus_verified:
            messages.warning(self.request, 'Confirm your email address to report, flag '
                                           'and claim items.')
        return super().form_valid(form)

    def get_success_url(self):
        explicit = self.get_redirect_url()  # validated against ALLOWED_HOSTS
        if not explicit and not self.request.user.is_campus_verified:
            return reverse('accounts:verification_pending')
        return explicit or self.get_default_redirect_url()


@require_http_methods(['GET', 'POST'])
def logout_view(request):
    """Sign out with a CSRF-protected POST. A GET (old links, the account
    menu) shows a confirmation page instead of signing out, so a third-party
    page cannot sign people out by embedding the URL."""
    if request.method == 'POST':
        logout(request)
        messages.info(request, 'You have been signed out.')
        return redirect('items:home')
    if not request.user.is_authenticated:
        return redirect('items:home')
    return render(request, 'accounts/logout.html')


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@login_required
def profile(request):
    if request.method == 'POST':
        old_email = normalise_email(request.user.email)
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            user = form.save(commit=False)
            email_changed = normalise_email(user.email) != old_email
            if email_changed:
                # A new address has not been proven yet.
                user.clear_email_verification()
            user.save()
            if email_changed and user.email and not user.is_campus_verified:
                send_verification_email(request, user)
                messages.warning(request, 'Profile updated. We sent a confirmation link to your '
                                          'new email address; confirm it to keep reporting and '
                                          'claiming items.')
            else:
                messages.success(request, 'Profile updated.')
            return redirect('accounts:profile')
    else:
        form = ProfileForm(instance=request.user)
    return render(request, 'accounts/profile.html', {'form': form})


# ---------------------------------------------------------------------------
# Password recovery
# ---------------------------------------------------------------------------
class PasswordResetView(auth_views.PasswordResetView):
    template_name = 'accounts/password_reset_form.html'
    email_template_name = 'accounts/emails/password_reset_email.txt'
    subject_template_name = 'accounts/emails/password_reset_subject.txt'
    success_url = reverse_lazy('accounts:password_reset_done')

    def post(self, request, *args, **kwargs):
        if _limited(f'reset:ip:{client_ip(request)}', 'reset_ip'):
            form = self.get_form()
            return self.render_to_response(
                self.get_context_data(form=form, rate_limited=True), status=TOO_MANY)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        email = normalise_email(form.cleaned_data['email'])
        if _limited(f'reset:email:{email}', 'reset_email'):
            # Silently skip: same page as a sent email, so neither the limit
            # nor the address's existence is revealed.
            return HttpResponseRedirect(self.get_success_url())
        self.extra_email_context = {
            'base_url': site_base_url(self.request),
            'hours': max(1, settings.PASSWORD_RESET_TIMEOUT // 3600),
        }
        return super().form_valid(form)


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = 'accounts/password_reset_done.html'


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    success_url = reverse_lazy('accounts:password_reset_complete')
    post_reset_login = False

    def form_valid(self, form):
        response = super().form_valid(form)
        user = form.user
        # The new password's hash changes the session auth hash, so every
        # other session of this account is signed out on its next request.
        # Signing out here too means the browser that reset starts clean.
        if self.request.user.is_authenticated and self.request.user.pk == user.pk:
            logout(self.request)
        # Following the emailed link proved control of the mailbox.
        if (user.email_verified_at is None and user.email
                and email_domain_allowed(user.email)):
            user.mark_email_verified(User.VerificationMethod.PASSWORD_RESET)
        return response


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = 'accounts/password_reset_complete.html'
