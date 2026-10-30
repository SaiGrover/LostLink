from django.test import SimpleTestCase
from datetime import date, datetime
from matching.engine import date_score, combine, image_similarity
from matching.management.commands.evaluate_hybrid import metrics


class ScoringTests(SimpleTestCase):
    def test_missing_image_reweights_without_score_ceiling(self):
        self.assertAlmostEqual(combine(dict(text=1,image=0,location=1,category=1,date=1,attribute=1),False),1)

    def test_dates_accept_model_default_and_database_values(self):
        self.assertEqual(date_score(datetime(2026,1,1),date(2026,1,1)),1)
        self.assertAlmostEqual(date_score(date(2026,1,1),date(2026,1,11)),.5)

    def test_incompatible_vectors_do_not_match(self):
        self.assertEqual(image_similarity([1,0],[1,0,0]),0)

    def test_retrieval_metrics_include_negatives_and_ranking(self):
        rows=[{'query_id':'q','candidate':{'id':str(i)},'label':y} for i,y in enumerate([0,1,0])]
        result=metrics(rows,[.9,.8,.1],.7)
        self.assertEqual(result['precision'],.5)
        self.assertEqual(result['recall'],1)
        self.assertEqual(result['mrr'],.5)
