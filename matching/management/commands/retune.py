"""Retune only after research review; engineering evaluation stays separate."""
from django.core.management.base import CommandError
from matching.research import validate_dataset
from .evaluate_hybrid import Command as EvaluationCommand


class Command(EvaluationCommand):
    help = 'Validate reviewed pairs, then select weights on validation and report holdout metrics.'

    def handle(self, dataset, **options):
        summary = validate_dataset(dataset)
        if not summary['research_ready']:
            raise CommandError('Retuning requires reviewed research data: ' + '; '.join(summary['errors']))
        return super().handle(dataset=dataset, **options)
