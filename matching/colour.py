"""Colour and brand comparison for the attribute component.

Colours are parsed into *families* (red, blue, grey, ...) with an optional
shade (dark / light). Two colour descriptions are compared as small sets so
that multicolour items are handled explicitly:

* identical families with compatible shades -> 1.0 per colour
  ("navy blue" vs "blue", "dark red" vs "maroon", "grey" vs "gray")
* same family, opposite shades ("light blue" vs "navy") -> 0.75
* neighbouring families that are easily confused under lighting or by
  different people ("grey" vs "silver", "beige" vs "brown") -> 0.5
* different families -> 0.0

The per-colour matches are combined with a soft F1 (harmonic mean of how much
of A is found in B and how much of B is found in A). "Red and blue" vs "red"
is therefore 0.67 (*partial*), never full agreement; "red" vs "blue" is 0.0
(*conflict*); an empty field on either side is *missing* (``None``), which the
engine drops instead of scoring as a mismatch.

All values are deterministic and documented heuristics, not learned.
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r'[a-z]+')

FAMILY_SYNONYMS = {
    'black': ('black', None), 'blackish': ('black', None), 'jet': ('black', None),
    'white': ('white', None), 'whitish': ('white', None), 'offwhite': ('white', 'light'),
    'ivory': ('white', 'light'), 'snow': ('white', None),
    'grey': ('grey', None), 'gray': ('grey', None), 'greyish': ('grey', None),
    'grayish': ('grey', None), 'charcoal': ('grey', 'dark'), 'ash': ('grey', 'light'),
    'silver': ('silver', None), 'silvery': ('silver', None), 'chrome': ('silver', None),
    'steel': ('silver', None),
    'gold': ('gold', None), 'golden': ('gold', None), 'bronze': ('gold', 'dark'),
    'red': ('red', None), 'reddish': ('red', None), 'crimson': ('red', None),
    'scarlet': ('red', None), 'maroon': ('red', 'dark'), 'burgundy': ('red', 'dark'),
    'wine': ('red', 'dark'), 'cherry': ('red', None),
    'pink': ('pink', None), 'rose': ('pink', None), 'magenta': ('pink', None),
    'fuchsia': ('pink', None), 'peach': ('pink', 'light'),
    'orange': ('orange', None), 'copper': ('orange', 'dark'), 'rust': ('orange', 'dark'),
    'yellow': ('yellow', None), 'mustard': ('yellow', 'dark'), 'lemon': ('yellow', 'light'),
    'green': ('green', None), 'greenish': ('green', None), 'olive': ('green', 'dark'),
    'lime': ('green', 'light'), 'mint': ('green', 'light'), 'emerald': ('green', None),
    'blue': ('blue', None), 'bluish': ('blue', None), 'blueish': ('blue', None),
    'navy': ('blue', 'dark'), 'cobalt': ('blue', None), 'azure': ('blue', 'light'),
    'sky': ('blue', 'light'), 'royal': ('blue', None),
    'teal': ('teal', None), 'turquoise': ('teal', None), 'aqua': ('teal', 'light'),
    'cyan': ('teal', 'light'),
    'purple': ('purple', None), 'violet': ('purple', None), 'lilac': ('purple', 'light'),
    'lavender': ('purple', 'light'), 'plum': ('purple', 'dark'),
    'brown': ('brown', None), 'tan': ('brown', 'light'), 'chocolate': ('brown', 'dark'),
    'coffee': ('brown', 'dark'),
    'beige': ('beige', None), 'cream': ('beige', 'light'), 'khaki': ('beige', None),
    'clear': ('clear', None), 'transparent': ('clear', None), 'translucent': ('clear', None),
}
SHADES = {'dark': 'dark', 'deep': 'dark', 'light': 'light', 'pale': 'light', 'pastel': 'light',
          'bright': None, 'matte': None, 'matt': None, 'glossy': None, 'metallic': None,
          'shiny': None, 'faded': 'light'}
MULTI_WORDS = {'multicolour', 'multicolor', 'multicoloured', 'multicolored', 'multi', 'mixed',
               'rainbow', 'colourful', 'colorful', 'printed', 'patterned', 'assorted'}
# Unordered neighbour families (half credit).
NEIGHBOURS = {frozenset(p) for p in (
    ('grey', 'silver'), ('grey', 'black'), ('grey', 'white'), ('silver', 'white'),
    ('beige', 'brown'), ('beige', 'white'), ('gold', 'yellow'), ('gold', 'brown'),
    ('purple', 'pink'), ('red', 'pink'), ('red', 'orange'), ('orange', 'yellow'),
    ('teal', 'blue'), ('teal', 'green'), ('blue', 'purple'), ('clear', 'white'),
)}

SAME_SHADE_CONFLICT = 0.75
NEIGHBOUR_CREDIT = 0.5


def parse_colours(value: str) -> dict:
    """``{'colours': [(family, shade), ...], 'multi': bool}`` for a colour field.

    Shade words apply to the next colour word ("dark red", "light-blue");
    separators such as "and", "/", "&", "with" just split colours.
    """
    words = _WORD_RE.findall((value or '').lower().replace('multi-', 'multi'))
    colours: list[tuple[str, str | None]] = []
    multi = False
    pending_shade = None
    for word in words:
        if word in MULTI_WORDS:
            multi = True
            continue
        if word in SHADES:
            pending_shade = SHADES[word] or pending_shade
            continue
        if word in FAMILY_SYNONYMS:
            family, shade = FAMILY_SYNONYMS[word]
            shade = pending_shade or shade
            pending_shade = None
            entry = (family, shade)
            # merge a repeated family ("navy blue" -> one dark blue)
            existing = next((c for c in colours if c[0] == family), None)
            if existing is None:
                colours.append(entry)
            elif existing[1] is None and shade:
                colours[colours.index(existing)] = entry
            continue
        pending_shade = None if word not in ('and', 'with', 'or') else pending_shade
    if len({c[0] for c in colours}) >= 3:
        multi = True
    return {'colours': colours, 'multi': multi}


def _pair_credit(a: tuple, b: tuple) -> float:
    if a[0] == b[0]:
        if a[1] and b[1] and a[1] != b[1]:
            return SAME_SHADE_CONFLICT
        return 1.0
    if frozenset((a[0], b[0])) in NEIGHBOURS:
        return NEIGHBOUR_CREDIT
    return 0.0


def compare_colours(value_a: str, value_b: str) -> dict:
    """``{'score': float | None, 'status': 'full'|'partial'|'conflict'|None, ...}``."""
    a, b = parse_colours(value_a), parse_colours(value_b)
    if not (a['colours'] or a['multi']) or not (b['colours'] or b['multi']):
        return {'score': None, 'status': None, 'a': a['colours'], 'b': b['colours']}
    if not a['colours'] or not b['colours']:
        # At least one side only says "multicolour" without naming colours.
        if a['multi'] and b['multi']:
            score = 1.0 if not (a['colours'] or b['colours']) else 0.75
        else:
            named = a['colours'] or b['colours']
            score = 0.5 if len(named) <= 1 else 0.75
    else:
        recall_a = sum(max(_pair_credit(x, y) for y in b['colours']) for x in a['colours'])
        recall_b = sum(max(_pair_credit(y, x) for x in a['colours']) for y in b['colours'])
        p, r = recall_a / len(a['colours']), recall_b / len(b['colours'])
        score = 0.0 if p + r == 0 else 2 * p * r / (p + r)
    score = round(score, 6)
    status = 'full' if score >= 0.999999 else 'partial' if score > 0 else 'conflict'
    return {'score': score, 'status': status, 'a': a['colours'], 'b': b['colours']}


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------
BRAND_ALIASES = {
    'hewlettpackard': 'hp', 'mi': 'xiaomi', 'redmi': 'xiaomi', 'poco': 'xiaomi',
    'iphone': 'apple', 'macbook': 'apple', 'ipad': 'apple', 'airpods': 'apple',
    'galaxy': 'samsung', 'thinkpad': 'lenovo', 'moto': 'motorola', 'pixel': 'google',
    'hydro': 'hydroflask', 'raybanmeta': 'rayban',
}


def brand_key(value: str) -> str:
    """'Hydro Flask', 'hydro-flask' and 'HydroFlask' compare equal."""
    key = re.sub(r'[^a-z0-9]', '', (value or '').lower())
    return BRAND_ALIASES.get(key, key)


def compare_brands(value_a: str, value_b: str) -> dict:
    a, b = brand_key(value_a), brand_key(value_b)
    if not a or not b:
        return {'score': None, 'status': None}
    return {'score': 1.0 if a == b else 0.0, 'status': 'full' if a == b else 'conflict'}


def compare_attributes(brand_a, color_a, brand_b, color_b) -> dict:
    """Brand + colour agreement as one component.

    ``score`` is the mean of the comparable sub-checks, or ``None`` when
    neither brand nor colour is given on both reports (missing evidence).
    """
    brand = compare_brands(brand_a, brand_b)
    colour = compare_colours(color_a, color_b)
    parts = [x['score'] for x in (brand, colour) if x['score'] is not None]
    if not parts:
        return {'score': None, 'status': None, 'brand': None, 'colour': None}
    score = sum(parts) / len(parts)
    statuses = {x['status'] for x in (brand, colour) if x['status']}
    status = 'full' if statuses == {'full'} else 'conflict' if statuses == {'conflict'} else 'partial'
    return {'score': score, 'status': status, 'brand': brand['status'], 'colour': colour['status']}
