"""seed_taxonomy: empty-database initialisation, idempotency, customisations,
and the seed_demo production guard."""
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from items.forms import ItemForm
from items.models import Category, Location
from items.taxonomy import CATEGORIES, LOCATIONS


def seed(*args):
    out = StringIO()
    call_command('seed_taxonomy', *args, stdout=out)
    return out.getvalue()


class SeedTaxonomyTests(TestCase):
    def test_empty_database_becomes_usable(self):
        self.assertEqual((Category.objects.count(), Location.objects.count()), (0, 0))
        self.assertFalse(ItemForm(data={}).fields['category'].queryset.exists())
        seed()
        self.assertEqual(Category.objects.count(), len(CATEGORIES))
        self.assertEqual(Location.objects.count(), len(LOCATIONS))
        bags, others = Category.objects.get(slug='bags-wallets'), Category.objects.get(slug='others')
        self.assertIn(others, bags.related.all())
        self.assertIn(Location.objects.get(slug='academic-block-a'),
                      Location.objects.get(slug='central-library').adjacent.all())
        self.assertEqual(Category.objects.get(slug='id-cards').icon, '🪪')
        form = ItemForm(data={'report_type': 'lost', 'title': 'Black wallet', 'category': bags.pk,
                              'location': Location.objects.first().pk,
                              'event_date': timezone.localdate().isoformat(),
                              'description': 'Black leather wallet with a silver logo'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_creates_no_accounts(self):
        seed()
        self.assertEqual(User.objects.count(), 0)

    def test_idempotent(self):
        seed()
        snapshot = (list(Category.objects.values_list('pk', 'name', 'slug', 'icon')),
                    list(Location.objects.values_list('pk', 'name', 'slug')),
                    Category.related.through.objects.count(), Location.adjacent.through.objects.count())
        out = seed()
        self.assertIn('all defaults already present', out)
        self.assertEqual(snapshot, (list(Category.objects.values_list('pk', 'name', 'slug', 'icon')),
                                    list(Location.objects.values_list('pk', 'name', 'slug')),
                                    Category.related.through.objects.count(),
                                    Location.adjacent.through.objects.count()))

    def test_admin_customisations_are_preserved(self):
        seed()
        electronics = Category.objects.get(slug='electronics')
        electronics.icon, electronics.description = '💻', 'Phones, laptops, chargers'
        electronics.save()
        Category.objects.get(slug='bags-wallets').related.clear()
        Location.objects.get(slug='cafeteria').adjacent.clear()
        # an admin-created category whose name matches a default, with another slug
        Category.objects.filter(slug='keys').update(slug='keys-and-fobs')
        seed()
        electronics.refresh_from_db()
        self.assertEqual((electronics.icon, electronics.description), ('💻', 'Phones, laptops, chargers'))
        self.assertFalse(Category.objects.get(slug='bags-wallets').related.exists())
        self.assertFalse(Location.objects.get(slug='cafeteria').adjacent.exists())
        self.assertFalse(Category.objects.filter(slug='keys').exists())
        self.assertEqual(Category.objects.count(), len(CATEGORIES))

    def test_missing_defaults_are_added_unless_if_empty(self):
        Category.objects.create(name='Umbrellas', slug='umbrellas', icon='☂')
        out = seed('--if-empty')
        self.assertIn('Categories: already configured', out)
        self.assertEqual(Category.objects.count(), 1)
        self.assertEqual(Location.objects.count(), len(LOCATIONS))  # zones were empty, so seeded
        seed()
        self.assertEqual(Category.objects.count(), len(CATEGORIES) + 1)


class SeedDemoGuardTests(TestCase):
    def test_refuses_when_debug_is_off(self):
        with self.assertRaisesMessage(CommandError, 'refusing to run with DEBUG off'):
            call_command('seed_demo', stdout=StringIO())
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Category.objects.count(), 0)

    def test_explicit_override_and_debug_pass_the_guard(self):
        sentinel = RuntimeError('guard passed')
        with patch('items.management.commands.seed_demo.ensure_default_taxonomy', side_effect=sentinel):
            with self.assertRaisesMessage(RuntimeError, 'guard passed'):
                call_command('seed_demo', '--allow-production', stdout=StringIO())
            with override_settings(DEBUG=True), self.assertRaisesMessage(RuntimeError, 'guard passed'):
                call_command('seed_demo', stdout=StringIO())
