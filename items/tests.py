from datetime import date, time

from django.test import TestCase

from accounts.models import User
from items.forms import ItemForm
from items.models import Category, Item, Location


class ReportSchemaTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Books', slug='books')
        self.location = Location.objects.create(name='Library', slug='library')
        self.user = User.objects.create_user(username='student')

    def test_approximate_event_time_is_stored(self):
        form = ItemForm(data={
            'report_type': 'lost', 'title': 'Notebook',
            'category': self.category.pk, 'location': self.location.pk,
            'event_date': date.today().isoformat(), 'event_time': '14:35',
            'description': 'Blue laboratory notebook', 'brand': '', 'color': 'Blue',
        })
        self.assertTrue(form.is_valid(), form.errors)
        item = form.save(commit=False)
        item.reporter = self.user
        item.save()
        self.assertEqual(item.event_time, time(14, 35))

    def test_manual_study_mode_disables_live_search_script(self):
        response = self.client.get('/search/', {'study': 'manual'})
        self.assertContains(response, 'Study baseline')
        self.assertNotContains(response, 'js/search.js')
