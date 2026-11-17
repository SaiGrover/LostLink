"""LostLink hybrid matching engine (synopsis Section 4).

One pure function, :func:`score_evidence`, scores a lost/found pair from two
:class:`Evidence` records. The production service (``matching.services``)
and the evaluation tooling (``matching.evaluation``) both build ``Evidence``
and call it, so there is exactly one implementation of normalisation,
calibration, chronology, missing-evidence handling, coverage and tiers.

Score
-----
::

    similarity = sum(w_c * s_c for c in compared) / sum(w_c for c in compared)

over the components with evidence on both reports (text .30, image .30,
location .15, category .10, date .10, brand/colour .05). The result is a
**weighted similarity (compatibility) score in [0, 1]**, not a probability
that the reports describe the same object: the weights are design choices and
the score has not been calibrated against real outcomes.

Missing vs contradictory evidence
---------------------------------
* *Missing* -- no evidence on one or both sides (no photo, a photo that could
  not be processed, no brand/colour on both, no usable text, no date). The
  component is dropped and the remaining weights are renormalised, so absent
  evidence is never scored as disagreement.
* *Contradictory* -- evidence on both sides that disagrees (different
  category, different zone, "red" vs "blue", photos that look unrelated, a
  found-before-lost chronology). It is scored, usually near zero.

Evidence coverage = share of the *base* weight whose component was compared.
Renormalisation keeps sparse pairs comparable in *strength*, but a 0.80 built
on 65% of the evidence is not as well supported as 0.80 on 100%, so the tier
is capped when coverage is low (``EVIDENCE_COVERAGE`` settings). Coverage is
stored and shown next to the score.

Tiers
-----
``high`` / ``medium`` / ``low`` (displayed as strong / moderate / weak
similarity) are heuristic display thresholds from the synopsis (> 0.85,
0.70-0.85, < 0.70), optionally lowered by caps: low evidence coverage and
suspicious / contradictory chronology. They are not confidence levels.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.conf import settings

from . import chronology as _chronology
from . import colour as _colour
from . import textmodel

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False


COMPONENTS = ('text', 'image', 'location', 'category', 'date', 'attribute')
TIER_ORDER = {'low': 0, 'medium': 1, 'high': 2}
TIER_LABELS = {'high': 'Strong similarity', 'medium': 'Moderate similarity', 'low': 'Weak similarity'}

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'with', 'for',
    'is', 'was', 'it', 'this', 'that', 'my', 'i', 'near', 'from', 'by', 'has',
    'have', 'had', 'lost', 'found', 'item', 'items', 'please', 'somewhere',
}
_IRREGULAR_LEMMAS = {
    'children': 'child', 'feet': 'foot', 'men': 'man', 'mice': 'mouse',
    'people': 'person', 'teeth': 'tooth', 'women': 'woman',
}


def weights() -> dict:
    """The active, versioned component weights (synopsis Section 4.9)."""
    return dict(settings.MATCHING['WEIGHTS'])


def lemmatize_token(token: str) -> str:
    """Conservative plural folding kept for :func:`clean_text` (legacy helper)."""
    if token in _IRREGULAR_LEMMAS:
        return _IRREGULAR_LEMMAS[token]
    if len(token) > 4 and token.endswith('ies'):
        return token[:-3] + 'y'
    if len(token) > 4 and token.endswith(('ches', 'shes', 'sses', 'xes', 'zes')):
        return token[:-2]
    if len(token) > 3 and token.endswith('s') and not token.endswith(('ss', 'us', 'is')):
        return token[:-1]
    return token


def clean_text(text: str) -> str:
    """Lowercase, strip punctuation, drop stop-words and fold plurals (legacy)."""
    tokens = _WORD_RE.findall((text or '').lower())
    tokens = [lemmatize_token(t) for t in tokens if t not in _STOPWORDS and len(t) > 1]
    return ' '.join(tokens)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def text_model_id(config: dict | None = None) -> str:
    """Identifier of the text representation currently in use."""
    from .embeddings import TEXT_MODEL, TEXT_REVISION, configuration
    config = config or configuration()
    if config['text'] == 'sbert':
        return f'sbert:{TEXT_MODEL}@{TEXT_REVISION[:12]}'
    return textmodel.active_release()


def text_representation(title: str = '', description: str = '', config: dict | None = None):
    """Per-report text features: a sparse dict (lexical) or a dense list (SBERT)."""
    from .embeddings import configuration, text_vectors
    config = config or configuration()
    if config['text'] == 'sbert':
        return text_vectors([_joined(title, description)])[0]
    return textmodel.represent(title, description)


def _joined(title: str, description: str) -> str:
    title, description = (title or '').strip(), (description or '').strip()
    if title and description:
        return f'{title}. {description}'
    return title or description


def text_similarity(query_text: str, candidate_texts: list[str]) -> list[float]:
    """Similarity of ``query_text`` to each candidate, each computed *pairwise*.

    Every value depends only on the two texts and the active model release,
    never on the other candidates in the list.
    """
    from .embeddings import configuration, text_vectors
    if not candidate_texts:
        return []
    if configuration()['text'] == 'sbert':
        vectors = text_vectors([query_text] + list(candidate_texts))
        return [vector_similarity(vectors[0], v) for v in vectors[1:]]
    query = textmodel.represent('', query_text)
    return [textmodel.cosine(query, textmodel.represent('', text)) for text in candidate_texts]


def _text_component(a: 'Evidence', b: 'Evidence') -> tuple[float | None, dict]:
    model = text_model_id()
    va, vb = a.text_vector, b.text_vector
    if va and vb and a.text_model == b.text_model == model and type(va) is type(vb):
        score = (textmodel.cosine(va, vb) if isinstance(va, dict)
                 else vector_similarity(va, vb))
        method = model
    else:
        # Missing or mixed cached features: fall back to the lexical release
        # computed from the raw text (deterministic, still pairwise).
        va = textmodel.represent(a.title, a.description)
        vb = textmodel.represent(b.title, b.description)
        method = textmodel.active_release()
        if not va or not vb:
            return None, {'status': 'missing', 'reason': 'no_text', 'method': method}
        score = textmodel.cosine(va, vb)
    info = {'method': method}
    if isinstance(va, dict) and isinstance(vb, dict):
        info['shared_terms'] = textmodel.shared_terms(va, vb)
    return score, info


# ---------------------------------------------------------------------------
# Image (pretrained extractor via embeddings.py; histogram baseline here)
# ---------------------------------------------------------------------------
_HIST_BINS = 8          # per RGB channel -> 24 dims
_THUMB = 8              # 8x8 luma thumbnail -> 64 dims
IMAGE_MODEL_LABELS = {'resnet': 'ResNet-50', 'clip': 'CLIP ViT-B/32',
                      'histogram': 'colour histogram (lightweight baseline, not a neural model)'}


def histogram_vector(img) -> list[float]:
    """Lightweight baseline: colour histogram (24 dims) + luma thumbnail (64)."""
    small = img.resize((64, 64))
    hist = [0.0] * (_HIST_BINS * 3)
    for r, g, b in small.getdata():
        hist[r * _HIST_BINS // 256] += 1
        hist[_HIST_BINS + (g * _HIST_BINS // 256)] += 1
        hist[2 * _HIST_BINS + (b * _HIST_BINS // 256)] += 1
    thumb = img.convert('L').resize((_THUMB, _THUMB))
    luma = [v / 255.0 for v in thumb.getdata()]
    vec = hist + luma
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def extract_image_vector(image_file, kind: str | None = None) -> list[float] | None:
    """Return a normalised visual descriptor for an uploaded photograph.

    Returns ``None`` for a missing or unreadable image. Model-loading or
    inference errors from the pretrained extractors propagate to the caller
    (``matching.features.prepare``), which records the failure.
    """
    if not _HAS_PIL or image_file is None:
        return None
    try:
        img = Image.open(image_file).convert('RGB')
    except Exception:
        return None
    from .embeddings import configuration, image_vector
    kind = kind or configuration()['image']
    if kind in ('resnet', 'clip'):
        return image_vector(img, kind)
    return histogram_vector(img)


def vector_similarity(vec_a, vec_b) -> float:
    """Cosine similarity between two equal-length vectors, clamped to [0, 1]."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    na = math.sqrt(sum(a * a for a in vec_a)) or 1.0
    nb = math.sqrt(sum(b * b for b in vec_b)) or 1.0
    return float(max(0.0, min(1.0, round(dot / (na * nb), 9))))


