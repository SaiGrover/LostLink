"""Evaluate text-only retrieval on a CSV without touching operational data.

Columns: ``query_id,query,candidate,label,split`` (optional ``query_title``,
``candidate_title``). Text similarity is the production pairwise text
function (``engine.text_similarity`` / the active text-model release), so a
candidate's score never depends on the other candidates of its query.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from matching import textmodel
from matching.embeddings import configuration
from matching.engine import text_similarity
from matching.evaluation import metrics


class Command(BaseCommand):
    help = 'Evaluate CSV query_id,query,candidate,label,split using the selected text model.'

    def add_arguments(self, parser):
        parser.add_argument('dataset')
        parser.add_argument('--threshold', type=float, default=.7)
        parser.add_argument('--split', choices=['validation', 'test'], default='test')

    def handle(self, dataset, threshold, split, **options):
        with Path(dataset).open(encoding='utf-8', newline='') as stream:
            rows = list(csv.DictReader(stream))
        if not rows or not {'query_id', 'query', 'candidate', 'label', 'split'} <= rows[0].keys():
            raise CommandError('Required columns: query_id,query,candidate,label,split')
        seen = defaultdict(set)
        for row in rows:
            seen[row['query_id']].add(row['split'])
            if row['label'] not in ('0', '1'):
                raise CommandError('Labels must be 0 or 1.')
        if any(len(parts) > 1 for parts in seen.values()):
            raise CommandError('Query leakage across dataset splits.')
        selected = [r for r in rows if r['split'] == split]
        if not selected:
            raise CommandError(f'No rows in split {split!r}.')
        scores = []
        for row in selected:
            query = f"{row.get('query_title', '')} {row['query']}".strip()
            candidate = f"{row.get('candidate_title', '')} {row['candidate']}".strip()
            scores.append(text_similarity(query, [candidate])[0])
        records = [{'query_id': r['query_id'], 'candidate': {'id': f'{i}'}, 'label': int(r['label'])}
                   for i, r in enumerate(selected)]
        result = metrics(records, scores, threshold)
        self.stdout.write(json.dumps({
            'scope': 'text retrieval only; not full hybrid evaluation',
            'models': configuration(), 'text_release': textmodel.release_info(),
            'split': split, **result}, indent=2, default=str))
