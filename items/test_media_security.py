"""Upload sanitisation, media access policy and sensitive-photo privacy."""
import io
import re
import struct
import zlib
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from claims.models import Claim
from config.testing import isolated_media, lightweight_models, make_taxonomy, make_users, png_bytes, png_upload
from items import media
from items.forms import ItemForm
from items.models import Category, Item
from matching.models import Match
from notifications.models import Notification


def jpeg_with_metadata(size=(60, 40), orientation=6):
    """Left half red, right half blue, with EXIF orientation + GPS, a comment and XMP."""
    img = Image.new('RGB', size, (200, 30, 30))
    img.paste((30, 30, 200), (size[0] // 2, 0, size[0], size[1]))
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = 'SpyCam'
    exif[0x8825] = {1: 'N', 2: (12.0, 58.0, 0.0), 3: 'E', 4: (77.0, 35.0, 0.0)}
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif.tobytes(), comment=b'owner phone 98110',
             xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/">secret-xmp</x:xmpmeta>')
    return buf.getvalue()


def png_bomb(width=100_000, height=100_000):
    """A tiny PNG whose header claims 10 billion pixels."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    ihdr = struct.pack('>IIBBBBB', width, height, 1, 0, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(b'\x00' * 64)) + chunk(b'IEND', b''))


def gif_bytes():
    buf = io.BytesIO()
    frames = [Image.new('P', (20, 20), i) for i in (1, 2)]
    frames[0].save(buf, 'GIF', save_all=True, append_images=frames[1:])
    return buf.getvalue()


class SanitiseBytesTests(TestCase):
    def test_exif_gps_comment_and_xmp_are_removed(self):
        clean = media.sanitize_image_bytes(jpeg_with_metadata())
        img = Image.open(io.BytesIO(clean))
        self.assertEqual(img.format, 'JPEG')
        self.assertEqual(dict(img.getexif()), {})
        self.assertNotIn('exif', img.info)
        self.assertNotIn('comment', img.info)
        self.assertNotIn('xmp', img.info)
        for secret in (b'SpyCam', b'98110', b'secret-xmp', b'GPS'):
            self.assertNotIn(secret, clean)

    def test_orientation_is_applied_before_stripping(self):
        img = Image.open(io.BytesIO(media.sanitize_image_bytes(jpeg_with_metadata(orientation=6))))
        # 60x40 rotated 90 degrees clockwise: 40x60, red half now on top.
        self.assertEqual(img.size, (40, 60))
        r, g, b = img.getpixel((20, 5))
        self.assertGreater(r, 150)
        self.assertLess(b, 80)
        r, g, b = img.getpixel((20, 55))
        self.assertGreater(b, 150)

    def test_transparent_png_and_animated_gif_become_flat_jpegs(self):
        rgba = Image.new('RGBA', (30, 30), (0, 0, 0, 0))
        buf = io.BytesIO()
        rgba.save(buf, 'PNG')
        for data in (buf.getvalue(), gif_bytes()):
            img = Image.open(io.BytesIO(media.sanitize_image_bytes(data)))
            self.assertEqual((img.format, img.mode, getattr(img, 'n_frames', 1)), ('JPEG', 'RGB', 1))

    def test_large_image_is_scaled_to_the_maximum_edge(self):
        big = io.BytesIO()
        Image.new('RGB', (3000, 1000), (10, 120, 60)).save(big, 'PNG')
        img = Image.open(io.BytesIO(media.sanitize_image_bytes(big.getvalue(), max_edge=1024)))
        self.assertEqual(img.size, (1024, 341))

    def test_pixel_bomb_and_pixel_limit_are_rejected(self):
        with self.assertRaises(media.ImageRejected):
            media.sanitize_image_bytes(png_bomb())
        with self.assertRaises(media.ImageRejected):
            media.sanitize_image_bytes(png_bytes(size=(200, 200)), max_pixels=10_000)

    def test_non_images_polyglots_and_truncated_files_are_rejected(self):
        good_jpeg = media.sanitize_image_bytes(jpeg_with_metadata())
        cases = [
            b'',
            b'hello world',
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            b'<!DOCTYPE html><html><body>hi</body></html>',
            gif_bytes() + b'<script>alert(document.cookie)</script>',
            png_bytes() + b'<html><iframe src=x>',
            good_jpeg[: len(good_jpeg) // 2],
        ]
        for data in cases:
            with self.subTest(data=data[:20]), self.assertRaises(media.ImageRejected):
                media.sanitize_image_bytes(data)

    def test_signature_sniffing(self):
        self.assertEqual(media.sniff_image_type(png_bytes()[:16]), 'image/png')
        self.assertEqual(media.sniff_image_type(gif_bytes()[:16]), 'image/gif')
        self.assertIsNone(media.sniff_image_type(b'<svg'))


@lightweight_models
@isolated_media
class UploadFormTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()

    def payload(self, **overrides):
        data = {'report_type': 'found', 'title': 'Black leather wallet',
                'category': self.tax['bags'].pk, 'location': self.tax['library'].pk,
                'event_date': timezone.localdate().isoformat(), 'event_time': '',
                'description': 'Black leather wallet with a silver logo', 'brand': '', 'color': ''}
        data.update(overrides)
        return data

    def form(self, upload, **overrides):
        return ItemForm(data=self.payload(**overrides), files={'image': upload})

    def test_upload_is_reencoded_with_a_random_name(self):
        upload = SimpleUploadedFile('my passport scan.jpg', jpeg_with_metadata(), content_type='image/jpeg')
        self.client.force_login(self.users['owner'])
        self.client.post(reverse('items:report'), self.payload(image=upload))
        item = Item.objects.get()
        self.assertRegex(item.image.name, r'^items/\d{4}/\d{2}/[0-9a-f]{32}\.jpg$')
        self.assertNotIn('passport', item.image.name)
        stored = item.image.read()
        self.assertNotIn(b'SpyCam', stored)
        self.assertEqual(dict(Image.open(io.BytesIO(stored)).getexif()), {})

    def test_bombs_polyglots_svg_and_oversize_files_fail_validation(self):
        cases = {
            'bomb': SimpleUploadedFile('a.png', png_bomb(), content_type='image/png'),
            'polyglot': SimpleUploadedFile('a.gif', gif_bytes() + b'<script>x()</script>', content_type='image/gif'),
            'svg': SimpleUploadedFile('a.svg', b'<svg xmlns="http://www.w3.org/2000/svg"/>',
                                      content_type='image/svg+xml'),
            'html as jpg': SimpleUploadedFile('a.jpg', b'<html><script>x()</script></html>',
                                              content_type='image/jpeg'),
            'oversize': SimpleUploadedFile('a.png', b'0' * (6 * 1024 * 1024), content_type='image/png'),
        }
        for label, upload in cases.items():
            with self.subTest(label):
                form = self.form(upload)
                self.assertFalse(form.is_valid())
                self.assertIn('image', form.errors)

    @override_settings(LOSTLINK_UPLOAD_MAX_PIXELS=1000)
    def test_configured_pixel_limit_applies(self):
        form = self.form(png_upload(size=(100, 100)))
        self.assertFalse(form.is_valid())
        self.assertIn('pixel', str(form.errors['image']))

    def test_editing_text_with_a_missing_stored_photo_does_not_crash(self):
        item = Item.objects.create(reporter=self.users['owner'], report_type='lost', title='Wallet',
                                   description='Black leather wallet', category=self.tax['bags'],
                                   location=self.tax['library'], image='items/2020/01/gone.jpg')
        self.client.force_login(self.users['owner'])
        response = self.client.post(reverse('items:edit', args=[item.pk]),
                                    self.payload(report_type='lost', description='Black leather wallet, scuffed'))
        self.assertRedirects(response, item.get_absolute_url(), fetch_redirect_response=False)
        item.refresh_from_db()
        self.assertEqual(item.image.name, 'items/2020/01/gone.jpg')


@lightweight_models
@isolated_media
class MediaAccessTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()
        self.item = Item.objects.create(reporter=self.users['finder'], report_type='found', title='Phone',
                                        description='Blue phone with a cracked case',
                                        category=self.tax['electronics'], location=self.tax['cafeteria'],
                                        image=png_upload('pending.png'))
        self.url = self.item.image.url

    def get(self, user, url=None):
        self.client.logout()
        if user:
            self.client.force_login(self.users[user])
        return self.client.get(url or self.url)

    def test_pending_photo_authorisation_matrix(self):
        expected = {None: 403, 'other': 403, 'owner': 403, 'finder': 200, 'mod': 200, 'admin': 200}
        for user, status in expected.items():
            with self.subTest(user=user):
                self.assertEqual(self.get(user).status_code, status)
        Claim.objects.create(item=self.item, claimant=self.users['owner'], statement='Mine')
        self.assertEqual(self.get('owner').status_code, 200)

    def test_public_photo_headers(self):
        self.item.status = 'approved'
        self.item.save()
        response = self.get(None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Content-Disposition'], f'inline; filename="report-{self.item.pk}.png"')
        self.assertIn("sandbox", response['Content-Security-Policy'])
        self.assertEqual(response['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertIn('Cookie', response['Vary'])
        self.assertNotIn('pending', response['Content-Disposition'])

    def test_content_type_comes_from_the_file_not_its_name(self):
        self.item.status = 'approved'
        self.item.image.save('looks-like.png', ContentFile(media.sanitize_image_bytes(png_bytes())))
        response = self.get(None, self.item.image.url)
        self.assertEqual(response['Content-Type'], 'image/jpeg')

    def test_stored_non_image_is_never_served(self):
        self.item.status = 'approved'
        self.item.image.save('legacy.svg', ContentFile(b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'))
        self.assertEqual(self.get('mod', self.item.image.url).status_code, 404)

    def test_path_traversal_and_malformed_paths_are_404(self):
        for path in ('/media/../config/settings.py', '/media/items/../../manage.py',
                     '/media/%2e%2e/manage.py', '/media/items/%2e%2e/%2e%2e/manage.py',
                     '/media/items//x.png', '/media/items/./x.png', '/media/items\\..\\manage.py',
                     '/media//etc/passwd', '/media/C:/Windows/win.ini', '/media/items'):
            with self.subTest(path=path):
                self.assertEqual(self.get('admin', path).status_code, 404)

    def test_unregistered_prefix_and_orphans_are_private_by_default(self):
        storage = self.item.image.storage
        storage.save('claims/evidence/receipt.png', ContentFile(png_bytes()))
        orphan = storage.save('items/2020/01/orphan.png', ContentFile(png_bytes()))
        for user in (None, 'finder', 'mod', 'admin'):
            with self.subTest(user=user):
                self.assertEqual(self.get(user, '/media/claims/evidence/receipt.png').status_code, 404)
                self.assertEqual(self.get(user, settings.MEDIA_URL + orphan).status_code, 404)

    def test_a_registered_rule_is_an_explicit_grant(self):
        storage = self.item.image.storage
        storage.save('claims/evidence/receipt.png', ContentFile(png_bytes()))

        def staff_only(request, path):
            from django.core.exceptions import PermissionDenied
            from django.core.files.storage import default_storage
            if not (request.user.is_authenticated and request.user.is_staff_role):
                raise PermissionDenied
            return media.raster_file_response(default_storage.open(path), basename='evidence')

        media.register_media_rule('claims', staff_only)
        try:
            self.assertEqual(self.get('owner', '/media/claims/evidence/receipt.png').status_code, 403)
            response = self.get('mod', '/media/claims/evidence/receipt.png')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
        finally:
            media.MEDIA_RULES.pop('claims', None)
        with self.assertRaises(ValueError):
            media.register_media_rule('claims/evidence', staff_only)

    def test_withdrawn_replaced_and_deleted_photos(self):
        self.item.status = 'approved'
        self.item.save()
        old_url = self.url
        self.item.image.save('replacement.png', ContentFile(png_bytes(color=(1, 2, 3))))
        self.assertEqual(self.get('finder', old_url).status_code, 404)  # replaced photo
        self.item.is_active = False
        self.item.save()
        self.assertEqual(self.get('other', self.item.image.url).status_code, 403)  # withdrawn
        url = self.item.image.url
        self.item.delete()
        self.assertEqual(self.get('admin', url).status_code, 404)  # deleted report

    def test_unknown_path_is_404(self):
        self.assertEqual(self.get(None, '/media/items/2026/01/nope.png').status_code, 404)


@lightweight_models
@isolated_media
class SensitivePhotoTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()
        self.original = png_bytes(size=(240, 160), pattern=True)
        self.item = Item.objects.create(
            reporter=self.users['finder'], report_type='found', title='Card', status='approved',
            description='A plastic card found near the counter', category=self.tax['others'],
            location=self.tax['cafeteria'], photo_sensitive=True,
            image=SimpleUploadedFile('card.png', self.original))
        self.url = self.item.image.url

    def fetch(self, user):
        self.client.logout()
        if user:
            self.client.force_login(self.users[user])
        response = self.client.get(self.url)
        body = b''.join(response.streaming_content) if response.streaming else response.content
        return response, body

    def assert_preview(self, response, body):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Photo-Variant'], 'restricted-preview')
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertNotEqual(body, self.original)
        self.assertLessEqual(max(Image.open(io.BytesIO(body)).size), media.PREVIEW_EDGE)

    def assert_full(self, response, body):
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('X-Photo-Variant', response)
        self.assertEqual(body, self.original)

    def test_who_gets_the_full_photo(self):
        for user in (None, 'other', 'owner'):
            with self.subTest(user=user):
                self.assert_preview(*self.fetch(user))
        for user in ('finder', 'mod', 'admin'):
            with self.subTest(user=user):
                self.assert_full(*self.fetch(user))
        claim = Claim.objects.create(item=self.item, claimant=self.users['owner'], statement='Mine')
        self.assert_preview(*self.fetch('owner'))  # an unverified claim is not enough
        claim.status = Claim.Status.APPROVED
        claim.save()
        self.assert_full(*self.fetch('owner'))

    def test_pending_sensitive_photo_is_still_hidden_from_strangers(self):
        self.item.status = 'pending'
        self.item.save()
        self.assertEqual(self.fetch('other')[0].status_code, 403)

    def test_every_rendering_place_uses_the_gated_url_and_explains_it(self):
        self.client.force_login(self.users['other'])
        html = self.client.get(reverse('items:search_api')).json()['html']
        self.assertIn(self.url, html)
        self.assertIn('Photo blurred for privacy', html)
        detail = self.client.get(self.item.get_absolute_url()).content.decode()
        self.assertIn('blurred for privacy', detail)
        Notification.objects.create(user=self.users['other'], kind='match', title='Possible match',
                                    item=self.item, url=self.item.get_absolute_url())
        self.assertIn(self.url, self.client.get(reverse('notifications:inbox')).content.decode())
        # match card on the other party's report
        lost = Item.objects.create(reporter=self.users['other'], report_type='lost', title='Card',
                                   description='Plastic card', category=self.tax['others'],
                                   location=self.tax['cafeteria'], status='approved')
        Match.objects.create(lost_item=lost, found_item=self.item, score=.9, tier='high',
                             engine_version=settings.MATCHING['VERSION'])
        self.assertIn(self.url, self.client.get(lost.get_absolute_url()).content.decode())
        self.assert_preview(*self.fetch('other'))
        # the reporter sees no blur note on their own copy
        self.client.force_login(self.users['finder'])
        self.assertNotIn('Photo blurred for privacy', self.client.get(reverse('items:mine')).content.decode())

    def test_landing_page_never_uses_sensitive_photos(self):
        public = Item.objects.create(reporter=self.users['owner'], report_type='lost', title='Bottle',
                                     description='Steel bottle', category=self.tax['others'],
                                     location=self.tax['cafeteria'], status='approved', image=png_upload('b.png'))
        response = self.client.get('/')
        self.assertIn(public, response.context['strip'])
        self.assertNotIn(self.item, response.context['strip'])
        self.assertNotIn(self.url, response.content.decode())

    def test_search_json_carries_no_reporter_details(self):
        payload = self.client.get(reverse('items:search_api')).content.decode()
        for secret in ('finder@x.edu', 'finder', 'Fin', 'pass-word'):
            self.assertNotIn(f'"{secret}"', payload)
        self.assertNotIn('finder@x.edu', payload)


@lightweight_models
@isolated_media
class SensitiveDeclarationTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()
        self.client.force_login(self.users['owner'])

    def payload(self, **overrides):
        data = {'report_type': 'lost', 'title': 'Black leather wallet', 'category': self.tax['bags'].pk,
                'location': self.tax['library'].pk, 'event_date': timezone.localdate().isoformat(),
                'event_time': '', 'description': 'Black leather wallet with a silver logo',
                'brand': '', 'color': '', 'image': png_upload()}
        data.update(overrides)
        return data

    def test_reporter_declaration(self):
        self.client.post(reverse('items:report'), self.payload(declare_sensitive='on'))
        item = Item.objects.get()
        self.assertTrue(item.photo_sensitive)
        self.assertEqual(item.photo_sensitivity_source, 'reporter')

    def test_plain_report_is_not_restricted(self):
        self.client.post(reverse('items:report'), self.payload())
        self.assertFalse(Item.objects.get().photo_sensitive)

    def test_id_cards_are_restricted_as_a_precaution(self):
        ids = Category.objects.create(name='ID & Cards', slug='id-cards')
        self.client.post(reverse('items:report'), self.payload(category=ids.pk))
        self.client.post(reverse('items:report'), self.payload(title='Student ID card',
                                                               description='Blue lanyard with my student ID card'))
        for item in Item.objects.all():
            self.assertTrue(item.photo_sensitive)
            self.assertEqual(item.photo_sensitivity_source, 'auto')

    def test_reporter_cannot_lift_protection_but_a_new_photo_resets_review(self):
        self.client.post(reverse('items:report'), self.payload(declare_sensitive='on'))
        item = Item.objects.get()
        item.photo_reviewed_by = self.users['mod']
        item.save()
        self.client.post(reverse('items:edit', args=[item.pk]), self.payload(image=''))
        item.refresh_from_db()
        self.assertTrue(item.photo_sensitive)
        self.assertEqual(item.photo_reviewed_by, self.users['mod'])
        self.client.post(reverse('items:edit', args=[item.pk]), self.payload())
        item.refresh_from_db()
        self.assertTrue(item.photo_sensitive)  # field disabled: stays declared
        self.assertIsNone(item.photo_reviewed_by)


@lightweight_models
@isolated_media
class ModeratorPhotoControlTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()
        self.item = Item.objects.create(reporter=self.users['owner'], report_type='lost', title='Wallet',
                                        description='Black leather wallet', category=self.tax['bags'],
                                        location=self.tax['library'], image=png_upload())

    def url(self, decision):
        return reverse('dashboard:photo_privacy', args=[self.item.pk, decision])

    def test_students_cannot_change_photo_privacy(self):
        for user in ('owner', 'other'):
            self.client.force_login(self.users[user])
            self.client.post(self.url('restrict'))
        self.item.refresh_from_db()
        self.assertFalse(self.item.photo_sensitive)

    def test_moderator_restricts_and_lifts_with_audit_fields(self):
        self.client.force_login(self.users['mod'])
        page = self.client.get(reverse('dashboard:moderation')).content.decode()
        self.assertIn(self.url('restrict'), page)
        self.assertEqual(self.client.get(self.url('restrict')).status_code, 405)
        self.client.post(self.url('restrict'))
        self.item.refresh_from_db()
        self.assertTrue(self.item.photo_sensitive)
        self.assertEqual(self.item.photo_sensitivity_source, 'moderator')
        self.assertEqual(self.item.photo_reviewed_by, self.users['mod'])
        self.assertIsNotNone(self.item.photo_reviewed_at)
        self.assertIn(self.url('public'), self.client.get(reverse('dashboard:moderation')).content.decode())
        self.client.post(self.url('public'))
        self.item.refresh_from_db()
        self.assertFalse(self.item.photo_sensitive)
        self.client.post(self.url('bogus'))
        self.item.refresh_from_db()
        self.assertFalse(self.item.photo_sensitive)


@isolated_media
class SanitizePhotosCommandTests(TestCase):
    def test_legacy_photos_are_cleaned_and_keep_working(self):
        from io import StringIO

        from django.core.management import call_command
        tax = make_taxonomy()
        users = make_users()
        item = Item.objects.create(reporter=users['owner'], report_type='lost', title='Wallet', status='approved',
                                   description='Black leather wallet', category=tax['bags'],
                                   location=tax['library'],
                                   image=SimpleUploadedFile('legacy.jpg', jpeg_with_metadata()))
        legacy_png = Item.objects.create(reporter=users['owner'], report_type='lost', title='Bottle',
                                         status='approved', description='Steel bottle', category=tax['bags'],
                                         location=tax['library'], image=png_upload('legacy.png'))
        name = item.image.name
        call_command('sanitize_photos', '--dry-run', stdout=StringIO())
        self.assertIn(b'SpyCam', Path(item.image.path).read_bytes())
        call_command('sanitize_photos', stdout=StringIO())
        item.refresh_from_db()
        legacy_png.refresh_from_db()
        self.assertEqual(item.image.name, name)
        self.assertNotIn(b'SpyCam', Path(item.image.path).read_bytes())
        self.assertTrue(re.search(r'[0-9a-f]{32}\.jpg$', legacy_png.image.name))
        self.assertEqual(self.client.get(item.image.url).status_code, 200)
        self.assertEqual(self.client.get(legacy_png.image.url)['Content-Type'], 'image/jpeg')
        out = StringIO()
        call_command('sanitize_photos', stdout=out)
        self.assertIn('0 rewritten, 0 converted', out.getvalue())

    def test_restrict_suggested_backfills_id_cards_but_respects_moderators(self):
        from io import StringIO

        from django.core.management import call_command
        tax, users = make_taxonomy(), make_users()

        def report(title, **extra):
            return Item.objects.create(reporter=users['owner'], report_type='found', title=title,
                                       status='approved', description='Found near the gate',
                                       category=tax['others'], location=tax['library'],
                                       image=png_upload(), **extra)
        card, cleared, bottle = (report('Student ID card'),
                                 report('College ID card', photo_reviewed_by=users['mod']),
                                 report('Steel bottle'))
        call_command('sanitize_photos', '--restrict-suggested', stdout=StringIO())
        for item, expected in ((card, True), (cleared, False), (bottle, False)):
            item.refresh_from_db()
            self.assertEqual(item.photo_sensitive, expected, item.title)
        self.assertEqual(card.photo_sensitivity_source, 'auto')
