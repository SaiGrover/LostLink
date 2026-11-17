"""Campus identity: email verification, domain policy, restricted actions,
resend rate limiting and enumeration resistance."""
import importlib
import io
import re
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts import tokens
from accounts.models import User
from config.testing import make_taxonomy, make_user

VERIFY_LINK = re.compile(r'https?://[^\s]+/accounts/verify/([^\s/]+)/')


def link_token(message):
    match = VERIFY_LINK.search(message.body)
    assert match, message.body
    return match.group(1)


def register(client, username='riya', email='riya@college.edu', **extra):
    data = {'first_name': 'Riya', 'username': username, 'email': email,
            'password1': 'campus-recovery-9', 'password2': 'campus-recovery-9'}
    data.update(extra)
    return client.post(reverse('accounts:register'), data)


def item_payload(taxonomy):
    return {'report_type': 'lost', 'title': 'Black leather wallet',
            'description': 'Black leather wallet with a metro card inside.',
            'category': taxonomy['bags'].pk, 'location': taxonomy['library'].pk,
            'event_date': '2026-11-10', 'color': 'black', 'brand': ''}


class RestrictedActionTests(TestCase):
    """Unverified accounts cannot report, edit or flag; the check is server-side."""

    def setUp(self):
        cache.clear()
        self.tax = make_taxonomy()
        self.unverified = make_user('newbie', email='newbie@college.edu', verified=False)
        self.verified = make_user('trusted', email='trusted@college.edu')

    def _item(self, reporter):
        from items.models import Item
        return Item.objects.create(
            reporter=reporter, report_type='found', title='Blue umbrella',
            description='Blue folding umbrella near the library entrance.',
            category=self.tax['others'], location=self.tax['library'], event_date='2026-11-10')

    def test_unverified_cannot_open_or_submit_report(self):
        from items.models import Item
        self.client.force_login(self.unverified)
        response = self.client.get(reverse('items:report'))
        self.assertRedirects(response, reverse('accounts:verification_pending'))
        response = self.client.post(reverse('items:report'), item_payload(self.tax))
        self.assertRedirects(response, reverse('accounts:verification_pending'))
        self.assertFalse(Item.objects.filter(reporter=self.unverified).exists())

    def test_unverified_cannot_edit_or_flag(self):
        from items.models import ReportFlag
        own = self._item(self.unverified)
        theirs = self._item(self.verified)
        self.client.force_login(self.unverified)
        response = self.client.post(reverse('items:edit', args=[own.pk]), {'title': 'Changed'})
        self.assertRedirects(response, reverse('accounts:verification_pending'))
        own.refresh_from_db()
        self.assertEqual(own.title, 'Blue umbrella')
        response = self.client.post(reverse('items:flag', args=[theirs.pk]),
                                    {'reason': 'spam', 'note': 'x'})
        self.assertRedirects(response, reverse('accounts:verification_pending'))
        self.assertFalse(ReportFlag.objects.exists())

    def test_json_callers_get_403(self):
        self.client.force_login(self.unverified)
        response = self.client.get(reverse('items:report'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'verification_required')

    def test_verified_and_staff_accounts_pass(self):
        self.client.force_login(self.verified)
        self.assertEqual(self.client.get(reverse('items:report')).status_code, 200)
        # Staff created by an administrator never confirmed an email, but are
        # not locked out.
        moderator = make_user('mod2', role='moderator', verified=False)
        self.assertTrue(moderator.is_campus_verified)
        self.client.force_login(moderator)
        self.assertEqual(self.client.get(reverse('items:report')).status_code, 200)

    def test_anonymous_goes_to_sign_in(self):
        response = self.client.get(reverse('items:report'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_public_browsing_unaffected(self):
        self._item(self.verified)
        for name in ('items:home', 'items:search', 'items:lost', 'items:found'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.client.force_login(self.unverified)
        for name in ('items:search', 'items:dashboard', 'items:mine', 'claims:mine',
                     'notifications:inbox', 'accounts:profile'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_inactive_account_is_never_verified(self):
        self.verified.is_active = False
        self.assertFalse(self.verified.is_campus_verified)


class VerificationFlowTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_link_verifies_after_confirm_post(self):
        register(self.client)
        user = User.objects.get(username='riya')
        token = link_token(mail.outbox[0])
        url = reverse('accounts:verify_email', args=[token])

        # A GET (e.g. a mail scanner prefetching the link) changes nothing.
        response = self.client.get(url)
        self.assertContains(response, 'Confirm email address')
        user.refresh_from_db()
        self.assertFalse(user.is_campus_verified)

        response = self.client.post(url)
        self.assertRedirects(response, reverse('accounts:verification_complete'))
        user.refresh_from_db()
        self.assertTrue(user.is_campus_verified)
        self.assertEqual(user.email_verification_method, User.VerificationMethod.EMAIL)

        # Now the account can report.
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('items:report')).status_code, 200)

    def test_token_cannot_be_reused(self):
        register(self.client)
        token = link_token(mail.outbox[0])
        url = reverse('accounts:verify_email', args=[token])
        self.client.post(url)
        stamped = User.objects.get(username='riya').email_verified_at
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "isn't valid", status_code=400)
        self.assertEqual(User.objects.get(username='riya').email_verified_at, stamped)

    def test_token_dies_when_email_changes(self):
        user = make_user('ana', email='ana@college.edu', verified=False)
        token = tokens.make_token(user)
        user.email = 'ana.new@college.edu'
        user.save()
        response = self.client.post(reverse('accounts:verify_email', args=[token]))
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

    def test_expired_token(self):
        user = make_user('ana', email='ana@college.edu', verified=False)
        token = tokens.make_token(user)
        future = time.time() + tokens.max_age() + 60
        with patch('django.core.signing.time', SimpleNamespace(time=lambda: future)):
            response = self.client.post(reverse('accounts:verify_email', args=[token]))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'expired', status_code=400)
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

    def test_invalid_and_tampered_tokens(self):
        user = make_user('ana', email='ana@college.edu', verified=False)
        good = tokens.make_token(user)
        for bad in ('garbage', good[:-2] + ('aa' if not good.endswith('aa') else 'bb'),
                    good.replace(good.split(':')[0], 'eyJ1IjoxfQ', 1)):
            response = self.client.post(reverse('accounts:verify_email', args=[bad]))
            self.assertEqual(response.status_code, 400, bad)
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

    def test_token_for_inactive_account_is_rejected(self):
        user = make_user('ana', email='ana@college.edu', verified=False)
        token = tokens.make_token(user)
        User.objects.filter(pk=user.pk).update(is_active=False)
        response = self.client.post(reverse('accounts:verify_email', args=[token]))
        self.assertEqual(response.status_code, 400)

    def test_unverified_login_lands_on_pending_page(self):
        make_user('ana', email='ana@college.edu', verified=False)
        response = self.client.post(reverse('accounts:login'),
                                    {'username': 'ana', 'password': 'pass-word-123'})
        self.assertRedirects(response, reverse('accounts:verification_pending'))
        response = self.client.get(reverse('accounts:verification_pending'))
        self.assertContains(response, 'Verification pending')
        self.assertContains(response, 'ana@college.edu')

    def test_pending_page_shows_complete_state_when_verified(self):
        self.client.force_login(make_user('ana', email='ana@college.edu'))
        response = self.client.get(reverse('accounts:verification_pending'))
        self.assertContains(response, 'Your account is verified')

    def test_profile_email_change_requires_new_verification(self):
        user = make_user('ana', email='ana@college.edu')
        self.client.force_login(user)
        response = self.client.post(reverse('accounts:profile'), {
            'first_name': 'Ana', 'last_name': '', 'email': 'ana.two@college.edu',
            'enrolment_no': '', 'phone': ''})
        self.assertRedirects(response, reverse('accounts:profile'))
        user.refresh_from_db()
        self.assertEqual(user.email, 'ana.two@college.edu')
        self.assertFalse(user.is_campus_verified)
        self.assertEqual(mail.outbox[-1].to, ['ana.two@college.edu'])
        # Keeping the same address (other case) does not reset verification.
        user.mark_email_verified(User.VerificationMethod.EMAIL)
        self.client.post(reverse('accounts:profile'), {
            'first_name': 'Ana', 'last_name': '', 'email': 'ANA.two@college.edu',
            'enrolment_no': '', 'phone': '1'})
        user.refresh_from_db()
        self.assertTrue(user.is_campus_verified)

    def test_registration_cannot_set_role_or_verification(self):
        register(self.client, username='eve', email='eve@college.edu', role='admin',
                 is_staff='on', is_superuser='on', email_verified_at='2026-01-01T00:00:00Z',
                 email_verification_method='admin')
        eve = User.objects.get(username='eve')
        self.assertEqual(eve.role, User.Role.STUDENT)
        self.assertFalse(eve.is_staff or eve.is_superuser)
        self.assertIsNone(eve.email_verified_at)
        self.assertEqual(eve.email_verification_method, '')
        self.assertFalse(eve.is_campus_verified)


@override_settings(LOSTLINK_ALLOWED_EMAIL_DOMAINS=['college.edu'])
class EmailDomainPolicyTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_outside_domain_cannot_register(self):
        response = register(self.client, email='riya@gmail.com')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Use your campus email address (@college.edu)')
        self.assertFalse(User.objects.filter(username='riya').exists())
        self.assertEqual(len(mail.outbox), 0)
        # Lookalike domains are not subdomains.
        register(self.client, username='riya2', email='riya@notcollege.edu')
        self.assertFalse(User.objects.filter(username='riya2').exists())

    def test_campus_domain_and_subdomain_allowed(self):
        self.assertRedirects(register(self.client, email='riya@college.edu'),
                             reverse('accounts:verification_sent'))
        self.assertRedirects(register(self.client, username='dev', email='dev@cs.college.edu'),
                             reverse('accounts:verification_sent'))
        self.assertEqual(User.objects.count(), 2)

    def test_profile_cannot_switch_to_outside_domain(self):
        user = make_user('ana', email='ana@college.edu')
        self.client.force_login(user)
        response = self.client.post(reverse('accounts:profile'), {
            'first_name': 'Ana', 'email': 'ana@gmail.com', 'last_name': '',
            'enrolment_no': '', 'phone': ''})
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.email, 'ana@college.edu')
        self.assertTrue(user.is_campus_verified)

    def test_link_rechecks_domain(self):
        user = make_user('ana', email='ana@gmail.com', verified=False)
        token = tokens.make_token(user)
        response = self.client.post(reverse('accounts:verify_email', args=[token]))
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

    def test_legacy_accounts_outside_domain_keep_working(self):
        legacy = make_user('old', email='old@gmail.com', verified=False)
        legacy.mark_email_verified(User.VerificationMethod.LEGACY)
        self.assertTrue(legacy.is_campus_verified)


class ResendAndEnumerationTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_signed_in_resend_is_rate_limited(self):
        user = make_user('ana', email='ana@college.edu', verified=False)
        self.client.force_login(user)
        for _ in range(6):
            response = self.client.post(reverse('accounts:resend_verification'))
            self.assertRedirects(response, reverse('accounts:verification_pending'),
                                 fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 3)  # resend_email limit

    def test_anonymous_resend_is_rate_limited_per_address(self):
        make_user('ana', email='ana@college.edu', verified=False)
        for _ in range(6):
            self.client.post(reverse('accounts:resend_verification'), {'email': 'ANA@college.edu'})
        self.assertEqual(len(mail.outbox), 3)

    @override_settings(LOSTLINK_AUTH_RATE_LIMITS={'resend_ip': (2, 3600)})
    def test_anonymous_resend_is_rate_limited_per_ip(self):
        for i in range(2):
            self.client.post(reverse('accounts:resend_verification'), {'email': f'x{i}@college.edu'})
        response = self.client.post(reverse('accounts:resend_verification'), {'email': 'y@college.edu'})
        self.assertEqual(response.status_code, 429)

    def test_newest_link_still_works_after_resend(self):
        user = make_user('ana', email='ana@college.edu', verified=False)
        self.client.force_login(user)
        self.client.post(reverse('accounts:resend_verification'))
        self.client.post(reverse('accounts:resend_verification'))
        token = link_token(mail.outbox[-1])
        self.client.post(reverse('accounts:verify_email', args=[token]))
        user.refresh_from_db()
        self.assertTrue(user.is_campus_verified)
        # ...and once verified, the earlier link is dead.
        old = link_token(mail.outbox[0])
        self.assertEqual(self.client.post(reverse('accounts:verify_email', args=[old])).status_code, 400)

    def test_resend_gives_same_response_for_known_and_unknown_addresses(self):
        make_user('ana', email='ana@college.edu', verified=False)
        make_user('ver', email='ver@college.edu')
        responses = [self.client.post(reverse('accounts:resend_verification'), {'email': e}, follow=True)
                     for e in ('ana@college.edu', 'nobody@college.edu', 'ver@college.edu')]
        for r in responses:
            self.assertEqual(r.redirect_chain, [(reverse('accounts:verification_sent'), 302)])
        self.assertEqual(responses[0].content, responses[1].content)
        self.assertEqual(responses[1].content, responses[2].content)
        self.assertEqual([m.to for m in mail.outbox], [['ana@college.edu']])

    def test_registration_gives_same_response_for_new_and_existing_email(self):
        make_user('taken', email='dup@college.edu')
        existing = register(self.client, username='someone', email='DUP@college.edu')
        fresh = register(self.client, username='someone2', email='fresh@college.edu')
        self.assertEqual(existing.status_code, fresh.status_code)
        self.assertEqual(existing.url, fresh.url)
        self.assertEqual(self.client.get(existing.url).content, self.client.get(fresh.url).content)
        self.assertFalse(User.objects.filter(username='someone').exists())
        # The owner of the existing address is told; no verification link is
        # sent for the existing account (that could verify a squatted account).
        notice = mail.outbox[0]
        self.assertEqual(notice.to, ['dup@college.edu'])
        self.assertIsNone(VERIFY_LINK.search(notice.body))
        self.assertIn(reverse('accounts:password_reset'), notice.body)

    def test_registration_rate_limited_per_email(self):
        make_user('taken', email='dup@college.edu')
        for i in range(5):
            register(self.client, username=f'u{i}', email='dup@college.edu')
        response = register(self.client, username='u9', email='dup@college.edu')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 5)

    @override_settings(LOSTLINK_AUTH_RATE_LIMITS={'register_ip': (3, 3600)})
    def test_registration_rate_limited_per_ip(self):
        for i in range(3):
            register(self.client, username=f'u{i}', email=f'u{i}@college.edu')
        response = register(self.client, username='u9', email='u9@college.edu')
        self.assertEqual(response.status_code, 429)
        self.assertFalse(User.objects.filter(username='u9').exists())


class EmailDeliveryTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(EMAIL_BACKEND='accounts.mail.UnconfiguredEmailBackend')
    def test_unconfigured_production_email_fails_loudly_without_breaking_signup(self):
        with self.assertLogs('lostlink.accounts', level='ERROR') as logs:
            response = register(self.client)
        self.assertRedirects(response, reverse('accounts:verification_sent'))
        self.assertIn('Email NOT sent', logs.output[0])
        self.assertNotIn('/accounts/verify/', ''.join(logs.output))  # no one-time link in logs

    @override_settings(EMAIL_BACKEND='accounts.mail.UnconfiguredEmailBackend', DEBUG=False)
    def test_system_check_reports_missing_email_configuration(self):
        from accounts.checks import email_delivery_configured
        self.assertEqual([w.id for w in email_delivery_configured(None)], ['accounts.W001'])

    def test_smtp_failure_is_logged_not_raised(self):
        with patch('accounts.emails.send_mail', side_effect=OSError('smtp down')):
            with self.assertLogs('lostlink.accounts', level='ERROR'):
                response = register(self.client)
        self.assertRedirects(response, reverse('accounts:verification_sent'))
        self.assertTrue(User.objects.filter(username='riya').exists())


class LegacyAccountPolicyTests(TestCase):
    def test_migration_marks_existing_accounts_as_legacy_verified(self):
        old = make_user('old', email='old@x.edu', verified=False)
        already = make_user('done', email='done@x.edu')
        migration = importlib.import_module('accounts.migrations.0003_mark_existing_accounts_verified')
        migration.mark_legacy(apps, None)
        old.refresh_from_db()
        already.refresh_from_db()
        self.assertTrue(old.is_campus_verified)
        self.assertEqual(old.email_verification_method, User.VerificationMethod.LEGACY)
        self.assertEqual(already.email_verification_method, User.VerificationMethod.EMAIL)
        migration.unmark_legacy(apps, None)
        old.refresh_from_db()
        self.assertIsNone(old.email_verified_at)

    def test_management_command(self):
        old = make_user('old', email='old@x.edu', verified=False)
        call_command('mark_accounts_verified', '--dry-run', stdout=io.StringIO())
        old.refresh_from_db()
        self.assertIsNone(old.email_verified_at)
        call_command('mark_accounts_verified', stdout=io.StringIO())
        old.refresh_from_db()
        self.assertEqual(old.email_verification_method, User.VerificationMethod.LEGACY)
