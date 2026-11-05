from django.test import TestCase
from django.urls import reverse

from config.testing import lightweight_models, make_taxonomy, make_users
from items.models import Item
from matching.services import run_matching
from notifications.models import Notification
from notifications.utils import notify_user


@lightweight_models
class NotificationTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.u = make_users()
        self.item = Item.objects.create(
            reporter=self.u['owner'], report_type='lost', title='Canvas bag',
            description='Cream canvas bag with a brown strap', category=self.tax['bags'],
            location=self.tax['cafeteria'], status='approved')

    def test_notification_keeps_item_thumbnail_context(self):
        note = notify_user(self.u['owner'], kind='match', title='Potential match', item=self.item,
                           url=self.item.get_absolute_url())
        self.assertEqual(note.item, self.item)
        self.client.force_login(self.u['owner'])
        response = self.client.get(reverse('notifications:inbox'))
        self.assertContains(response, 'Potential match')
        self.assertContains(response, 'notification-visual')

    def test_match_trigger_notifies_both_reporters_once(self):
        Item.objects.create(reporter=self.u['finder'], report_type='found', title='Bag',
                            description='Cream canvas bag with a brown strap', category=self.tax['bags'],
                            location=self.tax['cafeteria'], status='approved')
        run_matching(self.item)
        run_matching(self.item)
        self.assertEqual(Notification.objects.filter(user=self.u['owner'], kind='match').count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.u['finder'], kind='match').count(), 1)
        self.assertFalse(Notification.objects.filter(user=self.u['other']).exists())
        note = Notification.objects.get(user=self.u['finder'])
        self.assertIn('not proof of ownership', note.body)
        self.assertTrue(note.url)

    def test_dedupe_reuses_identical_unread_notification(self):
        a = notify_user(self.u['owner'], kind='match', title='Same', url='/x/', dedupe=True)
        b = notify_user(self.u['owner'], kind='match', title='Same', url='/x/', dedupe=True)
        self.assertEqual(a.pk, b.pk)
        a.is_read = True
        a.save()
        c = notify_user(self.u['owner'], kind='match', title='Same', url='/x/', dedupe=True)
        self.assertNotEqual(a.pk, c.pk)
        d = notify_user(self.u['owner'], kind='match', title='Same', url='/x/')
        self.assertNotEqual(c.pk, d.pk)

    def test_read_unread_updates_and_json(self):
        n1 = notify_user(self.u['owner'], title='One', url='/dashboard/')
        n2 = notify_user(self.u['owner'], title='Two', url='/dashboard/')
        self.client.force_login(self.u['owner'])
        self.assertEqual(self.client.get(reverse('notifications:unread_count')).json()['unread'], 2)
        response = self.client.post(reverse('notifications:read', args=[n1.pk]),
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.json(), {'id': n1.pk, 'is_read': True, 'unread': 1})
        response = self.client.post(reverse('notifications:read', args=[n1.pk]), {'unread': '1'},
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.json()['unread'], 2)
        response = self.client.get(reverse('notifications:open', args=[n2.pk]))
        self.assertRedirects(response, '/dashboard/')
        n2.refresh_from_db()
        self.assertTrue(n2.is_read)
        self.client.post(reverse('notifications:read_all'))
        self.assertEqual(self.u['owner'].notifications.filter(is_read=False).count(), 0)
        page = self.client.get(reverse('notifications:inbox'), {'unread': '1'})
        self.assertEqual(page.context['page_obj'].paginator.count, 0)

    def test_notifications_are_private_to_their_recipient(self):
        note = notify_user(self.u['owner'], title='Private', url='/dashboard/')
        self.client.force_login(self.u['other'])
        self.assertEqual(self.client.get(reverse('notifications:open', args=[note.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('notifications:read', args=[note.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('notifications:inbox')), 'Private')
        self.assertEqual(self.client.get(reverse('notifications:read', args=[note.pk])).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('notifications:unread_count')).status_code, 302)

    def test_inbox_filters_and_paginates(self):
        for i in range(25):
            notify_user(self.u['owner'], kind='claim' if i % 2 else 'match', title=f'N{i}', url='/')
        self.client.force_login(self.u['owner'])
        page = self.client.get(reverse('notifications:inbox'))
        self.assertEqual(page.context['page_obj'].paginator.num_pages, 2)
        page = self.client.get(reverse('notifications:inbox'), {'kind': 'claim'})
        self.assertEqual(page.context['page_obj'].paginator.count, 12)
