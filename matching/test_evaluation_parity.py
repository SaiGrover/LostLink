"""Requirements 5.1-5.4: evaluation shares production scoring, handles missing
evidence, is reproducible, leak-free, provenance-stamped and safe with
undefined metrics."""
import json
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users
from items.models import Item
from matching import evaluation as ev
from matching.research import validate_dataset
from matching.services import score_pair


def record(identity, physical, description, image='', **kw):
    base = {'id': identity, 'physical_id': physical, 'title': '', 'description': description,
            'category': 'bags', 'location': 'library', 'date': '2026-08-01', 'time': '',
            'brand': '', 'color': '', 'image': image}
    base.update(kw)
    return base


REVIEW = {'label_source': 'manual', 'status': 'approved', 'reviewer': 'reviewer-1',
          'independent_photos': True, 'independent_descriptions': True}


def build_dataset(folder: Path, kind='independent_reviewed', with_images=True):
    """Tiny two-split fixture: schema/behaviour checks only, not evidence."""
    pairs = []
    for n, split in enumerate(('validation', 'test')):
        images = []
        for i in range(3):
            name = f'{split}-{i}.png'
            if with_images:
                Image.new('RGB', (16, 16), (40 + 90 * i, 20 + n * 60, 60)).save(folder / name)
            images.append(name if with_images else '')
        q = record(f'{split}-lost', f'{split}-obj-a', 'Black leather wallet with silver logo',
                   image=images[0], color='black', title='Wallet')
        pos = record(f'{split}-found-a', f'{split}-obj-a', 'Dark wallet made of leather, silver branding',
                     image=images[1], color='black', date='2026-08-02', title='Wallet')
        neg = record(f'{split}-found-b', f'{split}-obj-b', 'Red water bottle with dents',
                     image=images[2], color='red and blue', category='bottles', title='Bottle')
        pairs.append({'query_id': q['id'], 'split': split, 'label': 1, 'query': q, 'candidate': pos,
                      'conditions': ['genuine_positive', 'different_wording'], 'review': REVIEW})
        pairs.append({'query_id': q['id'], 'split': split, 'label': 0, 'query': q, 'candidate': neg,
                      'conditions': ['multicolour'], 'review': REVIEW})
    payload = {'dataset_kind': kind, 'provenance': 'Unit-test fixture (synthetic, not evidence).',
               'limitations': ['Synthetic fixture.'], 'pairs': pairs}
    path = folder / 'pairs.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path, payload


class MetricTests(SimpleTestCase):
    def rows(self, labels, query='q'):
        return [{'query_id': query, 'candidate': {'id': str(i)}, 'label': y} for i, y in enumerate(labels)]

    def test_metrics_cover_precision_recall_ranking_and_auc(self):
        m = ev.metrics(self.rows([0, 1, 0]), [.9, .8, .1], .7)
        self.assertEqual((m['precision'], m['recall'], m['mrr']), (.5, 1, .5))
        self.assertAlmostEqual(m['f1'], 2 / 3)
        self.assertEqual(m['roc_auc'], .5)
        self.assertEqual(m['precision_at_1'], 0)
        self.assertAlmostEqual(m['precision_at_3'], 1 / 3)
        self.assertEqual(m['hit_rate_at_3'], 1)
        self.assertEqual(m['confusion'], {'tp': 1, 'fp': 1, 'fn': 0, 'tn': 1})

    def test_single_class_and_undefined_metrics_are_none_with_reasons(self):
        only_neg = ev.metrics(self.rows([0, 0]), [.9, .1], .5)
        self.assertIsNone(only_neg['recall'])
        self.assertIsNone(only_neg['roc_auc'])
        self.assertIsNone(only_neg['mrr'])
        self.assertIn('roc_auc', only_neg['undefined'])
        nothing_predicted = ev.metrics(self.rows([1, 0]), [.2, .1], .5)
        self.assertIsNone(nothing_predicted['precision'])
        self.assertIsNone(nothing_predicted['f1'])
        self.assertEqual(nothing_predicted['recall'], 0)
        self.assertEqual(nothing_predicted['roc_auc'], 1.0)

    def test_ties_count_half_in_auc(self):
        self.assertEqual(ev.roc_auc([1, 0], [.5, .5]), .5)

    def test_legacy_import_path_still_works(self):
        from matching.management.commands.evaluate_hybrid import metrics
        self.assertIs(metrics, ev.metrics)


