from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from items.models import Category, Item, Location


class AnalyticsTests(TestCase):
    def test_operational_eda_is_visible_to_administrator(self):
        admin = User.objects.create_user(username='admin-user', role='admin')
        category = Category.objects.create(name='Electronics', slug='electronics')
        location = Location.objects.create(name='Lab', slug='lab')
        Item.objects.create(reporter=admin, report_type='lost', title='Phone',
                            description='Black phone', category=category,
                            location=location, status='approved')
        self.client.force_login(admin)
        response = self.client.get(reverse('dashboard:analytics'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Operational data quality')
        self.assertContains(response, 'Reports with photos')
