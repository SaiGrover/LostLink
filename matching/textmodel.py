"""Stable, pairwise text representation for LostLink reports.

Why this exists
---------------
The previous text component fitted a TF-IDF vectoriser on the active report
*plus its candidate pool* on every run. A pair's text score therefore changed
whenever an unrelated report was added, and differed depending on which side
started matching. This module replaces it with a representation that depends
only on the report itself and on a versioned, read-only model release:

    vector(report) = L2-normalise( { term: class_weight(term) * (1 + ln tf) } )
    text_similarity(a, b) = cosine(vector(a), vector(b))

* ``tf`` counts canonical terms over the title and the description together
  (one consistent representation for both fields).
* Canonical terms come from the release's normaliser: spelling variants,
  abbreviations, multi-word phrases, synonyms / paraphrases mapped to shared
  concepts, conservative plural folding, and identifier preservation.
* ``class_weight`` is fixed by the release (see ``matching/lexicon.py``).

Guarantees (tested in ``matching/test_stability.py``): the same pair always
gets the same score under the same release; the score is symmetric; adding,
removing or reordering other reports cannot change it; the release id is part
of every feature fingerprint and match manifest, so a new release invalidates
cached vectors and identifies rebuilt matches.

Release files live in ``matching/text_model/<release>.json`` and are produced
reproducibly by ``python manage.py build_text_model --release <release>``.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent / 'text_model'
DEFAULT_RELEASE = 'lexical-v1'

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_/][a-z0-9]+)*")
_HAS_DIGIT = re.compile(r'\d')
_HAS_ALPHA = re.compile(r'[a-z]')

_IRREGULAR_LEMMAS = {
    'children': 'child', 'feet': 'foot', 'men': 'man', 'mice': 'mouse',
    'people': 'person', 'teeth': 'tooth', 'women': 'woman', 'knives': 'knife',
    'leaves': 'leaf', 'scarves': 'scarf', 'wives': 'wife',
}
# Words whose final "s" is not a plural.
_KEEP_S = {'glasses', 'sunglasses', 'earphones', 'headphones', 'airpods', 'earpods', 'earbuds',
           'keys', 'shoes', 'spectacles', 'goggles', 'gloves', 'trainers', 'sneakers',
           'documents', 'papers', 'notes', 'studs', 'earrings', 'scissors', 'jeans', 'shorts',
           'trousers', 'pants', 'slippers', 'sandals', 'chappals', 'mittens', 'labs', 'stripes',
           'decals', 'stickers', 'scratches', 'dents', 'straps', 'zips', 'pockets', 'sports',
           'class', 'glass', 'canvas', 'bus', 'campus', 'lens', 'status', 'apparatus', 'bonus',
           'news', 'series', 'species', 'adidas', 'asus', 'skybags', 'is', 'this', 'has', 'was',
           'us', 'its', 'boss', 'chess', 'dress', 'press', 'mattress', 'compass', 'brass',
           'hydroflask', 'wireless', 'stainless', 'colourless', 'strapless', 'cordless'}


def lemmatize_token(token: str) -> str:
    """Conservative English noun singularisation without a corpus download."""
    if token in _KEEP_S:
        return token
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
# Release building (offline, deterministic) and loading (run time)
# ---------------------------------------------------------------------------
def _lexicon_digest(lexicon) -> str:
    payload = {name: getattr(lexicon, name) for name in (
        'CLASS_WEIGHTS', 'DEFAULT_CLASS', 'STOPWORDS', 'SPELLING', 'ABBREVIATIONS', 'SHADE_WORDS',
        'CONCEPTS', 'EXPANSIONS', 'EXTRA_CLASSES', 'PHRASES', 'PHRASE_CLASSES')}
    payload = json.loads(json.dumps(payload, default=sorted))
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_release(release: str) -> dict:
    """Compile ``matching/lexicon.py`` into a self-contained release payload.

    Deterministic: the same lexicon always yields byte-identical JSON (see
    ``release_json``), which the test-suite checks against the committed file.
    """
    from . import lexicon

    tokens: dict[str, list[str]] = {}
    classes: dict[str, str] = {}
    for canonical, (term_class, surfaces) in lexicon.CONCEPTS.items():
        classes[canonical] = term_class
        for surface in [canonical, *surfaces]:
            if ' ' not in surface:
                tokens.setdefault(surface, [canonical])
    for word, targets in lexicon.EXPANSIONS.items():
        tokens[word] = list(targets)
    for word, term_class in lexicon.EXTRA_CLASSES.items():
        classes.setdefault(word, term_class)
    phrases = {phrase: list(targets) for phrase, targets in lexicon.PHRASES.items()}
    for term, term_class in lexicon.PHRASE_CLASSES.items():
        classes.setdefault(term, term_class)
    # Every emitted canonical token must have a class.
    for targets in list(tokens.values()) + list(phrases.values()):
        for target in targets:
            classes.setdefault(target, lexicon.DEFAULT_CLASS)
    return {
        'release': release,
        'kind': 'fixed-vocabulary weighted lexical model',
        'generated_by': f'python manage.py build_text_model --release {release}',
        'source': 'matching/lexicon.py',
        'lexicon_sha256': _lexicon_digest(lexicon),
        'notes': lexicon.RELEASE_NOTES,
        'class_weights': dict(sorted(lexicon.CLASS_WEIGHTS.items())),
        'default_class': lexicon.DEFAULT_CLASS,
        'identifier_class': 'identifier',
        'number_class': 'descriptor',
        'stopwords': sorted(lexicon.STOPWORDS),
        'shade_words': sorted(lexicon.SHADE_WORDS),
        'spelling': dict(sorted(lexicon.SPELLING.items())),
        'abbreviations': dict(sorted(lexicon.ABBREVIATIONS.items())),
        'phrases': dict(sorted(phrases.items())),
        'tokens': dict(sorted(tokens.items())),
        'classes': dict(sorted(classes.items())),
        'vocabulary_size': len(classes),
    }


def release_json(payload: dict) -> str:
    return json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + '\n'


def release_path(release: str) -> Path:
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', release or ''):
        raise ValueError(f'Invalid text model release name: {release!r}')
    return RELEASE_DIR / f'{release}.json'


def load_release(release: str | None = None) -> dict:
    """Load a compiled release by name (default: the active one); cached per name."""
    return _load_release(release or active_release())


@lru_cache(maxsize=4)
def _load_release(release: str) -> dict:
    path = release_path(release)
    if not path.exists():
        raise FileNotFoundError(
            f'Text model release {release!r} not found at {path}. '
            f'Run: python manage.py build_text_model --release {release}')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('release') != release:
        raise ValueError(f'{path} declares release {data.get("release")!r}, expected {release!r}')
    # Hash the newline-normalised text so CRLF checkouts report the same id.
    data['_sha256'] = hashlib.sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()
    data['_stopwords'] = frozenset(data['stopwords'])
    data['_shades'] = frozenset(data['shade_words'])
    data['_phrase_max'] = max((len(p.split()) for p in data['phrases']), default=1)
    return data


def active_release() -> str:
    from django.conf import settings
    return settings.MATCHING.get('TEXT_MODEL_RELEASE', DEFAULT_RELEASE)


def release_info(release: str | None = None) -> dict:
    data = load_release(release)
    return {'release': data['release'], 'sha256': data['_sha256'][:16],
            'lexicon_sha256': data['lexicon_sha256'][:16]}


# ---------------------------------------------------------------------------
# Normalisation and representation
# ---------------------------------------------------------------------------
def _fold(text: str) -> str:
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().replace("'", '').replace('’', '')


def _raw_tokens(text: str) -> list[str]:
    """Split into tokens; hyphenated identifiers like ``MX-500`` become ``mx500``."""
    out = []
    for token in _TOKEN_RE.findall(_fold(text)):
        if _HAS_DIGIT.search(token):
            out.append(re.sub(r'[-_/]', '', token))
        else:
            out.extend(part for part in re.split(r'[-_/]', token) if part)
    return out


def _token_class(token: str, release: dict) -> str:
    if token in release['classes']:
        return release['classes'][token]
    if _HAS_DIGIT.search(token):
        if _HAS_ALPHA.search(token) or len(token) >= 3:
            return release['identifier_class']
        return release['number_class']
    return release['default_class']


def normalise(text: str, release: str | None = None) -> list[str]:
    """Canonical term sequence for ``text`` under ``release``.

    Order: fold accents/case -> tokenise -> abbreviations and spelling ->
    drop shade words that qualify a colour -> longest multi-word phrase ->
    single-word concept (surface form first, then its singular) -> drop
    stop-words. Unknown words keep their (singularised) surface form;
    identifiers keep their exact characters.
    """
    rel = load_release(release)
    spelling, abbreviations = rel['spelling'], rel['abbreviations']
    words = []
    for token in _raw_tokens(text):
        token = abbreviations.get(token, token)
        token = spelling.get(token, token)
        words.extend(token.split())
    out: list[str] = []
    i, n = 0, len(words)
    phrases, tokens, stop = rel['phrases'], rel['tokens'], rel['_stopwords']
    while i < n:
        matched = False
        for size in range(min(rel['_phrase_max'], n - i), 1, -1):
            key = ' '.join(words[i:i + size])
            if key not in phrases:
                # also try the phrase with its last word singularised
                key = ' '.join(words[i:i + size - 1] + [lemmatize_token(words[i + size - 1])])
            if key in phrases:
                out.extend(phrases[key])
                i += size
                matched = True
                break
        if matched:
            continue
        word = words[i]
        i += 1
        if word in rel['_shades'] and i < n and _is_colour(words[i], rel):
            continue  # "dark blue" -> blue: shade qualifies the next colour
        if word in tokens:
            out.extend(tokens[word])
            continue
        lemma = lemmatize_token(word)
        if lemma in tokens:
            out.extend(tokens[lemma])
            continue
        if word in stop or lemma in stop or len(lemma) < 2 and not lemma.isdigit():
            continue
        out.append(word if _HAS_DIGIT.search(word) else lemma)
    return [t for t in out if t not in stop]


def _is_colour(word: str, rel: dict) -> bool:
    targets = rel['tokens'].get(word) or rel['tokens'].get(lemmatize_token(word)) or []
    return any(rel['classes'].get(t) == 'colour' for t in targets)


def represent(title: str = '', description: str = '', release: str | None = None) -> dict:
    """Sparse, L2-normalised term-weight vector for one report.

    The title and the description are normalised separately and pooled, so a
    report whose key noun appears only in its title ("Calculator") is still
    represented by it. The result is a plain ``{term: weight}`` dict with
    sorted keys and values rounded to 6 decimals, safe to cache as JSON.
    """
    rel = load_release(release)
    counts: dict[str, int] = {}
    for term in normalise(title, release) + normalise(description, release):
        counts[term] = counts.get(term, 0) + 1
    weights = rel['class_weights']
    vector = {term: weights.get(_token_class(term, rel), 1.0) * (1.0 + math.log(tf))
              for term, tf in counts.items()}
    norm = math.sqrt(sum(v * v for v in vector.values()))
    if not norm:
        return {}
    return {term: round(vector[term] / norm, 6) for term in sorted(vector)}


def cosine(vec_a: dict, vec_b: dict) -> float:
    """Cosine of two sparse vectors (already L2-normalised), clamped to [0, 1]."""
    if not vec_a or not vec_b:
        return 0.0
    if len(vec_b) < len(vec_a):
        vec_a, vec_b = vec_b, vec_a
    dot = sum(weight * vec_b.get(term, 0.0) for term, weight in vec_a.items())
    na = math.sqrt(sum(v * v for v in vec_a.values()))
    nb = math.sqrt(sum(v * v for v in vec_b.values()))
    if not na or not nb:
        return 0.0
    # Round so the stored score is bit-stable regardless of argument order.
    return float(max(0.0, min(1.0, round(dot / (na * nb), 9))))


def shared_terms(vec_a: dict, vec_b: dict, limit: int = 6) -> list[str]:
    """Terms present in both vectors, most influential first (for explanations)."""
    common = [(vec_a[t] * vec_b[t], t) for t in vec_a if t in vec_b]
    return [t for _, t in sorted(common, key=lambda x: (-x[0], x[1]))[:limit]]
