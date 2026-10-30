"""Explicit pretrained feature extractors. No silent fallback between models."""
import os
from functools import lru_cache

TEXT_MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
TEXT_REVISION = 'c9745ed1d9f207416be6d2e6f8de32d1f16199bf'
CLIP_MODEL = 'openai/clip-vit-base-patch32'
CLIP_REVISION = '3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268'
RESNET_MODEL = 'microsoft/resnet-50'
RESNET_REVISION = '34c2154c194f829b11125337b98c8f5f9965ff19'


@lru_cache(maxsize=1)
def sentence_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(TEXT_MODEL, revision=TEXT_REVISION, device='cpu')


def text_vectors(texts):
    return sentence_model().encode(texts, normalize_embeddings=True).tolist()


@lru_cache(maxsize=2)
def vision_model(kind):
    if kind == 'resnet':
        from transformers import AutoImageProcessor, ResNetModel
        return (ResNetModel.from_pretrained(RESNET_MODEL, revision=RESNET_REVISION).eval(),
                AutoImageProcessor.from_pretrained(RESNET_MODEL, revision=RESNET_REVISION, use_fast=False))
    if kind == 'clip':
        from transformers import CLIPModel, CLIPProcessor
        return (CLIPModel.from_pretrained(CLIP_MODEL, revision=CLIP_REVISION).eval(),
                CLIPProcessor.from_pretrained(CLIP_MODEL, revision=CLIP_REVISION, use_fast=False))
    raise ValueError(f'Unknown vision model: {kind}')


def image_vector(image, kind):
    import torch
    model, preprocess = vision_model(kind)
    with torch.inference_mode():
        if kind == 'resnet':
            vector = model(**preprocess(images=image, return_tensors='pt')).pooler_output.flatten()
        else:
            vector = model.get_image_features(**preprocess(images=image, return_tensors='pt'))[0]
        return torch.nn.functional.normalize(vector, dim=0).tolist()


def configuration():
    from django.conf import settings
    config = {'text': os.environ.get('LOSTLINK_TEXT_MODEL', settings.MATCHING_PROFILE['text']),
              'image': os.environ.get('LOSTLINK_IMAGE_MODEL', settings.MATCHING_PROFILE['image'])}
    if config['text'] not in ('tfidf','sbert') or config['image'] not in ('histogram','resnet','clip'):
        raise ValueError('Unsupported matching model configuration.')
    return config
