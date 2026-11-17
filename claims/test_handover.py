"""OTP-based physical handover (claim approved ≠ item returned).

The owner (recipient) holds a one-time code; the person handing the item
over enters it. Covers both directions of the claim workflow, the code's
security properties, disputes, notifications and the audit trail.
"""
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from claims.models import Claim, ClaimDispute, HandoverSession, VerificationQuestion
from config.testing import lightweight_models, make_taxonomy, make_users
from dashboard.models import ModerationDecision
from items.models import Item
from notifications.models import Notification


@lightweight_models
class HandoverCodeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.t = make_taxonomy()
        self.u = make_users()
        self.found = Item.objects.create(
            reporter=self.u['finder'], report_type='found', title='Black wallet', status='claimed',
            description='Black leather wallet with silver logo', category=self.t['bags'],
            location=self.t['library'])
        self.claim = Claim.objects.create(item=self.found, claimant=self.u['owner'], statement='Mine',
                                          status='approved', reviewed_by=self.u['mod'])

    def issue(self, user=None, claim=None):
        claim = claim or self.claim
        self.client.force_login(user or self.u['owner'])
        return self.client.post(reverse('claims:issue_code', args=[claim.pk]))

    def code_from(self, response):
        return response.context['code'].replace(' ', '')

    def verify(self, code, user=None, claim=None):
        claim = claim or self.claim
        self.client.force_login(user or self.u['finder'])
        return self.client.post(reverse('claims:verify_code', args=[claim.pk]), {'code': code})

    # 1 / 13 — success and the resulting lifecycle
    def test_found_report_owner_holds_code_and_finder_completes_handover(self):
        response = self.issue()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        code = self.code_from(response)
        self.assertRegex(code, r'^\d{6}$')
        self.found.refresh_from_db()
        self.assertEqual(self.found.status, 'claimed')  # code alone never returns the item
        response = self.verify(code)
        self.assertRedirects(response, self.claim.get_absolute_url())
        self.claim.refresh_from_db()
        self.found.refresh_from_db()
        self.assertEqual(self.claim.status, 'returned')
        self.assertEqual(self.claim.handover_method, 'otp')
        self.assertIsNotNone(self.claim.returned_at)
        self.assertEqual(self.found.status, 'returned')
        self.assertFalse(self.found.is_active)
        self.assertEqual(HandoverSession.objects.get().status, 'verified')
        self.assertTrue(ModerationDecision.objects.filter(action='handover_confirmed', claim=self.claim).exists())
        # 12 — both parties are told, and the item leaves matching and claim intake
        self.assertEqual(Notification.objects.filter(title__icontains='Handover completed').count(), 2)
        self.assertFalse(Item.objects.matchable().filter(pk=self.found.pk).exists())

    # 11 — lost report: the reporter is the owner and holds the code
    def test_lost_report_owner_holds_code_and_finder_enters_it(self):
        lost = Item.objects.create(
            reporter=self.u['owner'], report_type='lost', title='My wallet', status='claimed',
            description='Black leather wallet', category=self.t['bags'], location=self.t['library'])
        offer = Claim.objects.create(item=lost, claimant=self.u['finder'], statement='I found it',
                                     status='approved', reviewed_by=self.u['mod'])
        self.assertEqual(offer.recipient, self.u['owner'])
        self.assertEqual(offer.provider, self.u['finder'])
        # the finder (claimant) cannot create the code, the owner can
        self.assertEqual(self.issue(user=self.u['finder'], claim=offer).status_code, 403)
        code = self.code_from(self.issue(user=self.u['owner'], claim=offer))
        # the owner cannot enter their own code; the finder does
        self.assertEqual(self.verify(code, user=self.u['owner'], claim=offer).status_code, 403)
        self.verify(code, user=self.u['finder'], claim=offer)
        offer.refresh_from_db()
        self.assertEqual(offer.status, 'returned')

    # 2 — invalid code
    def test_wrong_code_is_counted_and_does_not_complete(self):
        code = self.code_from(self.issue())
        wrong = '000000' if code != '000000' else '111111'
        self.verify(wrong)
        self.claim.refresh_from_db()
        session = HandoverSession.objects.get()
        self.assertEqual(self.claim.status, 'approved')
        self.assertEqual(session.failed_attempts, 1)
        self.assertTrue(ModerationDecision.objects.filter(action='handover_code_failed').exists())
        self.verify('12ab56')  # malformed input is rejected by the form, not counted
        self.assertEqual(HandoverSession.objects.get().failed_attempts, 1)

    # 3 — expired code
    def test_expired_code_is_refused(self):
        code = self.code_from(self.issue())
        HandoverSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.verify(code)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, 'approved')
        self.assertEqual(HandoverSession.objects.get().status, 'expired')

    # 4 — a verified code cannot be reused
    def test_code_is_single_use(self):
        code = self.code_from(self.issue())
        self.verify(code)
        self.assertEqual(self.verify(code).status_code, 403)  # claim is returned: nothing to verify
        self.assertEqual(ModerationDecision.objects.filter(action='handover_confirmed').count(), 1)

    # 5 — regeneration invalidates the previous code
    def test_new_code_supersedes_the_old_one(self):
        first = self.code_from(self.issue())
        second = self.code_from(self.issue())
        sessions = list(HandoverSession.objects.order_by('generation_number'))
        self.assertEqual([s.status for s in sessions], ['superseded', 'active'])
        self.assertEqual(sessions[1].generation_number, 2)
        if first != second:
            self.verify(first)
            self.claim.refresh_from_db()
            self.assertEqual(self.claim.status, 'approved')
        self.verify(second)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, 'returned')

    # 6 — only the provider may verify; only the recipient may issue
    def test_unauthorised_users_cannot_issue_or_verify(self):
        for user in (self.u['finder'], self.u['other'], self.u['mod']):
            self.assertEqual(self.issue(user=user).status_code, 403, user)
        code = self.code_from(self.issue())
        for user in (self.u['owner'], self.u['other'], self.u['mod']):
            self.assertEqual(self.verify(code, user=user).status_code, 403, user)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, 'approved')

    # 7 — no code before approval
    def test_no_code_before_moderator_approval(self):
        open_claim = Claim.objects.create(item=Item.objects.create(
            reporter=self.u['finder'], report_type='found', title='Keys', status='claimed',
            description='Bunch of keys', category=self.t['others'], location=self.t['library']),
            claimant=self.u['owner'], statement='Mine', status='answered')
        self.assertEqual(self.issue(claim=open_claim).status_code, 403)
        self.assertFalse(HandoverSession.objects.filter(claim=open_claim).exists())

    # 8 — a dispute suspends the code and blocks verification, without releasing the reservation
    def test_dispute_suspends_handover_but_keeps_reservation(self):
        code = self.code_from(self.issue())
        self.client.force_login(self.u['finder'])
        self.client.post(reverse('claims:dispute', args=[self.claim.pk]), {'reason': 'Wrong person showed up'})
        self.assertEqual(HandoverSession.objects.get().status, 'suspended')
        self.assertEqual(self.verify(code).status_code, 403)
        self.assertEqual(self.issue().status_code, 403)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, 'approved')
        self.assertIsNotNone(self.found.reserved_claim)
        # dismissed by an independent moderator: a *new* code is needed
        mod2 = type(self.u['mod']).objects.create_user(username='mod2', password='pass-word-123',
                                                        role='moderator', email='mod2@x.edu')
        self.client.force_login(mod2)
        self.client.post(reverse('claims:resolve_dispute', args=[self.claim.pk]),
                         {'outcome': 'dismissed', 'note': 'Checked ID, correct claimant'})
        self.assertEqual(HandoverSession.objects.get().status, 'cancelled')
        self.assertEqual(self.verify(code).status_code, 403)  # no active code
        new_code = self.code_from(self.issue())
        self.verify(new_code)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, 'returned')

    # 9 — repeated / concurrent submissions complete at most once
    def test_double_submission_completes_once_and_db_allows_one_active_code(self):
        code = self.code_from(self.issue())
        self.verify(code)
        self.verify(code)
        self.assertEqual(Claim.objects.get(pk=self.claim.pk).status, 'returned')
        self.assertEqual(ModerationDecision.objects.filter(action='handover_confirmed').count(), 1)
        other = Claim.objects.create(item=Item.objects.create(
            reporter=self.u['finder'], report_type='found', title='Bag', status='claimed',
            description='Blue bag', category=self.t['bags'], location=self.t['library']),
            claimant=self.u['other'], statement='Mine', status='approved')
        HandoverSession.objects.create(claim=other, recipient=self.u['other'], provider=self.u['finder'],
                                       salt='a', otp_hash='b', expires_at=timezone.now())
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HandoverSession.objects.create(claim=other, recipient=self.u['other'],
                                               provider=self.u['finder'], salt='c', otp_hash='d',
                                               expires_at=timezone.now())

    # 10 — with several claims, only the reserved claimant can hold a code
    def test_rejected_rival_cannot_get_a_code(self):
        rival = Claim.objects.create(item=self.found, claimant=self.u['other'], statement='Also mine',
                                     status='rejected')
        self.assertEqual(self.issue(user=self.u['other'], claim=rival).status_code, 403)

    # 14 — attempt limit and generation rate limit
    @override_settings(HANDOVER_CODE_MAX_ATTEMPTS=3, HANDOVER_CODES_PER_HOUR=2)
    def test_attempt_limit_locks_and_generation_is_rate_limited(self):
        code = self.code_from(self.issue())
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(3):
            self.verify(wrong)
        session = HandoverSession.objects.get()
        self.assertEqual(session.status, 'locked')
        self.verify(code)  # even the right code is refused once locked
        self.assertEqual(Claim.objects.get(pk=self.claim.pk).status, 'approved')
        self.assertTrue(Notification.objects.filter(user=self.u['owner'], title__icontains='locked').exists())
        self.assertEqual(self.issue().status_code, 200)   # second code this hour
        self.assertEqual(self.issue().status_code, 403)   # third is rate limited

    # 12 / 15 — the code never leaks
    def test_code_is_never_stored_logged_or_shown_to_others(self):
        code = self.code_from(self.issue())
        session = HandoverSession.objects.get()
        self.assertNotIn(code, session.otp_hash)
        self.assertEqual(len(session.otp_hash), 64)
        for decision in ModerationDecision.objects.all():
            self.assertNotIn(code, decision.note + str(decision.metadata))
        for note in Notification.objects.all():
            self.assertNotIn(code, note.title + note.body + note.url)
        for user in (self.u['finder'], self.u['mod'], self.u['owner']):
            self.client.force_login(user)
            page = self.client.get(self.claim.get_absolute_url()).content.decode()
            self.assertNotIn(code, page)
            self.assertNotIn(f'{code[:3]} {code[3:]}', page)
        # the provider is told a code exists (once, even if re-issued) without the code
        self.issue()
        self.assertEqual(Notification.objects.filter(user=self.u['finder'],
                                                     title__icontains='Handover code ready').count(), 1)

    def test_moderator_sees_code_status_and_exception_needs_independence(self):
        self.issue()
        self.client.force_login(self.u['mod'])
        page = self.client.get(self.claim.get_absolute_url())
        self.assertContains(page, 'Code #1')
        self.assertContains(page, '0 failed attempts')
        # the reporter-moderator of a report cannot use the exception path
        self.found.reporter = self.u['mod']
        self.found.save()
        response = self.client.post(reverse('claims:return', args=[self.claim.pk]),
                                    {'reason': 'desk_collection', 'note': 'Collected at the desk after ID check'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Claim.objects.get(pk=self.claim.pk).status, 'approved')


@lightweight_models
class HandoverAfterApprovalFlowTests(TestCase):
    """End to end through the moderator's checklist approval."""

    def test_approval_reserves_item_notifies_both_without_code(self):
        cache.clear()
        t = make_taxonomy()
        u = make_users()
        found = Item.objects.create(reporter=u['finder'], report_type='found', title='Wallet', status='claimed',
                                    description='Black leather wallet', category=t['bags'], location=t['library'])
        claim = Claim.objects.create(item=found, claimant=u['owner'], statement='Mine', status='answered')
        VerificationQuestion.objects.create(claim=claim, asked_by=u['finder'], text='Inside?', answer='Card')
        self.client.force_login(u['mod'])
        from claims.test_support import APPROVE
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE)
        claim.refresh_from_db()
        found.refresh_from_db()
        self.assertEqual(claim.status, 'approved')
        self.assertEqual(found.status, 'claimed')
        self.assertTrue(found.is_handover_pending)
        self.assertEqual(Notification.objects.filter(title__icontains='handover pending').count(), 2)
        self.assertFalse(HandoverSession.objects.exists())  # the owner creates the code at the meeting
        self.assertFalse(ClaimDispute.objects.exists())