# Backwards-compatible alias used by older callers and the evaluation CLI.
image_similarity = vector_similarity


def calibrated_image_similarity(vec_a, vec_b, kind: str | None = None) -> float:
    """Image similarity rescaled above the vision model's chance level.

    ``(cos - chance) / (1 - chance)`` clamped to [0, 1]; ``chance`` is the
    median cosine between photos of *different* items for that model (see
    ``image_chance`` in ``matching/profile.json``). Unrelated photos score
    about zero, identical photos one, and candidate order is unchanged.
    """
    raw = vector_similarity(vec_a, vec_b)
    if kind is None:
        from .embeddings import configuration
        kind = configuration()['image']
    chance = float(settings.MATCHING.get('IMAGE_CHANCE', {}).get(kind, 0.0))
    if chance <= 0 or chance >= 1:
        return raw
    return float(max(0.0, min(1.0, (raw - chance) / (1.0 - chance))))


def _image_component(a: 'Evidence', b: 'Evidence') -> tuple[float | None, dict]:
    a_photo, b_photo = a.has_photo or bool(a.image_vector), b.has_photo or bool(b.image_vector)
    if not a_photo or not b_photo:
        side = ('both' if not (a_photo or b_photo) else a.report_type if not a_photo else b.report_type)
        return None, {'status': 'missing', 'reason': f'no_photo_{side}'}
    if not a.image_vector or not b.image_vector:
        side = 'both' if not (a.image_vector or b.image_vector) else (
            a.report_type if not a.image_vector else b.report_type)
        # Only a photo that has never been attempted is "pending"; a failed or
        # retrying extraction is "not processed" (diagnostics are staff-only).
        states = {e.image_state for e in (a, b) if not e.image_vector}
        reason = 'photo_pending' if states == {'pending'} else 'photo_not_processed'
        return None, {'status': 'missing', 'reason': reason, 'side': side}
    from .embeddings import configuration
    kind_a = a.image_model or configuration()['image']
    kind_b = b.image_model or configuration()['image']
    if kind_a != kind_b or len(a.image_vector) != len(b.image_vector):
        return None, {'status': 'missing', 'reason': 'incompatible_models',
                      'models': sorted({kind_a, kind_b})}
    return calibrated_image_similarity(a.image_vector, b.image_vector, kind_a), {'model': kind_a}


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def location_score(loc_a, loc_b, adjacent_ids: set | None = None) -> float:
    """1.0 same zone, 0.5 adjacent zone, else 0.0 (synopsis Section 4.7)."""
    if loc_a is None or loc_b is None:
        return 0.0
    if loc_a == loc_b:
        return 1.0
    if adjacent_ids and loc_b in adjacent_ids:
        return 0.5
    return 0.0


