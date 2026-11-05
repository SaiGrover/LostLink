"""Versioned feature caching prevents mixing incompatible embeddings.

Each report's derived features (Sentence-BERT text vector, pretrained image
vector) are computed once at submission time (synopsis Section 4.4) and keyed
by a fingerprint of the description, the image path, the selected models and
their pinned revisions. Changing any of these invalidates the cache.

Model loading or inference failures are logged and leave the report without
that feature rather than aborting the request: moderation, claims and
metadata matching keep working when inference is temporarily unavailable
(synopsis Section 4.17 operational robustness). The fingerprint is not
recorded in that case, so the next ``prepare`` call retries.
"""
import hashlib
import json
import logging

from .embeddings import (CLIP_REVISION, RESNET_REVISION, TEXT_REVISION,
                         configuration, text_vectors)

logger = logging.getLogger('lostlink.matching')


def manifest():
    from django.conf import settings
    return {**configuration(), 'text_revision': TEXT_REVISION,
            'clip_revision': CLIP_REVISION, 'resnet_revision': RESNET_REVISION,
            'weights': dict(settings.MATCHING['WEIGHTS']), 'version': settings.MATCHING['VERSION']}


def fingerprint(item, config=None):
    config = config or configuration()
    payload = {**config, 'description': item.description, 'image_path': str(item.image),
               'revisions': [TEXT_REVISION, CLIP_REVISION, RESNET_REVISION]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def prepare(item) -> bool:
    """Ensure ``item`` carries current-version feature vectors.

    Returns ``True`` when every requested feature was produced (or none was
    needed) and the fingerprint was recorded; ``False`` when inference failed
    for at least one feature, in which case the available vectors are stored
    but the fingerprint is left empty so a later call retries.
    """
    from .engine import extract_image_vector
    config = configuration()
    version = fingerprint(item, config)
    if item.feature_version == version:
        return True

    complete = True
    text_vector = None
    if config['text'] == 'sbert':
        try:
            text_vector = text_vectors([item.description])[0]
        except Exception:  # model download / inference failure
            logger.exception('Text embedding failed for report %s', item.pk)
            complete = False

    image_vector = None
    if item.image:
        try:
            image_vector = extract_image_vector(item.image.path)
        except Exception:
            logger.exception('Image embedding failed for report %s', item.pk)
            complete = False

    item.text_vector = text_vector
    item.image_vector = image_vector
    item.feature_version = version if complete else ''
    item.save(update_fields=['text_vector', 'image_vector', 'feature_version'])
    return complete
