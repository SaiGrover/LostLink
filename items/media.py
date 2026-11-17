"""Uploaded media: sanitisation, photo privacy and the access policy.

Three responsibilities live here so every photo path goes through one place:

1. **Sanitising uploads** (``sanitize_upload``). An uploaded photograph is
   decoded, checked and *re-encoded*; the original bytes are never stored.
   The size and pixel count are bounded before any pixels are decoded
   (decompression-bomb protection), the leading bytes must be a real JPEG,
   PNG, GIF or WebP signature, files carrying HTML/SVG/script markup
   (polyglots) are refused, EXIF orientation is applied and then every piece
   of metadata (EXIF incl. GPS, XMP, comments, ICC after conversion to sRGB)
   is dropped. Animated images keep only their first frame. The result is a
   baseline JPEG with a random, unguessable file name.

2. **Sensitive photographs** (``photo_variant``). A report photo can be
   marked sensitive: by the reporter when submitting, automatically as a
   precaution when the report looks like an ID or bank card
   (``suggests_sensitive``; a hint, never a guarantee), or by a moderator,
   who always has the final word. A sensitive photo is served at full
   resolution only to the reporter, staff (moderators/administrators) and a
   claimant whose ownership a moderator has verified (claim approved or
   returned). Everyone else who may see the report gets a heavily blurred,
   low-resolution preview generated on the fly; it is never stored.

3. **Media access policy** (``serve``). ``/media/<path>`` is served only by
   the Django view, never by WhiteNoise or the web server. The rule is
   *deny by default*:

   * The path is normalised first; absolute paths, ``..``/``.`` segments,
     empty segments, backslashes and NUL bytes are rejected with 404.
   * The first path segment selects a rule from ``MEDIA_RULES``. A prefix
     with no registered rule is never served (404), so a future private
     attachment type (for example claim evidence under ``claims/``) is
     private until its app registers an explicit authorisation rule with
     ``register_media_rule``.
   * ``items/`` (report photographs): the file must be the current photo of
     at least one report. Orphaned files, replaced photos and photos of
     deleted reports are never served. The viewer gets the full photo, the
     restricted preview or a 403 according to ``photo_variant``.
   * Every response carries ``Cache-Control: private, no-store``,
     ``Vary: Cookie``, ``X-Content-Type-Options: nosniff``, an inline
     ``Content-Disposition`` with a neutral file name, a sandboxing
     ``Content-Security-Policy`` and ``Cross-Origin-Resource-Policy:
     same-origin``. The content type comes from the file's own signature,
     never from its name; a stored file that is not a supported raster image
     (e.g. a legacy SVG or HTML upload) is refused instead of being served.
"""
from __future__ import annotations

import io
import logging
import re
import secrets
import warnings

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import FileResponse, Http404, HttpResponse
from django.utils.cache import patch_vary_headers

logger = logging.getLogger('lostlink.media')

# --- limits (overridable in settings) -----------------------------------------
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_PIXELS = 40_000_000
DEFAULT_MAX_EDGE = 2048
JPEG_QUALITY = 86
PREVIEW_EDGE = 360          # restricted preview: final size …
PREVIEW_SOURCE_EDGE = 20    # … rebuilt from this many pixels on the long edge

ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}
EXTENSION_FOR = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp'}

# Byte sequences that never belong in a photograph but make a file
# interpretable as a web document (polyglot uploads).
_MARKUP = re.compile(rb'<\s*(script|html|svg|iframe|object|embed|body)\b|<\?php|<!doctype\s+html|javascript:',
                     re.IGNORECASE)


class ImageRejected(ValueError):
    """An upload that is not a safe, supported photograph."""


def max_upload_bytes():
    return getattr(settings, 'LOSTLINK_UPLOAD_MAX_BYTES', DEFAULT_MAX_BYTES)


def max_upload_pixels():
    return getattr(settings, 'LOSTLINK_UPLOAD_MAX_PIXELS', DEFAULT_MAX_PIXELS)


def max_upload_edge():
    return getattr(settings, 'LOSTLINK_UPLOAD_MAX_EDGE', DEFAULT_MAX_EDGE)


def sniff_image_type(head: bytes):
    """MIME type from a file's leading bytes, or ``None`` if it is not a
    supported raster image. File names and declared types are never trusted."""
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


def contains_markup(data: bytes) -> bool:
    return bool(_MARKUP.search(data))


def random_photo_name(ext='jpg'):
    """Unguessable storage name (128 bits); the original name is discarded."""
    return f'{secrets.token_hex(16)}.{ext}'


def _to_srgb(img, icc):
    """Convert to sRGB using the embedded ICC profile when possible, so colours
    survive once the profile itself is stripped."""
    from PIL import ImageCms
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        return ImageCms.profileToProfile(img, source, ImageCms.createProfile('sRGB'), outputMode='RGB')
    except (ImageCms.PyCMSError, OSError, ValueError, TypeError):
        logger.info('Ignoring an unusable ICC profile on an upload')
        return img


