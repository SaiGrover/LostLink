"""Research data checks. Human attestations cannot be independently verified here.

Dataset format (JSON, documented in docs/EVALUATION.md)::

    {
      "dataset_kind": "independent_reviewed" | "constructed",
      "provenance": "...", "limitations": [...],
      "taxonomy": {"related_categories": [[a, b]], "adjacent_locations": [[a, b]]},   # optional
      "pairs": [{
        "query_id": "<query.id>", "split": "validation" | "test", "label": 0 | 1,
        "conditions": ["genuine_positive", "hard_negative", ...],                        # optional tags
        "query":     {record}, "candidate": {record},
        "review": {"label_source": "manual", "status": "approved", "reviewer": "...",
                   "independent_photos": true, "independent_descriptions": true}
      }]
    }

    record = {"id", "physical_id", "description",                 # required, non-empty
              "title", "category", "location", "date", "time",   # optional strings ("" = not given)
              "brand", "color", "image", "report_type"}          # optional strings

Optional fields may be empty so that missing photos and partial metadata can
be represented honestly; the engine then treats them as missing evidence
exactly as in production.
"""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, time
from pathlib import Path

from PIL import Image
from django.core.management.base import CommandError

CONDITIONS = {
    'genuine_positive': 'a real lost/found pair of the same physical object',
    'hard_negative': 'different objects that look or read alike (same category, colour or brand)',
    'independent_photos': 'photos taken separately by different people / at different times',
    'different_wording': 'descriptions written independently with different words',
    'missing_photo': 'at least one report has no photo',
    'partial_metadata': 'brand / colour / time (or other optional fields) missing on a report',
    'inaccurate_date': 'a date or time that is wrong or approximate (e.g. found "before" lost)',
    'multicolour': 'an item with more than one colour',
    'ambiguous_category': 'reports filed under different but plausible categories',
    'lighting': 'photos under clearly different lighting or backgrounds',
}
REQUIRED_TEXT = ('id', 'physical_id', 'description')
OPTIONAL_TEXT = ('title', 'category', 'location', 'date', 'time', 'brand', 'color', 'image')