def category_score(cat_a, cat_b, related_ids: set | None = None) -> float:
    """1.0 identical, 0.5 compatible (related) category, 0.0 incompatible."""
    if cat_a is None or cat_b is None:
        return 0.0
    if cat_a == cat_b:
        return 1.0
    if related_ids and cat_b in related_ids:
        return 0.5
    return 0.0


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value


def temporal_gap_days(lost_date, found_date) -> int | None:
    """Signed day gap ``found - lost``; negative means found before lost."""
    lost_date, found_date = _as_date(lost_date), _as_date(found_date)
    if not lost_date or not found_date:
        return None
    return (found_date - lost_date).days


def chronology(lost_date, found_date, lost_time=None, found_time=None) -> dict:
    """Graded chronology assessment (see ``matching.chronology``)."""
    return _chronology.assess(lost_date, found_date, lost_time, found_time)


def is_temporally_plausible(lost_date, found_date, lost_time=None, found_time=None) -> bool:
    """True unless the chronology is suspicious or contradictory."""
    return chronology(lost_date, found_date, lost_time, found_time)['status'] in (
        'plausible', 'approximate', 'unknown')


def date_score(lost_date, found_date, lost_time=None, found_time=None) -> float:
    """Date-component score: proximity decay times the chronology penalty."""
    result = chronology(lost_date, found_date, lost_time, found_time)
    return 0.0 if result['score'] is None else result['score']


def attribute_score(brand_a, color_a, brand_b, color_b) -> float | None:
    """Mean of the comparable brand / colour checks; ``None`` if neither is comparable."""
    return _colour.compare_attributes(brand_a, color_a, brand_b, color_b)['score']


