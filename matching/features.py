"""Versioned feature caching prevents mixing incompatible embeddings."""
import hashlib
import json
from .embeddings import configuration, text_vectors, TEXT_REVISION, CLIP_REVISION, RESNET_REVISION


def manifest():
    from django.conf import settings
    return {**configuration(), 'text_revision':TEXT_REVISION,
            'clip_revision':CLIP_REVISION, 'resnet_revision':RESNET_REVISION,
            'weights':dict(settings.MATCHING['WEIGHTS']), 'version':settings.MATCHING['VERSION']}


def prepare(item):
    from .engine import extract_image_vector
    config = configuration()
    fingerprint = hashlib.sha256(json.dumps({**config, 'description':item.description,
        'image_path':str(item.image), 'revisions':[TEXT_REVISION,CLIP_REVISION,RESNET_REVISION]}, sort_keys=True).encode()).hexdigest()
    if item.feature_version == fingerprint:
        return
    item.text_vector = text_vectors([item.description])[0] if config['text'] == 'sbert' else None
    item.image_vector = extract_image_vector(item.image.path) if item.image else None
    item.feature_version = fingerprint
    item.save(update_fields=['text_vector','image_vector','feature_version'])
