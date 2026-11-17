"""Evaluation library shared by ``evaluate_hybrid``, ``retune`` and ``evaluate_matches``.

Production parity
-----------------
Every pair is scored by :func:`matching.engine.score_evidence` -- the exact
function the live service uses -- from :class:`matching.engine.Evidence`
records built here from dataset rows. Text normalisation and the text-model
release, image calibration, colour/brand comparison, graded chronology,
missing-evidence handling, evidence coverage and tier caps are therefore
identical. The only evaluation-specific inputs are candidate weight vectors
tried during validation tuning (passed as ``base_weights``) and ablations
such as "ranking without images" (passed as ``disabled``).

Dataset format: see docs/EVALUATION.md. Records may omit a photo, brand,
colour, category, location, date or time; those components are then missing
evidence exactly as in production.

Metrics are computed in pure Python with explicit handling of undefined
values (``None`` plus a reason) instead of silently reporting zeros.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from django.conf import settings

from . import engine

COMPONENT_ORDER = list(engine.COMPONENTS)
REPORT_SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# Dataset loading, identity and leakage
# ---------------------------------------------------------------------------
def load_dataset(path) -> tuple[dict, bytes]:
    path = Path(path)
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get('pairs'), list) or not payload['pairs']:
        raise ValueError('Dataset must be an object with a non-empty "pairs" list.')
    return payload, raw


def dataset_sha256(raw: bytes) -> str:
    """Dataset hash over LF-normalised bytes, so Windows (CRLF) and Linux checkouts agree."""
    return hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()


def dataset_kind(payload: dict) -> str:
    """'constructed', 'independent_reviewed' or 'unspecified' (treated as unreviewed)."""
    kind = payload.get('dataset_kind')
    if kind in ('constructed', 'independent_reviewed'):
        return kind
    if 'constructed' in str(payload.get('provenance', '')).lower():
        return 'constructed'
    return 'unspecified'


def check_split_leakage(pairs, base_dir: Path | None = None) -> list[str]:
    """Report / physical-object / photo identities must not cross splits."""
    membership = defaultdict(set)
    hashes = {}
    for row in pairs:
        split = row.get('split')
        for side in ('query', 'candidate'):
            record = row.get(side) or {}
            if record.get('id'):
                membership[('report', record['id'])].add(split)
            if record.get('physical_id'):
                membership[('physical object', record['physical_id'])].add(split)
            image = record.get('image')
            if image and base_dir is not None:
                image_path = (base_dir / image)
                if image_path not in hashes:
                    hashes[image_path] = (hashlib.sha256(image_path.read_bytes()).hexdigest()
                                          if image_path.exists() else None)
                if hashes[image_path]:
                    membership[('image', hashes[image_path])].add(split)
    errors = []
    for kind in ('report', 'physical object', 'image'):
        leaked = sum(len(s) > 1 for (k, _), s in membership.items() if k == kind)
        if leaked:
            errors.append(f'{leaked} {kind} identities appear in more than one split.')
    return errors


def condition_counts(pairs) -> dict:
    counts = Counter()
    for row in pairs:
        for tag in row.get('conditions') or ():
            counts[tag] += 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Evidence from dataset records (same Evidence type as production)
# ---------------------------------------------------------------------------
def _parse_date(value):
    if not value:
        return None
    return date.fromisoformat(value)


def record_evidence(record: dict, report_type: str, *, image_vector=None, image_kind='',
                    image_state='') -> engine.Evidence:
    has_photo = bool(record.get('image'))
    return engine.Evidence(
        report_type=report_type, title=record.get('title', '') or '',
        description=record.get('description', '') or '',
        category=record.get('category') or None, location=record.get('location') or None,
        event_date=_parse_date(record.get('date')), event_time=record.get('time') or None,
        brand=record.get('brand', '') or '', color=record.get('color', '') or '',
        image_vector=image_vector, image_model=image_kind if image_vector else '',
        has_photo=has_photo, image_state=image_state or ('success' if image_vector else
                                                         'failed' if has_photo else 'missing'),
        key=record.get('id'))


def taxonomy_pairs(payload: dict):
    tax = payload.get('taxonomy') or {}
    related = frozenset(frozenset(p) for p in tax.get('related_categories', []) if len(set(p)) == 2)
    adjacent = frozenset(frozenset(p) for p in tax.get('adjacent_locations', []) if len(set(p)) == 2)
    return related, adjacent


class ImageFeatureCache:
    """Image vectors per (file bytes, model, revision), computed by the
    production extractor and cached on disk next to the dataset."""

    def __init__(self, base_dir: Path, cache_dir: Path | None = None):
        self.base_dir = Path(base_dir)
        self.cache_dir = cache_dir
        self.memory = {}

    def vector(self, record: dict, kind: str):
        image = record.get('image')
        if not image:
            return None
        from .embeddings import CLIP_REVISION, RESNET_REVISION
        path = self.base_dir / image
        revision = {'resnet': RESNET_REVISION, 'clip': CLIP_REVISION}.get(kind, 'histogram-v1')
        key = hashlib.sha256(path.read_bytes() + kind.encode() + revision.encode()).hexdigest()
        if key in self.memory:
            return self.memory[key]
        file = self.cache_dir / f'{key}.json' if self.cache_dir else None
        if file is not None and file.exists():
            vector = json.loads(file.read_text())
        else:
            vector = engine.extract_image_vector(str(path), kind)
            if file is not None and vector is not None:
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(json.dumps(vector))
        self.memory[key] = vector
        return vector


def pair_roles(row):
    """(lost_record, found_record): records may declare report_type, else the
    query is the lost report (the convention of every bundled dataset)."""
    query, candidate = row['query'], row['candidate']
    if query.get('report_type') == 'found' or candidate.get('report_type') == 'lost':
        return candidate, query
    return query, candidate


def build_evidence(pairs, image_kind: str | None, cache: ImageFeatureCache | None):
    out = []
    for row in pairs:
        lost_rec, found_rec = pair_roles(row)
        vectors = {}
        for name, rec in (('lost', lost_rec), ('found', found_rec)):
            vectors[name] = cache.vector(rec, image_kind) if (image_kind and cache) else None
        out.append((record_evidence(lost_rec, 'lost', image_vector=vectors['lost'], image_kind=image_kind),
                    record_evidence(found_rec, 'found', image_vector=vectors['found'], image_kind=image_kind)))
    return out


def score_all(evidence_pairs, related=frozenset(), adjacent=frozenset(), base_weights=None,
              disabled=()) -> list[dict]:
    return [engine.score_evidence(lost, found, related_pairs=related, adjacent_pairs=adjacent,
                                  base_weights=base_weights, disabled=disabled)
            for lost, found in evidence_pairs]


# ---------------------------------------------------------------------------
# Metrics with explicit undefined handling
# ---------------------------------------------------------------------------
def roc_auc(labels, scores):
    """Mann-Whitney ROC-AUC with ties counted as half; ``None`` if single-class."""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(pos) * len(neg))


def metrics(rows, scores, threshold, k_values=(1, 3)) -> dict:
    """Pair classification at ``threshold`` plus per-query ranking metrics.

    Undefined values are ``None`` and explained in ``undefined``:
    precision with no predicted positives, recall / F1 with no positives,
    ROC-AUC with a single class, ranking metrics with no query that has a
    positive candidate.
    """
    labels = [int(r['label']) for r in rows]
    predicted = [s >= threshold for s in scores]
    tp = sum(1 for p, y in zip(predicted, labels) if p and y)
    fp = sum(1 for p, y in zip(predicted, labels) if p and not y)
    fn = sum(1 for p, y in zip(predicted, labels) if not p and y)
    tn = sum(1 for p, y in zip(predicted, labels) if not p and not y)
    undefined = {}
    precision = tp / (tp + fp) if (tp + fp) else None
    if precision is None:
        undefined['precision'] = 'no pair scored at or above the threshold'
    recall = tp / (tp + fn) if (tp + fn) else None
    if recall is None:
        undefined['recall'] = 'no positive pairs'
    if precision is None or recall is None:
        f1 = None
        undefined['f1'] = 'precision or recall undefined'
    else:
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    auc = roc_auc(labels, scores)
    if auc is None:
        undefined['roc_auc'] = 'needs both positive and negative pairs'

    groups = defaultdict(list)
    for row, score in zip(rows, scores):
        groups[row['query_id']].append((score, str(row['candidate']['id']), int(row['label'])))
    rr, at_k = [], {k: [] for k in k_values}
    for group in groups.values():
        if not any(y for _, _, y in group):
            continue  # no positive candidate: rank of the true match undefined
        ranked = sorted(group, key=lambda x: (-x[0], x[1]))
        rr.append(next(1 / i for i, entry in enumerate(ranked, 1) if entry[2]))
        for k in k_values:
            at_k[k].append(sum(entry[2] for entry in ranked[:k]) / k)
    mrr = sum(rr) / len(rr) if rr else None
    if mrr is None:
        undefined['mrr'] = 'no query has a positive candidate'
    result = {
        'threshold': threshold, 'pairs': len(rows), 'positives': sum(labels),
        'negatives': len(labels) - sum(labels),
        'confusion': {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn},
        'precision': precision, 'recall': recall, 'f1': f1, 'roc_auc': auc, 'mrr': mrr,
        'queries_ranked': len(rr),
    }
    for k in k_values:
        result[f'precision_at_{k}'] = (sum(at_k[k]) / len(at_k[k])) if at_k[k] else None
        result[f'hit_rate_at_{k}'] = (sum(1 for v in at_k[k] if v > 0) / len(at_k[k])) if at_k[k] else None
    result['undefined'] = undefined
    return result


def _key(value):
    return -1.0 if value is None else value


def tune_on_validation(results_by_weights: dict, rows, val_idx, thresholds) -> dict:
    """Pick (weights, threshold) by validation F1, then precision, then MRR.

    Only validation indices are ever passed in; the test split is not seen.
    Ties are broken deterministically (lower threshold, then weight order).
    """
    best = None
    for wkey, scores in results_by_weights.items():
        val_rows = [rows[i] for i in val_idx]
        val_scores = [scores[i] for i in val_idx]
        for threshold in thresholds:
            m = metrics(val_rows, val_scores, threshold)
            key = (_key(m['f1']), _key(m['precision']), _key(m['mrr']), -threshold)
            if best is None or key > best['key']:
                best = {'key': key, 'weights_key': wkey, 'threshold': threshold, 'validation': m}
    return best


def error_analysis(rows, results, threshold, limit=15) -> dict:
    """False positives / negatives at ``threshold`` with their evidence."""
    def summary(row, res):
        lost, found = pair_roles(row)
        return {'query_id': row['query_id'], 'candidate_id': row['candidate']['id'],
                'label': row['label'], 'score': round(res['score'], 4), 'tier': res['tier'],
                'coverage': res['coverage'], 'missing': res['missing'],
                'chronology': res['chronology'],
                'components': {k: (None if v is None else round(v, 4)) for k, v in res['components'].items()},
                'conditions': row.get('conditions', []),
                'lost_text': (lost.get('title', '') + ' ' + lost.get('description', '')).strip()[:160],
                'found_text': (found.get('title', '') + ' ' + found.get('description', '')).strip()[:160]}
    fps = [summary(r, s) for r, s in zip(rows, results) if s['score'] >= threshold and not r['label']]
    fns = [summary(r, s) for r, s in zip(rows, results) if s['score'] < threshold and r['label']]
    fps.sort(key=lambda x: -x['score'])
    fns.sort(key=lambda x: x['score'])
    return {'false_positives': fps[:limit], 'false_negatives': fns[:limit],
            'false_positive_count': len(fps), 'false_negative_count': len(fns)}


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
def git_commit() -> str | None:
    try:
        out = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=settings.BASE_DIR, capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


def git_dirty() -> bool | None:
    """True when tracked matching/config code differs from the recorded commit."""
    try:
        out = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no', '--', 'matching',
                              'config'], cwd=settings.BASE_DIR, capture_output=True, text=True, timeout=10)
        return bool(out.stdout.strip()) if out.returncode == 0 else None
    except Exception:
        return None


def environment() -> dict:
    import django
    env = {'python': platform.python_version(), 'django': django.get_version(),
           'platform': platform.platform(terse=True)}
    for module in ('PIL', 'torch', 'transformers', 'sentence_transformers'):
        try:
            mod = __import__(module)
            env[module] = getattr(mod, '__version__', 'installed')
        except Exception:
            env[module] = None
    return env


def engine_identity(text_kind: str, image_kind: str | None) -> dict:
    from . import chronology, textmodel
    from .embeddings import CLIP_MODEL, CLIP_REVISION, RESNET_MODEL, RESNET_REVISION, TEXT_MODEL, TEXT_REVISION
    image_models = {'resnet': f'{RESNET_MODEL}@{RESNET_REVISION}', 'clip': f'{CLIP_MODEL}@{CLIP_REVISION}',
                    'histogram': 'Pillow colour histogram + luma thumbnail (baseline)'}
    return {
        'engine_version': settings.MATCHING['VERSION'],
        'profile_version': settings.MATCHING_PROFILE.get('version'),
        'text': text_kind,
        'text_model': (f'{TEXT_MODEL}@{TEXT_REVISION}' if text_kind == 'sbert'
                       else textmodel.release_info()),
        'image': image_kind,
        'image_model': image_models.get(image_kind) if image_kind else None,
        'image_chance': settings.MATCHING.get('IMAGE_CHANCE', {}),
        'weights': dict(settings.MATCHING['WEIGHTS']),
        'thresholds': {'medium': settings.MATCHING['TIER_MEDIUM'], 'high': settings.MATCHING['TIER_HIGH'],
                       'min_score': settings.MATCHING['MIN_SCORE']},
        'coverage_rules': engine.coverage_rules(),
        'chronology': chronology.config(),
        'scoring_function': 'matching.engine.score_evidence',
    }


def dataset_identity(path: Path, payload: dict, raw: bytes, validation: dict | None) -> dict:
    kind = dataset_kind(payload)
    reviewed = bool(validation and validation.get('research_ready')) and kind == 'independent_reviewed'
    splits = Counter(row.get('split') for row in payload['pairs'])
    labels = Counter(int(row.get('label', -1)) for row in payload['pairs'])
    try:
        shown = str(path.resolve().relative_to(Path(settings.BASE_DIR).resolve()))
    except ValueError:
        shown = path.name
    return {
        'path': shown.replace('\\', '/'),
        'sha256': dataset_sha256(raw),
        'sha256_note': 'SHA-256 of the file with LF line endings (identical on every checkout)',
        'dataset_kind': kind,
        'evidence_class': ('independently collected, manually reviewed' if reviewed
                           else 'engineering check (constructed or unreviewed data)'),
        'research_ready': bool(validation and validation.get('research_ready')),
        'provenance': payload.get('provenance'),
        'pairs': len(payload['pairs']),
        'splits': dict(sorted(splits.items())),
        'labels': {str(k): v for k, v in sorted(labels.items())},
        'conditions': condition_counts(payload['pairs']),
        'validation_errors': (validation or {}).get('errors', [])[:10],
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def finite(value):
    return value if value is None or math.isfinite(value) else None
