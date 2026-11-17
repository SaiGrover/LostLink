"""Grandfather accounts that existed before email verification was introduced.

Policy: every account present when this migration runs keeps working. Its
``email_verified_at`` is set to the migration time and
``email_verification_method`` to ``legacy`` so these accounts stay
distinguishable from ones verified by link (admins can filter on it in the
Django admin and ask legacy users to re-confirm later). Changing the email
address of a legacy account clears the mark like any other.

Reverse: only the ``legacy`` marks written here are removed.
"""
from django.db import migrations
from django.utils import timezone


def mark_legacy(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(email_verified_at__isnull=True).update(
        email_verified_at=timezone.now(), email_verification_method='legacy')


def unmark_legacy(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(email_verification_method='legacy').update(
        email_verified_at=None, email_verification_method='')


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_email_verification'),
    ]

    operations = [
        migrations.RunPython(mark_legacy, unmark_legacy),
    ]
