"""Construct a licensed, source-disjoint retrieval pilot, explicitly labelled as constructed."""
import hashlib
import io
import json
import re
from pathlib import Path
import requests
from PIL import Image, ImageEnhance
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Download Commons images with attribution and construct 180 source-identity-labelled pairs.'

    def add_arguments(self, parser):
        parser.add_argument('--output', default='evaluation/pilot')

    def handle(self, output, **options):
        # Visual curation of the first source pass: these files depicted a
        # cell tower, utility wiring, construction scene and shoes respectively.
        excluded={'184788351','112149305','64191489','89839469','160992723'}
        root = Path(output)
        root.mkdir(parents=True, exist_ok=True)
        assets = root / 'images'
        assets.mkdir(exist_ok=True)
        session = requests.Session()
        session.headers['User-Agent'] = 'LostLinkAcademicPilot/1.0 (educational retrieval evaluation)'
        objects = []
        for category, query in [('bags','backpack'),('wallets','wallet'),('electronics','mobile phone'),
                                ('books','book cover'),('accessories','sunglasses'),('bottles','water bottle')]:
            response = session.get('https://commons.wikimedia.org/w/api.php', params={
                'action':'query','generator':'search','gsrsearch':query+' filetype:bitmap',
                'gsrnamespace':6,'gsrlimit':30,'prop':'imageinfo','iiprop':'url|extmetadata',
                'iiurlwidth':400,'format':'json'}, timeout=45)
            response.raise_for_status()
            pages = sorted(response.json().get('query',{}).get('pages',{}).values(), key=lambda p:p.get('index',0))
            accepted = 0
            for page in pages:
                info = page.get('imageinfo',[{}])[0]
                meta = info.get('extmetadata',{})
                license_name = meta.get('LicenseShortName',{}).get('value','')
                if not (license_name.startswith(('CC BY','CC0','Public domain','PD'))):
                    continue
                identity = str(page['pageid'])
                if identity in excluded:
                    continue
                if any(o['id']==identity for o in objects):
                    continue
                try:
                    raw = session.get(info.get('thumburl',info['url']),timeout=45)
                    raw.raise_for_status()
                    im = Image.open(io.BytesIO(raw.content)).convert('RGB')
                    im.thumbnail((400,400))
                    im.save(assets/f'{identity}.jpg', quality=90)
                    ImageEnhance.Brightness(im).enhance(.85).save(assets/f'{identity}-query.jpg',quality=85)
                except Exception as exc:
                    self.stderr.write(f'Skipping {identity}: {exc}')
                    continue
                caption = re.sub(r'\.(jpg|jpeg|png|webp)$','',page['title'].removeprefix('File:'),flags=re.I)
                objects.append({'id':identity,'category':category,'description':caption,
                    'image':f'images/{identity}.jpg','query_image':f'images/{identity}-query.jpg',
                    'source':info.get('descriptionurl'),'download':info.get('thumburl',info['url']),
                    'license':license_name,'license_url':meta.get('LicenseUrl',{}).get('value',''),
                    'artist':meta.get('Artist',{}).get('value',''),'credit':meta.get('Credit',{}).get('value',''),
                    'sha256':hashlib.sha256((assets/f'{identity}.jpg').read_bytes()).hexdigest(),
                    'split':'validation' if accepted<5 else 'test'})
                accepted += 1
                self.stdout.write(f'{category}: {accepted}/10')
                if accepted == 10:
                    break
            if accepted < 10:
                (root/'sources.partial.json').write_text(json.dumps(objects,indent=2),encoding='utf-8')
                raise CommandError(f'Only {accepted} licensed images available for {category}. Partial manifest retained.')
        pairs = []
        for obj in objects:
            pool = [o for o in objects if o['split']==obj['split'] and o['id']!=obj['id']]
            hard = next(o for o in pool if o['category']==obj['category'])
            easy = next(o for o in pool if o['category']!=obj['category'])
            query = {'id':obj['id'], 'description':f"Missing {obj['description']}. Please help locate it.",
                     'category':obj['category'],'location':'library','date':'2026-08-01',
                     'brand':'','color':'','image':obj['query_image']}
            for candidate in (obj,hard,easy):
                pairs.append({'query_id':obj['id'],'split':obj['split'],'label':int(candidate['id']==obj['id']),
                    'query':query,'candidate':{'id':candidate['id'], 'description':f"Found: {candidate['description']}",
                    'category':candidate['category'],'location':'library','date':'2026-08-02',
                    'brand':'','color':'','image':candidate['image']}})
        payload = {'dataset_kind':'constructed','provenance':'Project-constructed source-identity pilot; automatic labels, not human-reviewed campus reports.',
                   'limitations':['Positive pairs reuse the same photograph with brightness/compression changes.',
                     'Source titles generate descriptions; lexical overlap makes this an optimistic sanity benchmark.',
                     'Negatives use different source files, which are not guaranteed different physical objects.',
                     'Do not interpret metrics as real-world accuracy.'], 'sources':objects,'pairs':pairs}
        (root/'dataset.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Created {len(pairs)} labelled pairs with attribution.'))
