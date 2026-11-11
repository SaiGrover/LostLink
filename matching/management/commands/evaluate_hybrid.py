import hashlib
import json
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from sklearn.metrics import precision_recall_fscore_support, roc_auc_score
from matching import engine
from matching.embeddings import (CLIP_REVISION, RESNET_REVISION, image_vector,
                                 text_vectors)
from matching.features import manifest
from PIL import Image


def metrics(rows, scores, threshold):
    labels = [r['label'] for r in rows]
    p,r,f,_ = precision_recall_fscore_support(labels,[s>=threshold for s in scores],average='binary',zero_division=0)
    groups = defaultdict(list)
    for row, score in zip(rows,scores):
        groups[row['query_id']].append((score,row['candidate']['id'],row['label']))
    rr, pk = [], []
    for group in groups.values():
        ranked = sorted(group,key=lambda x:(-x[0],x[1]))
        rr.append(next((1/i for i,entry in enumerate(ranked,1) if entry[2]),0))
        pk.append(sum(entry[2] for entry in ranked[:3])/3)
    return {'precision':float(p),'recall':float(r),'f1':float(f),
            'roc_auc':float(roc_auc_score(labels,scores)), 'mrr':sum(rr)/len(rr),
            'precision_at_3':sum(pk)/len(pk), 'pairs':len(rows)}


class Command(BaseCommand):
    help = 'Compare TF-IDF/SBERT and ResNet/CLIP, tune on validation only, evaluate untouched test pairs.'

    def add_arguments(self, parser):
        parser.add_argument('dataset')
        parser.add_argument('--output',default='evaluation/results.json')
        parser.add_argument('--text',nargs='+',default=['tfidf','sbert'])
        parser.add_argument('--image',nargs='+',default=['resnet','clip'])
        parser.add_argument('--apply',action='store_true',help='Activate the validation-selected model profile; rebuild operational features afterwards.')

    def handle(self,dataset,output,text,image,**options):
        if options.get('apply'):
            from matching.research import validate_dataset
            summary = validate_dataset(dataset)
            if not summary['research_ready']:
                raise CommandError('Profile activation requires reviewed research data: ' + '; '.join(summary['errors']))
        import torch
        torch.set_num_threads(2)
        path = Path(dataset)
        raw = path.read_bytes()
        payload = json.loads(raw)
        pairs = payload['pairs']
        membership = defaultdict(set)
        for row in pairs:
            if row['label'] not in (0,1):
                raise CommandError('Labels must be binary.')
            for field in ('query','candidate'):
                membership[row[field]['id']].add(row['split'])
        if any(len(s)>1 for s in membership.values()):
            raise CommandError('Physical/source identity leaks across splits.')
        for split in ('validation','test'):
            if set(r['label'] for r in pairs if r['split']==split)!={0,1}:
                raise CommandError('Both splits need both classes.')
        feature_cache = path.parent/'features'
        feature_cache.mkdir(exist_ok=True)
        cache = {}
        def vector(record, kind):
            image_path = path.parent/record['image']
            revision = RESNET_REVISION if kind == 'resnet' else CLIP_REVISION
            # Image embeddings depend on the source bytes and model revision,
            # not on hybrid weights or notification thresholds.
            key = hashlib.sha256(
                image_path.read_bytes() + kind.encode() + revision.encode()).hexdigest()
            if key not in cache:
                file = feature_cache/f'{key}.json'
                if file.exists():
                    cache[key]=json.loads(file.read_text())
                else:
                    with Image.open(image_path) as im:
                        cache[key]=image_vector(im.convert('RGB'),kind)
                    file.write_text(json.dumps(cache[key]))
            return cache[key]
        text_cache = {}
        if 'sbert' in text:
            descriptions = sorted({r[k]['description'] for r in pairs for k in ('query','candidate')})
            text_cache = dict(zip(descriptions,text_vectors(descriptions)))
        experiments = []
        original = os.environ.get('LOSTLINK_TEXT_MODEL')
        os.environ['LOSTLINK_TEXT_MODEL']='tfidf'
        try:
            for txt in text:
                for img in image:
                    self.stdout.write(f'Evaluating {txt} + {img}...',ending='\n')
                    components = []
                    for row in pairs:
                        a,b = row['query'],row['candidate']
                        ts = engine.image_similarity(text_cache[a['description']],text_cache[b['description']]) if txt=='sbert' else engine.text_similarity(a['description'],[b['description']])[0]
                        components.append([ts,engine.calibrated_image_similarity(vector(a,img),vector(b,img),img),
                            float(a['location']==b['location']),float(a['category']==b['category']),
                            engine.date_score(date.fromisoformat(a['date']),date.fromisoformat(b['date'])),
                            engine.attribute_score(a['brand'],a['color'],b['brand'],b['color'])])
                    val_idx=[i for i,r in enumerate(pairs) if r['split']=='validation']
                    test_idx=[i for i,r in enumerate(pairs) if r['split']=='test']
                    best=None
                    for tw in (.2,.3,.4):
                        weights=[tw,.6-tw,.15,.1,.1,.05]
                        scores=[sum(c*w for c,w in zip(comp,weights)) for comp in components]
                        for threshold in (.6,.65,.7,.75,.8,.85,.9):
                            result=metrics([pairs[i] for i in val_idx],[scores[i] for i in val_idx],threshold)
                            key=(result['f1'],result['precision'],result['mrr'],threshold)
                            if best is None or key>best[0]:
                                best=(key,weights,threshold,scores,result)
                    _,weights,threshold,scores,val=best
                    test=metrics([pairs[i] for i in test_idx],[scores[i] for i in test_idx],threshold)
                    experiments.append({'text':txt,'image':img,'weights':dict(zip(['text','image','location','category','date','attribute'],weights)),
                        'threshold':threshold,'validation':val,'test':test})
                    self.stdout.write(json.dumps(experiments[-1]))
        finally:
            if original is None: os.environ.pop('LOSTLINK_TEXT_MODEL',None)
            else: os.environ['LOSTLINK_TEXT_MODEL']=original
        selected=max(experiments,key=lambda e:(e['validation']['f1'],e['validation']['precision'],e['validation']['mrr']))
        report={'dataset_sha256':hashlib.sha256(raw).hexdigest(),'provenance':payload.get('provenance'),
                'limitations':payload.get('limitations',[]),'model_versions':manifest(),
                'experiments':experiments,'selected_by_validation':selected}
        target=Path(output)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,indent=2),encoding='utf-8')
        if options.get('apply'):
            from django.conf import settings
            profile={'version':'tuned-'+hashlib.sha256(raw).hexdigest()[:10],
                'text':selected['text'],'image':selected['image'],'weights':selected['weights'],
                'medium':selected['threshold'],'high':min(.99,max(.85,selected['threshold']+.1)),
                'selection':'Validation-only selection; see evaluation report and dataset limitations.'}
            (settings.BASE_DIR/'matching'/'profile.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
            self.stdout.write('Profile activated. Restart the app and run rebuild_matches before serving traffic.')
        self.stdout.write(self.style.SUCCESS(str(target)))
