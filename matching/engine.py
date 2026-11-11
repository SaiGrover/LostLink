"""LostLink hybrid matching engine (synopsis Section 4).

The engine combines text, image and structured-metadata signals into one
weighted confidence score::

    Final = sum(weight_c * similarity_c)  over the components with evidence

Design notes
------------
* Pretrained models are used *only as fixed feature extractors*; nothing is
  trained here (synopsis Sections 1.1 and 4.6).
* Text similarity uses TF-IDF + cosine (scikit-learn) or pinned
  Sentence-BERT embeddings, selected by ``matching/profile.json``. A
  dependency-free cosine keeps the engine running if scikit-learn is absent.
* Image similarity uses a pinned pretrained ResNet-50 or CLIP extractor. A
  Pillow histogram descriptor exists only as an explicitly selected
  lightweight baseline; it is never substituted silently.
* Missing-data policy (synopsis Section 4.9, "absent evidence is not negative
  evidence"): a component with no evidence on one or both sides (no
  photograph on either report, or no brand/colour on either report) is
  *dropped* from the weighted sum and the remaining weights are renormalised
  to sum to one. The dropped components are recorded on the stored match so
  the explanation can say "not compared" instead of showing a false zero.
* Temporal plausibility: an item cannot be found before it was lost. A found
  report dated more than ``TEMPORAL_TOLERANCE_DAYS`` before the lost report
  is treated as impossible and receives a date score of zero; the
  orchestration layer also refuses to store such pairs.
* Image calibration: pretrained image features never put unrelated photos
  at zero (ResNet-50 0.275, CLIP 0.568 median cosine for different items on
  the pilot set), so image similarity is rescaled above each model's chance
  level before weighting (``calibrated_image_similarity``).
* Confidence tiers follow the synopsis table exactly: ``> 85%`` high,
  ``70%–85%`` (inclusive) medium, ``< 70%`` low.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime

from django.conf import settings

# --- optional heavy deps, imported defensively ---------------------------
try:  # scikit-learn powers the deployed TF-IDF text similarity.
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity as _sk_cosine
    _HAS_SKLEARN = True
except Exception:  # pragma: no cover - fallback path
    _HAS_SKLEARN = False

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False


COMPONENTS = ('text', 'image', 'location', 'category', 'date', 'attribute')

_STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'with', 'for',
    'is', 'was', 'it', 'this', 'that', 'my', 'i', 'near', 'from', 'by', 'has',
    'have', 'had', 'lost', 'found', 'item', 'items', 'please', 'somewhere',
}

_WORD_RE = re.compile(r"[a-z0-9]+")

_IRREGULAR_LEMMAS = {
    'children': 'child', 'feet': 'foot', 'men': 'man', 'mice': 'mouse',
    'people': 'person', 'teeth': 'tooth', 'women': 'woman',
}


def weights() -> dict:
    """The active, versioned component weights (synopsis Section 4.9)."""
    return dict(settings.MATCHING['WEIGHTS'])


def lemmatize_token(token: str) -> str:
    """Apply conservative noun lemmatization without a runtime corpus download.

    Lost-and-found descriptions are noun-heavy. These rules normalize common
    English plural forms while deliberately leaving ambiguous verb forms alone.
    """
    if token in _IRREGULAR_LEMMAS:
        return _IRREGULAR_LEMMAS[token]
    if len(token) > 4 and token.endswith('ies'):
        return token[:-3] + 'y'
    if len(token) > 4 and token.endswith(('ches', 'shes', 'sses', 'xes', 'zes')):
        return token[:-2]
    if len(token) > 3 and token.endswith('s') and not token.endswith(('ss', 'us', 'is')):
        return token[:-1]
    return token


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """Lowercase, strip punctuation, drop stop-words and lemmatize nouns."""
    tokens = _WORD_RE.findall((text or '').lower())
    tokens = [lemmatize_token(t) for t in tokens if t not in _STOPWORDS and len(t) > 1]
    return ' '.join(tokens)


def _python_cosine(query: str, candidates: list[str]) -> list[float]:
    """Dependency-free TF cosine used when scikit-learn is unavailable."""
    def vec(s):
        v = {}
        for tok in s.split():
            v[tok] = v.get(tok, 0) + 1
        return v

    qv = vec(query)
    qnorm = math.sqrt(sum(w * w for w in qv.values())) or 1.0
    out = []
    for cand in candidates:
        cv = vec(cand)
        dot = sum(qv.get(t, 0) * c for t, c in cv.items())
        cnorm = math.sqrt(sum(w * w for w in cv.values())) or 1.0
        out.append(dot / (qnorm * cnorm))
    return out


def text_similarity(query_text: str, candidate_texts: list[str]) -> list[float]:
    """Cosine similarity of ``query_text`` against each candidate, in [0, 1].

    For TF-IDF the vector space is fitted over the query *and the whole
    candidate pool* in one call, so terms common across the pool are
    down-weighted (classical IR, synopsis Section 4.5). Callers scoring one
    report against many candidates should therefore pass all candidates at
    once rather than looping pairwise.
    """
    from .embeddings import configuration, text_vectors
    if not candidate_texts:
        return []
    if configuration()['text'] == 'sbert':
        vectors = text_vectors([query_text] + candidate_texts)
        return [vector_similarity(vectors[0], vector) for vector in vectors[1:]]
    q = clean_text(query_text)
    cands = [clean_text(c) for c in candidate_texts]
    if not q or not any(cands):
        return [0.0] * len(cands)

    if _HAS_SKLEARN:
        try:
            corpus = [q] + cands
            tfidf = TfidfVectorizer().fit_transform(corpus)
            sims = _sk_cosine(tfidf[0:1], tfidf[1:]).ravel()
            return [float(max(0.0, min(1.0, s))) for s in sims]
        except ValueError:
            # e.g. empty vocabulary after cleaning -> fall through
            pass
    return [float(max(0.0, min(1.0, s))) for s in _python_cosine(q, cands)]


# ---------------------------------------------------------------------------
# Image  (pretrained extractor via embeddings.py; histogram baseline here)
# ---------------------------------------------------------------------------
_HIST_BINS = 8          # per RGB channel -> 24 dims
_THUMB = 8              # 8x8 luma thumbnail -> 64 dims


def extract_image_vector(image_file) -> list[float] | None:
    """Return a normalised visual descriptor for an uploaded photograph.

    Returns ``None`` for a missing or unreadable image. Model-loading or
    inference errors from the pretrained extractors propagate to the caller
    (``matching.features.prepare``), which logs them and records the report
    as having no usable image evidence rather than guessing.
    """
    if not _HAS_PIL or image_file is None:
        return None
    try:
        img = Image.open(image_file).convert('RGB')
    except Exception:
        return None

    from .embeddings import configuration, image_vector
    kind = configuration()['image']
    if kind in ('resnet', 'clip'):
        return image_vector(img, kind)

    # Lightweight baseline: colour histogram (24 dims) + luma thumbnail (64).
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


def vector_similarity(vec_a, vec_b) -> float:
    """Cosine similarity between two equal-length vectors, clamped to [0, 1]."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    na = math.sqrt(sum(a * a for a in vec_a)) or 1.0
    nb = math.sqrt(sum(b * b for b in vec_b)) or 1.0
    return float(max(0.0, min(1.0, dot / (na * nb))))