# ---------------------------------------------------------------------------
# Combine
# ---------------------------------------------------------------------------
def effective_weights(missing=(), base: dict | None = None) -> dict:
    """Weights after dropping ``missing`` components and renormalising."""
    active = {k: v for k, v in (base or weights()).items() if k not in set(missing)}
    total = sum(active.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in active.items()}


def evidence_coverage(missing=(), base: dict | None = None) -> float:
    """Share of the base weight backed by evidence on both reports."""
    base = base or weights()
    total = sum(base.values())
    if total <= 0:
        return 0.0
    return round(sum(v for k, v in base.items() if k not in set(missing)) / total, 6)


def combine(components: dict, has_image_pair: bool = True, missing=None, base: dict | None = None) -> float:
    """Weighted sum of compared component scores -> similarity in [0, 1].

    ``missing`` lists components with no evidence; a component whose value
    is ``None`` is treated as missing too. ``has_image_pair=False`` is a
    shorthand for ``missing=['image']`` kept for older callers.
    """
    missing = set(missing or ())
    if not has_image_pair:
        missing.add('image')
    missing.update(k for k, v in components.items() if v is None)
    active = effective_weights(missing, base)
    total = sum(active[k] * float(components.get(k) or 0.0) for k in active)
    return float(max(0.0, min(1.0, round(total, 9))))


def confidence_tier(score: float) -> str:
    """Threshold tier before caps: ``> high`` high, ``[medium, high]`` medium, else low.

    Kept under its historical name; the tiers are heuristic display
    thresholds of similarity strength, not statistical confidence.
    """
    high = settings.MATCHING['TIER_HIGH']
    medium = settings.MATCHING['TIER_MEDIUM']
    score = round(float(score), 6)  # avoid float noise such as 0.8500000001
    if score > high:
        return 'high'
    if score >= medium:
        return 'medium'
    return 'low'


similarity_tier = confidence_tier


def coverage_rules() -> dict:
    rules = settings.MATCHING.get('EVIDENCE_COVERAGE') or {}
    return {'STRONG_MIN': float(rules.get('STRONG_MIN', 0.70)),
            'MODERATE_MIN': float(rules.get('MODERATE_MIN', 0.50))}


def _cap(tier: str, cap: str) -> str:
    return cap if TIER_ORDER[cap] < TIER_ORDER[tier] else tier


# ---------------------------------------------------------------------------
# Evidence records and the single scoring function
# ---------------------------------------------------------------------------
@dataclass
class Evidence:
    """Everything the engine may use about one report.

    Built from an ``Item`` by ``matching.services.evidence_for`` and from a
    dataset record by ``matching.evaluation.record_evidence``.
    """
    report_type: str                      # 'lost' or 'found'
    title: str = ''
    description: str = ''
    category: object = None               # id or slug
    location: object = None
    event_date: date | None = None
    event_time: object = None
    brand: str = ''
    color: str = ''
    text_vector: object = None            # cached text features (dict lexical / list SBERT)
    text_model: str = ''
    image_vector: list | None = None
    image_model: str = ''                 # 'resnet' | 'clip' | 'histogram'
    has_photo: bool = False               # a photograph was supplied
    image_state: str = ''                 # feature-processing state for the photo
    key: object = None                    # identity (pk / dataset id), informational
    extras: dict = field(default_factory=dict)


def _status_for(component: str, score: float) -> str:
    if component in ('location', 'category'):
        return 'agree' if score >= 0.999 else 'partial' if score > 0 else 'conflict'
    if score >= 0.75:
        return 'agree'
    if score >= 0.35:
        return 'partial'
    return 'low'


