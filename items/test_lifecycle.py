"""Report lifecycle integrity and independent moderation (spec 2.1, 2.2, 9.4)."""
from django.test import TestCase
from django.urls import reverse

from claims.models import Claim
from config.testing import lightweight_models, make_taxonomy, make_users
from dashboard.models import ModerationDecision
from items import lifecycle
from items.models import Item, ReportFlag
from matching.models import Match
from notifications.models import Notification


@lightweight_models
class IndependentModerationTests(TestCase):
    def setUp(self):
        self.t = make_taxonomy()
        self.u = make_users()
        self.own = Item.objects.create(reporter=self.u['mod'], report_type='found', title='Blue umbrella',
                                       description='Blue folding umbrella', category=self.t['others'],
                                       location=self.t['library'])

    def test_moderator_cannot_approve_or_reject_their_own_report(self):
        self.client.force_login(self.u['mod'])
        for decision in ('approve', 'reject'):
            self.client.post(reverse('dashboard:review_report', args=[self.own.pk, decision]), {'note': 'x'})
            self.own.refresh_from_db()
            self.assertEqual(self.own.status, 'pending', decision)
        self.assertFalse(ModerationDecision.objects.filter(item=self.own).exists())
        # the item page shows no actionable review for them either
        page = self.client.get(reverse('dashboard:moderation'))
        self.assertNotIn(self.own, list(page.context['pending_items']))
        self.assertIn(self.own, list(page.context['own_pending']))
        # another moderator can
        self.client.force_login(self.u['admin'])
        self.client.post(reverse('dashboard:review_report', args=[self.own.pk, 'approve']))
        self.own.refresh_from_db()
        self.assertEqual(self.own.status, 'approved')
        decision = ModerationDecision.objects.get(item=self.own, action='report_approved')
        self.assertEqual((decision.from_state, decision.to_state, decision.actor), ('pending', 'approved', self.u['admin']))

    def test_moderator_cannot_remove_their_own_report_through_flags(self):
        self.own.status = 'approved'
        self.own.save()
        ReportFlag.objects.create(item=self.own, reporter=self.u['other'], reason='duplicate')
        self.client.force_login(self.u['mod'])
        self.client.post(reverse('dashboard:resolve_flag', args=[self.own.pk, 'reject']))
        self.own.refresh_from_db()
        self.assertEqual(self.own.status, 'approved')
        self.assertTrue(self.own.is_active)

    def test_repeated_approval_is_refused_without_duplicate_records(self):
        self.client.force_login(self.u['admin'])
        url = reverse('dashboard:review_report', args=[self.own.pk, 'approve'])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(ModerationDecision.objects.filter(item=self.own, action='report_approved').count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.u['mod'], title__icontains='approved').count(), 1)


@lightweight_models
class LifecycleTests(TestCase):
    def setUp(self):
        self.t = make_taxonomy()
        self.u = make_users()
        self.found = Item.objects.create(reporter=self.u['finder'], report_type='found', title='Black wallet',
                                         description='Black leather wallet', category=self.t['bags'],
                                         location=self.t['library'], status='approved')
        self.lost = Item.objects.create(reporter=self.u['owner'], report_type='lost', title='Wallet',
                                        description='Black leather wallet', category=self.t['bags'],
                                        location=self.t['library'], status='approved')

    def test_invalid_transitions_are_refused(self):
        lifecycle.transition(self.found, Item.Status.RETURNED, actor=self.u['admin'])
        for target in (Item.Status.APPROVED, Item.Status.PENDING, Item.Status.CLAIMED):
            with self.assertRaises(lifecycle.InvalidTransition):
                lifecycle.transition(self.found, target, actor=self.u['admin'])
        pending = Item.objects.create(reporter=self.u['other'], report_type='lost', title='Keys',
                                      description='Bunch of keys', category=self.t['others'],
                                      location=self.t['library'])
        with self.assertRaises(lifecycle.InvalidTransition):
            lifecycle.transition(pending, Item.Status.RETURNED, actor=self.u['admin'])

    def test_withdrawn_report_leaves_search_matching_and_claims(self):
        from matching.services import run_matching
        run_matching(self.lost)
        self.assertTrue(Match.objects.filter(found_item=self.found).exists())
        self.client.force_login(self.u['finder'])
        self.client.post(reverse('items:withdraw', args=[self.found.pk]))
        self.found.refresh_from_db()
        self.assertEqual((self.found.status, self.found.is_active), ('withdrawn', False))
        self.assertFalse(Item.objects.matchable().filter(pk=self.found.pk).exists())
        self.assertFalse(Match.objects.filter(found_item=self.found).exists())
        self.assertEqual(self.client.get(reverse('items:search_api')).json()['total'], 1)  # only the lost one
        self.client.force_login(self.u['owner'])
        self.client.post(reverse('claims:start', args=[self.found.pk]), {'statement': 'mine'})
        self.assertFalse(Claim.objects.exists())
        self.assertTrue(ModerationDecision.objects.filter(action='report_withdrawn', to_state='withdrawn').exists())
        # withdrawing twice is refused
        self.client.force_login(self.u['finder'])
        self.client.post(reverse('items:withdraw', args=[self.found.pk]))
        self.assertEqual(ModerationDecision.objects.filter(action='report_withdrawn').count(), 1)
        page = self.client.get(reverse('items:mine') + '?status=withdrawn')
        self.assertContains(page, 'Black wallet')

    def _edit_found(self, **extra):
        self.client.force_login(self.u['finder'])
        self.client.post(reverse('items:edit', args=[self.found.pk]), {
            'report_type': 'found', 'title': 'Black wallet', 'category': self.t['bags'].pk,
            'location': self.t['library'].pk, 'event_date': '2026-11-01',
            'description': 'Black leather wallet, silver logo', 'private_details': 'Coin pocket zip broken',
            **extra})
        self.found.refresh_from_db()

    def test_editing_a_plain_report_keeps_it_published_and_audited(self):
        self._edit_found()
        self.assertEqual(self.found.status, 'approved')
        self.assertEqual(self.found.private_details, 'Coin pocket zip broken')
        self.assertTrue(ModerationDecision.objects.filter(action='report_auto_published', from_state='approved',
                                                          to_state='approved', item=self.found).exists())

    def test_editing_to_declare_personal_details_sends_report_back_to_review(self):
        self._edit_found(declare_sensitive='on')
        self.assertEqual(self.found.status, 'pending')
        self.assertTrue(ModerationDecision.objects.filter(action='report_resubmitted', from_state='approved',
                                                          to_state='pending').exists())


class AuditTrailTests(TestCase):
    def test_audit_records_are_append_only(self):
        u = make_users()
        record = ModerationDecision.objects.create(actor=u['mod'], action='report_approved', note='ok')
        record.note = 'tampered'
        with self.assertRaises(ValueError):
            record.save()
        with self.assertRaises(ValueError):
            record.delete()
        self.assertEqual(ModerationDecision.objects.get().note, 'ok')

    def test_admin_cannot_edit_or_delete_history(self):
        from django.contrib.admin.sites import site
        u = make_users()
        u['admin'].is_staff = True
        u['admin'].is_superuser = True
        u['admin'].save()
        model_admin = site._registry[ModerationDecision]

        class Req:
            user = u['admin']
        self.assertFalse(model_admin.has_change_permission(Req()))
        self.assertFalse(model_admin.has_delete_permission(Req()))
        self.assertFalse(model_admin.has_add_permission(Req()))
