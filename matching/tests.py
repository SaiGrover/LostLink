import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users, png_upload
from items.models import Item
from matching import engine
from matching.engine import (attribute_score, category_score, clean_text, combine, confidence_tier,
                             date_score, effective_weights, image_similarity, is_temporally_plausible,
                             location_score, text_similarity, vector_similarity)
from matching.management.commands.evaluate_hybrid import metrics
from matching.models import Match
from matching.services import run_matching, score_pair
from notifications.models import Notification


@lightweight_models
class ScoringTests(SimpleTestCase):
    def test_missing_image_reweights_without_score_ceiling(self):
        self.assertAlmostEqual(combine(dict(text=1, image=0, location=1, category=1, date=1, attribute=1), False), 1)

    def test_missing_components_are_dropped_not_penalised(self):
        full = dict(text=.8, image=0, location=1, category=1, date=1, attribute=0)
        # image and attribute missing: remaining weights renormalise to 1.0
        score = combine(full, missing=['image', 'attribute'])
        w = effective_weights(['image', 'attribute'])
        self.assertAlmostEqual(sum(w.values()), 1.0)
        self.assertAlmostEqual(score, w['text'] * .8 + w['location'] + w['category'] + w['date'])
        # the same evidence scored *with* a false zero would be lower
        self.assertGreater(score, combine(full))
        # None values count as missing too
        self.assertAlmostEqual(combine(dict(full, image=None, attribute=None)), score)

    def test_dates_accept_model_default_and_database_values(self):
        self.assertEqual(date_score(datetime(2026, 1, 1), date(2026, 1, 1)), 1)
        self.assertAlmostEqual(date_score(date(2026, 1, 1), date(2026, 1, 11)), .5)

    def test_found_before_lost_is_implausible(self):
        lost, found = date(2026, 3, 10), date(2026, 3, 5)
        self.assertFalse(is_temporally_plausible(lost, found))
        self.assertEqual(date_score(lost, found), 0.0)
        # one day of tolerance for approximate dates
        self.assertTrue(is_temporally_plausible(lost, lost - timedelta(days=1)))
        self.assertAlmostEqual(date_score(lost, lost - timedelta(days=1)), 0.5 ** 0.1)

    def test_incompatible_vectors_do_not_match(self):
        self.assertEqual(image_similarity([1, 0], [1, 0, 0]), 0)
        self.assertEqual(vector_similarity([1, 0], [0, 1]), 0)
        self.assertAlmostEqual(vector_similarity([1, 1], [2, 2]), 1)

    def test_metadata_scores(self):
        self.assertEqual(location_score(1, 1), 1.0)
        self.assertEqual(location_score(1, 2, {2}), 0.5)
        self.assertEqual(location_score(1, 2, {3}), 0.0)
        self.assertEqual(category_score(1, 1), 1.0)
        self.assertEqual(category_score(1, 2, {2}), 0.5)
        self.assertEqual(category_score(1, 2), 0.0)
        self.assertEqual(attribute_score('Apple', 'blue', 'apple', 'Blue'), 1.0)
        self.assertEqual(attribute_score('Apple', 'blue', 'Nike', 'blue'), 0.5)
        self.assertIsNone(attribute_score('', '', 'Nike', ''))
        self.assertEqual(attribute_score('', 'red', '', 'blue'), 0.0)

    def test_text_cleaning_includes_conservative_lemmatization(self):
        self.assertEqual(clean_text('The wallets and batteries'), 'wallet battery')
        self.assertEqual(clean_text('Children with watches'), 'child watch')
        self.assertEqual(clean_text('Glasses in cases'), 'glass case')

    def test_text_similarity_identical_similar_and_unrelated(self):
        q = 'Black leather wallet with a silver logo'
        sims = text_similarity(q, [q, 'Black wallet made of leather, silver branding',
                                   'Red steel water bottle with a dent', ''])
        self.assertAlmostEqual(sims[0], 1.0)
        self.assertGreater(sims[1], 0.4)
        self.assertLess(sims[2], 0.15)
        self.assertEqual(sims[3], 0.0)
        self.assertGreater(sims[0], sims[1])
        self.assertGreater(sims[1], sims[2])
        self.assertEqual(text_similarity(q, []), [])

    def test_confidence_tier_boundaries_follow_the_synopsis_table(self):
        # > 85% high, 70%–85% inclusive medium, < 70% low
        self.assertEqual(confidence_tier(0.86), 'high')
        self.assertEqual(confidence_tier(0.8500001), 'medium')
        self.assertEqual(confidence_tier(0.85), 'medium')
        self.assertEqual(confidence_tier(0.70), 'medium')
        self.assertEqual(confidence_tier(0.699999), 'low')
        self.assertEqual(confidence_tier(0.0), 'low')
        self.assertEqual(confidence_tier(1.0), 'high')

    def test_active_profile_matches_synopsis_weights_and_thresholds(self):
        from django.conf import settings
        self.assertEqual(settings.MATCHING['WEIGHTS'], {'text': .3, 'image': .3, 'location': .15,
                                                        'category': .1, 'date': .1, 'attribute': .05})
        self.assertEqual((settings.MATCHING['TIER_MEDIUM'], settings.MATCHING['TIER_HIGH']), (.7, .85))

    def test_retrieval_metrics_include_negatives_and_ranking(self):
        rows = [{'query_id': 'q', 'candidate': {'id': str(i)}, 'label': y} for i, y in enumerate([0, 1, 0])]
        result = metrics(rows, [.9, .8, .1], .7)
        self.assertEqual(result['precision'], .5)
        self.assertEqual(result['recall'], 1)
        self.assertEqual(result['mrr'], .5)