def score_evidence(lost: Evidence, found: Evidence, *, related_pairs=frozenset(),
                   adjacent_pairs=frozenset(), base_weights: dict | None = None,
                   disabled=()) -> dict:
    """Score one lost/found pair. The single implementation used everywhere.

    ``related_pairs`` / ``adjacent_pairs`` are sets of ``frozenset({a, b})``
    for compatible categories and neighbouring zones. ``disabled`` lists
    components to treat as missing on purpose (evaluation ablations, e.g.
    ranking without images). Returns a dict with ``score``, ``tier``,
    ``components``, ``missing``, ``coverage`` and an explanation ``details``.
    """
    base = dict(base_weights or weights())
    components: dict[str, float | None] = {}
    info: dict[str, dict] = {}

    text, info['text'] = _text_component(lost, found)
    components['text'] = text

    image, info['image'] = _image_component(lost, found)
    components['image'] = image

    if lost.location in (None, '') or found.location in (None, ''):
        components['location'], info['location'] = None, {'status': 'missing', 'reason': 'no_location'}
    else:
        adjacent = {x for pair in adjacent_pairs if lost.location in pair for x in pair} - {lost.location}
        components['location'] = location_score(lost.location, found.location, adjacent)
        info['location'] = {'adjacent': found.location in adjacent}

    if lost.category in (None, '') or found.category in (None, ''):
        components['category'], info['category'] = None, {'status': 'missing', 'reason': 'no_category'}
    else:
        related = {x for pair in related_pairs if lost.category in pair for x in pair} - {lost.category}
        components['category'] = category_score(lost.category, found.category, related)
        info['category'] = {'related': found.category in related}

    chrono = chronology(lost.event_date, found.event_date, lost.event_time, found.event_time)
    components['date'] = chrono['score']
    info['date'] = {'chronology': chrono['status'], 'day_gap': chrono['day_gap'],
                    'hour_gap': chrono['hour_gap'], 'time_compared': chrono['time_compared']}
    if chrono['score'] is None:
        info['date'].update(status='missing', reason='no_date')

    attrs = _colour.compare_attributes(lost.brand, lost.color, found.brand, found.color)
    components['attribute'] = attrs['score']
    info['attribute'] = {'brand': attrs['brand'], 'colour': attrs['colour']}
    if attrs['score'] is None:
        info['attribute'].update(status='missing', reason='no_attributes')

    for name in disabled:
        components[name] = None
        info[name] = {'status': 'missing', 'reason': 'disabled'}

    missing = [c for c in COMPONENTS if components.get(c) is None]
    for c in COMPONENTS:
        if components.get(c) is None:
            info[c].setdefault('status', 'missing')
            continue
        if c == 'attribute':
            info[c]['status'] = {'full': 'agree', 'partial': 'partial', 'conflict': 'conflict'}[attrs['status']]
        elif c == 'date':
            info[c]['status'] = ('conflict' if chrono['status'] in ('suspicious', 'contradictory')
                                 else _status_for(c, components[c]))
        else:
            info[c]['status'] = _status_for(c, components[c])

    score = combine(components, missing=missing, base=base)
    coverage = evidence_coverage(missing, base)
    tier_before = confidence_tier(score)
    tier = tier_before
    caps, warnings = [], []
    rules = coverage_rules()
    if coverage < rules['MODERATE_MIN']:
        caps.append({'reason': 'coverage', 'tier': 'low'})
    elif coverage < rules['STRONG_MIN']:
        caps.append({'reason': 'coverage', 'tier': 'medium'})
    if chrono.get('tier_cap'):
        caps.append({'reason': 'chronology', 'tier': chrono['tier_cap']})
    for cap in caps:
        tier = _cap(tier, cap['tier'])
    applied = [c for c in caps if TIER_ORDER[c['tier']] < TIER_ORDER[tier_before]]
    if chrono['status'] in ('approximate', 'suspicious', 'contradictory'):
        warnings.append(chrono['warning'])
    if info['image'].get('reason') == 'incompatible_models':
        warnings.append('Photos were processed by different image models and were not compared.')

    sanitized = {k: (None if v is None else round(float(v), 9)) for k, v in components.items()}
    return {
        'score': score,
        'tier': tier,
        'tier_before_caps': tier_before,
        'components': sanitized,
        'missing': missing,
        'coverage': coverage,
        'chronology': chrono['status'],
        'plausible': chrono['status'] in ('plausible', 'approximate', 'unknown'),
        'details': {
            'coverage': coverage,
            'components': info,
            'chronology': {'status': chrono['status'], 'warning': chrono['warning'],
                           'day_gap': chrono['day_gap'], 'hour_gap': chrono['hour_gap'],
                           'time_compared': chrono['time_compared']},
            'tier_before_caps': tier_before,
            'caps': applied,
            'warnings': warnings,
        },
    }
