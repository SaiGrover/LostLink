"""A second report is closed at handover only after an explicit confirmation
that it describes the same physical item (review follow-up)."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from claims.models import Claim, VerificationQuestion
from claims.test_support import APPROVE, EXCEPTION
from config.testing import lightweight_models, make_taxonomy, make_users
from dashboard.models import ModerationDecision
from items.models import Item
from matching.services import run_matching


@lightweight_models
class LinkedReportConfirmationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.t = make_taxonomy()
        self.u = make_users()
        # The owner lost two similar black wallets and filed two reports.
        self.wallet_a = Item.objects.create(
            reporter=self.u['owner'], report_type='lost', title='Black leather wallet', status='approved',
            description='Black leather wallet with a silver logo', category=self.t['bags'],
            location=self.t['library'])
        self.wallet_b = Item.objects.create(
            reporter=self.u['owner'], report_type='lost', title='Black wallet', status='approved',
            description='Black leather wallet, slim, no logo', category=self.t['bags'],
            location=self.t['cafeteria'])
        # A finder hands in wallet B, but matching suggests it is wallet A.
        self.found = Item.objects.create(
            reporter=self.u['finder'], report_type='found', title='Black leather wallet', status='approved',
            description='Black leather wallet with a silver logo', category=self.t['bags'],
            location=self.t['library'])
        run_matching(self.found, notify=False)

    def _claim_from_suggestion(self):
        from matching.models import Match
        match = Match.objects.get(lost_item=self.wallet_a, found_item=self.found)
        self.client.force_login(self.u['owner'])
        self.client.post(reverse('claims:start', args=[self.found.pk]) + f'?match={match.pk}',
                         {'statement': 'It is my wallet; my metro card is inside'})
        claim = Claim.objects.get()
        VerificationQuestion.objects.create(claim=claim, asked_by=self.u['finder'], text='?', answer='Metro card')
        claim.status = 'answered'
        claim.save()
        return claim

    def test_false_positive_match_then_legitimate_handover_keeps_the_other_report_open(self):
        claim = self._claim_from_suggestion()
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE)  # no confirmation
        self.client.post(reverse('claims:return', args=[claim.pk]), EXCEPTION)
        claim.refresh_from_db()
        self.wallet_a.refresh_from_db()
        self.found.refresh_from_db()
        self.assertEqual(claim.status, 'returned')
        self.assertEqual(self.found.status, 'returned')
        self.assertEqual(self.wallet_a.status, 'approved')    # still missing: stays open
        self.assertTrue(self.wallet_a.is_active)
        self.assertTrue(Item.objects.matchable().filter(pk=self.wallet_a.pk).exists())
        self.assertIsNone(claim.linked_report)

    def test_moderator_confirmation_at_approval_closes_the_linked_report_with_audit(self):
        claim = self._claim_from_suggestion()
        self.client.force_login(self.u['mod'])
        page = self.client.get(claim.get_absolute_url())
        self.assertContains(page, 'confirm_linked_report')
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']),
                         {**APPROVE, 'confirm_linked_report': 'on'})
        claim.refresh_from_db()
        self.assertEqual((claim.linked_report, claim.linked_report_confirmed_by), (self.wallet_a, self.u['mod']))
        decision = ModerationDecision.objects.get(action='claim_approved')
        self.assertEqual(decision.metadata['linked_report_confirmed'], self.wallet_a.pk)
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.status, 'approved')    # not closed until the handover
        self.client.post(reverse('claims:return', args=[claim.pk]), EXCEPTION)
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.status, 'returned')
        self.assertTrue(ModerationDecision.objects.filter(item=self.wallet_a, to_state='returned').exists())

    def test_claimant_can_confirm_and_close_their_report_after_the_handover(self):
        claim = self._claim_from_suggestion()
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE)
        self.client.post(reverse('claims:return', args=[claim.pk]), EXCEPTION)
        url = reverse('claims:close_own_report', args=[claim.pk, self.wallet_a.pk])
        # only the claimant, and only their own report
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.u['owner'])
        self.assertContains(self.client.get(claim.get_absolute_url()), 'is still open')
        other = Item.objects.create(reporter=self.u['other'], report_type='lost', title='Bag', status='approved',
                                    description='Blue bag', category=self.t['bags'], location=self.t['library'])
        self.assertEqual(self.client.post(reverse('claims:close_own_report', args=[claim.pk, other.pk])).status_code, 403)
        self.client.post(url)
        self.wallet_a.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(self.wallet_a.status, 'returned')
        self.assertEqual(claim.linked_report_confirmed_by, self.u['owner'])
        self.assertEqual(self.client.post(url).status_code, 403)  # already closed