@lightweight_models
@isolated_media
class MatchingPipelineTests(TestCase):
    """End-to-end behaviour of run_matching on real reports."""

    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()

    def report(self, user, rtype, title, description, *, cat='bags', loc='library', days_ago=3,
               status='approved', brand='', color='', image=None):
        return Item.objects.create(
            reporter=self.users[user], report_type=rtype, title=title, description=description,
            category=self.tax[cat], location=self.tax[loc], status=status, brand=brand, color=color,
            event_date=date.today() - timedelta(days=days_ago), image=image)

    def test_identical_reports_with_same_photo_score_high_and_notify(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo',
                           image=png_upload('a.png', pattern=True), brand='Fossil', color='black')
        found = self.report('finder', 'found', 'Wallet', 'Black leather wallet with silver logo',
                            image=png_upload('b.png', pattern=True), brand='Fossil', color='black')
        matches = run_matching(lost)
        self.assertEqual(len(matches), 1)
        m = matches[0]
        self.assertEqual((m.lost_item, m.found_item), (lost, found))
        self.assertGreater(m.score, 0.85)
        self.assertEqual(m.tier, 'high')
        self.assertEqual(m.missing_components, [])
        self.assertAlmostEqual(m.image_score, 1.0, places=4)
        self.assertEqual(m.engine_version, 'synopsis-v1')
        self.assertEqual(m.configuration['image'], 'histogram')
        self.assertTrue(Notification.objects.filter(user=self.users['finder'], kind='match').exists())
        self.assertTrue(Notification.objects.filter(user=self.users['owner'], kind='match').exists())

    def test_semantically_similar_wording_still_matches(self):
        lost = self.report('owner', 'lost', 'Wallet', 'black leather wallet, silver logo on the front')
        found = self.report('finder', 'found', 'Wallet', 'Black wallet made of leather with silver branding')
        m = run_matching(lost)[0]
        self.assertGreater(m.text_score, 0.3)
        self.assertIn('image', m.missing_components)
        self.assertIn('attribute', m.missing_components)
        self.assertIn(m.tier, ('medium', 'high'))

    def test_unrelated_and_contradictory_reports_are_not_surfaced(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo',
                           cat='bags', loc='library', color='black')
        self.report('finder', 'found', 'Charger', 'White laptop charger with a frayed cable',
                    cat='electronics', loc='cafeteria', color='white', days_ago=1)
        self.report('finder', 'found', 'Wallet', 'Black leather wallet with silver logo',
                    cat='electronics', loc='cafeteria', color='red', days_ago=1)
        matches = run_matching(lost)
        for m in matches:
            self.assertEqual(m.tier, 'low')
        self.assertFalse(Notification.objects.filter(kind='match').exists())
        # the contradictory copy scores lower than an agreeing copy would
        agreeing = self.report('other', 'found', 'Wallet', 'Black leather wallet with silver logo',
                               cat='bags', loc='library', color='black', days_ago=1)
        best = run_matching(lost)[0]
        self.assertEqual(best.found_item, agreeing)
        self.assertEqual(best.tier, 'high')

    def test_different_photographs_lower_the_score(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet', image=png_upload('a.png', color=(10, 10, 10)))
        same = self.report('finder', 'found', 'Wallet', 'Black leather wallet', image=png_upload('b.png', color=(10, 10, 10)))
        diff = self.report('other', 'found', 'Wallet', 'Black leather wallet', image=png_upload('c.png', color=(240, 240, 240)))
        scores = {m.found_item_id: m for m in run_matching(lost)}
        self.assertGreater(scores[same.pk].image_score, scores[diff.pk].image_score)
        self.assertGreater(scores[same.pk].score, scores[diff.pk].score)

    def test_missing_photo_on_one_side_drops_image_component(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet', image=png_upload('a.png'))
        self.report('finder', 'found', 'Wallet', 'Black leather wallet')
        m = run_matching(lost)[0]
        self.assertIn('image', m.missing_components)
        self.assertEqual(m.image_score, 0.0)
        weights = m.effective_weights
        self.assertNotIn('image', weights)
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        rows = {r['key']: r for r in m.breakdown()}
        self.assertTrue(rows['image']['missing'])
        self.assertAlmostEqual(sum(r['contribution'] for r in rows.values()), m.score, places=6)

    def test_found_before_lost_is_never_stored(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet', days_ago=2)
        self.report('finder', 'found', 'Wallet', 'Black leather wallet', days_ago=9)
        self.assertEqual(run_matching(lost), [])
        self.assertEqual(Match.objects.count(), 0)
        # and the reverse direction agrees
        found = self.report('other', 'found', 'Wallet', 'Black leather wallet', days_ago=20)
        self.assertEqual(run_matching(found), [])

    def test_compatible_category_and_adjacent_location_earn_partial_credit(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet', cat='bags', loc='library')
        partial = self.report('finder', 'found', 'Wallet', 'Black leather wallet', cat='others', loc='block_a')
        none = self.report('other', 'found', 'Wallet', 'Black leather wallet', cat='electronics', loc='cafeteria')
        scores = {m.found_item_id: m for m in run_matching(lost)}
        self.assertEqual(scores[partial.pk].category_score, 0.5)
        self.assertEqual(scores[partial.pk].location_score, 0.5)
        self.assertEqual(scores[none.pk].category_score, 0.0)
        self.assertEqual(scores[none.pk].location_score, 0.0)

    def test_only_eligible_opposite_type_reports_are_candidates(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet')
        self.report('owner', 'found', 'Wallet', 'Black leather wallet')            # own report
        self.report('finder', 'lost', 'Wallet', 'Black leather wallet')            # same type
        self.report('finder', 'found', 'Wallet', 'Black leather wallet', status='pending')
        self.report('finder', 'found', 'Wallet', 'Black leather wallet', status='rejected')
        withdrawn = self.report('finder', 'found', 'Wallet', 'Black leather wallet')
        withdrawn.is_active = False
        withdrawn.save()
        self.assertEqual(run_matching(lost), [])
        pending = self.report('finder', 'found', 'Wallet', 'Black leather wallet', status='pending')
        self.assertEqual(run_matching(pending), [])

    def test_rerun_is_idempotent_and_removes_stale_rows(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo')
        found = self.report('finder', 'found', 'Wallet', 'Black leather wallet with silver logo')
        run_matching(lost)
        run_matching(lost)
        run_matching(found)
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(kind='match').count(), 2)
        # the found report is rewritten to something unrelated -> row removed
        found.description = 'Completely different umbrella, yellow with polka dots'
        found.category = self.tax['electronics']
        found.location = self.tax['cafeteria']
        found.feature_version = ''
        found.save()
        run_matching(lost)
        self.assertEqual(Match.objects.count(), 0)

    def test_notification_sent_when_a_rescore_lifts_a_low_match(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo',
                           cat='bags', loc='library')
        found = self.report('finder', 'found', 'Wallet', 'Black leather wallet with silver logo',
                            cat='electronics', loc='cafeteria')
        m = run_matching(lost)[0]
        self.assertEqual(m.tier, 'low')
        self.assertFalse(Notification.objects.filter(kind='match').exists())
        found.category, found.location = self.tax['bags'], self.tax['library']
        found.save()
        m = run_matching(lost)[0]
        self.assertEqual(m.tier, 'high')
        self.assertEqual(Notification.objects.filter(user=self.users['finder'], kind='match').count(), 1)
        run_matching(lost)
        self.assertEqual(Notification.objects.filter(kind='match').count(), 2)

    def test_engine_version_change_hides_old_matches_until_rebuilt(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo')
        self.report('finder', 'found', 'Wallet', 'Black leather wallet with silver logo')
        run_matching(lost)
        self.client.force_login(self.users['owner'])
        self.assertEqual(len(self.client.get(lost.get_absolute_url()).context['match_rows']), 1)
        from django.conf import settings
        new = dict(settings.MATCHING, VERSION='synopsis-v2')
        with override_settings(MATCHING=new):
            self.assertEqual(len(self.client.get(lost.get_absolute_url()).context['match_rows']), 0)
            self.assertEqual(len(self.client.get(reverse('items:dashboard')).context['top_matches']), 0)
            call_command('rebuild_matches', verbosity=0)
            self.assertEqual(len(self.client.get(lost.get_absolute_url()).context['match_rows']), 1)
            self.assertEqual(Match.objects.get().engine_version, 'synopsis-v2')

    def test_inference_failure_degrades_gracefully(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo',
                           image=png_upload('a.png'))
        found = self.report('finder', 'found', 'Wallet', 'Black leather wallet with silver logo',
                            image=png_upload('b.png'))
        with patch('matching.engine.extract_image_vector', side_effect=RuntimeError('model unavailable')), \
                self.assertLogs('lostlink.matching', level='ERROR'):
            matches = run_matching(lost)
        self.assertEqual(len(matches), 1)
        self.assertIn('image', matches[0].missing_components)
        lost.refresh_from_db()
        self.assertEqual(lost.feature_version, '')  # retried next time
        run_matching(lost)
        lost.refresh_from_db()
        self.assertTrue(lost.feature_version)
        self.assertNotIn('image', Match.objects.get().missing_components)

    def test_low_matches_are_search_only_for_other_users(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo', cat='bags')
        found = self.report('finder', 'found', 'Wallet', 'Black leather wallet with silver logo',
                            cat='electronics', loc='cafeteria')
        self.assertEqual(run_matching(lost)[0].tier, 'low')
        self.client.force_login(self.users['owner'])
        self.assertEqual(len(self.client.get(lost.get_absolute_url()).context['match_rows']), 1)
        self.client.force_login(self.users['other'])
        self.assertEqual(len(self.client.get(lost.get_absolute_url()).context['match_rows']), 0)
        self.assertEqual(self.client.get(reverse('items:search_api'), {'q': 'wallet'}).json()['total'], 2)

    def test_score_pair_breakdown_is_consistent_with_stored_match(self):
        lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet', brand='Fossil', color='black')
        found = self.report('finder', 'found', 'Wallet', 'Black leather wallet', brand='fossil', color='Black')
        m = run_matching(lost)[0]
        result = score_pair(lost, found)
        self.assertAlmostEqual(result['score'], m.score, places=6)
        self.assertEqual(result['components']['attribute'], 1.0)
        self.assertEqual(result['tier'], m.tier)


class ResearchToolingTests(SimpleTestCase):
    def test_discovery_study_analyser_requires_and_compares_both_modes(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'study.csv'
            output = Path(folder) / 'result.json'
            source.write_text(
                'participant_id,task_id,mode,success,seconds\n'
                'p1,t1,manual,1,40\n'
                'p1,t2,ajax,1,20\n', encoding='utf-8')
            call_command('evaluate_discovery_study', str(source), output=str(output))
            result = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['participants'], 1)
            self.assertEqual(result['median_time_reduction_percent'], 50)

    def test_unreviewed_dataset_fails_research_gate(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'pilot.json'
            source.write_text(json.dumps({'pairs': [{
                'query_id': 'q1', 'split': 'validation', 'label': 1,
                'query': {'id': 'q1'}, 'candidate': {'id': 'c1'},
            }]}), encoding='utf-8')
            with self.assertRaises(CommandError):
                call_command('validate_research_dataset', str(source), allow_small=True)


@lightweight_models
class MaintenanceSchedulerTests(TestCase):
    def test_once_records_monitoring_snapshot(self):
        from matching.models import ScoreSnapshot
        with TemporaryDirectory() as folder:
            call_command('run_maintenance_scheduler', once=True,
                         export_output=str(Path(folder) / 'confirmed.json'))
            self.assertEqual(ScoreSnapshot.objects.count(), 1)


@skipUnless(os.environ.get('LOSTLINK_TEST_PRETRAINED') == '1',
            'set LOSTLINK_TEST_PRETRAINED=1 to run pretrained-model inference')
class PretrainedSmokeTests(SimpleTestCase):
    """Verifies the pinned extractors really produce the documented vectors."""

    def test_resnet_clip_and_sbert_vectors(self):
        from PIL import Image
        from matching.embeddings import image_vector, text_vectors
        img = Image.new('RGB', (64, 64), (120, 40, 40))
        self.assertEqual(len(image_vector(img, 'resnet')), 2048)
        self.assertEqual(len(image_vector(img, 'clip')), 512)
        self.assertEqual(len(text_vectors(['black wallet'])[0]), 384)
        a, b = text_vectors(['black leather wallet', 'wallet made of black leather'])
        self.assertGreater(engine.vector_similarity(a, b), 0.7)
