"""Evaluate the production matcher on a labelled pair dataset.

    python manage.py evaluate_hybrid evaluation/pilot/dataset.json
    python manage.py evaluate_hybrid reviewed.json --text tfidf sbert --image resnet clip

Every pair is scored by ``matching.engine.score_evidence`` (the production
scoring function) through ``matching.evaluation``. Weights and the decision
threshold are tuned on the validation split only; the test split is scored
once with the selected setting. Output records dataset identity and hash,
evidence class (constructed engineering check vs independently reviewed),
model names / revisions, profile and engine version, configuration, weights,
thresholds, timestamp, metrics, ablations and limitations.
"""
import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from matching import evaluation as ev
from matching.evaluation import metrics  # noqa: F401  (re-exported for callers/tests)

THRESHOLDS = [round(0.40 + 0.05 * i, 2) for i in range(12)]  # 0.40 .. 0.95


def weight_candidates():
    base = dict(settings.MATCHING['WEIGHTS'])
    candidates = {'profile': base}
    for tw in (0.2, 0.3, 0.4):
        w = dict(base, text=tw, image=round(0.6 - tw, 2))
        key = f'text{tw:.1f}-image{0.6 - tw:.1f}'
        if w != base:
            candidates[key] = w
    return candidates


class Command(BaseCommand):
    help = ('Evaluate the production scoring function on labelled pairs: tune weights/threshold on '
            'validation only, report untouched test metrics with provenance.')

    def add_arguments(self, parser):
        parser.add_argument('dataset')
        parser.add_argument('--output', default=None,
                            help='Default: evaluation/results.json for a constructed dataset, '
                                 'evaluation/research/results-<hash>.json otherwise.')
        parser.add_argument('--text', nargs='+', default=['tfidf'], choices=['tfidf', 'sbert'])
        parser.add_argument('--image', nargs='+', default=['resnet', 'clip'],
                            choices=['resnet', 'clip', 'histogram', 'none'])
        parser.add_argument('--no-cache', action='store_true', help='Do not cache image vectors on disk.')
        parser.add_argument('--apply', action='store_true',
                            help='Activate the validation-selected profile (reviewed research data only); '
                                 'bump the engine version and run rebuild_matches afterwards.')

    def handle(self, dataset, output, text, image, **options):
        from matching.research import validate_dataset
        path = Path(dataset)
        if options.get('apply'):
            summary = validate_dataset(path)
            if not summary['research_ready']:
                raise CommandError('Profile activation requires reviewed research data: '
                                   + '; '.join(summary['errors']))
        try:
            payload, raw = ev.load_dataset(path)
        except (OSError, ValueError) as exc:
            raise CommandError(f'Cannot read dataset: {exc}') from exc
        pairs = payload['pairs']
        for row in pairs:
            if row.get('label') not in (0, 1) or row.get('split') not in ('validation', 'test'):
                raise CommandError('Every pair needs a 0/1 label and a validation/test split.')
        leaks = ev.check_split_leakage(pairs, path.parent)
        if leaks:
            raise CommandError('Split leakage: ' + ' '.join(leaks))
        for split in ('validation', 'test'):
            if {r['label'] for r in pairs if r['split'] == split} != {0, 1}:
                raise CommandError(f'Split {split!r} needs both positive and negative pairs.')
        try:
            validation = validate_dataset(path, allow_small=True)
        except CommandError as exc:
            validation = {'research_ready': False, 'errors': [str(exc)]}
        identity = ev.dataset_identity(path, payload, raw, validation)

        related, adjacent = ev.taxonomy_pairs(payload)
        cache_dir = None if options.get('no_cache') else path.parent / 'features'
        cache = ev.ImageFeatureCache(path.parent, cache_dir)
        val_idx = [i for i, r in enumerate(pairs) if r['split'] == 'validation']
        test_idx = [i for i, r in enumerate(pairs) if r['split'] == 'test']
        experiments = []
        saved_env = {k: os.environ.get(k) for k in ('LOSTLINK_TEXT_MODEL', 'LOSTLINK_IMAGE_MODEL')}
        try:
            for txt in text:
                for img in image:
                    img_kind = None if img == 'none' else img
                    os.environ['LOSTLINK_TEXT_MODEL'] = txt
                    os.environ['LOSTLINK_IMAGE_MODEL'] = img_kind or 'histogram'
                    self.stdout.write(f'Evaluating {txt} + {img} ...')
                    experiments.append(self._experiment(pairs, txt, img_kind, cache, related, adjacent,
                                                        val_idx, test_idx))
        finally:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        selected = max(experiments, key=lambda e: (ev._key(e['validation']['f1']),
                                                   ev._key(e['validation']['precision']),
                                                   ev._key(e['validation']['mrr'])))
        limitations = list(payload.get('limitations', []))
        if identity['dataset_kind'] != 'independent_reviewed' or not identity['research_ready']:
            limitations.insert(0, 'ENGINEERING CHECK ONLY: this dataset is constructed or not independently '
                                  'reviewed; these metrics are not evidence of real-world accuracy.')
        limitations.append('Scores are weighted similarity scores, not calibrated probabilities; '
                           'tiers are heuristic thresholds.')
        report = {
            'schema_version': ev.REPORT_SCHEMA_VERSION,
            'generated_at': ev.utc_now(),
            'code_commit': ev.git_commit(),
            'code_uncommitted_changes': ev.git_dirty(),
            'dataset': identity,
            'engine': ev.engine_identity(text[0], None if image[0] == 'none' else image[0]),
            'environment': ev.environment(),
            'protocol': {
                'tuning': 'weights and threshold selected on the validation split only '
                          '(F1, then precision, then MRR; ties -> lower threshold)',
                'test': 'test split scored once with the selected setting',
                'weight_candidates': weight_candidates(),
                'thresholds_tried': THRESHOLDS,
                'leakage_checks': 'report ids, physical-object ids and image bytes are split-disjoint',
            },
            'experiments': experiments,
            'selected_by_validation': {k: selected[k] for k in ('text', 'image', 'weights_key', 'weights',
                                                                'threshold')},
            'limitations': limitations,
        }
        if output:
            target = Path(output)
        elif identity['dataset_kind'] == 'constructed':
            target = Path(settings.BASE_DIR) / 'evaluation' / 'results.json'
        else:
            target = Path(settings.BASE_DIR) / 'evaluation' / 'research' / f'results-{identity["sha256"][:12]}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, default=str) + '\n', encoding='utf-8')

        if options.get('apply'):
            profile_path = Path(settings.BASE_DIR) / 'matching' / 'profile.json'
            profile = json.loads(profile_path.read_text(encoding='utf-8'))
            profile.update({
                'version': 'tuned-' + identity['sha256'][:10],
                'text': selected['text'], 'image': selected['image'] or profile.get('image'),
                'weights': selected['weights'], 'medium': selected['threshold'],
                'high': min(.99, max(.85, selected['threshold'] + .1)),
                'selection': f'Validation-only selection on {identity["path"]} '
                             f'(sha256 {identity["sha256"][:12]}); see {target.name}.'})
            profile_path.write_text(json.dumps(profile, indent=2) + '\n', encoding='utf-8')
            self.stdout.write('Profile activated (other profile fields kept). Restart the app and run '
                              'rebuild_matches before serving traffic.')
        self.stdout.write(self.style.SUCCESS(str(target)))

    def _experiment(self, pairs, txt, img_kind, cache, related, adjacent, val_idx, test_idx):
        evidence = ev.build_evidence(pairs, img_kind, cache)
        if txt == 'sbert':
            from matching import engine
            from matching.embeddings import text_vectors
            texts = sorted({engine._joined(e.title, e.description) for pair in evidence for e in pair})
            vectors = dict(zip(texts, text_vectors(texts)))
            model = engine.text_model_id({'text': 'sbert', 'image': img_kind or 'histogram'})
            for pair in evidence:
                for e in pair:
                    e.text_vector, e.text_model = vectors[engine._joined(e.title, e.description)], model
        candidates = weight_candidates()
        disabled = () if img_kind else ('image',)  # text + metadata only
        results = {key: ev.score_all(evidence, related, adjacent, base_weights=w, disabled=disabled)
                   for key, w in candidates.items()}
        scores = {key: [r['score'] for r in res] for key, res in results.items()}
        best = ev.tune_on_validation(scores, pairs, val_idx, THRESHOLDS)
        wkey, threshold = best['weights_key'], best['threshold']
        test_rows = [pairs[i] for i in test_idx]
        test = ev.metrics(test_rows, [scores[wkey][i] for i in test_idx], threshold)

        # As deployed: profile weights at the production "moderate" threshold.
        deployed_threshold = settings.MATCHING['TIER_MEDIUM']
        deployed = ev.metrics(test_rows, [scores['profile'][i] for i in test_idx], deployed_threshold)
        # Ranking with vs without images (same weights, image dropped and renormalised).
        no_image = ev.score_all(evidence, related, adjacent, base_weights=candidates[wkey], disabled=('image',))
        without_images = ev.metrics(test_rows, [no_image[i]['score'] for i in test_idx], threshold)
        test_results = [results[wkey][i] for i in test_idx]
        tiers = {t: sum(1 for r in test_results if r['tier'] == t) for t in ('high', 'medium', 'low')}
        coverage = [r['coverage'] for r in test_results]
        return {
            'text': txt, 'image': img_kind, 'weights_key': wkey, 'weights': candidates[wkey],
            'threshold': threshold, 'validation': best['validation'], 'test': test,
            'test_as_deployed': {'weights': 'profile', 'threshold': deployed_threshold, **deployed},
            'test_without_images': without_images,
            'test_tiers': tiers,
            'test_mean_coverage': round(sum(coverage) / len(coverage), 4) if coverage else None,
            'test_errors': ev.error_analysis(test_rows, test_results, threshold),
        }
