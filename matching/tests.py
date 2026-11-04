import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from datetime import date, datetime
from matching.engine import clean_text, date_score, combine, image_similarity
from matching.management.commands.evaluate_hybrid import metrics


class ScoringTests(SimpleTestCase):
    def test_missing_image_reweights_without_score_ceiling(self):
        self.assertAlmostEqual(combine(dict(text=1,image=0,location=1,category=1,date=1,attribute=1),False),1)

    def test_dates_accept_model_default_and_database_values(self):
        self.assertEqual(date_score(datetime(2026,1,1),date(2026,1,1)),1)
        self.assertAlmostEqual(date_score(date(2026,1,1),date(2026,1,11)),.5)

    def test_incompatible_vectors_do_not_match(self):
        self.assertEqual(image_similarity([1,0],[1,0,0]),0)

    def test_text_cleaning_includes_conservative_lemmatization(self):
        self.assertEqual(clean_text('The wallets and batteries'), 'wallet battery')
        self.assertEqual(clean_text('Children with watches'), 'child watch')
        self.assertEqual(clean_text('Glasses in cases'), 'glass case')

    def test_retrieval_metrics_include_negatives_and_ranking(self):
        rows=[{'query_id':'q','candidate':{'id':str(i)},'label':y} for i,y in enumerate([0,1,0])]
        result=metrics(rows,[.9,.8,.1],.7)
        self.assertEqual(result['precision'],.5)
        self.assertEqual(result['recall'],1)
        self.assertEqual(result['mrr'],.5)


class ResearchToolingTests(SimpleTestCase):
    def test_discovery_study_analyser_requires_and_compares_both_modes(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'study.csv'
            output = Path(folder) / 'result.json'
            source.write_text(
                'participant_id,task_id,mode,success,seconds\n'
                'p1,t1,manual,1,40\n'
                'p1,t2,ajax,1,20\n', encoding='utf-8')
            call_command('evaluate_discovery_study', str(source), output=str(output))
            result = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['participants'], 1)
            self.assertEqual(result['median_time_reduction_percent'], 50)

    def test_unreviewed_dataset_fails_research_gate(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'pilot.json'
            source.write_text(json.dumps({'pairs': [{
                'query_id': 'q1', 'split': 'validation', 'label': 1,
                'query': {'id': 'q1'}, 'candidate': {'id': 'c1'},
            }]}), encoding='utf-8')
            with self.assertRaises(CommandError):
                call_command('validate_research_dataset', str(source), allow_small=True)


class MaintenanceSchedulerTests(TestCase):
    def test_once_records_monitoring_snapshot(self):
        from matching.models import ScoreSnapshot
        with TemporaryDirectory() as folder:
            call_command('run_maintenance_scheduler', once=True,
                         export_output=str(Path(folder) / 'confirmed.json'))
            self.assertEqual(ScoreSnapshot.objects.count(), 1)
