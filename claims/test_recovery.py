"""Claim verification and recovery workflow coverage (synopsis Section 4.12)."""
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from claims.models import Claim, VerificationQuestion
from config.testing import lightweight_models, make_taxonomy, make_users
from dashboard.models import ModerationDecision
from items.models import Item
from matching.models import Match
from notifications.models import Notification


from claims.test_support import APPROVE


@lightweight_models
class ClaimWorkflowTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.u = make_users()
        self.found = Item.objects.create(reporter=self.u['finder'], report_type='found', title='Black wallet',
                                         description='Black leather wallet with silver logo',
                                         category=self.tax['bags'], location=self.tax['library'], status='approved')
        self.lost = Item.objects.create(reporter=self.u['owner'], report_type='lost', title='My wallet',
                                        description='Black leather wallet with silver logo',
                                        category=self.tax['bags'], location=self.tax['library'], status='approved')

    def start(self, user, item, **extra):
        self.client.force_login(user)
        return self.client.post(reverse('claims:start', args=[item.pk]),
                                {'statement': 'It has my library card inside', **extra})

    def test_owner_claims_found_item_and_finder_is_notified(self):
        response = self.start(self.u['owner'], self.found)
        claim = Claim.objects.get()
        self.assertRedirects(response, claim.get_absolute_url())
        self.assertEqual(claim.status, 'submitted')
        self.assertEqual(claim.contact_phone, self.u['owner'].phone)  # defaulted from profile
        self.found.refresh_from_db()
        self.assertEqual(self.found.status, 'claimed')
        note = Notification.objects.get(user=self.u['finder'], kind='claim')
        self.assertIn('claimed', note.title)

    def test_finder_offers_to_return_a_lost_item_with_distinct_copy(self):
        response = self.start(self.u['finder'], self.lost)
        claim = Claim.objects.get()
        self.assertEqual(claim.role_label, 'Finder')
        page = self.client.get(claim.get_absolute_url())
        self.assertContains(page, 'Finder')
        self.assertIn('offered to return', Notification.objects.get(user=self.u['owner']).title)
        self.assertRedirects(response, claim.get_absolute_url())

    def test_invalid_claims(self):
        # own report
        response = self.start(self.u['finder'], self.found)
        self.assertRedirects(response, self.found.get_absolute_url())
        self.assertEqual(Claim.objects.count(), 0)
        # pending / withdrawn report
        pending = Item.objects.create(reporter=self.u['finder'], report_type='found', title='x',
                                      description='pending thing', category=self.tax['bags'],
                                      location=self.tax['library'])
        self.assertEqual(self.start(self.u['owner'], pending).status_code, 404)
        # empty statement
        self.client.force_login(self.u['owner'])
        response = self.client.post(reverse('claims:start', args=[self.found.pk]), {'statement': ''})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Claim.objects.count(), 0)
        # a second verified claimant is blocked: the item is reserved (handover
        # pending), so the link explains why instead of opening a claim form
        Claim.objects.create(item=self.found, claimant=self.u['other'], statement='mine', status='approved')
        self.found.status = 'claimed'
        self.found.save()
        response = self.start(self.u['owner'], self.found)
        self.assertRedirects(response, self.found.get_absolute_url())
        self.assertEqual(Claim.objects.filter(claimant=self.u['owner']).count(), 0)

    def test_match_link_must_belong_to_the_claimant(self):
        match = Match.objects.create(lost_item=self.lost, found_item=self.found, score=.9, tier='high')
        self.client.force_login(self.u['other'])
        self.assertEqual(self.client.get(reverse('claims:start', args=[self.found.pk]) + f'?match={match.pk}').status_code, 403)
        self.client.force_login(self.u['owner'])
        response = self.client.post(reverse('claims:start', args=[self.found.pk]) + f'?match={match.pk}',
                                    {'statement': 'The silver logo is scratched'})
        self.assertEqual(Claim.objects.get().match, match)
        self.assertEqual(response.status_code, 302)

    def test_verification_questions_and_answers(self):
        self.start(self.u['owner'], self.found)
        claim = Claim.objects.get()
        # only the reporter or a moderator can ask
        self.client.force_login(self.u['other'])
        self.assertEqual(self.client.post(reverse('claims:add_question', args=[claim.pk]),
                                          {'text': 'what?'}).status_code, 403)
        self.client.force_login(self.u['finder'])
        self.client.post(reverse('claims:add_question', args=[claim.pk]), {'text': 'What is inside?'})
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:add_question', args=[claim.pk]), {'text': 'Which card?'})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'questions')
        q1, q2 = claim.questions.all()
        # only the claimant may answer
        self.assertEqual(self.client.post(reverse('claims:answer', args=[claim.pk, q1.pk]),
                                          {'answer': 'x'}).status_code, 403)
        self.client.force_login(self.u['owner'])
        self.client.post(reverse('claims:answer', args=[claim.pk, q1.pk]), {'answer': 'A library card'})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'questions')  # one still unanswered
        self.client.post(reverse('claims:answer', args=[claim.pk, q2.pk]), {'answer': 'The blue one'})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'answered')
        self.assertTrue(claim.all_answered)
        # answered questions cannot be rewritten
        self.assertEqual(self.client.post(reverse('claims:answer', args=[claim.pk, q1.pk]),
                                          {'answer': 'changed'}).status_code, 403)
        self.assertTrue(Notification.objects.filter(user=self.u['finder'], title__icontains='answers submitted').exists())

    def _answered_claim(self, claimant='owner'):
        claim = Claim.objects.create(item=self.found, claimant=self.u[claimant], statement='mine')
        VerificationQuestion.objects.create(claim=claim, asked_by=self.u['mod'], text='Inside?', answer='Card')
        claim.status = 'answered'
        claim.save()
        self.found.status = 'claimed'
        self.found.save()
        return claim

    def test_moderator_approval_records_decision_rejects_rivals_and_reveals_contacts(self):
        claim = self._answered_claim()
        rival = Claim.objects.create(item=self.found, claimant=self.u['other'], statement='also mine')
        # contacts hidden before approval
        self.client.force_login(self.u['owner'])
        page = self.client.get(claim.get_absolute_url())
        self.assertNotContains(page, 'finder@x.edu')
        self.client.force_login(self.u['mod'])
        response = self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE)
        self.assertRedirects(response, claim.get_absolute_url())
        claim.refresh_from_db()
        rival.refresh_from_db()
        self.found.refresh_from_db()
        self.assertEqual(claim.status, 'approved')
        self.assertEqual(claim.reviewed_by, self.u['mod'])
        self.assertEqual(rival.status, 'rejected')
        self.assertEqual(self.found.status, 'claimed')  # not returned yet
        self.assertTrue(ModerationDecision.objects.filter(action='claim_approved', claim=claim, actor=self.u['mod']).exists())
        self.assertTrue(Notification.objects.filter(user=self.u['other'], title__icontains='closed').exists())
        self.client.force_login(self.u['owner'])
        page = self.client.get(claim.get_absolute_url())
        self.assertContains(page, 'finder@x.edu')
        self.assertNotContains(page, 'Mark item returned')

    def test_approval_requires_answers_and_moderators_cannot_decide_own_claims(self):
        claim = Claim.objects.create(item=self.found, claimant=self.u['owner'], statement='mine')
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE)
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'submitted')
        own = Claim.objects.create(item=self.lost, claimant=self.u['mod'], statement='I found it')
        VerificationQuestion.objects.create(claim=own, asked_by=self.u['owner'], text='?', answer='!')
        self.assertEqual(self.client.post(reverse('claims:decide', args=[own.pk, 'approve']), APPROVE).status_code, 403)
        self.assertEqual(self.client.post(reverse('claims:decide', args=[claim.pk, 'bogus'])).status_code, 403)
        self.assertEqual(self.client.get(reverse('claims:decide', args=[claim.pk, 'approve'])).status_code, 405)

    def test_rejection_returns_item_to_pool_and_closes_the_claim(self):
        claim = self._answered_claim()
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:decide', args=[claim.pk, 'reject']), {'note': 'Answers did not match'})
        claim.refresh_from_db()
        self.found.refresh_from_db()
        self.assertEqual(claim.status, 'rejected')
        self.assertEqual(self.found.status, 'approved')
        self.assertIn('Answers did not match', Notification.objects.get(user=self.u['owner'], kind='claim').body)
        # finalised: no re-decision, no answers, no dispute
        self.assertEqual(self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE).status_code, 403)
        self.client.force_login(self.u['owner'])
        self.assertEqual(self.client.post(reverse('claims:dispute', args=[claim.pk]), {'reason': 'x'}).status_code, 403)
        q = claim.questions.first()
        self.assertEqual(self.client.post(reverse('claims:answer', args=[claim.pk, q.pk]), {'answer': 'again'}).status_code, 403)

    def test_double_approval_is_prevented(self):
        claim = self._answered_claim()
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE)
        self.assertEqual(self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE).status_code, 403)
        self.assertEqual(self.client.post(reverse('claims:decide', args=[claim.pk, 'reject']), {'note': 'x'}).status_code, 403)
        self.assertEqual(ModerationDecision.objects.filter(claim=claim).count(), 1)

    def test_handover_exception_requires_evidence_and_completes_recovery(self):
        """The normal handover is the owner's code (claims.test_handover); a
        moderator can only record a documented exception."""
        claim = self._answered_claim()
        exception = {'reason': 'desk_collection', 'note': 'Collected at the desk after an ID check'}
        self.client.force_login(self.u['mod'])
        # cannot confirm before approval, students never can
        self.assertEqual(self.client.post(reverse('claims:return', args=[claim.pk]), exception).status_code, 403)
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE)
        self.client.force_login(self.u['owner'])
        self.assertEqual(self.client.post(reverse('claims:return', args=[claim.pk]), exception).status_code, 403)
        self.client.force_login(self.u['mod'])
        self.assertEqual(self.client.get(reverse('claims:return', args=[claim.pk])).status_code, 405)
        # an undocumented exception is refused
        self.client.post(reverse('claims:return', args=[claim.pk]), {'reason': 'desk_collection', 'note': 'ok'})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'approved')
        self.client.post(reverse('claims:return', args=[claim.pk]), exception)
        claim.refresh_from_db()
        self.found.refresh_from_db()
        self.assertEqual(claim.status, 'returned')
        self.assertEqual(claim.handover_method, 'exception')
        self.assertEqual(self.found.status, 'returned')
        self.assertFalse(self.found.is_active)
        self.assertTrue(ModerationDecision.objects.filter(action='handover_exception',
                                                          note=exception['note']).exists())
        self.assertFalse(ModerationDecision.objects.filter(action='handover_confirmed').exists())
        self.assertEqual(Notification.objects.filter(title__icontains='Handover completed').count(), 2)
        self.assertEqual(self.client.post(reverse('claims:return', args=[claim.pk]), exception).status_code, 403)
        # history reflects it
        self.assertEqual([s['state'] for s in claim.timeline()], ['done'] * 6)

    def test_dispute_flow(self):
        claim = self._answered_claim()
        self.client.force_login(self.u['finder'])
        self.client.post(reverse('claims:dispute', args=[claim.pk]), {'reason': ''})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'answered')
        self.client.post(reverse('claims:dispute', args=[claim.pk]), {'reason': 'The scratch is on the other side'})
        claim.refresh_from_db()
        # A dispute is recorded alongside the claim; it never replaces its status.
        self.assertEqual(claim.status, 'answered')
        self.assertTrue(claim.has_open_dispute)
        self.assertTrue(claim.is_open)
        self.assertTrue(Notification.objects.filter(user=self.u['owner'], title__icontains='Dispute').exists())
        self.assertTrue(ModerationDecision.objects.filter(action='dispute_raised').exists())
        self.client.force_login(self.u['mod'])
        self.assertContains(self.client.get(reverse('dashboard:moderation')), 'other side')
        self.client.post(reverse('claims:decide', args=[claim.pk, 'reject']), {'note': 'Not the owner'})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'rejected')
        self.assertFalse(claim.has_open_dispute)  # rejecting the claim settles the dispute
        self.client.force_login(self.u['other'])
        self.assertEqual(self.client.get(claim.get_absolute_url()).status_code, 403)

    def test_private_answers_are_only_shown_to_participants(self):
        claim = self._answered_claim()
        for user in (self.u['owner'], self.u['finder'], self.u['mod']):
            self.client.force_login(user)
            self.assertContains(self.client.get(claim.get_absolute_url()), 'Card')
        self.client.force_login(self.u['other'])
        self.assertEqual(self.client.get(claim.get_absolute_url()).status_code, 403)
        self.client.force_login(self.u['admin'])
        csv = self.client.get(reverse('dashboard:export_csv')).content.decode()
        self.assertNotIn('Card', csv)

    def test_my_claims_lists_outgoing_and_incoming(self):
        claim = self._answered_claim()
        self.client.force_login(self.u['owner'])
        page = self.client.get(reverse('claims:mine'))
        self.assertContains(page, 'Black wallet')
        self.client.force_login(self.u['finder'])
        page = self.client.get(reverse('claims:mine'))
        self.assertEqual(list(page.context['incoming']), [claim])


class ConcurrentApprovalTests(TransactionTestCase):
    """The database itself refuses two verified claimants for one item."""

    def test_unique_verified_claim_constraint(self):
        tax = make_taxonomy()
        u = make_users()
        found = Item.objects.create(reporter=u['finder'], report_type='found', title='Wallet',
                                    description='Black leather wallet', category=tax['bags'],
                                    location=tax['library'], status='claimed')
        Claim.objects.create(item=found, claimant=u['owner'], statement='mine', status='approved')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Claim.objects.create(item=found, claimant=u['other'], statement='mine too', status='approved')
        # a rejected rival is fine
        Claim.objects.create(item=found, claimant=u['other'], statement='mine too', status='rejected')
