"""Replace known demo placeholders with credited photographs of real objects."""
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from items.models import Item
from matching.engine import extract_image_vector


ROOT = Path(settings.BASE_DIR)
PILOT = json.loads((ROOT / 'evaluation/pilot/dataset.json').read_text(encoding='utf-8'))
SOURCES = {row['id']: row for row in PILOT['sources']}

# Demo illustrations only: photos of representative objects, not actual campus reports.
PHOTOS = {
    'Black leather wallet': ('pilot', '87189439'),
    'Black wallet made of leather with silver logo': ('pilot', '87189440'),
    'iPhone 13 with blue case': ('commons', 'IPhone 13 Pro Max Sierra Blue 128g.jpg'),
    'Blue Apple phone found on table': ('commons', 'IPhone 13 Pro Sierra blue A2483 US version rear.jpg'),
    'Silver steel water bottle': ('pilot', '19133709'),
    'Metal water bottle, steel colour': ('pilot', '182337243'),
    'Navy blue backpack': ('pilot', '80619479'),
    'Dark blue bag left in classroom': ('pilot', '145601019'),
    'Bunch of keys with red tag': ('commons', 'Key ring.jpg'),
    'Keys with a red keychain': ('commons', 'Key ring.jpg'),
    'Black spectacles in a case': ('pilot', '4469278'),
    'Brown leather diary': ('pilot', '158256776'),
    'Silver ring': ('commons', 'Silverring.jpg'),
    'Green hoodie, size M': ('commons', 'Hoodie with a DIP-8 integrated circuit design.jpg'),
}


def commons_photo(title):
    params = urlencode({'action': 'query', 'format': 'json', 'prop': 'imageinfo',
                        'titles': 'File:' + title, 'iiprop': 'url|extmetadata', 'iiurlwidth': 640})
    request = Request('https://commons.wikimedia.org/w/api.php?' + params,
                      headers={'User-Agent': 'LostLink educational demo/1.0 (photograph attribution)'})
    with urlopen(request, timeout=30) as response:
        pages = json.load(response)['query']['pages']
    page = next(iter(pages.values()))
    if 'imageinfo' not in page:
        raise CommandError(f'Commons photograph unavailable: {title}')
    info = page['imageinfo'][0]
    metadata = info.get('extmetadata', {})
    licence = metadata.get('LicenseShortName', {}).get('value', '')
    if not licence or licence.lower() in ('copyrighted', 'unknown'):
        raise CommandError(f'Unverified Commons licence for {title}')
    image_request = Request(info.get('thumburl', info['url']),
                            headers={'User-Agent': 'LostLink educational demo/1.0 (photograph attribution)'})
    with urlopen(image_request, timeout=40) as response:
        photo = response.read()
    return photo, {'source': info.get('descriptionurl', 'https://commons.wikimedia.org/wiki/File:' + title.replace(' ', '_')),
                   'artist': metadata.get('Artist', {}).get('value', ''),
                   'license': licence, 'license_url': metadata.get('LicenseUrl', {}).get('value', ''),
                   'change': 'Commons 640px thumbnail; used as a representative demo image.'}


class Command(BaseCommand):
    help = 'Replace only generated demo pictures with real, attributed object photographs.'

    def add_arguments(self, parser):
        parser.add_argument('--only-title', help='Refresh one named demo report already using a representative photo.')

    def handle(self, only_title=None, **options):
        source_cache = {}
        credits = []
        changed = 0
        for item in Item.objects.order_by('pk'):
            source = PHOTOS.get(item.title)
            filename = Path(item.image.name).name if item.image else ''
            if (not source or (only_title and item.title != only_title) or
                    not (filename.startswith('demo_') or
                         (only_title and filename.startswith('demo-real-')))):
                continue
            if source not in source_cache:
                if source[0] == 'pilot':
                    original = SOURCES[source[1]]
                    photo = (ROOT / 'evaluation/pilot' / original['image']).read_bytes()
                    credit = {'source': original['source'], 'artist': original['artist'],
                              'license': original['license'], 'license_url': original['license_url'],
                              'change': 'Bundled 500px Wikimedia Commons thumbnail; representative demo image.'}
                else:
                    photo, credit = commons_photo(source[1])
                source_cache[source] = (photo, credit)
            photo, credit = source_cache[source]
            item.image.save(f'demo-real-{item.pk}.jpg', ContentFile(photo), save=False)
            item.image_vector = extract_image_vector(item.image.path)
            item.feature_version = ''  # Force recomputation by rebuild_matches.
            item.save(update_fields=['image', 'image_vector', 'feature_version', 'updated_at'])
            credits.append({'item_title': item.title, 'item_id': item.pk, **credit})
            changed += 1
        if credits:
            target = ROOT / 'docs/DEMO_IMAGE_CREDITS.json'
            existing = json.loads(target.read_text(encoding='utf-8')) if target.exists() else []
            # Keyed by the stable demo title: database IDs change on every reseed.
            by_item = {row['item_title']: row for row in existing}
            by_item.update({row['item_title']: row for row in credits})
            target.write_text(json.dumps(list(by_item.values()), indent=2, ensure_ascii=False), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'{changed} demo photos updated. Run rebuild_matches next.'))
