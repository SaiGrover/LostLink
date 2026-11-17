"""Versioned per-report features and their processing state.

Each report's derived features -- the text representation (fixed-vocabulary
lexical vector, or a Sentence-BERT embedding) and the image vector -- are
computed once (synopsis Section 4.4) and keyed by a fingerprint of the title,
description, image path, the selected models, their pinned revisions and the
text-model release. Changing any of these invalidates the cache.

Processing state is recorded per report in ``matching.models.ItemFeatures``:
``success`` / ``pending`` / ``missing`` (no photo supplied) / ``failed`` /
``retrying``, together with the model that actually produced the vector.
Failures are logged to ``lostlink.matching`` (operators) and summarised for
moderators; students only ever see "photo not compared". After
``FEATURE_MAX_ATTEMPTS`` failed attempts the report stops retrying on every
matching run (avoiding repeated model loads) until
``python manage.py retry_features`` resets it.
"""
import hashlib
import json
import logging

from django.conf import settings
from django.utils import timezone

from .embeddings import (CLIP_MODEL, CLIP_REVISION, RESNET_MODEL, RESNET_REVISION,
                         TEXT_REVISION, configuration)

logger = logging.getLogger('lostlink.matching')


def max_attempts() -> int:
    return int(settings.MATCHING.get('FEATURE_MAX_ATTEMPTS', 3))


def manifest():
    """Everything that defines how a stored score was produced."""
    from . import chronology, engine, textmodel
    return {**configuration(), 'text_revision': TEXT_REVISION,
            'clip_revision': CLIP_REVISION, 'resnet_revision': RESNET_REVISION,
            'text_release': textmodel.active_release(),
            'weights': dict(settings.MATCHING['WEIGHTS']), 'version': settings.MATCHING['VERSION'],
            'thresholds': {'medium': settings.MATCHING['TIER_MEDIUM'],
                           'high': settings.MATCHING['TIER_HIGH'],
                           'min_score': settings.MATCHING['MIN_SCORE']},
            'coverage_rules': engine.coverage_rules(),
            'chronology': {k: v for k, v in chronology.config().items()}}


def fingerprint(item, config=None):
    from . import textmodel
    config = config or configuration()
    payload = {**config, 'title': item.title, 'description': item.description,
               'image_path': str(item.image),
               'text_release': textmodel.active_release(),
               'revisions': [TEXT_REVISION, CLIP_REVISION, RESNET_REVISION]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _image_detail(kind):
    return {'resnet': f'{RESNET_MODEL}@{RESNET_REVISION[:12]}',
            'clip': f'{CLIP_MODEL}@{CLIP_REVISION[:12]}',
            'histogram': 'Pillow colour histogram + luma thumbnail (baseline)'}.get(kind, kind)


def _short_error(exc) -> str:
    return f'{type(exc).__name__}: {exc}'[:200]


def features_for(item):
    """The item's ``ItemFeatures`` row, created on first access."""
    from .models import ItemFeatures
    try:
        return item.match_features
    except ItemFeatures.DoesNotExist:
        features, _ = ItemFeatures.objects.get_or_create(item=item)
        item.match_features = features
        return features


def prepare(item, force: bool = False) -> bool:
    """Ensure ``item`` carries current-version features and a recorded state.

    Returns ``True`` when every requested feature was produced (or none was
    needed); ``False`` when processing failed for at least one, in which case
    the available vectors are stored and the fingerprint is left empty so a
    later call retries (up to ``FEATURE_MAX_ATTEMPTS``).
    """
    from . import engine
    from .models import ItemFeatures
    State = ItemFeatures.State
    config = configuration()
    version = fingerprint(item, config)
    if not force and item.feature_version == version:
        return True
    features = features_for(item)
    if (not force and features.fingerprint == version and features.attempts >= max_attempts()
            and State.FAILED in (features.text_state, features.image_state)):
        return False  # retries exhausted for this content; wait for retry_features

    now = timezone.now()
    complete = True
    errors = []

    # --- text -----------------------------------------------------------
    text_vector, text_model = None, ''
    try:
        text_vector = engine.text_representation(item.title, item.description, config)
        text_model = engine.text_model_id(config)
        text_state = State.SUCCESS
    except Exception as exc:  # model download / inference failure
        logger.exception('Text features failed for report %s', item.pk)
        errors.append(_short_error(exc))
        complete = False
        text_state = State.FAILED
        if config['text'] == 'sbert':
            # Keep text matching alive with the deterministic lexical release;
            # the state still records that the configured model failed.
            from . import textmodel
            text_vector = textmodel.represent(item.title, item.description)
            text_model = textmodel.active_release()

    # --- image ----------------------------------------------------------
    image_vector, image_model = None, ''
    if not item.image:
        image_state = State.MISSING
    else:
        try:
            image_vector = engine.extract_image_vector(item.image.path, config['image'])
        except Exception as exc:
            logger.exception('Image features failed for report %s', item.pk)
            errors.append(_short_error(exc))
            image_vector = None
        else:
            if image_vector is None:
                logger.warning('Image for report %s is missing or unreadable', item.pk)
                errors.append('Photo file is missing or unreadable')
        if image_vector is None:
            complete = False
            image_state = State.FAILED
        else:
            image_state = State.SUCCESS
            image_model = config['image']

    attempts = 0 if complete else (features.attempts + 1 if features.fingerprint == version else 1)
    if not complete:
        exhausted = attempts >= max_attempts()
        if text_state == State.FAILED and not exhausted:
            text_state = State.RETRYING
        if image_state == State.FAILED and not exhausted:
            image_state = State.RETRYING

    item.text_vector = text_vector
    item.image_vector = image_vector
    item.feature_version = version if complete else ''
    item.save(update_fields=['text_vector', 'image_vector', 'feature_version'])

    features.text_state, features.text_model = text_state, text_model
    features.image_state, features.image_model = image_state, image_model
    features.image_model_detail = _image_detail(image_model) if image_model else ''
    features.attempts = attempts
    features.last_error = '; '.join(errors)[:200]
    features.fingerprint = version
    features.last_attempt_at = now
    if complete:
        features.succeeded_at = now
    features.save()
    return complete