# Backwards-compatible alias used by older callers and the evaluation CLI.
image_similarity = vector_similarity


def calibrated_image_similarity(vec_a, vec_b, kind: str | None = None) -> float:
    """Image similarity rescaled above the vision model's chance level.

    Pretrained image features do not put unrelated photos at zero: ResNet's
    pooled features are all non-negative and CLIP's share a large common
    component, so two photos of different objects still have a cosine of
    0.25 to 0.57 (see ``image_chance`` in ``matching/profile.json``). Used raw,
    that gives every pair unearned image points. The cosine is therefore
    mapped to ``(cos - chance) / (1 - chance)``, clamped to [0, 1]: unrelated
    photos score about zero, identical photos still score one, and the order
    of candidates is unchanged.
    """
    raw = vector_similarity(vec_a, vec_b)
    if kind is None:
        from .embeddings import configuration
        kind = configuration()['image']
    chance = float(settings.MATCHING.get('IMAGE_CHANCE', {}).get(kind, 0.0))
    if chance <= 0 or chance >= 1:
        return raw
    return float(max(0.0, min(1.0, (raw - chance) / (1.0 - chance))))


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def location_score(loc_a, loc_b, adjacent_ids: set[int] | None = None) -> float:
    """1.0 same zone, 0.5 adjacent zone, else 0.0 (synopsis Section 4.7)."""
    if loc_a is None or loc_b is None:
        return 0.0
    if loc_a == loc_b:
        return 1.0
    if adjacent_ids and loc_b in adjacent_ids:
        return 0.5
    return 0.0


def category_score(cat_a, cat_b, related_ids: set[int] | None = None) -> float:
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