def _flatten_rgb(img):
    from PIL import Image
    has_alpha = img.mode in ('RGBA', 'LA', 'PA') or (img.mode == 'P' and 'transparency' in img.info)
    if has_alpha:
        rgba = img.convert('RGBA')
        canvas = Image.new('RGB', rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel('A'))
        return canvas
    return img if img.mode == 'RGB' else img.convert('RGB')


def sanitize_image_bytes(data: bytes, *, max_pixels=None, max_edge=None) -> bytes:
    """Decode, validate and re-encode ``data``; returns clean JPEG bytes.

    Raises ``ImageRejected`` with a user-facing message for anything that is
    not a safe, supported photograph.
    """
    from PIL import Image, ImageOps

    max_pixels = max_pixels or max_upload_pixels()
    max_edge = max_edge or max_upload_edge()
    if not data:
        raise ImageRejected('That file is empty.')
    if sniff_image_type(data[:16]) is None:
        raise ImageRejected('Please upload a JPG, PNG, WEBP or GIF image.')
    if contains_markup(data):
        raise ImageRejected('That file contains non-image content and was rejected.')

    with warnings.catch_warnings():
        # Pillow warns (rather than fails) between 1x and 2x its own pixel
        # limit; treat that as a rejection too.
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
                width, height = probe.size
                if fmt not in ALLOWED_FORMATS:
                    raise ImageRejected('Please upload a JPG, PNG, WEBP or GIF image.')
                if width < 1 or height < 1 or width * height > max_pixels:
                    raise ImageRejected('That image is too large in pixel dimensions.')
                probe.verify()
            img = Image.open(io.BytesIO(data))
            if fmt == 'JPEG':
                # Decode large JPEGs at reduced scale (DCT scaling) to bound memory.
                img.draft('RGB', (max_edge, max_edge))
            img.seek(0)  # first frame only for animated GIF/WebP
            img.load()
            icc = img.info.get('icc_profile')
            img = ImageOps.exif_transpose(img)  # orientation applied before EXIF is dropped
            if icc and img.mode in ('RGB', 'RGBA', 'CMYK', 'L'):
                img = _to_srgb(img.convert('RGB') if img.mode == 'CMYK' else img, icc)
            img = _flatten_rgb(img)
            img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            # Pillow falls back to ``img.info`` for some metadata (e.g. the
            # JPEG comment and XMP) when saving, so the carried-over info is
            # discarded; with no exif=/icc_profile= arguments only pixels and
            # the JFIF header are written.
            img.info = {}
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=JPEG_QUALITY, optimize=True, progressive=True)
        except ImageRejected:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ImageRejected('That image is too large in pixel dimensions.')
        except Exception as exc:  # noqa: BLE001 - decoders raise many types for hostile input
            # Not swallowed: every decoder failure becomes a rejection of the
            # upload; the class name is logged for diagnosis, never shown.
            logger.info('Rejected an unreadable upload (%s)', type(exc).__name__)
            raise ImageRejected('That file is not a readable image.') from exc
    return out.getvalue()


def sanitize_upload(upload) -> SimpleUploadedFile:
    """Validate an ``UploadedFile`` and return a clean, renamed JPEG upload."""
    limit = max_upload_bytes()
    if upload.size is not None and upload.size > limit:
        raise ImageRejected(f'Image must be under {limit // (1024 * 1024)} MB.')
    upload.seek(0)
    data = upload.read(limit + 1)
    if len(data) > limit:
        raise ImageRejected(f'Image must be under {limit // (1024 * 1024)} MB.')
    clean = sanitize_image_bytes(data)
    return SimpleUploadedFile(random_photo_name('jpg'), clean, content_type='image/jpeg')


# ---------------------------------------------------------------------------
# Sensitive photographs
# ---------------------------------------------------------------------------
FULL = 'full'
PREVIEW = 'preview'

SENSITIVE_CATEGORY_SLUGS = {'id-cards'}
_SENSITIVE_WORDS = re.compile(
    r'\b(id[\s-]?card|identity card|student id|college id|employee id|aadhaa?r|pan card|passport|'
    r'driving licen[cs]e|licen[cs]e card|voter id|credit card|debit card|atm card|bank card|'
    r'admit card|hall ticket|cheque|marksheet|mark sheet)\b', re.IGNORECASE)


def suggests_sensitive(title='', description='', category=None) -> bool:
    """Heuristic hint that a photo may show personal details (ID numbers,
    phone numbers, QR/barcodes). Assistance only: it can miss things and is
    never presented as a guarantee; moderators decide."""
    if category is not None and getattr(category, 'slug', '') in SENSITIVE_CATEGORY_SLUGS:
        return True
    return bool(_SENSITIVE_WORDS.search(f'{title} {description}'))


VERIFIED_CLAIM_STATUSES = ('approved', 'returned')


def _is_staff(user):
    return bool(user.is_authenticated and getattr(user, 'is_staff_role', False))


