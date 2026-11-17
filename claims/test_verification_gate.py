"""Claim actions require a verified campus account (spec 1.1)."""
from django.test import TestCase
from django.urls import reverse

from claims.models import Claim
from config.testing import lightweight_models, make_taxonomy, make_user, make_users
from items.models import Item


@lightweight_models
class VerifiedClaimantTests(TestCase):
    def test_unverified_user_cannot_start_or_dispute_claims(self):
        t = make_taxonomy()
        u = make_users()
        found = Item.objects.create(reporter=u['finder'], report_type='found', title='Wallet', status='approved',
                                    description='Black leather wallet', category=t['bags'], location=t['library'])
        newcomer = make_user('newcomer', verified=False)
        self.client.force_login(newcomer)
        response = self.client.post(reverse('claims:start', args=[found.pk]), {'statement': 'It is mine for sure'})
        self.assertRedirects(response, reverse('accounts:verification_pending'), fetch_redirect_response=False)
        self.assertFalse(Claim.objects.exists())
        claim = Claim.objects.create(item=found, claimant=u['owner'], statement='Mine')
        self.client.post(reverse('claims:dispute', args=[claim.pk]), {'reason': 'x'})
        self.assertFalse(claim.disputes.exists())
