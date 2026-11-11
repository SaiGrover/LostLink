"""Regression tests for logic bugs found in a full-application audit.

Each test names the broken behaviour it guards against.
"""
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from claims.models import Claim, VerificationQuestion
from config.testing import lightweight_models, make_taxonomy, make_users
from items.models import Item, ReportFlag
from matching.models import Match
from notifications.models import Notification


@lightweight_models
class AuditRegressionTests(TestCase):
    def setUp(self):
        self.t = make_taxonomy()
        self.u = make_users()
        self.found = Item.objects.create(
            reporter=self.u['finder'], title='Black wallet', report_type='found', status='approved',
            description='Black leather wallet with a library card', category=self.t['bags'],
            location=self.t['library'])
        self.lost = Item.objects.create(
            reporter=self.u['owner'], title='Lost black wallet', report_type='lost', status='approved',
            description='Black leather wallet, library card inside', category=self.t['bags'],
            location=self.t['library'])

    # --- claims on removed reports ------------------------------------------
    def test_removing_a_flagged_report_closes_its_claims(self):
        claim = Claim.objects.create(item=self.found, claimant=self.u['owner'], statement='Mine')
        self.found.status = 'claimed'
        self.found.save()
        ReportFlag.objects.create(item=self.found, reporter=self.u['other'], reason='suspicious')
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('dashboard:resolve_flag', args=[self.found.pk, 'reject']))
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.REJECTED)
        self.assertIn('removed by a moderator', claim.decision_note)
        self.assertTrue(Notification.objects.filter(user=self.u['owner'], title__startswith='Claim closed').exists())
        page = self.client.get(reverse('dashboard:moderation'))
        self.assertNotIn(claim, list(page.context['open_claims']))

    # --- handover resolves the claimant's own report ------------------------
    def test_handover_resolves_the_claimants_matched_report(self):
        match = Match.objects.create(lost_item=self.lost, found_item=self.found, score=0.9, tier='high')
        claim = Claim.objects.create(item=self.found, claimant=self.u['owner'], statement='Mine',
                                     match=match, status='approved')
        self.found.status = 'claimed'
        self.found.save()
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:return', args=[claim.pk]))
        self.lost.refresh_from_db()
        self.assertEqual(self.lost.status, Item.Status.RETURNED)
        self.assertFalse(self.lost.is_active)
        self.assertEqual(self.client.get(reverse('items:search_api')).json()['total'], 0)

    def test_handover_without_a_linked_match_uses_the_single_current_match(self):
        Match.objects.create(lost_item=self.lost, found_item=self.found, score=0.9, tier='high',
                             engine_version=settings.MATCHING['VERSION'])
        claim = Claim.objects.create(item=self.found, claimant=self.u['owner'], statement='Mine', status='approved')
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:return', args=[claim.pk]))
        self.lost.refresh_from_db()
        self.assertEqual(self.lost.status, Item.Status.RETURNED)

    def test_handover_leaves_unrelated_reports_alone(self):
        other_lost = Item.objects.create(
            reporter=self.u['owner'], title='Blue umbrella', report_type='lost', status='approved',
            description='Blue folding umbrella', category=self.t['others'], location=self.t['cafeteria'])
        claim = Claim.objects.create(item=self.found, claimant=self.u['owner'], statement='Mine', status='approved')
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:return', args=[claim.pk]))
        other_lost.refresh_from_db()
        self.lost.refresh_from_db()
        self.assertEqual(other_lost.status, Item.Status.APPROVED)   # no match links it
        self.assertEqual(self.lost.status, Item.Status.APPROVED)    # no match stored either

    # --- separation of duties --------------------------------------------
    def test_reporter_who_is_a_moderator_cannot_decide_claims_on_their_report(self):
        self.found.reporter = self.u['mod']
        self.found.save()
        claim = Claim.objects.create(item=self.found, claimant=self.u['owner'], statement='Mine')
        VerificationQuestion.objects.create(claim=claim, asked_by=self.u['mod'], text='Inside?', answer='A card')
        self.client.force_login(self.u['mod'])
        response = self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']))
        self.assertEqual(response.status_code, 403)
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.SUBMITTED)
        self.assertContains(self.client.get(claim.get_absolute_url()), 'another moderator has to decide')
        # an independent moderator can
        self.client.force_login(self.u['admin'])
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']))
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.APPROVED)

    # --- input handling ------------------------------------------------------
    def test_impossible_period_dates_do_not_crash_analytics_or_exports(self):
        self.client.force_login(self.u['admin'])
        self.assertEqual(self.client.get(reverse('dashboard:analytics'), {'from': '2026-02-30'}).status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboard:export_csv'), {'to': '2026-13-01'}).status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboard:export_pdf'), {'from': '2026-02-30'}).status_code, 400)

    def test_role_change_with_a_malformed_user_id_is_rejected_cleanly(self):
        self.client.force_login(self.u['admin'])
        response = self.client.post(reverse('dashboard:users'), {'user_id': 'abc', 'role': 'moderator'})
        self.assertEqual(response.status_code, 302)

    def test_staff_pages_send_signed_out_visitors_back_after_sign_in(self):
        response = self.client.get(reverse('dashboard:moderation'))
        self.assertEqual(response.headers['Location'], '/accounts/login/?next=/manage/moderation/')
        response = self.client.get(reverse('dashboard:analytics'))
        self.assertEqual(response.headers['Location'], '/accounts/login/?next=/manage/analytics/')

    def test_profile_cannot_take_another_accounts_email(self):
        self.client.force_login(self.u['other'])
        response = self.client.post(reverse('accounts:profile'), {
            'first_name': 'O', 'last_name': '', 'email': 'OWNER@x.edu', 'enrolment_no': '', 'phone': ''})
        self.assertContains(response, 'Another account already uses this email.')
        self.u['other'].refresh_from_db()
        self.assertEqual(self.u['other'].email, 'other@x.edu')

    def test_profile_email_is_stored_lowercase(self):
        self.client.force_login(self.u['other'])
        self.client.post(reverse('accounts:profile'), {
            'first_name': 'O', 'last_name': '', 'email': 'New@X.edu', 'enrolment_no': '', 'phone': ''})
        self.u['other'].refresh_from_db()
        self.assertEqual(self.u['other'].email, 'new@x.edu')

    # --- flags on withdrawn reports ------------------------------------------
    def test_withdrawing_a_report_resolves_its_open_flags(self):
        ReportFlag.objects.create(item=self.lost, reporter=self.u['other'], reason='duplicate')
        self.client.force_login(self.u['owner'])
        self.client.post(reverse('items:withdraw', args=[self.lost.pk]))
        self.assertFalse(self.lost.flags.filter(resolved_at__isnull=True).exists())
        self.client.force_login(self.u['mod'])
        page = self.client.get(reverse('dashboard:moderation'))
        self.assertEqual(page.context['flag_count'], 0)
