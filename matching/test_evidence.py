"""Requirements 4.2-4.5: score representation, missing vs contradictory
evidence, evidence coverage, titles and paraphrases in text, colours."""
from datetime import date, timedelta

from django.test import SimpleTestCase, TestCase

from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users, png_upload
from items.models import Item
from matching import colour, engine, textmodel
from matching.engine import Evidence, score_evidence
from matching.models import Match
from matching.services import run_matching

TODAY = date(2026, 5, 4)


def ev(rtype, description='Black leather wallet', **kw):
    kw.setdefault('category', 1)
    kw.setdefault('location', 1)
    kw.setdefault('event_date', TODAY)
    return Evidence(report_type=rtype, description=description, **kw)


@lightweight_models
class MissingVersusContradictoryTests(SimpleTestCase):
    def test_missing_photo_is_dropped_but_a_contradictory_photo_is_scored(self):
        missing = score_evidence(ev('lost', color='black'), ev('found', color='black'))
        self.assertIn('image', missing['missing'])
        self.assertEqual(missing['details']['components']['image']['reason'], 'no_photo_both')
        contradictory = score_evidence(ev('lost', image_vector=[1.0, 0.0], has_photo=True, image_model='histogram',
                                          color='black'),
                                       ev('found', image_vector=[0.0, 1.0], has_photo=True, image_model='histogram',
                                          color='black'))
        self.assertNotIn('image', contradictory['missing'])
        self.assertEqual(contradictory['components']['image'], 0.0)
        self.assertEqual(contradictory['details']['components']['image']['status'], 'low')
        self.assertGreater(missing['score'], contradictory['score'])
        self.assertEqual(missing['coverage'], 0.7)
        self.assertEqual(contradictory['coverage'], 1.0)

    def test_missing_photo_side_and_processing_failure_are_distinguished(self):
        one_side = score_evidence(ev('lost', has_photo=True, image_vector=[1.0, 0.0], image_model='histogram'),
                                  ev('found'))
        self.assertEqual(one_side['details']['components']['image']['reason'], 'no_photo_found')
        failed = score_evidence(ev('lost', has_photo=True, image_state='failed'),
                                ev('found', has_photo=True, image_vector=[1.0, 0.0], image_model='histogram'))
        self.assertEqual(failed['details']['components']['image']['reason'], 'photo_not_processed')
        mixed = score_evidence(ev('lost', has_photo=True, image_vector=[1.0, 0.0], image_model='resnet'),
                               ev('found', has_photo=True, image_vector=[1.0, 0.0], image_model='histogram'))
        self.assertEqual(mixed['details']['components']['image']['reason'], 'incompatible_models')
        self.assertIn('image', mixed['missing'])

    def test_missing_attributes_are_not_scored_as_disagreement(self):
        none = score_evidence(ev('lost'), ev('found'))
        self.assertIn('attribute', none['missing'])
        conflict = score_evidence(ev('lost', color='red'), ev('found', color='blue'))
        self.assertNotIn('attribute', conflict['missing'])
        self.assertEqual(conflict['components']['attribute'], 0.0)
        self.assertEqual(conflict['details']['components']['attribute']['status'], 'conflict')
        one_sided = score_evidence(ev('lost', brand='Fossil'), ev('found', color='black'))
        self.assertIn('attribute', one_sided['missing'])

    def test_coverage_caps_sparse_evidence_below_rich_evidence(self):
        sparse = score_evidence(ev('lost'), ev('found'))   # no photo, no brand/colour
        self.assertEqual(sparse['coverage'], 0.65)
        self.assertEqual(sparse['tier_before_caps'], 'high')
        self.assertEqual(sparse['tier'], 'medium')
        self.assertEqual(sparse['details']['caps'], [{'reason': 'coverage', 'tier': 'medium'}])
        rich = score_evidence(ev('lost', color='black', has_photo=True, image_vector=[1.0, 0.0],
                                 image_model='histogram'),
                              ev('found', color='black', has_photo=True, image_vector=[1.0, 0.0],
                                 image_model='histogram'))
        self.assertEqual((rich['coverage'], rich['tier']), (1.0, 'high'))
        # weights redistributed, never lost: compared weights always sum to one
        w = engine.effective_weights(sparse['missing'])
        self.assertAlmostEqual(sum(w.values()), 1.0)
        self.assertAlmostEqual(w['text'], 0.3 / 0.65)

    def test_very_low_coverage_is_capped_weak(self):
        result = score_evidence(ev('lost', category=None, location=None), ev('found', category=None, location=None))
        self.assertAlmostEqual(result['coverage'], 0.4)
        self.assertEqual(result['tier'], 'low')

    def test_components_report_their_status(self):
        r = score_evidence(ev('lost', category=1, location=1), ev('found', category=2, location=1),
                           related_pairs=frozenset({frozenset((1, 2))}))
        comps = r['details']['components']
        self.assertEqual((comps['category']['status'], comps['category']['related']), ('partial', True))
        self.assertEqual(comps['location']['status'], 'agree')


