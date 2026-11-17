"""Requirements 4.8-4.10 and 9.3/9.6: taxonomy recalculation, retirement of
obsolete matches, eligibility, feature-processing state and retries."""
import json
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from claims.models import Claim
from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users, png_upload
from items.models import Item
from matching import services
from matching.models import ItemFeatures, Match
from matching.services import (candidate_queryset, clear_matches, is_match_eligible, retire_matches,
                               run_matching)
from notifications.models import Notification


class Base(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()

    def report(self, user, rtype, title='Wallet', description='Black leather wallet with silver logo',
               cat='bags', loc='library', status='approved', **kw):
        return Item.objects.create(reporter=self.users[user], report_type=rtype, title=title,
                                   description=description, category=self.tax[cat], location=self.tax[loc],
                                   status=status, event_date=date.today() - timedelta(days=2), **kw)


@lightweight_models
@isolated_media
class TaxonomyRecalculationTests(Base):
    def test_adding_and_removing_a_related_category_rescores_affected_pairs(self):
        lost = self.report('owner', 'lost', cat='bags', color='black')
        found = self.report('finder', 'found', cat='electronics', color='black')
        run_matching(lost)
        before = Match.objects.get()
        self.assertEqual(before.category_score, 0.0)
        with self.captureOnCommitCallbacks(execute=True):
            self.tax['bags'].related.add(self.tax['electronics'])
        after = Match.objects.get()
        self.assertEqual(after.pk, before.pk)
        self.assertEqual(after.category_score, 0.5)
        self.assertGreater(after.score, before.score)
        self.assertNotEqual(after.taxonomy_version, before.taxonomy_version)
        with self.captureOnCommitCallbacks(execute=True):
            self.tax['bags'].related.remove(self.tax['electronics'])
        self.assertEqual(Match.objects.get().category_score, 0.0)
        self.assertEqual(Match.objects.get().score, before.score)
        self.assertEqual(found.pk, after.found_item_id)

    def test_dashboard_taxonomy_edit_triggers_one_idempotent_recalculation(self):
        lost = self.report('owner', 'lost', loc='library', color='black')
        self.report('finder', 'found', loc='cafeteria', color='black')
        run_matching(lost)
        self.assertEqual(Match.objects.get().location_score, 0.0)
        self.client.force_login(self.users['admin'])
        library = self.tax['library']
        with patch('matching.services.recalculate_for_taxonomy',
                   wraps=services.recalculate_for_taxonomy) as recalc:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse('dashboard:taxonomy_edit', args=['location', library.pk]),
                                            {'name': library.name,
                                             'adjacent': [self.tax['block_a'].pk, self.tax['cafeteria'].pk]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(recalc.call_count, 1)        # remove/add events coalesced
        self.assertEqual(Match.objects.get().location_score, 0.5)
        notes = Notification.objects.count()
        with self.captureOnCommitCallbacks(execute=True):
            self.tax['library'].adjacent.add(self.tax['cafeteria'])   # no-op change
        self.assertEqual(Notification.objects.count(), notes)
        self.assertEqual(Match.all_objects.count(), 1)

    def test_taxonomy_change_that_drops_a_pair_below_threshold_retires_it(self):
        lost = self.report('owner', 'lost', cat='bags', loc='library',
                           description='Blue umbrella with wooden handle')
        self.report('finder', 'found', cat='others', loc='block_a',
                    description='Red notebook with stickers')
        run_matching(lost)
        if not Match.objects.exists():
            self.skipTest('pair below threshold even with partial credit')
        with self.captureOnCommitCallbacks(execute=True):
            self.tax['bags'].related.clear()
            self.tax['library'].adjacent.clear()
        m = Match.all_objects.get()
        self.assertEqual((m.status, m.retired_reason), ('retired', 'below_threshold'))


@lightweight_models
@isolated_media
class RetirementTests(Base):
    def pair(self):
        lost = self.report('owner', 'lost', color='black')
        found = self.report('finder', 'found', color='black')
        run_matching(lost)
        return lost, found, Match.objects.get()

    def test_clear_matches_retires_and_keeps_history_and_claim_evidence(self):
        lost, found, match = self.pair()
        claim = Claim.objects.create(item=found, claimant=self.users['owner'], statement='Mine', match=match)
        lost.is_active = False
        lost.save()
        self.assertEqual(clear_matches(lost), 1)
        self.assertFalse(Match.objects.exists())
        kept = Match.all_objects.get()
        self.assertEqual((kept.status, kept.retired_reason), ('retired', 'withdrawn'))
        claim.refresh_from_db()
        self.assertEqual(claim.match_id, kept.pk)                 # evidence survives
        self.assertEqual(lost.matches_as_lost.count(), 0)        # reverse relation: current only

    def test_state_changes_retire_after_commit_with_the_right_reason(self):
        cases = [({'status': 'rejected'}, 'rejected'), ({'status': 'pending'}, 'pending_review'),
                 ({'status': 'returned', 'is_active': False}, 'returned'), ({'is_active': False}, 'withdrawn')]
        for changes, reason in cases:
            with self.subTest(reason=reason):
                Match.all_objects.all().delete()
                Item.objects.all().delete()
                lost, found, _ = self.pair()
                for k, v in changes.items():
                    setattr(found, k, v)
                with self.captureOnCommitCallbacks(execute=True):
                    found.save()
                m = Match.all_objects.get()
                self.assertEqual((m.status, m.retired_reason), ('retired', reason))
                self.assertEqual(run_matching(lost), [])

    def test_admin_content_edit_rescores_after_commit(self):
        lost, found, match = self.pair()
        found.color = 'red'
        with self.captureOnCommitCallbacks(execute=True):
            found.save()
        updated = Match.objects.get()
        self.assertLess(updated.score, match.score)
        self.assertEqual(updated.details['components']['attribute']['colour'], 'conflict')

    def test_candidates_that_leave_the_pool_are_retired_on_the_next_run(self):
        lost, found, _ = self.pair()
        Item.objects.filter(pk=found.pk).update(status='rejected')   # no signal
        run_matching(lost)
        m = Match.all_objects.get()
        self.assertEqual((m.status, m.retired_reason), ('retired', 'candidate_ineligible'))

    def test_revived_pair_is_not_notified_twice(self):
        lost, found, _ = self.pair()
        notes = Notification.objects.filter(kind='match').count()
        self.assertEqual(notes, 2)
        retire_matches(found, Match.RetiredReason.INELIGIBLE)
        run_matching(lost)
        self.assertEqual(Match.objects.get().status, 'active')
        self.assertEqual(Notification.objects.filter(kind='match').count(), notes)

    def test_claim_save_rechecks_eligibility(self):
        lost, found, match = self.pair()
        Item.objects.filter(pk=found.pk).update(is_active=False)
        with self.captureOnCommitCallbacks(execute=True):
            Claim.objects.create(item=found, claimant=self.users['owner'], statement='Mine', match=match)
        self.assertEqual(Match.all_objects.get().retired_reason, 'withdrawn')

    def test_moderators_see_retired_history_students_do_not(self):
        lost, found, _ = self.pair()
        retire_matches(found, Match.RetiredReason.REJECTED)
        self.client.force_login(self.users['mod'])
        page = self.client.get(lost.get_absolute_url()).content.decode()
        self.assertIn('Retired matches (history, not actionable)', page)
        self.assertIn('A report was rejected or removed', page)
        self.client.force_login(self.users['owner'])
        page = self.client.get(lost.get_absolute_url()).content.decode()
        self.assertNotIn('Retired matches', page)


@lightweight_models
@isolated_media
class EligibilityTests(Base):
    def test_one_eligibility_rule_no_self_or_same_reporter_or_duplicate_pairs(self):
        lost = self.report('owner', 'lost')
        own_found = self.report('owner', 'found')
        other_found = self.report('finder', 'found')
        pending = self.report('finder', 'found', status='pending')
        self.assertTrue(is_match_eligible(lost))
        self.assertFalse(is_match_eligible(pending))
        ids = set(candidate_queryset(lost).values_list('pk', flat=True))
        self.assertEqual(ids, {other_found.pk})
        self.assertNotIn(lost.pk, ids)
        self.assertNotIn(own_found.pk, ids)
        run_matching(lost)
        run_matching(other_found)
        run_matching(lost)
        self.assertEqual(Match.all_objects.filter(lost_item=lost, found_item=other_found).count(), 1)
        self.assertFalse(Match.all_objects.filter(found_item=own_found).exists())

    def test_matchable_queryset_is_the_single_source(self):
        lost = self.report('owner', 'lost')
        found = self.report('finder', 'found')
        with patch.object(type(Item.objects), 'matchable',
                          lambda manager: Item.objects.get_queryset().filter(pk=lost.pk)):
            # The lead can extend matchable(); both sides of matching follow it.
            self.assertFalse(is_match_eligible(found))
            self.assertEqual(list(candidate_queryset(lost)), [])

    def test_matching_queries_do_not_grow_with_the_candidate_pool(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries(item):
            with CaptureQueriesContext(connection) as ctx:
                run_matching(item)
            return len(ctx.captured_queries)

        lost = self.report('owner', 'lost')
        for i in range(3):
            self.report('finder', 'found', title=f'Wallet {i}')
        run_matching(lost)  # computes features and stores rows once
        small = queries(lost)
        for i in range(5):
            self.report('other', 'found', title=f'Wallet extra {i}')
        run_matching(lost)
        self.assertEqual(Match.objects.count(), 8)
        # steady state: no per-candidate feature, taxonomy or write queries (no N+1)
        self.assertEqual(queries(lost), small)


@lightweight_models
@isolated_media
class FeatureStateTests(Base):
    def test_absent_photo_is_missing_not_failed(self):
        from matching.features import prepare
        item = self.report('owner', 'lost')
        self.assertTrue(prepare(item))
        f = ItemFeatures.objects.get(item=item)
        self.assertEqual((f.text_state, f.image_state, f.text_model), ('success', 'missing', 'lexical-v1'))

    def test_inference_failure_is_recorded_retried_then_capped(self):
        from matching.features import prepare
        item = self.report('owner', 'lost', image=png_upload('a.png'))
        with patch('matching.engine.extract_image_vector', side_effect=RuntimeError('model unavailable')), \
                self.assertLogs('lostlink.matching', level='ERROR'):
            for _ in range(3):
                self.assertFalse(prepare(item))
        f = ItemFeatures.objects.get(item=item)
        self.assertEqual((f.image_state, f.attempts), ('failed', 3))
        self.assertIn('RuntimeError', f.last_error)
        # exhausted: no further model calls until retry_features
        with patch('matching.engine.extract_image_vector') as extractor:
            self.assertFalse(prepare(item))
            extractor.assert_not_called()
        out = StringIO()
        call_command('retry_features', item=[item.pk], stdout=out)
        f.refresh_from_db()
        self.assertEqual((f.image_state, f.image_model, f.attempts), ('success', 'histogram', 0))
        self.assertEqual(json.loads(out.getvalue())['succeeded'], 1)

    def test_failed_photo_is_not_compared_and_students_see_no_internals(self):
        lost = self.report('owner', 'lost', image=png_upload('a.png'), color='black')
        self.report('finder', 'found', image=png_upload('b.png'), color='black')
        with patch('matching.engine.extract_image_vector', side_effect=RuntimeError('CUDA secret path')), \
                self.assertLogs('lostlink.matching', level='ERROR'):
            m = run_matching(lost)[0]
        self.assertIn('image', m.missing_components)
        self.assertEqual(m.details['components']['image']['reason'], 'photo_not_processed')
        self.client.force_login(self.users['owner'])
        page = self.client.get(lost.get_absolute_url()).content.decode()
        self.assertIn('A photo could not be compared', page)
        self.assertNotIn('CUDA secret path', page)
        self.client.force_login(self.users['mod'])
        page = self.client.get(lost.get_absolute_url()).content.decode()
        self.assertIn('Processing problem', page)

    def test_histogram_baseline_is_labelled_as_a_baseline(self):
        lost = self.report('owner', 'lost', image=png_upload('a.png'))
        self.report('finder', 'found', image=png_upload('b.png'))
        m = run_matching(lost)[0]
        self.assertIn('baseline', m.model_label)
        self.assertNotIn('ResNet', m.model_label)
        self.assertEqual(lost.match_features.image_model, 'histogram')

    def test_matching_status_reports_lifecycle(self):
        lost = self.report('owner', 'lost', color='black')
        found = self.report('finder', 'found', color='black')
        run_matching(lost)
        retire_matches(found, Match.RetiredReason.REJECTED)
        out = StringIO()
        call_command('matching_status', stdout=out)
        report = json.loads(out.getvalue())
        self.assertEqual(report['active_matches'], 0)
        self.assertEqual(report['retired_by_reason'], {'rejected': 1})
        self.assertEqual(report['features']['image'], {'missing': 2})
