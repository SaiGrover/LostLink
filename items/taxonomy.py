"""Default controlled vocabulary: report categories and campus zones.

Used by ``manage.py seed_taxonomy`` (safe for production) and by
``seed_demo``. Seeding is idempotent and conservative:

* a default is created only if no category/zone with the same slug or the
  same name (case-insensitive) exists, so renamed, re-iconed or re-described
  rows are never touched;
* a default relation (related categories, adjacent zones) is added only when
  at least one of its two ends was created in the same run, so a relation an
  administrator removed is not silently restored;
* ``only_if_empty=True`` seeds a vocabulary only when it has no rows at all,
  so a deliberately deleted default is not recreated on every deploy.

No accounts, passwords or reports are created.
"""
from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify

from .models import Category, Location

CATEGORIES = [
    ('Electronics', '📱'), ('Bags & Wallets', '👜'), ('ID & Cards', '🪪'),
    ('Keys', '🔑'), ('Books & Stationery', '📚'), ('Clothing', '🧥'),
    ('Water Bottles', '🍶'), ('Jewellery', '💍'), ('Eyewear', '👓'),
    ('Others', '📦'),
]

LOCATIONS = [
    'Central Library', 'Cafeteria', 'Academic Block A', 'Academic Block B',
    'Sports Complex', 'Hostel', 'Auditorium', 'Parking Lot', 'Main Gate',
    'Computer Labs',
]

ADJACENCY = [
    ('Central Library', 'Academic Block A'),
    ('Academic Block A', 'Academic Block B'),
    ('Academic Block B', 'Computer Labs'),
    ('Cafeteria', 'Auditorium'),
    ('Parking Lot', 'Main Gate'),
]

# Compatible categories earn partial category credit (synopsis Section 4.7).
RELATED_CATEGORIES = [
    ('Bags & Wallets', 'Others'), ('Electronics', 'Others'), ('Keys', 'ID & Cards'),
    ('Jewellery', 'Eyewear'), ('Books & Stationery', 'Others'), ('Clothing', 'Others'),
]


def _find(model, name):
    return model.objects.filter(Q(slug=slugify(name)) | Q(name__iexact=name)).order_by('pk').first()


def _ensure(model, name, defaults, created):
    existing = _find(model, name)
    if existing is not None:
        return existing
    obj = model.objects.create(name=name, slug=slugify(name), **defaults)
    created.add(name)
    return obj


@transaction.atomic
def ensure_default_taxonomy(only_if_empty=False):
    """Create missing default categories and zones. Returns a summary dict."""
    summary = {'categories_created': [], 'locations_created': [], 'relations_added': 0,
               'categories_skipped': False, 'locations_skipped': False}

    seed_categories = not (only_if_empty and Category.objects.exists())
    seed_locations = not (only_if_empty and Location.objects.exists())
    summary['categories_skipped'] = not seed_categories
    summary['locations_skipped'] = not seed_locations

    if seed_categories:
        created = set()
        cats = {name: _ensure(Category, name, {'icon': icon}, created) for name, icon in CATEGORIES}
        for a, b in RELATED_CATEGORIES:
            if (a in created or b in created) and not cats[a].related.filter(pk=cats[b].pk).exists():
                cats[a].related.add(cats[b])
                summary['relations_added'] += 1
        summary['categories_created'] = [name for name, _ in CATEGORIES if name in created]

    if seed_locations:
        created = set()
        locs = {name: _ensure(Location, name, {}, created) for name in LOCATIONS}
        for a, b in ADJACENCY:
            if (a in created or b in created) and not locs[a].adjacent.filter(pk=locs[b].pk).exists():
                locs[a].adjacent.add(locs[b])
                summary['relations_added'] += 1
        summary['locations_created'] = [name for name in LOCATIONS if name in created]
    return summary


def default_categories():
    """Default categories keyed by their default name (for demo seeding)."""
    return {name: _find(Category, name) for name, _ in CATEGORIES}


def default_locations():
    return {name: _find(Location, name) for name in LOCATIONS}
