"""Evaluate labelled retrieval candidates without touching operational data."""
import csv
import json
from collections import defaultdict
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from sklearn.metrics import precision_recall_fscore_support, roc_auc_score
from matching.engine import text_similarity
from matching.embeddings import configuration


class Command(BaseCommand):
    help = 'Evaluate CSV query_id,query,candidate,label,split using the selected text model.'

    def add_arguments(self, parser):
        parser.add_argument('dataset')
        parser.add_argument('--threshold', type=float, default=.7)
        parser.add_argument('--split', choices=['validation', 'test'], default='test')

    def handle(self, dataset, threshold, split, **options):
        rows = list(csv.DictReader(Path(dataset).open(encoding='utf-8', newline='')))
        if not rows or not {'query_id','query','candidate','label','split'} <= rows[0].keys():
            raise CommandError('Required columns: query_id,query,candidate,label,split')
        groups = defaultdict(list)
        seen = defaultdict(set)
        for row in rows:
            seen[row['query_id']].add(row['split'])
            if row['split'] == split:
                groups[row['query_id']].append(row)
        if any(len(parts) > 1 for parts in seen.values()):
            raise CommandError('Query leakage across dataset splits.')
        labels, scores, reciprocal, precision_at_3 = [], [], [], []
        for candidates in groups.values():
            values = text_similarity(candidates[0]['query'], [r['candidate'] for r in candidates])
            truth = [int(r['label']) for r in candidates]
            if any(x not in (0, 1) for x in truth):
                raise CommandError('Labels must be 0 or 1.')
            labels.extend(truth)
            scores.extend(values)
            ranked = sorted(zip(values, truth), reverse=True)
            reciprocal.append(next((1/i for i, (_, y) in enumerate(ranked, 1) if y), 0))
            precision_at_3.append(sum(y for _, y in ranked[:3])/3)
        if len(set(labels)) != 2:
            raise CommandError('Evaluation requires positive and negative labels.')
        p, r, f, _ = precision_recall_fscore_support(labels, [s >= threshold for s in scores], average='binary', zero_division=0)
        self.stdout.write(json.dumps({'scope':'text retrieval only; not full hybrid evaluation',
            'models':configuration(), 'split':split, 'pairs':len(labels), 'threshold':threshold,
            'precision':p, 'recall':r, 'f1':f, 'roc_auc':roc_auc_score(labels,scores),
            'mrr':sum(reciprocal)/len(reciprocal), 'precision_at_3':sum(precision_at_3)/len(precision_at_3)}, indent=2))
