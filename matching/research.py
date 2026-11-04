"""Research data checks. Human attestations cannot be independently verified here."""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from PIL import Image
from django.core.management.base import CommandError


def validate_dataset(path, allow_small=False):
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise CommandError(f'Cannot read dataset: {exc}') from exc
    pairs = payload.get('pairs') if isinstance(payload, dict) else None
    if not isinstance(pairs, list) or not pairs:
        raise CommandError('Dataset must contain a non-empty pairs list.')
    errors, records, image_cache, seen_pairs = [], {}, {}, set()
    labels, membership, split_counts = defaultdict(set), defaultdict(set), Counter()
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
        ids, physical_ids, hashes = [], [], []
        for side in ('query', 'candidate'):
            record = row.get(side)
            if not isinstance(record, dict):
                errors.append(f'{prefix} lacks a {side} record.')
                continue
            for field in ('id', 'physical_id', 'description', 'category', 'location', 'date', 'image'):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    errors.append(f'{prefix} {side} needs a non-empty {field}.')
            for field in ('brand', 'color'):
                if not isinstance(record.get(field), str):
                    errors.append(f'{prefix} {side} needs a string {field} (empty is allowed).')
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
            try:
                date.fromisoformat(record.get('date', ''))
            except (ValueError, TypeError):
                errors.append(f'{prefix} {side} has an invalid ISO date.')
            image_name = record.get('image')
            if isinstance(image_name, str) and image_name:
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
        if label == 1 and review.get('independent_photos') is not True:
            errors.append(f'{prefix} needs independently captured photos confirmed by a reviewer.')
    for split in ('validation', 'test'):
        if labels[split] != {0, 1}:
            errors.append(f'Split {split!r} must contain positive and negative labels.')
    for kind in ('report', 'physical object', 'image'):
        leaked = sum(len(splits) > 1 for (key_kind, _), splits in membership.items() if key_kind == kind)
        if leaked:
            errors.append(f'{leaked} {kind} identities leak across validation/test splits.')
    return {
        'dataset': str(path), 'pairs': len(pairs), 'splits': dict(split_counts),
        'checks_passed': not errors, 'research_ready': not errors and not allow_small,
        'sample_size_check_disabled': allow_small,
        'errors': errors[:25], 'additional_errors': max(0, len(errors) - 25),
        'limitation': 'Structural checks and reviewer attestations do not independently establish research validity.',
    }