@lightweight_models
class TitleAndParaphraseTests(SimpleTestCase):
    def sim(self, a, b, title_a='', title_b=''):
        return textmodel.cosine(textmodel.represent(title_a, a), textmodel.represent(title_b, b))

    def test_regression_paraphrase_is_not_unrelated(self):
        s = self.sim('Black mobile phone with cracked screen', 'Dark cellphone with damaged display')
        self.assertGreater(s, 0.7)
        self.assertGreater(s, self.sim('Black mobile phone with cracked screen', 'Black leather wallet'))

    def test_synonyms_abbreviations_spelling_and_plurals(self):
        self.assertEqual(textmodel.normalise('gray backpacks'), textmodel.normalise('grey backpack'))
        self.assertIn('phone', textmodel.normalise('lost my smartphone'))
        self.assertIn('idcard', textmodel.normalise('College identity card'))
        self.assertEqual(textmodel.normalise('blk specs'), textmodel.normalise('black spectacles'))
        self.assertIn('bag', textmodel.normalise('rucksack'))
        self.assertGreater(self.sim('Navy blue backpack', 'Dark blue bag left in classroom'), 0.5)
        self.assertGreater(self.sim('iPhone 13 with blue case', 'Blue Apple phone found on table'), 0.5)
        self.assertGreater(self.sim('Student ID card', 'College identity card near class'), 0.7)

    def test_identifiers_keep_their_lexical_strength(self):
        same = self.sim('Casio calculator fx-991ES', 'calculator FX991ES')
        other = self.sim('Casio calculator fx-991ES', 'Casio calculator fx-82MS')
        self.assertGreater(same, other + 0.2)
        self.assertIn('fx991es', textmodel.normalise('FX-991ES'))

    def test_title_contributes_to_the_text_representation(self):
        with_title = self.sim('Left it after the lecture', 'Handed in at the gate',
                              title_a='Calculator', title_b='Scientific calculator')
        without = self.sim('Left it after the lecture', 'Handed in at the gate')
        self.assertGreater(with_title, without)
        self.assertIn('calculator', textmodel.represent('Calculator', ''))

    def test_title_only_match_is_found_by_the_engine(self):
        a = ev('lost', description='Left it after the lecture', title='Calculator')
        b = ev('found', description='Found on a desk', title='Scientific calculator')
        self.assertGreater(score_evidence(a, b)['components']['text'], 0.5)


class ColourComparisonTests(SimpleTestCase):
    def test_multicolour_partial_agreement(self):
        r = colour.compare_colours('Red and blue', 'Red')
        self.assertEqual(r['status'], 'partial')
        self.assertAlmostEqual(r['score'], 2 / 3, places=5)
        self.assertEqual(colour.compare_colours('red/blue', 'blue & red')['status'], 'full')

    def test_full_partial_and_contradictory(self):
        self.assertEqual(colour.compare_colours('navy blue', 'blue')['status'], 'full')
        self.assertEqual(colour.compare_colours('grey', 'Gray')['score'], 1.0)
        self.assertEqual(colour.compare_colours('dark red', 'maroon')['score'], 1.0)
        self.assertEqual(colour.compare_colours('light blue', 'navy')['score'], 0.75)
        self.assertEqual(colour.compare_colours('silver', 'grey')['score'], 0.5)
        self.assertEqual(colour.compare_colours('red', 'blue')['status'], 'conflict')
        self.assertIsNone(colour.compare_colours('', 'red')['score'])
        self.assertIsNone(colour.compare_colours('dark', 'light')['score'])

    def test_mixed_and_unnamed_multicolour(self):
        self.assertEqual(colour.compare_colours('multicolour', 'red')['score'], 0.5)
        self.assertEqual(colour.compare_colours('multi-coloured', 'mixed')['score'], 1.0)
        self.assertEqual(colour.compare_colours('mixed', 'red, green and yellow')['score'], 0.75)
        self.assertTrue(colour.parse_colours('red, green and yellow')['multi'])

    def test_attribute_component_combines_brand_and_colour(self):
        self.assertAlmostEqual(engine.attribute_score('Apple', 'Red and blue', 'apple', 'red'), (1 + 2 / 3) / 2,
                               places=5)
        self.assertEqual(colour.compare_brands('HP', 'Hewlett-Packard')['score'], 1.0)
        self.assertEqual(colour.compare_attributes('', 'red', '', 'red and blue')['status'], 'partial')


@lightweight_models
@isolated_media
class ScoreRepresentationTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()

    def make(self, user, rtype, **kw):
        return Item.objects.create(reporter=self.users[user], report_type=rtype, title='Wallet',
                                   description='Black leather wallet with silver logo',
                                   category=self.tax['bags'], location=self.tax['library'], status='approved',
                                   event_date=date.today() - timedelta(days=2), **kw)

    def test_stored_match_exposes_coverage_caps_and_redistribution(self):
        lost = self.make('owner', 'lost', image=png_upload('a.png'))
        self.make('finder', 'found')
        m = run_matching(lost)[0]
        self.assertEqual(m.evidence_coverage, 0.65)
        self.assertEqual(m.coverage_percent, 65)
        self.assertEqual(m.tier, 'medium')
        self.assertTrue(m.tier_caps)
        rows = {r['key']: r for r in m.breakdown()}
        self.assertEqual(rows['image']['missing_reason'], 'No photo on the found report')
        self.assertEqual((rows['image']['base_percent'], rows['image']['effective_percent']), (30, 0))
        self.assertEqual(rows['text']['effective_percent'], round(30 / .65))
        self.assertAlmostEqual(sum(r['contribution'] for r in rows.values()), m.score, places=6)

    def test_pages_call_it_a_similarity_score_not_a_probability(self):
        lost = self.make('owner', 'lost', color='black')
        self.make('finder', 'found', color='black')
        run_matching(lost)
        self.client.force_login(self.users['owner'])
        page = self.client.get(lost.get_absolute_url()).content.decode()
        self.assertIn('Similarity score', page)
        self.assertIn('evidence coverage', page)
        self.assertIn('not a probability', page)
        self.assertIn('heuristic', page)
        self.assertNotIn('confidence', page.lower())
        self.assertEqual(Match.Tier('high').label, 'Strong')
        self.assertEqual(Match.objects.get().tier_label, 'Strong similarity')
