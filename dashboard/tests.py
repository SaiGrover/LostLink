import csv
import io
from datetime import date, timedelta

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from claims.models import Claim
from config.testing import lightweight_models, make_taxonomy, make_users
from dashboard.models import ModerationDecision
from items.models import Category, Item, Location, ReportFlag
from matching.models import Match
from notifications.models import Notification


@lightweight_models
class ModerationTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.u = make_users()
        self.pending = Item.objects.create(reporter=self.u['owner'], report_type='lost', title='Pending wallet',
                                           description='Black leather wallet with silver logo',
                                           category=self.tax['bags'], location=self.tax['library'])
        self.found = Item.objects.create(reporter=self.u['finder'], report_type='found', title='Found wallet',
                                         description='Black leather wallet with silver logo',
                                         category=self.tax['bags'], location=self.tax['library'], status='approved')

    def test_approve_publishes_runs_matching_and_records_decision(self):
        self.client.force_login(self.u['mod'])
        response = self.client.post(reverse('dashboard:review_report', args=[self.pending.pk, 'approve']),
                                    {'note': 'Looks genuine'})
        self.assertRedirects(response, reverse('dashboard:moderation'))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'approved')
        self.assertEqual(Match.objects.count(), 1)
        decision = ModerationDecision.objects.get(action='report_approved')
        self.assertEqual((decision.actor, decision.item, decision.note), (self.u['mod'], self.pending, 'Looks genuine'))
        self.assertTrue(Notification.objects.filter(user=self.u['owner'], kind='report', title__icontains='approved').exists())

    def test_reject_with_reason_notifies_reporter(self):
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('dashboard:review_report', args=[self.pending.pk, 'reject']), {'note': 'Blurry photo'})
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'rejected')
        self.assertIn('Blurry photo', Notification.objects.get(user=self.u['owner'], kind='report').body)
        # cannot be re-moderated, GET not allowed, students forbidden
        self.client.post(reverse('dashboard:review_report', args=[self.pending.pk, 'approve']))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, 'rejected')
        self.assertEqual(self.client.get(reverse('dashboard:review_report', args=[self.pending.pk, 'approve'])).status_code, 405)
        self.client.force_login(self.u['owner'])
        self.assertEqual(self.client.post(reverse('dashboard:review_report', args=[self.pending.pk, 'approve'])).status_code, 403)

    def test_queue_shows_pending_claims_disputes_flags_and_history(self):
        claim = Claim.objects.create(item=self.found, claimant=self.u['owner'], statement='mine')
        Claim.objects.create(item=self.found, claimant=self.u['other'], statement='no, mine', status='disputed',
                             decision_note='Both say silver logo')
        ReportFlag.objects.create(item=self.found, reporter=self.u['other'], reason='duplicate', note='Seen twice')
        self.pending.duplicate_of = self.found
        self.pending.save()
        self.client.force_login(self.u['mod'])
        page = self.client.get(reverse('dashboard:moderation'))
        self.assertEqual(page.status_code, 200)
        ctx = page.context
        self.assertEqual((ctx['pending_count'], ctx['claim_count'], ctx['dispute_count'],
                          ctx['flag_count'], ctx['duplicate_count']), (1, 1, 1, 1, 1))
        self.assertContains(page, 'Seen twice')
        self.assertContains(page, 'Possible duplicate')
        self.assertIn(claim, list(ctx['open_claims']))

    def test_flag_resolution(self):
        ReportFlag.objects.create(item=self.found, reporter=self.u['other'], reason='suspicious')
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('dashboard:resolve_flag', args=[self.found.pk, 'dismiss']))
        self.assertEqual(ReportFlag.objects.filter(resolved_at__isnull=True).count(), 0)
        self.found.refresh_from_db()
        self.assertEqual(self.found.status, 'approved')
        ReportFlag.objects.create(item=self.found, reporter=self.u['owner'], reason='inappropriate')
        self.client.post(reverse('dashboard:resolve_flag', args=[self.found.pk, 'reject']), {'note': 'Not an item'})
        self.found.refresh_from_db()
        self.assertEqual(self.found.status, 'rejected')
        self.assertFalse(self.found.is_active)
        self.assertEqual(ModerationDecision.objects.filter(action='flag_resolved').count(), 2)
        self.assertTrue(Notification.objects.filter(user=self.u['finder'], title__icontains='removed').exists())

    def test_decision_history_page_filters(self):
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('dashboard:review_report', args=[self.pending.pk, 'approve']))
        page = self.client.get(reverse('dashboard:decisions'), {'action': 'report_approved'})
        self.assertContains(page, 'Pending wallet')
        page = self.client.get(reverse('dashboard:decisions'), {'action': 'claim_rejected'})
        self.assertEqual(page.context['page_obj'].paginator.count, 0)


