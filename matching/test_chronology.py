"""Requirements 4.6 / 4.7: date + time chronology, graded (never hard) exclusions."""
from datetime import date, time, timedelta

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users
from items.models import Item
from matching import chronology, engine
from matching.models import Match
from matching.services import run_matching

D = date(2026, 3, 10)


class ChronologyStatusTests(SimpleTestCase):
    def test_found_after_lost_is_plausible_and_decays_with_the_gap(self):
        same = chronology.assess(D, D)
        later = chronology.assess(D, D + timedelta(days=10))
        self.assertEqual((same['status'], same['score']), ('plausible', 1.0))
        self.assertEqual(later['status'], 'plausible')
        self.assertAlmostEqual(later['score'], 0.5)

    def test_one_day_earlier_on_dates_alone_is_approximate_not_excluded(self):
        result = chronology.assess(D, D - timedelta(days=1))
        self.assertEqual(result['status'], 'approximate')
        self.assertAlmostEqual(result['score'], 0.9 * 0.5 ** 0.1)
        self.assertIsNone(result['tier_cap'])

    def test_found_before_lost_beyond_tolerance_is_suspicious_then_contradictory(self):
        suspicious = chronology.assess(D, D - timedelta(days=5))
        self.assertEqual(suspicious['status'], 'suspicious')
        self.assertAlmostEqual(suspicious['score'], 0.5 * 0.5 ** 0.5)
        self.assertEqual(suspicious['tier_cap'], 'medium')
        self.assertTrue(suspicious['warning'])
        contradictory = chronology.assess(D, D - timedelta(days=8))
        self.assertEqual(contradictory['status'], 'contradictory')
        self.assertEqual(contradictory['score'], 0.0)
        self.assertEqual(contradictory['tier_cap'], 'low')

    def test_same_day_contradictory_times_do_not_get_full_compatibility(self):
        result = chronology.assess(D, D, time(18, 0), time(9, 0))
        self.assertEqual(result['status'], 'suspicious')
        self.assertEqual(result['hour_gap'], -9.0)
        self.assertLess(result['score'], 1.0)
        self.assertAlmostEqual(result['score'], 0.5)
        consistent = chronology.assess(D, D, time(9, 0), time(18, 0))
        self.assertEqual((consistent['status'], consistent['score']), ('plausible', 1.0))

    def test_approximate_times_are_not_over_excluded(self):
        result = chronology.assess(D, D, time(18, 0), time(16, 30))
        self.assertEqual(result['status'], 'approximate')
        self.assertGreaterEqual(result['score'], 0.9)
        self.assertIsNone(result['tier_cap'])

    def test_across_midnight(self):
        # lost late evening, found just after midnight: consistent
        forward = chronology.assess(D, D + timedelta(days=1), time(23, 30), time(0, 30))
        self.assertEqual(forward['status'], 'plausible')
        self.assertEqual(forward['hour_gap'], 1.0)
        # lost just after midnight, found the previous late evening: approximate
        backward = chronology.assess(D + timedelta(days=1), D, time(0, 30), time(23, 30))
        self.assertEqual(backward['status'], 'approximate')
        self.assertEqual(backward['hour_gap'], -1.0)

    def test_missing_time_is_independent_from_missing_date(self):
        one_time = chronology.assess(D, D, time(18, 0), None)
        self.assertEqual((one_time['status'], one_time['score'], one_time['time_compared']),
                         ('plausible', 1.0, False))
        no_date = chronology.assess(None, D, time(18, 0), time(9, 0))
        self.assertEqual(no_date['status'], 'unknown')
        self.assertIsNone(no_date['score'])

    def test_tolerances_are_configurable(self):
        custom = dict(settings.MATCHING, CHRONOLOGY=dict(settings.MATCHING['CHRONOLOGY'],
                                                         TIME_TOLERANCE_HOURS=10, CONTRADICTORY_DAYS=3))
        with override_settings(MATCHING=custom):
            self.assertEqual(chronology.assess(D, D, time(18, 0), time(9, 0))['status'], 'approximate')
            self.assertEqual(chronology.assess(D, D - timedelta(days=5))['status'], 'contradictory')

    def test_engine_wrappers_follow_the_graded_policy(self):
        self.assertTrue(engine.is_temporally_plausible(D, D - timedelta(days=1)))
        self.assertFalse(engine.is_temporally_plausible(D, D - timedelta(days=5)))
        self.assertEqual(engine.date_score(D, D - timedelta(days=30)), 0.0)


@lightweight_models
@isolated_media
class ChronologyInMatchingTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()

    def report(self, user, rtype, day, at=None, **kw):
        return Item.objects.create(
            reporter=self.users[user], report_type=rtype, title='Wallet',
            description='Black leather wallet with silver logo', category=self.tax['bags'],
            location=self.tax['library'], status='approved', color='black',
            event_date=day, event_time=at, **kw)

    def test_found_before_lost_is_stored_capped_and_flagged_for_moderators(self):
        lost = self.report('owner', 'lost', D)
        found = self.report('finder', 'found', D - timedelta(days=12))
        matches = run_matching(lost)
        self.assertEqual(len(matches), 1)
        m = matches[0]
        self.assertEqual(m.chronology_status, 'contradictory')
        self.assertEqual(m.tier, 'low')                       # not surfaced to students
        self.assertEqual(m.details['tier_before_caps'], 'high')
        self.assertTrue(m.chronology_warning)
        self.assertEqual(m.date_score, 0.0)
        self.assertEqual(run_matching(found)[0].pk, m.pk)     # same from the other side

    def test_same_day_time_conflict_is_capped_at_moderate(self):
        lost = self.report('owner', 'lost', D, time(18, 0))
        self.report('finder', 'found', D, time(9, 0))
        m = run_matching(lost)[0]
        self.assertEqual(m.chronology_status, 'suspicious')
        self.assertEqual(m.tier, 'medium')
        self.assertLess(m.date_score, 1.0)
        self.assertIn('chronology', [c['reason'] for c in m.details['caps']])

    def test_approximate_time_keeps_the_strong_tier(self):
        lost = self.report('owner', 'lost', D, time(18, 0))
        self.report('finder', 'found', D, time(17, 0))
        m = run_matching(lost)[0]
        self.assertEqual(m.chronology_status, 'approximate')
        self.assertEqual(m.tier, 'high')
        self.assertFalse(m.chronology_warning)

    def test_explanation_shows_the_chronology(self):
        lost = self.report('owner', 'lost', D, time(18, 0))
        self.report('finder', 'found', D, time(9, 0))
        run_matching(lost)
        self.client.force_login(self.users['owner'])
        page = self.client.get(lost.get_absolute_url()).content.decode()
        self.assertIn('Check the dates', page)
        self.assertIn('found before lost', page)
        self.assertEqual(Match.objects.count(), 1)