def is_temporally_plausible(lost_date, found_date) -> bool:
    """False when the item was reported found well before it was lost."""
    gap = temporal_gap_days(lost_date, found_date)
    if gap is None:
        return True
    tolerance = settings.MATCHING.get('TEMPORAL_TOLERANCE_DAYS', 1)
    return gap >= -tolerance


def date_score(lost_date, found_date) -> float:
    """Decaying score on the day gap between the lost and found event dates.

    The gap is directional: a found report dated before the lost report
    (beyond the approximation tolerance) is impossible and scores 0.0.
    Otherwise the score halves every ``DATE_HALF_LIFE_DAYS``.
    """
    gap = temporal_gap_days(lost_date, found_date)
    if gap is None:
        return 0.0
    if not is_temporally_plausible(lost_date, found_date):
        return 0.0
    half_life = settings.MATCHING['DATE_HALF_LIFE_DAYS']
    return float(0.5 ** (abs(gap) / half_life))


_COLOUR_SYNONYMS = {
    'gray': 'grey', 'greyish': 'grey', 'grayish': 'grey', 'golden': 'gold', 'silvery': 'silver',
    'blueish': 'blue', 'bluish': 'blue', 'reddish': 'red', 'greenish': 'green', 'blackish': 'black',
    'whitish': 'white', 'offwhite': 'white', 'cream': 'beige', 'maroon': 'red', 'burgundy': 'red',
    'crimson': 'red', 'scarlet': 'red', 'violet': 'purple', 'lilac': 'purple', 'turquoise': 'teal',
    'aqua': 'teal', 'tan': 'brown', 'khaki': 'beige', 'transparent': 'clear',
}
_COLOUR_MODIFIERS = {'dark', 'light', 'pale', 'bright', 'deep', 'matte', 'matt', 'glossy', 'metallic', 'colour',
                     'color', 'coloured', 'colored', 'shade', 'and', 'with', 'mixed'}


def _brand_key(value: str) -> str:
    """'Hydro Flask', 'hydro-flask' and 'HydroFlask' compare equal."""
    return re.sub(r'[^a-z0-9]', '', (value or '').lower())


def _colour_words(value: str) -> set[str]:
    """Normalised colour words, ignoring modifiers: 'Dark Navy-Blue' -> {'navy', 'blue'}."""
    words = _WORD_RE.findall((value or '').lower())
    return {_COLOUR_SYNONYMS.get(w, w) for w in words if w not in _COLOUR_MODIFIERS}


def attribute_score(brand_a, color_a, brand_b, color_b) -> float | None:
    """Fraction of comparable brand/colour attributes that agree.

    Brands are compared without case, spaces or punctuation. Colours agree
    when they share a colour word after normalising spelling variants and
    dropping modifiers, so 'Navy blue' matches 'blue' and 'grey' matches
    'gray', while 'red' and 'blue' do not.

    Returns ``None`` when neither attribute is present on both sides, so the
    caller can treat the component as *missing* rather than as a mismatch.
    """
    checks, hits = 0, 0
    key_a, key_b = _brand_key(brand_a), _brand_key(brand_b)
    if key_a and key_b:
        checks += 1
        hits += 1 if key_a == key_b else 0
    words_a, words_b = _colour_words(color_a), _colour_words(color_b)
    if words_a and words_b:
        checks += 1
        hits += 1 if words_a & words_b else 0
    if checks == 0:
        return None
    return hits / checks


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


def combine(components: dict, has_image_pair: bool = True, missing=None) -> float:
    """Weighted sum of component scores -> final confidence in [0, 1].

    ``missing`` lists components with no evidence (see the module docstring).
    ``has_image_pair=False`` is a shorthand for ``missing=['image']`` kept for
    older callers. A component whose value is ``None`` is also treated as
    missing.
    """
    missing = set(missing or ())
    if not has_image_pair:
        missing.add('image')
    missing.update(k for k, v in components.items() if v is None)
    active = effective_weights(missing)
    total = sum(active[k] * float(components.get(k) or 0.0) for k in active)
    return float(max(0.0, min(1.0, total)))


def confidence_tier(score: float) -> str:
    """Map a score to the synopsis tiers: > high, [medium, high] medium, else low."""
    high = settings.MATCHING['TIER_HIGH']
    medium = settings.MATCHING['TIER_MEDIUM']
    # Round to avoid float noise such as 0.8500000001 being 'above' 0.85.
    score = round(float(score), 6)
    if score > high:
        return 'high'
    if score >= medium:
        return 'medium'
    return 'low'
