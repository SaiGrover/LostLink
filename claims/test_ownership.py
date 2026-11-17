"""Ownership reservation, disputes, reconsideration, private evidence and
claim availability (spec sections 3.1–3.5)."""
import io

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from claims.models import Claim, ClaimDispute, ClaimEvidence, ClaimRevision, VerificationQuestion
from claims.test_support import APPROVE
from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users
from dashboard.models import ModerationDecision
from items.models import Item
from matching.models import Match
from notifications.models import Notification


def jpeg_with_gps():
    from PIL import Image
    img = Image.new('RGB', (64, 48), (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = 'SecretCam'  # camera make
    exif[0x013B] = 'Olive Rao'  # artist (personal data)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', exif=exif)
    return SimpleUploadedFile('receipt.jpg', buf.getvalue(), content_type='image/jpeg')


@lightweight_models
@isolated_media
class OwnershipTests(TestCase):
    def setUp(self):
        cache.clear()
        self.t = make_taxonomy()
        self.u = make_users()
        User = type(self.u['mod'])
        self.mod2 = User.objects.create_user(username='mod2', password='pass-word-123', role='moderator',
                                             email='mod2@x.edu')
        self.found = Item.objects.create(
            reporter=self.u['finder'], report_type='found', title='Black wallet', status='approved',
            description='Black leather wallet with silver logo', category=self.t['bags'],
            location=self.t['library'], private_details='Library card for Olive inside')

    def answered(self, user):
        claim = Claim.objects.create(item=self.found, claimant=user, statement='It is mine, with my card')
        VerificationQuestion.objects.create(claim=claim, asked_by=self.u['finder'], text='Card name?',
                                            expected_answer='Olive', answer='Olive')
        claim.status = 'answered'
        claim.save()
        self.found.status = 'claimed'
        self.found.save()
        return claim

    def approve(self, claim, mod=None, data=None):
        self.client.force_login(mod or self.u['mod'])
        return self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), data or APPROVE)

    # 3.1 — a dispute never releases the reservation
    def test_dispute_after_approval_keeps_reservation_and_blocks_rivals(self):
        claim = self.answered(self.u['owner'])
        self.approve(claim)
        self.client.force_login(self.u['finder'])
        self.client.post(reverse('claims:dispute', args=[claim.pk]), {'reason': 'Not sure it is theirs'})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'approved')
        self.assertTrue(claim.has_open_dispute)
        # a competitor cannot start a claim while the item is reserved
        self.client.force_login(self.u['other'])
        response = self.client.post(reverse('claims:start', args=[self.found.pk]), {'statement': 'mine!'})
        self.assertRedirects(response, self.found.get_absolute_url())
        self.assertFalse(Claim.objects.filter(claimant=self.u['other']).exists())
        page = self.client.get(self.found.get_absolute_url())
        self.assertContains(page, 'Handover pending')
        self.assertNotContains(page, 'Claim this item')

    def test_only_an_independent_moderator_resolves_and_revocation_allows_reassignment(self):
        claim = self.answered(self.u['owner'])
        rival = Claim.objects.create(item=self.found, claimant=self.u['other'], statement='It is mine too',
                                     status='answered')
        VerificationQuestion.objects.create(claim=rival, asked_by=self.u['finder'], text='?', answer='!')
        self.approve(claim)
        rival.refresh_from_db()
        self.assertEqual(rival.status, 'rejected')
        # the rival cannot reopen while ownership is verified for someone else
        self.client.force_login(self.u['other'])
        self.assertEqual(self.client.post(reverse('claims:appeal', args=[rival.pk]),
                                          {'statement': 'I have the receipt for this wallet'}).status_code, 403)
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:dispute', args=[claim.pk]), {'reason': 'Receipt shows another name'})
        # the moderator who raised it, and the parties, cannot resolve it
        self.assertEqual(self.client.post(reverse('claims:resolve_dispute', args=[claim.pk]),
                                          {'outcome': 'revoked', 'note': 'Revoking approval now'}).status_code, 403)
        self.client.force_login(self.u['finder'])
        self.assertEqual(self.client.post(reverse('claims:resolve_dispute', args=[claim.pk]),
                                          {'outcome': 'revoked', 'note': 'Revoking approval now'}).status_code, 403)
        self.client.force_login(self.mod2)
        self.client.post(reverse('claims:resolve_dispute', args=[claim.pk]),
                         {'outcome': 'revoked', 'note': 'Receipt confirms another owner'})
        claim.refresh_from_db()
        self.found.refresh_from_db()
        self.assertEqual(claim.status, 'rejected')
        self.assertIsNone(self.found.reserved_claim)
        self.assertTrue(ModerationDecision.objects.filter(action='claim_revoked', claim=claim,
                                                          actor=self.mod2).exists())
        self.assertEqual(ClaimDispute.objects.get().outcome, 'revoked')
        # now the rival may request reconsideration, and be approved (reassignment)
        self.client.force_login(self.u['other'])
        self.client.post(reverse('claims:appeal', args=[rival.pk]),
                         {'statement': 'I have the receipt for this wallet', 'additional_details': 'Receipt'})
        rival.refresh_from_db()
        self.assertEqual(rival.status, 'answered')
        self.approve(rival, mod=self.mod2)
        rival.refresh_from_db()
        self.assertEqual(rival.status, 'approved')

    def test_withdrawing_an_approved_claim_releases_the_item(self):
        claim = self.answered(self.u['owner'])
        self.approve(claim)
        self.assertFalse(Item.objects.matchable().filter(pk=self.found.pk).exists())
        self.client.force_login(self.u['owner'])
        self.client.post(reverse('claims:withdraw', args=[claim.pk]))
        claim.refresh_from_db()
        self.found.refresh_from_db()
        self.assertEqual(claim.status, 'withdrawn')
        self.assertEqual(self.found.status, 'approved')
        self.assertTrue(Item.objects.matchable().filter(pk=self.found.pk).exists())
        self.assertTrue(ModerationDecision.objects.filter(action='claim_withdrawn').exists())

    # 3.2 — reconsideration keeps history and is limited
    @override_settings(CLAIM_MAX_APPEALS=1)
    def test_rejected_claim_can_be_reconsidered_once_with_history(self):
        claim = self.answered(self.u['owner'])
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:decide', args=[claim.pk, 'reject']), {'note': 'Card name differs'})
        self.client.force_login(self.u['owner'])
        page = self.client.get(claim.get_absolute_url())
        self.assertContains(page, 'Card name differs')
        self.assertContains(page, 'Request reconsideration')
        self.client.post(reverse('claims:appeal', args=[claim.pk]),
                         {'statement': 'The card says O. Rao, my full name is Olive Rao',
                          'additional_details': 'Short name on the card'})
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.appeal_count), ('answered', 1))
        revisions = list(ClaimRevision.objects.filter(claim=claim))
        self.assertEqual([r.kind for r in revisions], ['original', 'appeal'])
        self.assertEqual(revisions[1].responds_to_decision, 'Card name differs')
        self.assertEqual(revisions[0].statement, 'It is mine, with my card')
        self.assertTrue(ModerationDecision.objects.filter(action='claim_reopened').exists())
        self.assertTrue(Notification.objects.filter(user=self.u['finder'], title__icontains='reopened').exists())
        # revisions are immutable
        with self.assertRaises(ValueError):
            revisions[0].save()
        # a second rejection cannot be appealed again (limit 1)
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('claims:decide', args=[claim.pk, 'reject']), {'note': 'Still unclear'})
        self.client.force_login(self.u['owner'])
        self.assertEqual(self.client.post(reverse('claims:appeal', args=[claim.pk]),
                                          {'statement': 'Please look again at my explanation'}).status_code, 403)

    # 3.3 — independent checks, not automatic answers
    def test_checklist_refuses_contradictions_and_needs_positive_evidence(self):
        claim = self.answered(self.u['owner'])
        self.approve(claim, data={**APPROVE, 'check_private': 'mismatch'})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'answered')
        self.approve(claim, data={**APPROVE, 'check_answers': 'not_applicable'})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'answered')  # no supporting independent check
        self.approve(claim, data={**APPROVE, 'note': ''})
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'answered')  # a reason is required
        self.approve(claim)
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'approved')
        decision = ModerationDecision.objects.get(action='claim_approved')
        self.assertEqual(decision.metadata['checklist']['check_answers'], 'correct')
        self.assertEqual((decision.from_state, decision.to_state), ('answered', 'approved'))

    def test_expected_answers_and_private_details_stay_hidden_from_the_claimant(self):
        claim = self.answered(self.u['owner'])
        q = claim.questions.get()
        q.expected_answer = 'Purple sticker'
        q.save()
        self.client.force_login(self.u['owner'])
        page = self.client.get(claim.get_absolute_url())
        self.assertNotContains(page, 'Purple sticker')
        self.assertNotContains(page, 'Library card for Olive inside')
        for user in (self.u['finder'], self.u['mod']):
            self.client.force_login(user)
            page = self.client.get(claim.get_absolute_url())
            self.assertContains(page, 'Purple sticker')
            self.assertContains(page, 'Library card for Olive inside')
        # the public item page never shows private details
        self.client.force_login(self.u['other'])
        self.assertNotContains(self.client.get(self.found.get_absolute_url()), 'Library card for Olive inside')

    def test_evidence_is_reencoded_private_and_metadata_free(self):
        from PIL import Image
        claim = self.answered(self.u['owner'])
        self.client.force_login(self.u['owner'])
        self.client.post(reverse('claims:add_evidence', args=[claim.pk]), {'evidence': jpeg_with_gps(),
                                                                            'note': 'Receipt'})
        evidence = ClaimEvidence.objects.get()
        with evidence.file.open('rb') as fh:
            stored = Image.open(fh)
            stored.load()
            self.assertEqual(len(stored.getexif()), 0)
            self.assertNotIn('exif', stored.info)
        url = reverse('claims:evidence_file', args=[claim.pk, evidence.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        for user, status in ((self.u['mod'], 200), (self.u['finder'], 403), (self.u['other'], 403)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, status, user)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)  # sign-in required
        # non-images are refused
        self.client.force_login(self.u['owner'])
        response = self.client.post(reverse('claims:add_evidence', args=[claim.pk]),
                                    {'evidence': SimpleUploadedFile('x.jpg', b'<?php echo 1; ?>',
                                                                    content_type='image/jpeg')})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ClaimEvidence.objects.count(), 1)

    # 3.5 — actions follow eligibility
    def test_match_alerts_stop_for_reserved_items(self):
        lost = Item.objects.create(reporter=self.u['owner'], report_type='lost', title='My wallet',
                                   status='approved', description='Black leather wallet with silver logo',
                                   category=self.t['bags'], location=self.t['library'])
        from matching.services import run_matching
        run_matching(lost)
        self.assertTrue(Match.objects.filter(found_item=self.found).exists())
        claim = self.answered(self.u['other'])
        self.approve(claim)
        self.assertFalse(Match.objects.filter(found_item=self.found, lost_item__is_active=True,
                                              found_item__in=Item.objects.matchable()).exists())
        # the owner's lost report gets no claim link to the reserved item
        self.client.force_login(self.u['owner'])
        page = self.client.get(lost.get_absolute_url())
        self.assertNotContains(page, reverse('claims:start', args=[self.found.pk]))


