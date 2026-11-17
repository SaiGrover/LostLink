"""Runs the deployment smoke flow (scripts/smoke_first_run.py) against the
empty test database, so the script cannot silently rot."""
import importlib.util
from io import StringIO

from django.conf import settings
from django.test import TestCase

from config.testing import isolated_media, lightweight_models
from items.models import Category, Location


def load_smoke():
    path = settings.BASE_DIR / 'scripts' / 'smoke_first_run.py'
    spec = importlib.util.spec_from_file_location('smoke_first_run', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lightweight_models
@isolated_media
class FirstRunSmokeTests(TestCase):
    def test_first_run_flow_on_an_empty_database(self):
        self.assertFalse(Category.objects.exists() or Location.objects.exists())
        out = StringIO()
        results = load_smoke().run_flow(out)
        statuses = {name: status for name, status, _ in results}
        self.assertEqual(statuses['claim_and_handover'], 'ok')
        self.assertEqual(statuses['notifications'], 'ok')
        self.assertEqual({s for s in statuses.values()} - {'ok', 'pending', 'external'}, set())
        self.assertEqual(statuses['register_and_verify_email'], 'pending')
        self.assertEqual(statuses['password_reset_email'], 'external')
