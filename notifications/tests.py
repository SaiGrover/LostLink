from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from items.models import Category, Item, Location
from notifications.utils import notify_user


class NotificationContextTests(TestCase):
    def test_notification_keeps_item_thumbnail_context(self):
        user = User.objects.create_user(username='student')
        category = Category.objects.create(name='Bags', slug='bags')
        location = Location.objects.create(name='Cafeteria', slug='cafeteria')
        item = Item.objects.create(
            reporter=user, report_type='lost', title='Canvas bag',
            description='Cream canvas bag', category=category, location=location,
            status='approved')
        note = notify_user(user, kind='match', title='Potential match', item=item,
                           url=item.get_absolute_url())
        self.assertEqual(note.item, item)
        self.client.force_login(user)
        response = self.client.get(reverse('notifications:inbox'))
        self.assertContains(response, 'Potential match')
        self.assertContains(response, 'notification-visual')
