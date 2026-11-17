"""Requirement 4.1: deterministic, stable pairwise text similarity, versioned
releases, feature invalidation and version-aware rebuilds."""
from datetime import date, timedelta
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users
from items.models import Item
from matching import engine, textmodel
from matching.models import Match
from matching.services import run_matching, score_pair
from notifications.models import Notification


class TextModelInvariantTests(SimpleTestCase):
    def test_same_pair_same_score_and_symmetry(self):
        a = textmodel.represent('Black phone', 'Black mobile phone with cracked screen')
        b = textmodel.represent('Phone', 'Dark cellphone with damaged display')
        first = textmodel.cosine(a, b)
        for _ in range(3):
            self.assertEqual(textmodel.cosine(a, b), first)
            self.assertEqual(textmodel.cosine(b, a), first)
        self.assertEqual(textmodel.represent('Black phone', 'Black mobile phone with cracked screen'), a)

    def test_text_similarity_does_not_depend_on_the_candidate_pool(self):
        query = 'Black leather wallet with a silver logo'
        target = 'Black wallet made of leather, silver branding'
        alone = engine.text_similarity(query, [target])[0]
        noisy = engine.text_similarity(query, ['Red umbrella', target, 'wallet wallet wallet',
                                               'Blue water bottle', 'Black leather wallet'])
        reordered = engine.text_similarity(query, ['Blue water bottle', 'Black leather wallet', target])
        self.assertEqual(noisy[1], alone)
        self.assertEqual(reordered[2], alone)

    def test_committed_release_is_reproducible_from_the_lexicon(self):
        built = textmodel.release_json(textmodel.build_release('lexical-v1'))
        committed = textmodel.release_path('lexical-v1').read_text(encoding='utf-8')
        self.assertEqual(built, committed, 'Run: python manage.py build_text_model --release lexical-v1')
        call_command('build_text_model', release='lexical-v1', check=True, stdout=StringIO())

    def test_releases_are_immutable_and_named_safely(self):
        with self.assertRaises(CommandError):
            call_command('build_text_model', release='../evil', stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('build_text_model', release='lexical-v999', check=True, stdout=StringIO())

    def test_missing_release_fails_loudly(self):
        with override_settings(MATCHING=dict(settings.MATCHING, TEXT_MODEL_RELEASE='lexical-v0-missing')):
            with self.assertRaises(FileNotFoundError):
                textmodel.represent('', 'wallet')

    def test_release_is_part_of_the_manifest(self):
        from matching.features import manifest
        self.assertEqual(manifest()['text_release'], settings.MATCHING['TEXT_MODEL_RELEASE'])
        self.assertEqual(settings.MATCHING['TEXT_MODEL_RELEASE'], settings.MATCHING_PROFILE['text_release'])


@lightweight_models
@isolated_media
class PairScoreStabilityTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()

    def report(self, user, rtype, title, description, **kw):
        defaults = dict(cat='bags', loc='library', days_ago=3, status='approved')
        defaults.update(kw)
        return Item.objects.create(
            reporter=self.users[user], report_type=rtype, title=title, description=description,
            category=self.tax[defaults['cat']], location=self.tax[defaults['loc']],
            status=defaults['status'], brand=kw.get('brand', ''), color=kw.get('color', ''),
            event_date=date.today() - timedelta(days=defaults['days_ago']))

    def snapshot(self):
        m = Match.objects.get(lost_item=self.lost, found_item=self.found)
        return (m.score, m.text_score, m.tier, m.evidence_coverage)

    def test_unrelated_reports_and_initiating_side_do_not_change_the_pair(self):
        self.lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo')
        self.found = self.report('finder', 'found', 'Wallet', 'Black wallet made of leather, silver branding')
        run_matching(self.lost)
        before = self.snapshot()
        # unrelated reports of both types, sharing some vocabulary
        for i in range(4):
            self.report('other', 'found', f'Wallet {i}', f'Brown wallet number {i} with cards')
            self.report('other', 'lost', f'Bottle {i}', 'Black steel water bottle with leather strap')
        run_matching(self.lost)
        self.assertEqual(self.snapshot(), before)
        # found -> lost initiation gives the identical stored pair
        run_matching(self.found)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(Match.objects.filter(lost_item=self.lost, found_item=self.found).count(), 1)
        # score_pair from either side agrees as well
        a, b = score_pair(self.lost, self.found), score_pair(self.found, self.lost)
        self.assertEqual(a['score'], b['score'])
        self.assertEqual(a['components'], b['components'])

    def test_candidate_retrieval_does_not_change_pairwise_similarity(self):
        self.lost = self.report('owner', 'lost', 'Phone', 'Black mobile phone with cracked screen')
        self.found = self.report('finder', 'found', 'Cellphone', 'Dark cellphone with damaged display')
        direct = score_pair(self.lost, self.found)['score']
        # A pending candidate is not retrieved; approving it later must not move the pair.
        extra = self.report('other', 'found', 'Phone', 'Black phone', status='pending')
        run_matching(self.lost)
        first = self.snapshot()[0]
        extra.status = 'approved'
        extra.save()
        run_matching(self.lost)
        self.assertEqual(self.snapshot()[0], first)
        self.assertAlmostEqual(first, direct, places=9)

    def test_repeated_runs_are_idempotent_and_notify_once(self):
        self.lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo', color='black')
        self.found = self.report('finder', 'found', 'Wallet', 'Black leather wallet with silver logo', color='black')
        for _ in range(3):
            run_matching(self.lost)
            run_matching(self.found)
        self.assertEqual(Match.all_objects.count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.users['finder'], kind='match').count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.users['owner'], kind='match').count(), 1)
        self.assertIsNotNone(Match.objects.get().notified_at)

    def test_engine_version_change_rebuilds_in_place_without_renotifying(self):
        self.lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet with silver logo', color='black')
        self.found = self.report('finder', 'found', 'Wallet', 'Black leather wallet with silver logo', color='black')
        run_matching(self.lost)
        notes = Notification.objects.count()
        old = Match.objects.get()
        with override_settings(MATCHING=dict(settings.MATCHING, VERSION='next-version')):
            call_command('rebuild_matches', notify=True, stdout=StringIO())
            call_command('rebuild_matches', notify=True, stdout=StringIO())  # idempotent
            m = Match.objects.get()
        self.assertEqual((m.pk, m.engine_version), (old.pk, 'next-version'))
        self.assertEqual(m.score, old.score)
        self.assertEqual(Notification.objects.count(), notes)

    def test_rebuild_retires_rows_of_an_old_version_that_are_not_recomputed(self):
        self.lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet')
        self.found = self.report('finder', 'found', 'Wallet', 'Black leather wallet')
        run_matching(self.lost)
        self.lost.is_active = False
        Item.objects.filter(pk=self.lost.pk).update(is_active=False)  # bypasses signals on purpose
        with override_settings(MATCHING=dict(settings.MATCHING, VERSION='next-version')):
            call_command('rebuild_matches', stdout=StringIO())
        m = Match.all_objects.get()
        self.assertEqual(m.status, Match.Status.RETIRED)
        self.assertEqual(m.retired_reason, Match.RetiredReason.WITHDRAWN)
        self.assertFalse(Match.objects.exists())

    def test_text_release_change_invalidates_cached_features(self):
        from matching.features import fingerprint, prepare
        self.lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet')
        prepare(self.lost)
        self.lost.refresh_from_db()
        self.assertEqual(self.lost.match_features.text_model, 'lexical-v1')
        self.assertIsInstance(self.lost.text_vector, dict)
        old = self.lost.feature_version
        with override_settings(MATCHING=dict(settings.MATCHING, TEXT_MODEL_RELEASE='lexical-v2-test')):
            self.assertNotEqual(fingerprint(self.lost), old)
        # Changing the title (part of the text representation) also invalidates.
        self.lost.title = 'Brown wallet'
        self.assertNotEqual(fingerprint(self.lost), old)

    def test_mixed_cached_text_models_fall_back_to_the_lexical_release(self):
        self.lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet')
        self.found = self.report('finder', 'found', 'Wallet', 'Black leather wallet')
        from matching.features import prepare
        prepare(self.lost)
        prepare(self.found)
        expected = score_pair(self.lost, self.found)['components']['text']
        # A stale dense vector from another model on one side must not be compared.
        Item.objects.filter(pk=self.found.pk).update(text_vector=[0.1, 0.2, 0.3])
        self.found.refresh_from_db()
        self.assertEqual(score_pair(self.lost, self.found)['components']['text'], expected)

    def test_stale_cached_vectors_are_never_compared(self):
        from matching.features import prepare
        from matching.services import evidence_for
        self.lost = self.report('owner', 'lost', 'Wallet', 'Black leather wallet')
        prepare(self.lost)
        self.assertEqual(evidence_for(self.lost).text_vector, self.lost.text_vector)
        # Content changes without re-preparing (e.g. a raw update): the cached
        # vector describes old text and must not be used.
        Item.objects.filter(pk=self.lost.pk).update(description='Red umbrella with wooden handle')
        self.lost.refresh_from_db()
        evidence = evidence_for(self.lost)
        self.assertIsNone(evidence.text_vector)
        self.assertEqual(evidence.image_state, 'pending')
