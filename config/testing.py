"""Shared helpers for the LostLink test suite.

* ``lightweight_models`` pins the engine to TF-IDF + the Pillow histogram so
  the suite never downloads pretrained weights. Pretrained inference is
  exercised separately by ``matching.tests.PretrainedSmokeTests`` when the
  models are installed and ``LOSTLINK_TEST_PRETRAINED=1`` is set.
* ``isolated_media`` writes uploads to a temporary directory.
* ``png_upload`` builds a small real PNG for upload tests.
* ``make_user`` / ``make_users`` create accounts that have confirmed their
  email (campus verified) unless ``verified=False`` is passed, because most
  tests exercise reporting and claiming, which require verification.
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


def make_user(username, password='pass-word-123', verified=True, **fields):
    """Create a user; campus-verified by default (see accounts.decorators)."""
    from django.utils import timezone

    from accounts.models import User
    if verified:
        fields.setdefault('email_verified_at', timezone.now())
        fields.setdefault('email_verification_method', User.VerificationMethod.EMAIL)
    return User.objects.create_user(username=username, password=password, **fields)


def make_users(verified=True):
    return {
        'owner': make_user('owner', email='owner@x.edu', first_name='Olive',
                           phone='+91 90000 00001', verified=verified),
        'finder': make_user('finder', email='finder@x.edu', first_name='Fin', verified=verified),
        'other': make_user('other', email='other@x.edu', verified=verified),
        'mod': make_user('mod', role='moderator', email='mod@x.edu', verified=verified),
        'admin': make_user('adm', role='admin', email='adm@x.edu', verified=verified),
    }
