"""Move legacy ``disputed`` claim statuses onto the dispute model.

Before this change a dispute *replaced* the claim status, which released an
approved claim's ownership reservation. Each disputed claim gets its real
verification status back (approved if the audit log shows an approval that
was never reversed) and an open ``ClaimDispute`` carrying the recorded reason.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Claim = apps.get_model('claims', 'Claim')
    ClaimDispute = apps.get_model('claims', 'ClaimDispute')
    Decision = apps.get_model('dashboard', 'ModerationDecision')
    for claim in Claim.objects.filter(status='disputed'):
        log = list(Decision.objects.filter(claim_id=claim.pk).order_by('created_at')
                   .values_list('action', 'actor_id'))
        approved = False
        for action, _ in log:
            if action == 'claim_approved':
                approved = True
            elif action == 'claim_rejected':
                approved = False
        taken = Claim.objects.filter(item_id=claim.item_id, status__in=['approved', 'returned']) \
            .exclude(pk=claim.pk).exists()
        if approved and not taken:
            status = 'approved'
        else:
            answers = list(claim.questions.values_list('answer', flat=True))
            status = 'answered' if answers and all(answers) else 'questions' if answers else 'submitted'
        raised_by = next((actor for action, actor in reversed(log) if action == 'dispute_raised'), None)
        Claim.objects.filter(pk=claim.pk).update(status=status)
        ClaimDispute.objects.create(claim_id=claim.pk, raised_by_id=raised_by,
                                    reason=(claim.decision_note or 'Dispute raised before the upgrade.')[:500])


class Migration(migrations.Migration):
    dependencies = [
        ('claims', '0006_claim_appeal_count_claim_handover_method_and_more'),
        ('dashboard', '0002_moderationdecision_from_state_and_more'),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
