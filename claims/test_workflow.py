from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from accounts.models import User
from config.testing import make_user
from items.models import Item, Category, Location
from claims.test_support import APPROVE
from claims.models import Claim, VerificationQuestion
from matching.services import run_matching
from notifications.models import Notification


class WorkflowTests(TestCase):
    def setUp(self):
        # Students who confirmed their campus email (claiming requires it).
        self.owner=make_user('owner',password='strong-example-pass')
        self.finder=make_user('finder',password=None)
        self.mod=User.objects.create_user(username='moderator',role='moderator')
        self.admin=User.objects.create_user(username='administrator',role='admin')
        self.category=Category.objects.create(name='Wallets',slug='wallets')
        self.location=Location.objects.create(name='Library',slug='library')
        self.item=Item.objects.create(reporter=self.finder,report_type='found',title='Black wallet',
            description='Black leather wallet with silver logo',category=self.category,
            location=self.location,status='approved')

    def test_complete_verification_and_handover(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('claims:start',args=[self.item.pk]),{'statement':'My wallet has a silver logo'})
        claim=Claim.objects.get(item=self.item)
        self.assertEqual(claim.status,'submitted')
        self.client.force_login(self.mod)
        self.client.post(reverse('claims:add_question',args=[claim.pk]),{'text':'What is inside?'})
        question=claim.questions.get()
        self.client.force_login(self.owner)
        self.client.post(reverse('claims:answer',args=[claim.pk,question.pk]),{'answer':'A library receipt'})
        self.client.force_login(self.mod)
        self.client.post(reverse('claims:decide',args=[claim.pk,'approve']),{**APPROVE,'note':'Verified private details'})
        claim.refresh_from_db(); self.item.refresh_from_db()
        self.assertEqual(claim.status,'approved')
        self.assertNotEqual(self.item.status,'returned')
        # Approved is not returned: the owner's one-time code completes the handover.
        self.client.force_login(self.owner)
        page=self.client.post(reverse('claims:issue_code',args=[claim.pk]))
        code=page.context['code'].replace(' ','')
        self.client.force_login(self.finder)
        self.client.post(reverse('claims:verify_code',args=[claim.pk]),{'code':code})
        claim.refresh_from_db(); self.item.refresh_from_db()
        self.assertEqual(claim.handover_method,'otp')
        self.assertEqual(claim.status,'returned')
        self.assertFalse(self.item.is_active)
        self.assertEqual(self.item.status,'returned')

    def test_student_cannot_decide(self):
        claim=Claim.objects.create(item=self.item,claimant=self.owner,statement='Mine')
        self.client.force_login(self.owner)
        response=self.client.post(reverse('claims:decide',args=[claim.pk,'approve']))
        self.assertEqual(response.status_code,403)

    def test_role_boundaries(self):
        for user,expected in [(self.owner,403),(self.mod,403),(self.admin,200)]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('dashboard:analytics')).status_code,expected)
            self.assertEqual(self.client.get(reverse('dashboard:export_csv')).status_code,expected)

    def test_match_notifications_once_for_both_reporters(self):
        lost=Item.objects.create(reporter=self.owner,report_type='lost',title='Wallet',
            description=self.item.description,category=self.category,location=self.location,status='approved')
        run_matching(lost)
        self.assertTrue(Notification.objects.filter(user=self.owner,kind='match').exists())
        self.assertTrue(Notification.objects.filter(user=self.finder,kind='match').exists())
        count=Notification.objects.count()
        run_matching(lost)
        self.assertEqual(Notification.objects.count(),count)

    def test_unapproved_never_generates_matches(self):
        self.item.status='pending'; self.item.save()
        self.assertEqual(run_matching(self.item),[])

    def test_dispute_is_private_and_reviewable(self):
        claim=Claim.objects.create(item=self.item,claimant=self.owner,statement='Mine',status='approved')
        self.client.force_login(self.owner)
        self.client.post(reverse('claims:dispute',args=[claim.pk]),{'reason':'The identifying mark is different'})
        claim.refresh_from_db()
        # The dispute is recorded without releasing the approved reservation.
        self.assertEqual(claim.status,'approved')
        self.assertTrue(claim.has_open_dispute)
        outsider=make_user('outsider',password=None)
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(claim.get_absolute_url()).status_code,403)

    def test_csv_formula_escape(self):
        self.item.title='=HYPERLINK("bad")'; self.item.save()
        self.client.force_login(self.admin)
        response=self.client.get(reverse('dashboard:export_csv'))
        self.assertIn(b"'=HYPERLINK",response.content)

    def test_invalid_month_rejected(self):
        self.client.force_login(self.admin)
        response=self.client.get(reverse('dashboard:export_pdf'),{'month':'invalid'})
        self.assertEqual(response.status_code,400)

    def test_student_cannot_edit_someone_elses_report(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('items:edit',args=[self.item.pk])).status_code,404)

    def test_unknown_match_rejected(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('claims:start',args=[self.item.pk]),{'match':'invalid'}).status_code,403)

    def test_claimed_report_cannot_be_withdrawn(self):
        Claim.objects.create(item=self.item,claimant=self.owner,statement='Mine')
        self.client.force_login(self.finder)
        self.client.post(reverse('items:withdraw',args=[self.item.pk]))
        self.item.refresh_from_db()
        self.assertTrue(self.item.is_active)