def validate_dataset(path, allow_small=False):
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise CommandError(f'Cannot read dataset: {exc}') from exc
    pairs = payload.get('pairs') if isinstance(payload, dict) else None
    if not isinstance(pairs, list) or not pairs:
        raise CommandError('Dataset must contain a non-empty pairs list.')
    errors, warnings, records, image_cache, seen_pairs = [], [], {}, {}, set()
    labels, membership, split_counts = defaultdict(set), defaultdict(set), Counter()
    conditions = Counter()
    if not allow_small and not 150 <= len(pairs) <= 200:
        errors.append('The synopsis requires 150–200 labelled pairs.')
    for index, row in enumerate(pairs, 1):
        prefix = f'Pair {index}'
        if not isinstance(row, dict):
            errors.append(f'{prefix} must be an object.')
            continue
        split, label = row.get('split'), row.get('label')
        if split not in ('validation', 'test'):
            errors.append(f'{prefix} must use validation or test split.')
            split = 'invalid'
        split_counts[split] += 1
        if type(label) is not int or label not in (0, 1):
            errors.append(f'{prefix} must have an integer binary label.')
        else:
            labels[split].add(label)
        tags = row.get('conditions', [])
        if not isinstance(tags, list) or any(t not in CONDITIONS for t in tags):
            errors.append(f'{prefix} has unknown condition tags (allowed: {", ".join(sorted(CONDITIONS))}).')
            tags = [t for t in tags if t in CONDITIONS] if isinstance(tags, list) else []
        conditions.update(tags)
        if 'genuine_positive' in tags and label != 1:
            errors.append(f'{prefix} is tagged genuine_positive but is not labelled 1.')
        if 'hard_negative' in tags and label != 0:
            errors.append(f'{prefix} is tagged hard_negative but is not labelled 0.')
        ids, physical_ids, hashes, photo_count = [], [], [], 0
        for side in ('query', 'candidate'):
            record = row.get(side)
            if not isinstance(record, dict):
                errors.append(f'{prefix} lacks a {side} record.')
                continue
            for field in REQUIRED_TEXT:
                if not isinstance(record.get(field), str) or not record[field].strip():
                    errors.append(f'{prefix} {side} needs a non-empty {field}.')
            for field in OPTIONAL_TEXT:
                if field in record and not isinstance(record[field], str):
                    errors.append(f'{prefix} {side} {field} must be a string (empty means not given).')
            identity, physical = record.get('id'), record.get('physical_id')
            if isinstance(identity, str) and identity:
                ids.append(identity)
                membership[('report', identity)].add(split)
                if identity in records and records[identity] != record:
                    errors.append(f'{prefix} changes the content of report {identity}.')
                records[identity] = record
            if isinstance(physical, str) and physical:
                physical_ids.append(physical)
                membership[('physical object', physical)].add(split)
            raw_date = record.get('date') or ''
            if isinstance(raw_date, str) and raw_date:
                try:
                    date.fromisoformat(raw_date)
                except ValueError:
                    errors.append(f'{prefix} {side} has an invalid ISO date.')
            elif 'date' not in record:
                errors.append(f'{prefix} {side} needs a date field (empty string if unknown).')
            raw_time = record.get('time') or ''
            if isinstance(raw_time, str) and raw_time:
                try:
                    time.fromisoformat(raw_time)
                except ValueError:
                    errors.append(f'{prefix} {side} has an invalid HH:MM time.')
            image_name = record.get('image')
            if isinstance(image_name, str) and image_name:
                photo_count += 1
                image_path = (path.parent / image_name).resolve()
                if image_path not in image_cache:
                    try:
                        with Image.open(image_path) as photo:
                            photo.verify()
                        image_cache[image_path] = hashlib.sha256(image_path.read_bytes()).hexdigest()
                    except (OSError, ValueError, Image.DecompressionBombError):
                        image_cache[image_path] = None
                digest = image_cache[image_path]
                if digest:
                    hashes.append(digest)
                    membership[('image', digest)].add(split)
                else:
                    errors.append(f'{prefix} {side} image is missing or unreadable.')
            elif 'image' not in record:
                errors.append(f'{prefix} {side} needs an image field (empty string if there is no photo).')
        if photo_count < 2 and 'missing_photo' not in tags:
            warnings.append(f'{prefix} has fewer than two photos but is not tagged missing_photo.')
        if len(ids) == 2:
            pair_key = tuple(sorted(ids))
            if pair_key in seen_pairs or ids[0] == ids[1]:
                errors.append(f'{prefix} duplicates a pair or compares a report with itself.')
            seen_pairs.add(pair_key)
            if row.get('query_id') != ids[0]:
                errors.append(f'{prefix} query_id must equal query.id.')
        if len(physical_ids) == 2 and type(label) is int and label in (0, 1):
            if (physical_ids[0] == physical_ids[1]) != bool(label):
                errors.append(f'{prefix} label disagrees with the physical object identities.')
        if label == 1 and len(hashes) == 2 and hashes[0] == hashes[1]:
            errors.append(f'{prefix} reuses identical photo bytes for a positive pair.')
        review = row.get('review')
        review = review if isinstance(review, dict) else {}
        if review.get('label_source') != 'manual' or review.get('status') != 'approved':
            errors.append(f'{prefix} needs an approved manual label.')
        if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
            errors.append(f'{prefix} needs a reviewer identifier.')
        if review.get('independent_descriptions') is not True:
            errors.append(f'{prefix} needs independently authored descriptions confirmed by a reviewer.')
        if label == 1 and len(hashes) == 2 and review.get('independent_photos') is not True:
            errors.append(f'{prefix} needs independently captured photos confirmed by a reviewer.')
    for split in ('validation', 'test'):
        if labels[split] != {0, 1}:
            errors.append(f'Split {split!r} must contain positive and negative labels.')
    for kind in ('report', 'physical object', 'image'):
        leaked = sum(len(splits) > 1 for (key_kind, _), splits in membership.items() if key_kind == kind)
        if leaked:
            errors.append(f'{leaked} {kind} identities leak across validation/test splits.')
    kind = payload.get('dataset_kind') if isinstance(payload, dict) else None
    if kind != 'independent_reviewed':
        warnings.append('dataset_kind is not "independent_reviewed"; results can only be an engineering check.')
    uncovered = sorted(c for c in CONDITIONS if not conditions.get(c))
    if uncovered:
        warnings.append('No pairs tagged: ' + ', '.join(uncovered) + '.')
    return {
        'dataset': str(path), 'pairs': len(pairs), 'splits': dict(split_counts),
        'dataset_kind': kind or 'unspecified',
        'checks_passed': not errors,
        'research_ready': not errors and not allow_small and kind == 'independent_reviewed',
        'sample_size_check_disabled': allow_small,
        'condition_counts': dict(sorted(conditions.items())),
        'errors': errors[:25], 'additional_errors': max(0, len(errors) - 25),
        'warnings': warnings[:25],
        'limitation': 'Structural checks and reviewer attestations do not independently establish research validity.',
    }