@lightweight_models
class AnalyticsAndExportTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.u = make_users()
        today = timezone.localdate()
        self.items = []
        for i, (rtype, cat, loc, status, days) in enumerate([
                ('lost', 'bags', 'library', 'approved', 1), ('lost', 'bags', 'library', 'returned', 2),
                ('found', 'bags', 'cafeteria', 'approved', 3), ('found', 'electronics', 'library', 'pending', 0),
                ('lost', 'electronics', 'block_a', 'rejected', 5)]):
            item = Item.objects.create(reporter=self.u['owner'] if i % 2 else self.u['finder'], report_type=rtype,
                                       title=f'Item {i}', description='Descriptive enough text',
                                       category=self.tax[cat], location=self.tax[loc], status=status,
                                       event_date=today - timedelta(days=days), is_active=status != 'returned')
            self.items.append(item)
        self.items[0].created_at = timezone.now() - timedelta(days=100)
        self.items[0].save(update_fields=['created_at'])
        Match.objects.create(lost_item=self.items[0], found_item=self.items[2], score=.9, tier='high',
                             engine_version=settings.MATCHING['VERSION'], text_score=.8)
        Match.objects.create(lost_item=self.items[1], found_item=self.items[2], score=.72, tier='medium',
                             engine_version=settings.MATCHING['VERSION'], text_score=.6)
        Match.objects.create(lost_item=self.items[4], found_item=self.items[2], score=.5, tier='low',
                             engine_version='old-version')
        Claim.objects.create(item=self.items[2], claimant=self.u['owner'], statement='x', status='approved')
        Claim.objects.create(item=self.items[2], claimant=self.u['other'], statement='y', status='rejected')
        Claim.objects.create(item=self.items[0], claimant=self.u['finder'], statement='z')
        self.client.force_login(self.u['admin'])

    def test_analytics_counts_are_computed_from_records(self):
        page = self.client.get(reverse('dashboard:analytics'))
        self.assertEqual(page.status_code, 200)
        k = page.context['kpis']
        self.assertEqual((k['users'], k['reports'], k['lost'], k['found'], k['pending']), (5, 5, 3, 2, 1))
        # Only live pairs count: the medium match involves a returned report, so
        # it stays stored as evidence but is no longer an open match.
        self.assertEqual((k['matches'], k['high'], k['medium'], k['low']), (1, 1, 0, 0))
        self.assertEqual((k['claims'], k['claims_pending'], k['claims_approved'], k['claims_rejected']), (3, 1, 1, 1))
        self.assertEqual(k['returned'], 1)
        self.assertEqual(page.context['by_category'][0]['category__name'], 'Bags & Wallets')
        self.assertEqual(page.context['by_location'][0]['location__name'], 'Central Library')
        self.assertEqual(sum(b['n'] for b in page.context['histogram']), 1)
        self.assertEqual(len(page.context['trends']), 12)
        self.assertEqual(sum(r['total'] for r in page.context['trends']), 4)  # one report is older than 12 weeks
        self.assertContains(page, 'Operational data quality')
        self.assertContains(page, 'Reports with photos')

    def test_analytics_date_range_filter(self):
        start = (timezone.localdate() - timedelta(days=1)).isoformat()
        page = self.client.get(reverse('dashboard:analytics'), {'from': start})
        self.assertEqual(page.context['kpis']['reports'], 4)
        self.assertTrue(page.context['period']['active'])

    def test_empty_database_renders_without_errors(self):
        Claim.objects.all().delete()
        Match.objects.all().delete()
        Item.objects.all().delete()
        self.assertEqual(self.client.get(reverse('dashboard:analytics')).status_code, 200)
        pdf = self.client.get(reverse('dashboard:export_pdf'))
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        rows = list(csv.reader(io.StringIO(self.client.get(reverse('dashboard:export_csv')).content.decode())))
        self.assertEqual(len(rows), 1)

    def test_csv_export_content_filters_and_privacy(self):
        response = self.client.get(reverse('dashboard:export_csv'))
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 6)
        header = rows[0]
        self.assertIn('Reporter ID', header)
        self.assertNotIn('Reporter', header[:-1] if 'Reporter' in header else header)
        body = response.content.decode()
        for private in ('owner@x.edu', 'finder', '+91 90000 00001'):
            self.assertNotIn(private, body)
        filtered = self.client.get(reverse('dashboard:export_csv'), {'type': 'found', 'status': 'approved'})
        self.assertEqual(len(list(csv.reader(io.StringIO(filtered.content.decode())))), 2)
        dated = self.client.get(reverse('dashboard:export_csv'),
                                {'from': (timezone.localdate() - timedelta(days=1)).isoformat()})
        self.assertEqual(len(list(csv.reader(io.StringIO(dated.content.decode())))), 5)
        self.assertIn('_', dated['Content-Disposition'])

    def test_csv_formula_escape(self):
        self.items[0].title = '=HYPERLINK("bad")'
        self.items[0].save()
        self.assertIn(b"'=HYPERLINK", self.client.get(reverse('dashboard:export_csv')).content)

    def test_pdf_month_and_range(self):
        month = timezone.localdate().strftime('%Y-%m')
        pdf = self.client.get(reverse('dashboard:export_pdf'), {'month': month})
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertIn(month, pdf['Content-Disposition'])
        self.assertEqual(self.client.get(reverse('dashboard:export_pdf'), {'month': 'invalid'}).status_code, 400)
        self.assertEqual(self.client.get(reverse('dashboard:export_pdf'), {'from': 'bad'}).status_code, 400)
        ranged = self.client.get(reverse('dashboard:export_pdf'), {'from': '2026-01-01', 'to': '2026-01-31'})
        self.assertTrue(ranged.content.startswith(b'%PDF'))

    def test_exports_restricted_to_administrators(self):
        for user in (self.u['owner'], self.u['mod']):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('dashboard:export_csv')).status_code, 403)
            self.assertEqual(self.client.get(reverse('dashboard:export_pdf')).status_code, 403)


