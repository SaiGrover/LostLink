from django.test import TestCase
from django.urls import reverse
from accounts.models import User
from config.testing import make_user
from items.models import Item, Category, Location
from claims.models import Claim
from matching.models import Match


class VerificationBoundaryTests(TestCase):
    def setUp(self):
        # Students who confirmed their campus email (claiming requires it).
        self.owner = make_user('owner', password=None)
        self.other = make_user('other', password=None)
        self.mod = User.objects.create_user(username='mod', role='moderator')
        self.category = Category.objects.create(name='Bags', slug='bags')
        self.location = Location.objects.create(name='Library', slug='library')
        self.item = Item.objects.create(reporter=self.owner, title='Bag',
            description='Canvas bag', report_type='found', category=self.category,
            location=self.location)

    def test_pending_not_public(self):
        response = self.client.get(reverse('items:search_api'))
        self.assertEqual(response.json()['total'], 0)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.item.get_absolute_url()).status_code, 403)
        self.assertEqual(self.client.get(reverse('claims:start', args=[self.item.pk])).status_code, 404)

    def test_unanswered_claim_cannot_be_approved(self):
        self.item.status = 'approved'
        self.item.save()
        claim = Claim.objects.create(item=self.item, claimant=self.other, statement='My bag')
        self.client.force_login(self.mod)
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']))
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'submitted')

    def test_unrelated_match_rejected(self):
        self.item.status = 'approved'
        self.item.save()
        lost = Item.objects.create(reporter=self.owner, title='Lost bag', description='Bag',
            report_type='lost', category=self.category, location=self.location)
        match = Match.objects.create(lost_item=lost, found_item=self.item, score=1)
        self.client.force_login(self.other)
        response = self.client.get(reverse('claims:start', args=[self.item.pk]) + f'?match={match.pk}')
        self.assertEqual(response.status_code, 403)

    def test_date_filter(self):
        self.item.status = 'approved'
        self.item.event_date = '2026-01-01'
        self.item.save()
        response = self.client.get(reverse('items:search_api'), {'date_from':'2026-02-01'})
        self.assertEqual(response.json()['total'], 0)

    def test_closed_claim_answer_cannot_reopen(self):
        from claims.models import VerificationQuestion
        claim = Claim.objects.create(item=self.item, claimant=self.other, statement='Mine', status='rejected')
        question = VerificationQuestion.objects.create(claim=claim, asked_by=self.mod, text='What is inside?')
        self.client.force_login(self.other)
        response = self.client.post(reverse('claims:answer', args=[claim.pk,question.pk]), {'answer':'Books'})
        self.assertEqual(response.status_code, 403)
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'rejected')
