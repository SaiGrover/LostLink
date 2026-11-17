"""Automatic publishing: only reports that may show personal details wait for
a moderator (items/publishing.py)."""
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users, png_upload
from dashboard.models import ModerationDecision
from items import publishing
from items.models import Category, Item
from matching.models import Match

A = ModerationDecision.Action


@lightweight_models
@isolated_media
class AutoPublishTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()
        self.client.force_login(self.users['owner'])

    def payload(self, **overrides):
        data = {'report_type': 'lost', 'title': 'Black leather wallet', 'category': self.tax['bags'].pk,
                'location': self.tax['library'].pk, 'event_date': timezone.localdate().isoformat(),
                'event_time': '', 'description': 'Black leather wallet with a silver logo',
                'brand': '', 'color': 'Black', 'private_details': ''}
        data.update(overrides)
        return data

    def submit(self, **overrides):
        response = self.client.post(reverse('items:report'), self.payload(**overrides))
        self.assertEqual(response.status_code, 302)
        return Item.objects.latest('pk')

    # --- new reports ---------------------------------------------------------
    def test_plain_report_is_published_immediately(self):
        item = self.submit()
        self.assertEqual(item.status, Item.Status.APPROVED)
        self.assertEqual(item.review_reasons, [])
        self.assertTrue(Item.objects.public().filter(pk=item.pk).exists())
        decision = ModerationDecision.objects.get(item=item)
        self.assertEqual(decision.action, A.REPORT_AUTO_PUBLISHED)
        self.assertIsNone(decision.actor)
        self.assertEqual((decision.from_state, decision.to_state), ('pending', 'approved'))

    def test_published_report_is_searchable_by_others(self):
        item = self.submit()
        self.client.force_login(self.users['other'])
        response = self.client.get(reverse('items:detail', args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Waiting for a moderator')

    def test_reporter_ticked_personal_details_waits_for_moderator(self):
        item = self.submit(declare_sensitive='on')
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertEqual(item.review_reasons, [publishing.REASON_DECLARED])
        self.assertFalse(Item.objects.public().filter(pk=item.pk).exists())
        self.assertFalse(ModerationDecision.objects.filter(item=item).exists())

    def test_detected_id_card_waits_for_moderator(self):
        item = self.submit(title='Student ID card', description='Blue lanyard with a college card')
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertEqual(item.review_reasons, [publishing.REASON_DETECTED])

    def test_id_card_category_waits_for_moderator(self):
        ids = Category.objects.create(name='ID & Cards', slug='id-cards')
        item = self.submit(category=ids.pk, title='Blue card holder', description='Small blue card holder')
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertIn(publishing.REASON_DETECTED, item.review_reasons)

    def test_detected_photo_is_also_restricted(self):
        item = self.submit(title='Debit card', description='Bank debit card near the counter',
                           image=png_upload())
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertTrue(item.photo_sensitive)

    def test_moderation_queue_shows_the_reason(self):
        item = self.submit(declare_sensitive='on')
        self.client.force_login(self.users['mod'])
        response = self.client.get(reverse('dashboard:moderation'))
        self.assertContains(response, item.title)
        self.assertContains(response, publishing.REASON_DECLARED)

    def test_published_report_is_matched_at_once(self):
        found = Item.objects.create(
            reporter=self.users['finder'], report_type='found', title='Black leather wallet found',
            description='Black leather wallet with a silver logo', category=self.tax['bags'],
            location=self.tax['library'], color='Black', status=Item.Status.APPROVED)
        from matching.features import prepare
        prepare(found)
        item = self.submit()
        self.assertTrue(Match.objects.filter(lost_item=item, found_item=found).exists())

    def test_held_report_is_not_matched(self):
        found = Item.objects.create(
            reporter=self.users['finder'], report_type='found', title='Student ID card found',
            description='Student ID card on a blue lanyard', category=self.tax['others'],
            location=self.tax['library'], status=Item.Status.APPROVED)
        item = self.submit(title='Student ID card', description='Student ID card on a blue lanyard',
                           category=self.tax['others'].pk)
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertFalse(Match.objects.filter(lost_item=item, found_item=found).exists())

    @override_settings(LOSTLINK_AUTO_PUBLISH=False)
    def test_review_everything_switch(self):
        item = self.submit()
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertEqual(item.review_reasons, [publishing.REASON_ALWAYS])

    # --- edits ---------------------------------------------------------------
    def edit(self, item, **overrides):
        response = self.client.post(reverse('items:edit', args=[item.pk]), self.payload(**overrides))
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        return item

    def test_editing_a_plain_report_keeps_it_published(self):
        item = self.submit()
        item = self.edit(item, description='Black leather wallet with a silver logo and a zip')
        self.assertEqual(item.status, Item.Status.APPROVED)
        self.assertEqual(ModerationDecision.objects.filter(item=item, action=A.REPORT_AUTO_PUBLISHED).count(), 2)

    def test_ticking_personal_details_on_edit_sends_it_to_review(self):
        item = self.submit()
        item = self.edit(item, declare_sensitive='on')
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertEqual(item.review_reasons, [publishing.REASON_DECLARED])
        self.assertFalse(Item.objects.public().filter(pk=item.pk).exists())

    def test_removing_the_detected_words_publishes_it(self):
        item = self.submit(title='Student ID card', description='Blue lanyard with a college card')
        self.assertEqual(item.status, Item.Status.PENDING)
        item = self.edit(item, title='Blue lanyard', description='Blue lanyard with a keyring')
        self.assertEqual(item.status, Item.Status.APPROVED)
        self.assertEqual(item.review_reasons, [])

    def test_rejected_report_always_goes_back_to_a_moderator(self):
        item = self.submit(declare_sensitive='on')
        self.client.force_login(self.users['mod'])
        self.client.post(reverse('dashboard:review_report', args=[item.pk, 'reject']), {'note': 'Blurry'})
        item.refresh_from_db()
        self.assertEqual(item.status, Item.Status.REJECTED)
        # Moderator cleared the photo flag, the reporter rewrites the text.
        Item.objects.filter(pk=item.pk).update(photo_sensitive=False, photo_sensitivity_source='')
        self.client.force_login(self.users['owner'])
        item = self.edit(item, description='Black leather wallet, silver logo, worn corners')
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertEqual(item.review_reasons, [publishing.REASON_REJECTED])

    def test_moderator_approval_still_works_for_held_reports(self):
        item = self.submit(declare_sensitive='on')
        self.client.force_login(self.users['mod'])
        self.client.post(reverse('dashboard:review_report', args=[item.pk, 'approve']), {'note': ''})
        item.refresh_from_db()
        self.assertEqual(item.status, Item.Status.APPROVED)
        self.assertTrue(item.photo_sensitive)