@lightweight_models
class EvaluationParityTests(SimpleTestCase):
    def test_missing_attributes_never_break_scoring(self):
        rows = [{'query_id': 'q', 'split': 'test', 'label': 1,
                 'query': record('q', 'a', 'Blue umbrella'),
                 'candidate': record('c', 'a', 'Blue folding umbrella', brand='', color='')}]
        evidence = ev.build_evidence(rows, None, None)
        result = ev.score_all(evidence)[0]
        self.assertIn('attribute', result['missing'])
        self.assertIn('image', result['missing'])
        self.assertGreater(result['score'], 0)

    def test_records_without_date_time_or_metadata_are_missing_evidence(self):
        rows = [{'query_id': 'q', 'split': 'test', 'label': 0,
                 'query': record('q', 'a', 'Wallet', date='', category='', location=''),
                 'candidate': record('c', 'b', 'Wallet', time='09:00')}]
        result = ev.score_all(ev.build_evidence(rows, None, None))[0]
        self.assertTrue({'date', 'category', 'location'} <= set(result['missing']))
        self.assertEqual(result['chronology'], 'unknown')

    def test_taxonomy_from_dataset_uses_the_production_rules(self):
        rows = [{'query_id': 'q', 'split': 'test', 'label': 1,
                 'query': record('q', 'a', 'Wallet', category='bags', location='library'),
                 'candidate': record('c', 'a', 'Wallet', category='others', location='block-a')}]
        related, adjacent = ev.taxonomy_pairs({'taxonomy': {'related_categories': [['bags', 'others']],
                                                            'adjacent_locations': [['library', 'block-a']]}})
        result = ev.score_all(ev.build_evidence(rows, None, None), related, adjacent)[0]
        self.assertEqual((result['components']['category'], result['components']['location']), (.5, .5))


@lightweight_models
@isolated_media
class ProductionEvaluationParityTests(TestCase):
    def test_evaluation_and_production_give_identical_scores(self):
        tax = make_taxonomy()
        users = make_users()
        tax['bags'].related.add(tax['electronics'])
        cases = [
            dict(lost=dict(title='Phone', description='Black mobile phone with cracked screen', color='black'),
                 found=dict(title='Cellphone', description='Dark cellphone with damaged display', color='Black',
                            category='electronics', location='block_a'), gap=1),
            dict(lost=dict(title='Bottle', description='Steel bottle', color='red and blue', brand='Milton'),
                 found=dict(title='Bottle', description='Metal flask', color='red', brand='milton'), gap=-3),
            dict(lost=dict(title='Umbrella', description='Blue umbrella'),
                 found=dict(title='Umbrella', description='Blue folding umbrella'), gap=-12),
        ]
        today = date.today()
        for case in cases:
            with self.subTest(case=case['lost']['title']):
                lost_kw = {'category': 'bags', 'location': 'library', **case['lost']}
                found_kw = {'category': 'bags', 'location': 'library', **case['found']}
                lost = Item.objects.create(reporter=users['owner'], report_type='lost', status='approved',
                                           event_date=today - timedelta(days=20),
                                           **{k: (tax[v] if k in ('category', 'location') else v)
                                              for k, v in lost_kw.items()})
                found = Item.objects.create(reporter=users['finder'], report_type='found', status='approved',
                                            event_date=today - timedelta(days=20 - case['gap']),
                                            **{k: (tax[v] if k in ('category', 'location') else v)
                                               for k, v in found_kw.items()})
                production = score_pair(lost, found)

                def as_record(item):
                    return record(str(item.pk), 'x', item.description, title=item.title,
                                  category=str(item.category_id), location=str(item.location_id),
                                  date=item.event_date.isoformat(), brand=item.brand, color=item.color)
                row = {'query_id': str(lost.pk), 'split': 'test', 'label': 1,
                       'query': as_record(lost), 'candidate': as_record(found)}
                related = frozenset({frozenset((str(tax['bags'].pk), str(tax['electronics'].pk)))})
                adjacent = frozenset({frozenset((str(tax['library'].pk), str(tax['block_a'].pk)))})
                evaluated = ev.score_all(ev.build_evidence([row], None, None), related, adjacent)[0]
                self.assertEqual(evaluated['score'], production['score'])
                self.assertEqual(evaluated['components'], production['components'])
                self.assertEqual(evaluated['missing'], production['missing'])
                self.assertEqual((evaluated['tier'], evaluated['coverage'], evaluated['chronology']),
                                 (production['tier'], production['coverage'], production['chronology']))