@lightweight_models
class AdministrationTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.u = make_users()
        self.client.force_login(self.u['admin'])

    def test_role_change_is_recorded_and_guarded(self):
        target = self.u['owner']
        self.client.post(reverse('dashboard:users'), {'user_id': target.pk, 'role': 'moderator'})
        target.refresh_from_db()
        self.assertEqual(target.role, 'moderator')
        self.assertTrue(ModerationDecision.objects.filter(action='role_changed', subject_user=target).exists())
        self.assertTrue(Notification.objects.filter(user=target, title__icontains='role').exists())
        self.client.post(reverse('dashboard:users'), {'user_id': self.u['admin'].pk, 'role': 'student'})
        self.u['admin'].refresh_from_db()
        self.assertEqual(self.u['admin'].role, 'admin')
        self.client.post(reverse('dashboard:users'), {'user_id': target.pk, 'role': 'president'})
        target.refresh_from_db()
        self.assertEqual(target.role, 'moderator')

    def test_user_search_and_status_toggle(self):
        page = self.client.get(reverse('dashboard:users'), {'q': 'owner'})
        self.assertEqual(page.context['page_obj'].paginator.count, 1)
        self.client.post(reverse('dashboard:user_status', args=[self.u['other'].pk]))
        self.u['other'].refresh_from_db()
        self.assertFalse(self.u['other'].is_active)
        self.client.post(reverse('dashboard:user_status', args=[self.u['admin'].pk]))
        self.u['admin'].refresh_from_db()
        self.assertTrue(self.u['admin'].is_active)

    def test_taxonomy_add_edit_delete(self):
        self.client.post(reverse('dashboard:taxonomy'), {'kind': 'category', 'name': 'Keys', 'icon': '🔑',
                                                         'related': [self.tax['others'].pk]})
        keys = Category.objects.get(slug='keys')
        self.assertIn(self.tax['others'], keys.related.all())
        self.client.post(reverse('dashboard:taxonomy'), {'kind': 'location', 'name': 'Sports Complex',
                                                         'adjacent': [self.tax['cafeteria'].pk]})
        sports = Location.objects.get(slug='sports-complex')
        self.assertIn(self.tax['cafeteria'], sports.adjacent.all())
        self.assertIn(sports, self.tax['cafeteria'].adjacent.all())
        # edit
        self.client.post(reverse('dashboard:taxonomy_edit', args=['category', keys.pk]),
                         {'name': 'Keys & Fobs', 'icon': '🔑', 'description': '', 'related': []})
        keys.refresh_from_db()
        self.assertEqual((keys.name, keys.slug, keys.related.count()), ('Keys & Fobs', 'keys-fobs', 0))
        # delete: unused ok, used protected
        self.client.post(reverse('dashboard:taxonomy_delete', args=['category', keys.pk]))
        self.assertFalse(Category.objects.filter(pk=keys.pk).exists())
        Item.objects.create(reporter=self.u['owner'], report_type='lost', title='x', description='descriptive',
                            category=self.tax['bags'], location=self.tax['library'])
        self.client.post(reverse('dashboard:taxonomy_delete', args=['category', self.tax['bags'].pk]))
        self.assertTrue(Category.objects.filter(pk=self.tax['bags'].pk).exists())
        self.assertEqual(self.client.get(reverse('dashboard:taxonomy_edit', args=['weird', 1])).status_code, 302)
