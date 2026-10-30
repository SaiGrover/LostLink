import json
from django.core.management.base import BaseCommand
from items.models import Item
from matching.models import Match, ScoreSnapshot
from matching.features import manifest


class Command(BaseCommand):
    help = 'Persist a score distribution snapshot and compare with the previous compatible snapshot.'

    def handle(self, **options):
        config = manifest()
        previous = ScoreSnapshot.objects.filter(configuration=config).first()
        scores = list(Match.objects.filter(configuration=config).values_list('score','tier'))
        bins = [0] * 10
        tiers = {'low':0, 'medium':0, 'high':0}
        for score, tier in scores:
            bins[min(9, int(score*10))] += 1
            tiers[tier] += 1
        snapshot = ScoreSnapshot.objects.create(configuration=config, report_count=Item.objects.count(),
            match_count=len(scores), mean_score=sum(s for s,_ in scores)/max(1,len(scores)), histogram=bins, tiers=tiers)
        drift = None
        if previous and previous.match_count and scores:
            drift = sum(abs(a/previous.match_count-b/len(scores)) for a,b in zip(previous.histogram,bins))/2
        self.stdout.write(json.dumps({'snapshot':snapshot.pk, 'matches':len(scores), 'mean':snapshot.mean_score,
            'tiers':tiers, 'total_variation':drift, 'review_recommended':drift is not None and drift>.2,
            'limitation':'Stored candidate scores only; not accuracy or population-wide drift.'}, indent=2))
