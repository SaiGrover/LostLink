"""Bring photos stored before upload sanitisation existed up to the same standard.

Usage:  python manage.py sanitize_photos [--dry-run] [--restrict-suggested]

New uploads are re-encoded on arrival (items/media.py). This command applies
the same treatment to photos already on disk: EXIF (including GPS), XMP, ICC
and comments are removed, orientation is applied and oversized images are
scaled down. JPEGs are rewritten in place (same name, so links and demo-photo
credits keep working); PNG/GIF/WebP files become JPEGs with a new random name
and the old file is removed. Files that are not a supported raster image are
reported and left alone; the media view already refuses to serve them.
Already-clean JPEGs are skipped, so the command is safe to re-run.
"""
import os
import tempfile
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from items import media
from items.models import Item

_METADATA_KEYS = {'exif', 'icc_profile', 'xmp', 'comment', 'photoshop', 'XML:com.adobe.xmp'}


def _is_clean_jpeg(data):
    import io

    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        return (img.format == 'JPEG' and not (_METADATA_KEYS & set(img.info))
                and not img.getexif() and max(img.size) <= media.max_upload_edge())


def _replace_in_place(storage, name, data):
    try:
        path = storage.path(name)
    except NotImplementedError:
        storage.delete(name)
        return storage.save(name, ContentFile(data))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.part')
    with os.fdopen(fd, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, path)  # atomic: the photo is never missing
    return name


class Command(BaseCommand):
    help = 'Strip metadata from and re-encode report photos stored before upload sanitisation.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would change; write nothing.')
        parser.add_argument('--restrict-suggested', action='store_true',
                            help='Also restrict existing photos that look like ID/bank cards and that no '
                                 'moderator has ruled on (same hint as for new uploads).')

    def handle(self, *args, dry_run=False, restrict_suggested=False, **options):
        if restrict_suggested:
            candidates = [it for it in Item.objects.exclude(image='').exclude(image__isnull=True)
                          .filter(photo_sensitive=False, photo_reviewed_by__isnull=True).select_related('category')
                          if media.suggests_sensitive(it.title, it.description, it.category)]
            if not dry_run:
                Item.objects.filter(pk__in=[it.pk for it in candidates]).update(
                    photo_sensitive=True, photo_sensitivity_source=Item.PhotoSensitivitySource.AUTO)
            self.stdout.write(f'{"Would restrict" if dry_run else "Restricted"} {len(candidates)} photo(s) '
                              'that may show personal details; moderators can lift this.')
        counts = {'clean': 0, 'rewritten': 0, 'converted': 0, 'missing': 0, 'unsafe': 0}
        items = Item.objects.exclude(image='').exclude(image__isnull=True).order_by('pk')
        for item in items.iterator():
            storage, name = item.image.storage, item.image.name
            try:
                with storage.open(name, 'rb') as fh:
                    data = fh.read()
            except (FileNotFoundError, OSError):
                counts['missing'] += 1
                continue
            kind = media.sniff_image_type(data[:16])
            if kind is None or media.contains_markup(data):
                counts['unsafe'] += 1
                self.stdout.write(self.style.WARNING(f'Report {item.pk}: stored file is not a safe image ({name}).'))
                continue
            try:
                if kind == 'image/jpeg' and _is_clean_jpeg(data):
                    counts['clean'] += 1
                    continue
                clean = media.sanitize_image_bytes(data)
            except (media.ImageRejected, OSError, ValueError) as exc:
                counts['unsafe'] += 1
                self.stdout.write(self.style.WARNING(f'Report {item.pk}: could not re-encode ({exc}).'))
                continue
            if dry_run:
                counts['converted' if kind != 'image/jpeg' else 'rewritten'] += 1
                continue
            if kind == 'image/jpeg':
                _replace_in_place(storage, name, clean)
                counts['rewritten'] += 1
            else:
                new_name = storage.save(str(PurePosixPath(name).parent / media.random_photo_name('jpg')),
                                        ContentFile(clean))
                Item.objects.filter(pk=item.pk).update(image=new_name)
                if not Item.objects.filter(image=name).exists():
                    storage.delete(name)
                counts['converted'] += 1
        prefix = 'Would change' if dry_run else 'Done'
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}: {counts["rewritten"]} rewritten, {counts["converted"]} converted to JPEG, '
            f'{counts["clean"]} already clean, {counts["missing"]} missing, {counts["unsafe"]} unsafe/unreadable.'))
