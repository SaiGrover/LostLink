from datetime import date, time, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users, png_upload
from items.forms import ItemForm
from items.models import Item, ReportFlag
from matching.models import Match


class ReportSchemaTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()

    def test_approximate_event_time_is_stored(self):
        form = ItemForm(data={
            'report_type': 'lost', 'title': 'Notebook',
            'category': self.tax['bags'].pk, 'location': self.tax['library'].pk,
            'event_date': date.today().isoformat(), 'event_time': '14:35',
            'description': 'Blue laboratory notebook', 'brand': '', 'color': 'Blue',
        })
        self.assertTrue(form.is_valid(), form.errors)
        item = form.save(commit=False)
        item.reporter = self.users['owner']
        item.save()
        self.assertEqual(item.event_time, time(14, 35))

    def test_manual_study_mode_disables_live_search_script(self):
        response = self.client.get('/search/', {'study': 'manual'})
        self.assertContains(response, 'Study baseline')
        self.assertNotContains(response, 'js/search.js')


@lightweight_models
@isolated_media
class ReportingTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()
        self.client.force_login(self.users['owner'])

    def payload(self, **overrides):
        data = {'report_type': 'lost', 'title': 'Black leather wallet',
                'category': self.tax['bags'].pk, 'location': self.tax['library'].pk,
                'event_date': timezone.localdate().isoformat(), 'event_time': '',
                'description': 'Black leather wallet with a silver logo and a library card inside',
                'brand': '', 'color': 'black'}
        data.update(overrides)
        return data

    def test_create_lost_report_with_photo_is_pending_and_featurised(self):
        response = self.client.post(reverse('items:report'), self.payload(image=png_upload()))
        item = Item.objects.get()
        self.assertRedirects(response, item.get_absolute_url())
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertEqual(item.reporter, self.users['owner'])
        self.assertTrue(item.image)
        self.assertIsNotNone(item.image_vector)
        self.assertTrue(item.feature_version)
        self.assertFalse(item.is_public)

    def test_create_found_report_without_photo(self):
        self.client.post(reverse('items:report'), self.payload(report_type='found'))
        item = Item.objects.get()
        self.assertTrue(item.is_found)
        self.assertIsNone(item.image_vector)

    def test_invalid_submissions_are_rejected(self):
        future = (timezone.localdate() + timedelta(days=2)).isoformat()
        cases = [
            self.payload(event_date=future),
            self.payload(description='short'),
            self.payload(title=''),
            self.payload(category=999),
            self.payload(image=SimpleUploadedFile('x.png', b'not an image at all', content_type='image/png')),
            self.payload(image=SimpleUploadedFile('x.txt', b'hello', content_type='text/plain')),
        ]
        for data in cases:
            response = self.client.post(reverse('items:report'), data)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(Item.objects.count(), 0)

    def test_oversized_image_rejected(self):
        big = SimpleUploadedFile('big.png', b'0' * (6 * 1024 * 1024), content_type='image/png')
        form = ItemForm(data=self.payload(), files={'image': big})
        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)

    def test_near_duplicate_by_same_user_is_flagged_for_review(self):
        self.client.post(reverse('items:report'), self.payload())
        self.client.post(reverse('items:report'), self.payload(title='Wallet again'))
        second = Item.objects.order_by('-pk').first()
        self.assertIsNotNone(second.duplicate_of)

    def test_owner_edit_returns_report_to_review_and_clears_matches(self):
        item = Item.objects.create(reporter=self.users['owner'], report_type='lost', title='Wallet',
                                   description='Black leather wallet', category=self.tax['bags'],
                                   location=self.tax['library'], status='approved')
        found = Item.objects.create(reporter=self.users['finder'], report_type='found', title='Wallet',
                                    description='Black leather wallet', category=self.tax['bags'],
                                    location=self.tax['library'], status='approved')
        Match.objects.create(lost_item=item, found_item=found, score=.9, tier='high')
        response = self.client.post(reverse('items:edit', args=[item.pk]),
                                    self.payload(description='Black leather wallet, now with more detail'))
        self.assertRedirects(response, item.get_absolute_url())
        item.refresh_from_db()
        self.assertEqual(item.status, Item.Status.PENDING)
        self.assertEqual(Match.objects.count(), 0)

    def test_other_users_cannot_edit_or_withdraw(self):
        item = Item.objects.create(reporter=self.users['finder'], report_type='found', title='Wallet',
                                   description='Black leather wallet', category=self.tax['bags'],
                                   location=self.tax['library'], status='approved')
        self.assertEqual(self.client.get(reverse('items:edit', args=[item.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('items:withdraw', args=[item.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('items:withdraw', args=[item.pk])).status_code, 405)

    def test_withdraw_deactivates_and_hides_report(self):
        item = Item.objects.create(reporter=self.users['owner'], report_type='lost', title='Wallet',
                                   description='Black leather wallet', category=self.tax['bags'],
                                   location=self.tax['library'], status='approved')
        self.client.post(reverse('items:withdraw', args=[item.pk]))
        item.refresh_from_db()
        self.assertFalse(item.is_active)
        self.client.force_login(self.users['other'])
        self.assertEqual(self.client.get(item.get_absolute_url()).status_code, 403)
        self.assertEqual(self.client.get(reverse('items:search_api')).json()['total'], 0)

    def test_my_reports_filters_and_paginates(self):
        for i in range(14):
            Item.objects.create(reporter=self.users['owner'], report_type='lost' if i % 2 else 'found',
                                title=f'Item {i}', description='Something descriptive here',
                                category=self.tax['bags'], location=self.tax['library'], status='approved')
        response = self.client.get(reverse('items:mine'))
        self.assertEqual(response.context['page_obj'].paginator.num_pages, 2)
        response = self.client.get(reverse('items:mine'), {'type': 'lost'})
        self.assertEqual(response.context['total'], 7)


@lightweight_models
@isolated_media
class VisibilityAndMediaTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()
        self.item = Item.objects.create(reporter=self.users['finder'], report_type='found', title='Phone',
                                        description='Blue phone with a cracked case',
                                        category=self.tax['electronics'], location=self.tax['cafeteria'],
                                        image=png_upload('pending.png'))

    def test_pending_report_hidden_from_search_detail_and_media(self):
        self.assertEqual(self.client.get(reverse('items:search_api')).json()['total'], 0)
        self.client.force_login(self.users['other'])
        self.assertEqual(self.client.get(self.item.get_absolute_url()).status_code, 403)
        self.assertEqual(self.client.get(self.item.image.url).status_code, 403)
        # the owner, a moderator and (once approved) everyone can see it
        self.client.force_login(self.users['finder'])
        self.assertEqual(self.client.get(self.item.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(self.item.image.url).status_code, 200)
        self.client.force_login(self.users['mod'])
        self.assertEqual(self.client.get(self.item.image.url).status_code, 200)
        self.item.status = 'approved'
        self.item.save()
        self.client.logout()
        self.assertEqual(self.client.get(self.item.image.url).status_code, 200)

    def test_media_response_is_private_and_unknown_path_is_404(self):
        self.item.status = 'approved'
        self.item.save()
        response = self.client.get(self.item.image.url)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(self.client.get('/media/items/2026/01/nope.png').status_code, 404)

    def test_flagging_queues_report_for_moderation(self):
        self.item.status = 'approved'
        self.item.save()
        self.client.force_login(self.users['other'])
        response = self.client.post(reverse('items:flag', args=[self.item.pk]),
                                    {'reason': 'suspicious', 'note': 'Looks like a repost'})
        self.assertRedirects(response, self.item.get_absolute_url())
        self.assertEqual(ReportFlag.objects.filter(item=self.item, resolved_at__isnull=True).count(), 1)
        # one open flag per user
        self.client.post(reverse('items:flag', args=[self.item.pk]), {'reason': 'other'})
        self.assertEqual(ReportFlag.objects.count(), 1)
        # owners cannot flag their own report
        self.client.force_login(self.users['finder'])
        self.assertEqual(self.client.post(reverse('items:flag', args=[self.item.pk]),
                                          {'reason': 'other'}).status_code, 403)
        self.client.force_login(self.users['mod'])
        self.assertContains(self.client.get(reverse('dashboard:moderation')), 'Looks like a repost')


@lightweight_models
class SearchTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()
        specs = [
            ('lost', 'Black leather wallet', 'bags', 'library', '2026-01-10'),
            ('found', 'Brown wallet', 'bags', 'block_a', '2026-01-12'),
            ('lost', 'Blue iPhone', 'electronics', 'cafeteria', '2026-02-01'),
            ('found', 'Silver laptop charger', 'electronics', 'library', '2026-02-05'),
        ]
        for rtype, title, cat, loc, day in specs:
            Item.objects.create(reporter=self.users['owner'], report_type=rtype, title=title,
                                description=f'{title} reported on campus', category=self.tax[cat],
                                location=self.tax[loc], event_date=date.fromisoformat(day), status='approved')
        Item.objects.create(reporter=self.users['owner'], report_type='lost', title='Hidden pending wallet',
                            description='should not appear', category=self.tax['bags'],
                            location=self.tax['library'], status='pending')

    def api(self, **params):
        return self.client.get(reverse('items:search_api'), params).json()

    def test_keyword_category_location_and_type_filters(self):
        self.assertEqual(self.api(q='wallet')['total'], 2)
        self.assertEqual(self.api(category='electronics')['total'], 2)
        self.assertEqual(self.api(location='library')['total'], 2)
        self.assertEqual(self.api(type='found')['total'], 2)

    def test_date_range_combined_filters_and_unapproved_exclusion(self):
        self.assertEqual(self.api(date_from='2026-02-01')['total'], 2)
        self.assertEqual(self.api(date_from='2026-01-11', date_to='2026-02-01')['total'], 2)
        self.assertEqual(self.api(q='wallet', type='lost', location='library')['total'], 1)
        self.assertEqual(self.api(q='pending')['total'], 0)
        self.assertNotIn('Hidden pending', self.api()['html'])

    def test_invalid_parameters_are_ignored_not_errors(self):
        data = self.api(type='weird', date_from='not-a-date', sort='bogus', page='99', category='x' * 500)
        self.assertEqual(data['total'], 0)  # unknown category slug -> no rows
        data = self.api(type='weird', date_from='2026-13-45', sort='bogus', page='abc')
        self.assertEqual(data['total'], 4)
        self.assertEqual(data['page'], 1)
        self.assertEqual(data['filters']['sort'], 'newest')

    def test_sorting_and_status_filter(self):
        titles = [t for t in ['Black leather wallet', 'Blue iPhone', 'Brown wallet', 'Silver laptop charger']]
        html = self.api(sort='title')['html']
        positions = [html.index(t) for t in titles]
        self.assertEqual(positions, sorted(positions))
        Item.objects.filter(title='Brown wallet').update(status='claimed')
        self.assertEqual(self.api(status='claimed')['total'], 1)
        self.assertEqual(self.api(status='approved')['total'], 3)

    def test_pagination_in_json_and_html(self):
        for i in range(30):
            Item.objects.create(reporter=self.users['owner'], report_type='found', title=f'Bottle {i}',
                                description='Steel bottle number', category=self.tax['others'],
                                location=self.tax['library'], status='approved')
        first = self.api(q='bottle')
        self.assertEqual((first['total'], first['pages'], first['page']), (30, 2, 1))
        second = self.api(q='bottle', page=2)
        self.assertEqual(second['page'], 2)
        self.assertIn('data-page="1"', second['html'])
        page = self.client.get(reverse('items:search'), {'q': 'bottle', 'page': 2})
        self.assertEqual(page.context['page_obj'].number, 2)
        manual = self.client.get(reverse('items:search'), {'q': 'bottle', 'study': 'manual'})
        self.assertContains(manual, 'page=2')

    def test_browse_pages_lock_the_type(self):
        response = self.client.get(reverse('items:lost'), {'type': 'found'})
        self.assertEqual(response.context['total'], 2)
        self.assertTrue(all(i.is_lost for i in response.context['items']))

    def test_empty_results_render_an_empty_state(self):
        self.assertIn('No items match', self.api(q='zzzz-nothing')['html'])
