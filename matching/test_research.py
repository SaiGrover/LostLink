import copy
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from matching.research import validate_dataset


class ResearchValidationTests(SimpleTestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / 'pairs.json'
        self.pairs = []
        for n, split in enumerate(('validation', 'test')):
            records = []
            for i in range(3):
                name = f'{split}-{i}'
                Image.new('RGB', (8, 8), (n * 100 + i * 20, 20, 40)).save(self.root / f'{name}.png')
                records.append({
                    'id': name, 'physical_id': f'{split}-object-{int(i == 2)}',
                    'description': f'Independent test description {name}', 'category': 'bags',
                    'location': 'library', 'date': '2026-01-01', 'brand': '', 'color': '',
                    'image': f'{name}.png',
                })
            for i, label in ((1, 1), (2, 0)):
                self.pairs.append({
                    'query_id': records[0]['id'], 'split': split, 'label': label,
                    'query': records[0], 'candidate': records[i],
                    'review': {'label_source': 'manual', 'status': 'approved',
                               'reviewer': 'reviewer-1', 'independent_photos': True,
                               'independent_descriptions': True},
                })

    def validate(self, pairs=None):
        self.path.write_text(json.dumps({'pairs': self.pairs if pairs is None else pairs}), encoding='utf-8')
        return validate_dataset(self.path, allow_small=True)

    def test_valid_small_fixture_does_not_become_research_evidence(self):
        result = self.validate()
        self.assertTrue(result['checks_passed'], result['errors'])
        self.assertFalse(result['research_ready'])
        self.assertFalse(validate_dataset(self.path)['checks_passed'])

    def test_missing_holdout_and_invalid_records_are_rejected(self):
        cases = [self.pairs[:2], [None], [{'split': [], 'label': {}}]]
        for pairs in cases:
            with self.subTest(pairs=pairs):
                self.assertFalse(self.validate(pairs)['checks_passed'])

    def test_false_labels_duplicate_photos_missing_images_and_unreviewed_data_fail(self):
        cases = [
            ('candidate', 'physical_id', 'other-object'),
            ('candidate', 'image', self.pairs[0]['query']['image']),
            ('candidate', 'image', 'missing.png'),
            ('review', 'independent_photos', 'true'),
            ('review', 'independent_descriptions', False),
        ]
        for section, field, value in cases:
            with self.subTest(field=field, value=value):
                pairs = copy.deepcopy(self.pairs)
                pairs[0][section][field] = value
                self.assertFalse(self.validate(pairs)['checks_passed'])

    def test_physical_identity_leakage_is_rejected_even_with_different_report_ids(self):
        pairs = copy.deepcopy(self.pairs)
        for row in pairs[2:]:
            row['query']['physical_id'] = 'validation-object-0'
        pairs[2]['candidate']['physical_id'] = 'validation-object-0'
        self.assertIn('physical object identities leak', ' '.join(self.validate(pairs)['errors']))

    def test_retuning_and_activation_reject_unreviewed_data_before_model_loading(self):
        self.validate()
        for command, options in (('retune', {}), ('evaluate_hybrid', {'apply': True})):
            with self.subTest(command=command), self.assertRaises(CommandError):
                call_command(command, str(self.path), stdout=StringIO(), **options)

    def test_study_rejects_nonfinite_times_blank_ids_and_duplicate_rows(self):
        source = self.root / 'study.csv'
        valid = 'p1,t1,manual,1,40\np1,t2,ajax,1,20\n'
        for body in (valid.replace('40', 'NaN'), valid.replace('40', 'inf'),
                     valid.replace('p1', ' '), valid + 'p1,t1,manual,1,40\n'):
            with self.subTest(body=body):
                source.write_text('participant_id,task_id,mode,success,seconds\n' + body)
                with self.assertRaises(CommandError):
                    call_command('evaluate_discovery_study', str(source), output=str(self.root / 'result.json'))

    def test_scheduler_rejects_invalid_interval_before_any_cycle(self):
        with patch('matching.management.commands.run_maintenance_scheduler.Command.run_cycle') as cycle:
            for interval in (0, -1, float('nan'), float('inf')):
                with self.assertRaises(CommandError):
                    call_command('run_maintenance_scheduler', interval_hours=interval, once=True)
            cycle.assert_not_called()
