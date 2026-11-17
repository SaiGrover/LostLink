"""Give closed reports an explicit lifecycle status.

Previously a withdrawn report kept its old status with ``is_active=False``
and a moderator-removed report was ``rejected`` and inactive. They become
``withdrawn`` and ``removed`` so search, analytics and history agree.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Item = apps.get_model('items', 'Item')
    Item.objects.filter(is_active=False, status='rejected').update(status='removed')
    Item.objects.filter(is_active=False, status__in=['pending', 'approved', 'claimed']).update(status='withdrawn')


class Migration(migrations.Migration):
    dependencies = [('items', '0006_item_private_details_alter_item_status')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
