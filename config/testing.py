"""Shared helpers for the LostLink test suite.

* ``lightweight_models`` pins the engine to TF-IDF + the Pillow histogram so
  the suite never downloads pretrained weights. Pretrained inference is
  exercised separately by ``matching.tests.PretrainedSmokeTests`` when the
  models are installed and ``LOSTLINK_TEST_PRETRAINED=1`` is set.
* ``isolated_media`` writes uploads to a temporary directory.
* ``png_upload`` builds a small real PNG for upload tests.
"""
import io
import os
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

lightweight_models = patch.dict(os.environ, {'LOSTLINK_TEXT_MODEL': 'tfidf',
                                             'LOSTLINK_IMAGE_MODEL': 'histogram'})

_media_dir = tempfile.mkdtemp(prefix='lostlink-test-media-')
isolated_media = override_settings(MEDIA_ROOT=_media_dir)


def png_bytes(color=(40, 90, 200), size=(48, 48), pattern=False):
    from PIL import Image, ImageDraw
    img = Image.new('RGB', size, color)
    if pattern:
        d = ImageDraw.Draw(img)
        d.rectangle([size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4],
                    fill=tuple(255 - c for c in color))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def png_upload(name='photo.png', **kwargs):
    return SimpleUploadedFile(name, png_bytes(**kwargs), content_type='image/png')


def make_taxonomy():
    from items.models import Category, Location
    bags, _ = Category.objects.get_or_create(slug='bags', defaults={'name': 'Bags & Wallets', 'icon': '👜'})
    electronics, _ = Category.objects.get_or_create(slug='electronics', defaults={'name': 'Electronics', 'icon': '📱'})
    others, _ = Category.objects.get_or_create(slug='others', defaults={'name': 'Others', 'icon': '📦'})
    bags.related.add(others)
    library, _ = Location.objects.get_or_create(slug='library', defaults={'name': 'Central Library'})
    block_a, _ = Location.objects.get_or_create(slug='block-a', defaults={'name': 'Academic Block A'})
    cafeteria, _ = Location.objects.get_or_create(slug='cafeteria', defaults={'name': 'Cafeteria'})
    library.adjacent.add(block_a)
    return {'bags': bags, 'electronics': electronics, 'others': others,
            'library': library, 'block_a': block_a, 'cafeteria': cafeteria}


def make_users():
    from accounts.models import User
    return {
        'owner': User.objects.create_user(username='owner', password='pass-word-123', email='owner@x.edu',
                                          first_name='Olive', phone='+91 90000 00001'),
        'finder': User.objects.create_user(username='finder', password='pass-word-123', email='finder@x.edu',
                                           first_name='Fin'),
        'other': User.objects.create_user(username='other', password='pass-word-123', email='other@x.edu'),
        'mod': User.objects.create_user(username='mod', password='pass-word-123', role='moderator',
                                        email='mod@x.edu'),
        'admin': User.objects.create_user(username='adm', password='pass-word-123', role='admin',
                                         email='adm@x.edu'),
    }