def report_visible_to(user, item) -> bool:
    """Same visibility rule as the report page: public once approved;
    otherwise only the reporter, staff and people with a claim on it."""
    if item.is_public:
        return True
    if not user.is_authenticated:
        return False
    return (_is_staff(user) or item.reporter_id == user.pk
            or item.claims.filter(claimant=user).exists())


def photo_variant(user, item):
    """``'full'``, ``'preview'`` or ``None`` (no access) for this viewer."""
    if not item.image:
        return None
    if user.is_authenticated and (_is_staff(user) or item.reporter_id == user.pk):
        return FULL
    if not item.photo_sensitive:
        return FULL if report_visible_to(user, item) else None
    if user.is_authenticated and item.claims.filter(
            claimant=user, status__in=VERIFIED_CLAIM_STATUSES).exists():
        return FULL
    return PREVIEW if report_visible_to(user, item) else None


def restricted_preview_bytes(fieldfile) -> bytes:
    """Blurred, low-resolution stand-in for a sensitive photo. Text, numbers
    and codes cannot be recovered: the image is rebuilt from at most
    ``PREVIEW_SOURCE_EDGE`` pixels on its long edge, then blurred."""
    from PIL import Image, ImageFilter, ImageOps
    try:
        with fieldfile.open('rb') as fh, warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            img = Image.open(fh)
            img.draft('RGB', (256, 256))
            img = _flatten_rgb(ImageOps.exif_transpose(img))
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        logger.warning('Could not build a restricted preview; using a placeholder')
        img = Image.new('RGB', (4, 3), (150, 150, 150))
    w, h = img.size
    scale = PREVIEW_SOURCE_EDGE / max(w, h)
    tiny = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BOX)
    scale = PREVIEW_EDGE / max(tiny.size)
    big = tiny.resize((max(1, round(tiny.width * scale)), max(1, round(tiny.height * scale))),
                      Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(PREVIEW_EDGE / 40))
    big.info = {}  # no comment/XMP carried over from a legacy original
    out = io.BytesIO()
    big.save(out, format='JPEG', quality=70)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Media access policy
# ---------------------------------------------------------------------------
def normalise_media_path(path: str):
    """The path if it is a clean relative path, else ``None``."""
    if not path or '\x00' in path or '\\' in path or path.startswith('/'):
        return None
    parts = path.split('/')
    if any(part in ('', '.', '..') for part in parts) or ':' in parts[0]:
        return None
    return path


def harden_media_response(response, *, filename):
    """Security headers shared by every media response (photos and any
    future private attachment)."""
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    response['Cache-Control'] = 'private, no-store'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response['Cross-Origin-Resource-Policy'] = 'same-origin'
    patch_vary_headers(response, ('Cookie',))
    return response


def raster_file_response(fieldfile, *, basename):
    """Stream a stored raster image with a content type taken from its own
    signature. Anything that is not a supported raster image is refused."""
    try:
        fh = fieldfile.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404('File unavailable')
    head = fh.read(16)
    mime = sniff_image_type(head)
    if mime is None:
        fh.close()
        logger.warning('Refused to serve a stored file that is not a raster image: %s', fieldfile.name)
        raise Http404('File unavailable')
    fh.seek(0)
    response = FileResponse(fh, content_type=mime)
    return harden_media_response(response, filename=f'{basename}.{EXTENSION_FOR[mime]}')


def _item_photo(request, path):
    from .models import Item
    items = list(Item.objects.filter(image=path).order_by('pk'))
    if not items:
        raise Http404('File unavailable')
    rank = {FULL: 2, PREVIEW: 1, None: 0}
    item, variant = max(((it, photo_variant(request.user, it)) for it in items),
                        key=lambda pair: rank[pair[1]])
    if variant is None:
        raise PermissionDenied('This image is awaiting review or is no longer public.')
    if variant == PREVIEW:
        response = HttpResponse(restricted_preview_bytes(item.image), content_type='image/jpeg')
        response['X-Photo-Variant'] = 'restricted-preview'
        return harden_media_response(response, filename=f'report-{item.pk}-preview.jpg')
    return raster_file_response(item.image, basename=f'report-{item.pk}')


# prefix (first path segment) -> callable(request, path) -> HttpResponse.
# Anything not listed here is private and never served.
MEDIA_RULES = {'items': _item_photo}


def register_media_rule(prefix, rule):
    """Allow files under ``<prefix>/`` to be served through ``rule``.

    ``rule(request, path)`` must return a response (use
    ``raster_file_response``/``harden_media_response``) or raise ``Http404``
    / ``PermissionDenied``. Rules are explicit grants: register one only for
    a prefix whose authorisation you have implemented."""
    if not prefix or '/' in prefix:
        raise ValueError('A media rule prefix is a single path segment, e.g. "claims".')
    MEDIA_RULES[prefix] = rule


def serve(request, path):
    clean = normalise_media_path(path)
    if clean is None:
        raise Http404('File unavailable')
    rule = MEDIA_RULES.get(clean.split('/', 1)[0])
    if rule is None or '/' not in clean:
        raise Http404('File unavailable')
    return rule(request, clean)