@lightweight_models
class EvaluationCommandTests(SimpleTestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def run_eval(self, path, out):
        call_command('evaluate_hybrid', str(path), output=str(out), image=['histogram', 'none'],
                     text=['tfidf'], stdout=StringIO())
        return json.loads(out.read_text(encoding='utf-8'))

    def test_report_records_provenance_and_is_reproducible(self):
        path, _ = build_dataset(self.root)
        first = self.run_eval(path, self.root / 'a.json')
        second = self.run_eval(path, self.root / 'b.json')
        for key in ('generated_at',):
            first.pop(key), second.pop(key)
        self.assertEqual(first, second)
        ds = first['dataset']
        self.assertEqual(len(ds['sha256']), 64)
        self.assertEqual(ds['dataset_kind'], 'independent_reviewed')
        self.assertEqual(ds['conditions'], {'different_wording': 2, 'genuine_positive': 2, 'multicolour': 2})
        engine = first['engine']
        for key in ('engine_version', 'profile_version', 'text_model', 'weights', 'thresholds',
                    'chronology', 'coverage_rules', 'image_chance', 'scoring_function'):
            self.assertIn(key, engine)
        self.assertEqual(engine['text_model']['release'], 'lexical-v1')
        self.assertIn('protocol', first)
        self.assertTrue(first['limitations'])
        exp = first['experiments'][0]
        for key in ('validation', 'test', 'test_as_deployed', 'test_without_images', 'test_errors'):
            self.assertIn(key, exp)
        # tiny fixture: not research-ready -> labelled as an engineering check
        self.assertFalse(ds['research_ready'])
        self.assertIn('ENGINEERING CHECK ONLY', first['limitations'][0])

    def test_constructed_dataset_is_labelled_and_never_research(self):
        path, _ = build_dataset(self.root, kind='constructed')
        report = self.run_eval(path, self.root / 'r.json')
        self.assertEqual(report['dataset']['dataset_kind'], 'constructed')
        self.assertIn('engineering check', report['dataset']['evidence_class'])

    def test_threshold_is_tuned_on_validation_only(self):
        path, payload = build_dataset(self.root)
        base = self.run_eval(path, self.root / 'a.json')
        # Flip every *test* label: the selected weights/threshold must not move.
        for row in payload['pairs']:
            if row['split'] == 'test':
                row['label'] = 1 - row['label']
                row['candidate']['physical_id'] = (row['query']['physical_id'] if row['label']
                                                   else row['candidate']['physical_id'] + '-x')
        path.write_text(json.dumps(payload), encoding='utf-8')
        flipped = self.run_eval(path, self.root / 'b.json')
        self.assertEqual(base['selected_by_validation'], flipped['selected_by_validation'])
        self.assertNotEqual(base['experiments'][0]['test'], flipped['experiments'][0]['test'])

    def test_split_leakage_is_rejected(self):
        path, payload = build_dataset(self.root)
        payload['pairs'][2]['candidate']['physical_id'] = payload['pairs'][0]['candidate']['physical_id']
        path.write_text(json.dumps(payload), encoding='utf-8')
        with self.assertRaises(CommandError):
            self.run_eval(path, self.root / 'x.json')
        path, payload = build_dataset(self.root)
        payload['pairs'][2]['candidate']['image'] = payload['pairs'][0]['candidate']['image']
        path.write_text(json.dumps(payload), encoding='utf-8')
        with self.assertRaises(CommandError):
            self.run_eval(path, self.root / 'y.json')

    def test_single_class_split_is_rejected(self):
        path, payload = build_dataset(self.root)
        payload['pairs'] = [p for p in payload['pairs'] if not (p['split'] == 'test' and p['label'] == 0)]
        path.write_text(json.dumps(payload), encoding='utf-8')
        with self.assertRaises(CommandError):
            self.run_eval(path, self.root / 'z.json')

    def test_missing_photos_are_supported_in_reviewed_data(self):
        path, payload = build_dataset(self.root, with_images=False)
        for row in payload['pairs']:
            row['conditions'].append('missing_photo')
        path.write_text(json.dumps(payload), encoding='utf-8')
        summary = validate_dataset(path, allow_small=True)
        self.assertTrue(summary['checks_passed'], summary['errors'])
        report = self.run_eval(path, self.root / 'm.json')
        exp = report['experiments'][0]
        # no photos anywhere: dropping the image component changes nothing
        self.assertEqual(exp['test']['confusion'], exp['test_without_images']['confusion'])
        self.assertEqual(exp['test']['roc_auc'], exp['test_without_images']['roc_auc'])

    def test_validator_reports_condition_coverage_and_bad_tags(self):
        path, payload = build_dataset(self.root)
        summary = validate_dataset(path, allow_small=True)
        self.assertTrue(summary['checks_passed'], summary['errors'])
        self.assertFalse(summary['research_ready'])          # sample-size check disabled
        self.assertEqual(summary['condition_counts']['multicolour'], 2)
        self.assertTrue(any('No pairs tagged' in w for w in summary['warnings']))
        payload['pairs'][0]['conditions'] = ['hard_negative']   # wrong for a positive
        payload['pairs'][1]['conditions'] = ['made-up']
        path.write_text(json.dumps(payload), encoding='utf-8')
        errors = ' '.join(validate_dataset(path, allow_small=True)['errors'])
        self.assertIn('hard_negative', errors)
        self.assertIn('unknown condition', errors)

    def test_bundled_pilot_is_not_research_ready(self):
        from django.conf import settings
        pilot = Path(settings.BASE_DIR) / 'evaluation' / 'pilot' / 'dataset.json'
        payload, raw = ev.load_dataset(pilot)
        self.assertEqual(ev.dataset_kind(payload), 'constructed')
        self.assertFalse(validate_dataset(pilot)['research_ready'])
        self.assertEqual(ev.check_split_leakage(payload['pairs'], pilot.parent), [])

    def test_saved_pilot_results_belong_to_the_current_matcher(self):
        from django.conf import settings
        results = json.loads((Path(settings.BASE_DIR) / 'evaluation' / 'results.json').read_text(encoding='utf-8'))
        self.assertEqual(results.get('schema_version'), ev.REPORT_SCHEMA_VERSION)
        self.assertEqual(results['engine']['engine_version'], settings.MATCHING['VERSION'])
        self.assertEqual(results['engine']['text_model']['release'], settings.MATCHING['TEXT_MODEL_RELEASE'])
        pilot = Path(settings.BASE_DIR) / 'evaluation' / 'pilot' / 'dataset.json'
        self.assertEqual(results['dataset']['sha256'], ev.dataset_sha256(pilot.read_bytes()))
        self.assertEqual(ev.dataset_sha256(b'a\r\nb\n'), ev.dataset_sha256(b'a\nb\n'))
        self.assertIn('ENGINEERING CHECK ONLY', results['limitations'][0])
