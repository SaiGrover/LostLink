"""Password recovery and sign-in abuse protection."""
import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from config.testing import make_user

RESET_LINK = re.compile(r'https?://[^\s]+(/accounts/password/reset/[^\s/]+/[^\s/]+/)')
NEW_PASSWORD = 'harbour-lantern-58'


def reset_link(message):
    match = RESET_LINK.search(message.body)
    assert match, message.body
    return match.group(1)


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user('ana', email='ana@college.edu', password='old-pass-word-1')

    def request_reset(self, email='ana@college.edu', client=None):
        return (client or self.client).post(reverse('accounts:password_reset'), {'email': email})

    def set_password(self, link, p1=NEW_PASSWORD, p2=NEW_PASSWORD):
        response = self.client.get(link)
        self.assertEqual(response.status_code, 302)  # token moved into the session
        return self.client.post(response.url, {'new_password1': p1, 'new_password2': p2})

    def test_full_reset_flow(self):
        self.assertEqual(self.client.get(reverse('accounts:password_reset')).status_code, 200)
        response = self.request_reset('ANA@college.edu')
        self.assertRedirects(response, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ana@college.edu'])
        response = self.set_password(reset_link(mail.outbox[0]))
        self.assertRedirects(response, reverse('accounts:password_reset_complete'))
        self.assertTrue(Client().login(username='ana', password=NEW_PASSWORD))
        self.assertFalse(Client().login(username='ana', password='old-pass-word-1'))

    def test_validators_and_confirmation_enforced(self):
        self.request_reset()
        link = reset_link(mail.outbox[0])
        response = self.set_password(link, '12345678', '12345678')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too common')
        response = self.client.post(response.wsgi_request.path,
                                    {'new_password1': NEW_PASSWORD, 'new_password2': 'other-pass-99'})
        self.assertContains(response, 'didn’t match')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-pass-word-1'))

    def test_link_is_single_use(self):
        self.request_reset()
        link = reset_link(mail.outbox[0])
        self.set_password(link)
        response = Client().get(link)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['validlink'])
        self.assertContains(response, "This reset link isn't valid")

    def test_invalid_token_and_unknown_user(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        for path in (reverse('accounts:password_reset_confirm', args=[uid, 'bad-token']),
                     reverse('accounts:password_reset_confirm', args=['zzzz', 'bad-token'])):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context['validlink'])

    def test_expired_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        later = PasswordResetTokenGenerator()._now() + timedelta(hours=2)
        with patch.object(PasswordResetTokenGenerator, '_now', return_value=later):
            response = self.client.get(reverse('accounts:password_reset_confirm', args=[uid, token]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['validlink'])

    def test_other_sessions_are_signed_out(self):
        other_device = Client()
        self.assertTrue(other_device.login(username='ana', password='old-pass-word-1'))
        self.assertEqual(other_device.get(reverse('items:dashboard')).status_code, 200)
        # The resetting browser is signed in too; it ends up signed out.
        self.client.force_login(self.user)
        self.request_reset()
        self.set_password(reset_link(mail.outbox[0]))
        response = other_device.get(reverse('items:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_same_response_for_unknown_address(self):
        known = self.request_reset('ana@college.edu')
        unknown = self.request_reset('nobody@college.edu')
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.url, unknown.url)
        self.assertEqual(len(mail.outbox), 1)

    def test_rate_limited_per_address_silently(self):
        for _ in range(6):
            response = self.request_reset()
            self.assertRedirects(response, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 3)

    @override_settings(LOSTLINK_AUTH_RATE_LIMITS={'reset_ip': (2, 3600)})
    def test_rate_limited_per_ip(self):
        self.request_reset('a@college.edu')
        self.request_reset('b@college.edu')
        response = self.request_reset('ana@college.edu')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_proves_mailbox_and_verifies_account(self):
        user = make_user('fresh', email='fresh@college.edu', verified=False)
        self.request_reset('fresh@college.edu')
        self.set_password(reset_link(mail.outbox[0]))
        user.refresh_from_db()
        self.assertTrue(user.is_campus_verified)
        self.assertEqual(user.email_verification_method, User.VerificationMethod.PASSWORD_RESET)

    def test_inactive_account_gets_no_email(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertRedirects(self.request_reset(), reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)


class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        make_user('sam', password='pass-word-123')

    def login(self, username='sam', password='pass-word-123', client=None):
        return (client or self.client).post(reverse('accounts:login'),
                                            {'username': username, 'password': password})

    def test_username_locked_after_repeated_failures(self):
        for _ in range(5):
            self.assertEqual(self.login(password='wrong').status_code, 200)
        # Even the right password is refused while locked, and case or
        # whitespace variants share the counter.
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Too many sign-in attempts', status_code=429)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.login(username=' SAM ').status_code, 429)

    def test_success_resets_username_counter(self):
        for _ in range(4):
            self.login(password='wrong')
        self.assertEqual(self.login().status_code, 302)
        self.client.logout()
        for _ in range(4):
            self.login(password='wrong')
        self.assertEqual(self.login().status_code, 302)

    @override_settings(LOSTLINK_AUTH_RATE_LIMITS={'login_ip': (3, 900)})
    def test_ip_locked_after_failures_across_usernames(self):
        for name in ('a', 'b', 'c'):
            self.login(username=name, password='wrong')
        self.assertEqual(self.login().status_code, 429)
        # Another client address is unaffected.
        self.assertEqual(self.login(client=Client(REMOTE_ADDR='10.0.0.9')).status_code, 302)
