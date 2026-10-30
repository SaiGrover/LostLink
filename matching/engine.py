"""LostLink hybrid matching engine (synopsis Section 4).

The engine combines four kinds of signal into one weighted confidence score:

    Final = sum(configured_weight * component_similarity)

Design notes
------------
* Pretrained models are used *only as fixed feature extractors* — nothing is
  trained here (Section 1.1, Section 4.6).
* Text similarity uses TF-IDF + cosine (scikit-learn) as the deployed default,
  which the synopsis lists as the lightweight candidate; a pure-Python cosine is
  provided as a dependency-free fallback so the engine always runs.
* Image similarity uses a pinned pretrained ResNet or CLIP feature extractor
  in the ML profile. A Pillow descriptor supports a lightweight demo mode.
* When neither report in a pair carries a photograph, the image weight is
  removed and the remaining weights are renormalised, so a strong text+metadata
  match is not artificially capped below the high-confidence tier.
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


WEIGHTS = settings.MATCHING['WEIGHTS']

_STOPWORDS = {
    'a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'with', 'for',
    'is', 'was', 'it', 'this', 'that', 'my', 'i', 'near', 'from', 'by', 'has',
    'have', 'had', 'lost', 'found', 'item', 'items', 'please', 'somewhere',
}

_WORD_RE = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """Lowercase, strip punctuation, drop stop-words (Section 4.2)."""
    tokens = _WORD_RE.findall((text or '').lower())
    tokens = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
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
    """Cosine similarity of ``query_text`` against each candidate.

    Uses a TF-IDF vector space fitted over the query + candidate pool so that
    terms common across the pool are down-weighted (classical IR, Section 4.5).
    """
    from .embeddings import configuration, text_vectors
    if configuration()['text'] == 'sbert' and candidate_texts:
        vectors = text_vectors([query_text] + candidate_texts)
        return [image_similarity(vectors[0], vector) for vector in vectors[1:]]
    q = clean_text(query_text)
    cands = [clean_text(c) for c in candidate_texts]
    if not q or not cands:
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
# Image  (compact content-based descriptor; swap-in seam for ResNet/CLIP)
# ---------------------------------------------------------------------------
_HIST_BINS = 8          # per RGB channel -> 24 dims
_THUMB = 8              # 8x8 luma thumbnail -> 64 dims


def extract_image_vector(image_file) -> list[float] | None:
    """Return a normalised visual descriptor for an uploaded photograph.

    This is the *only* place image features are produced. Replace the body with
    a call to a pretrained embedding model (Section 4.6) and the rest of the
    engine keeps working unchanged.
    """
    if not _HAS_PIL or image_file is None:
        return None
    try:
        img = Image.open(image_file).convert('RGB')
    except Exception:
        return None

    # 1) Colour histogram (24 dims).
    from .embeddings import configuration, image_vector
    kind = configuration()['image']
    if kind in ('resnet', 'clip'):
        return image_vector(img, kind)
    small = img.resize((64, 64))
    hist = [0.0] * (_HIST_BINS * 3)
    px = list(small.getdata())
    for r, g, b in px:
        hist[r * _HIST_BINS // 256] += 1
        hist[_HIST_BINS + (g * _HIST_BINS // 256)] += 1
        hist[2 * _HIST_BINS + (b * _HIST_BINS // 256)] += 1

    # 2) Luma thumbnail (64 dims) — captures coarse shape/layout.
    thumb = img.convert('L').resize((_THUMB, _THUMB))
    luma = [v / 255.0 for v in thumb.getdata()]

    vec = hist + luma
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def image_similarity(vec_a, vec_b) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    na = math.sqrt(sum(a * a for a in vec_a)) or 1.0
    nb = math.sqrt(sum(b * b for b in vec_b)) or 1.0
    return float(max(0.0, min(1.0, dot / (na * nb))))


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
def location_score(loc_a, loc_b, adjacent_ids: set[int] | None = None) -> float:
    """1.0 exact zone, 0.5 adjacent zone, else 0.0 (Section 4.7)."""
    if loc_a is None or loc_b is None:
        return 0.0
    if loc_a == loc_b:
        return 1.0
    if adjacent_ids and loc_b in adjacent_ids:
        return 0.5
    return 0.0


def category_score(cat_a, cat_b) -> float:
    if cat_a is None or cat_b is None:
        return 0.0
    return 1.0 if cat_a == cat_b else 0.0


def date_score(date_a: date, date_b: date) -> float:
    """Decaying score on the day-gap between reports (Section 4.7)."""
    if not date_a or not date_b:
        return 0.0
    if isinstance(date_a, datetime): date_a = date_a.date()
    if isinstance(date_b, datetime): date_b = date_b.date()
    gap = abs((date_a - date_b).days)
    half_life = settings.MATCHING['DATE_HALF_LIFE_DAYS']
    return float(0.5 ** (gap / half_life))


def attribute_score(brand_a, color_a, brand_b, color_b) -> float:
    """Fraction of provided brand/colour attributes that match."""
    checks, hits = 0, 0
    if brand_a and brand_b:
        checks += 1
        hits += 1 if brand_a.strip().lower() == brand_b.strip().lower() else 0
    if color_a and color_b:
        checks += 1
        hits += 1 if color_a.strip().lower() == color_b.strip().lower() else 0
    if checks == 0:
        return 0.0
    return hits / checks


# ---------------------------------------------------------------------------
# Combine
# ---------------------------------------------------------------------------
def combine(components: dict, has_image_pair: bool) -> float:
    """Weighted sum of component scores -> final confidence in [0, 1]."""
    weights = dict(settings.MATCHING['WEIGHTS'])
    if not has_image_pair:
        # Drop image weight and renormalise the rest (see module docstring).
        img_w = weights.pop('image')
        remaining = sum(weights.values())
        if remaining > 0:
            factor = 1.0 / remaining
            weights = {k: v * factor for k, v in weights.items()}
        components = {k: v for k, v in components.items() if k != 'image'}

    total = sum(weights.get(k, 0.0) * components.get(k, 0.0) for k in weights)
    return float(max(0.0, min(1.0, total)))


def confidence_tier(score: float) -> str:
    if score >= settings.MATCHING['TIER_HIGH']:
        return 'high'
    if score >= settings.MATCHING['TIER_MEDIUM']:
        return 'medium'
    return 'low'