@lightweight_models
class PublicDetailsTests(TestCase):
    def setUp(self):
        self.t = make_taxonomy()
        self.u = make_users()

    def post(self, **fields):
        data = {'report_type': 'found', 'title': 'Student ID card', 'category': self.t['bags'].pk,
                'location': self.t['library'].pk, 'event_date': '2026-11-01',
                'description': 'Blue lanyard with a college ID card', **fields}
        self.client.force_login(self.u['finder'])
        return self.client.post(reverse('items:report'), data)

    def test_identifiers_must_go_in_private_details(self):
        for text in ('ID card, roll no 2408030010', 'Phone, call +91 98110 20010', 'Laptop S/N C02XK1',
                     'Card for someone@college.edu'):
            response = self.post(description=text)
            self.assertEqual(response.status_code, 200, text)
            self.assertContains(response, 'Private verification details')
        self.assertFalse(Item.objects.exists())
        self.post(private_details='Roll number 2408030010')
        item = Item.objects.get()
        self.assertEqual(item.private_details, 'Roll number 2408030010')


@lightweight_models
class CounterpartAfterApprovalTests(TestCase):
    def test_handover_resolves_owner_report_even_after_approval_retired_matches(self):
        from claims.test_support import EXCEPTION
        from matching.services import run_matching
        cache.clear()
        t = make_taxonomy()
        u = make_users()
        found = Item.objects.create(reporter=u['finder'], report_type='found', title='Black wallet',
                                    status='approved', description='Black leather wallet with silver logo',
                                    category=t['bags'], location=t['library'])
        lost = Item.objects.create(reporter=u['owner'], report_type='lost', title='Black wallet',
                                   status='approved', description='Black leather wallet with silver logo',
                                   category=t['bags'], location=t['library'])
        run_matching(lost)
        claim = Claim.objects.create(item=found, claimant=u['owner'], statement='Mine', status='answered')
        VerificationQuestion.objects.create(claim=claim, asked_by=u['finder'], text='?', answer='!')
        found.status = 'claimed'
        found.save()
        self.client.force_login(u['mod'])
        self.client.post(reverse('claims:decide', args=[claim.pk, 'approve']), APPROVE)
        self.assertFalse(Match.objects.filter(found_item=found).exists())  # retired, not deleted
        self.client.post(reverse('claims:return', args=[claim.pk]), EXCEPTION)
        lost.refresh_from_db()
        self.assertEqual(lost.status, 'returned')
