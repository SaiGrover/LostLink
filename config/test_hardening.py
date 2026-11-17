"""Production configuration guards, error pages and the mobile tab-bar CSS."""
import os
import re
import subprocess
import sys

from django.conf import settings
from django.test import Client, RequestFactory, TestCase
from django.views import defaults

GOOD_KEY = 'Zq8#vB2!zL7@pR4^tY8&wN1*mK3$hJ6%gF9(dS0)aQ5-xC7+eV2=bN4_uI8~oP1'
PROD_ENV = {'DJANGO_DEBUG': 'false', 'DJANGO_SECRET_KEY': GOOD_KEY,
            'DATABASE_URL': 'postgres://u:p@db.internal:5432/lostlink',
            'DJANGO_ALLOWED_HOSTS': 'lostlink.example.edu',
            'DJANGO_CSRF_TRUSTED_ORIGINS': 'https://lostlink.example.edu'}
PROBE = ('import config.settings as s; print(s.DEBUG, s.SECURE_SSL_REDIRECT, s.SESSION_COOKIE_SECURE, '
         's.CSRF_COOKIE_SECURE, s.SECURE_HSTS_SECONDS > 0, s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS)')


def load_settings(probe=PROBE, **overrides):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('DJANGO_', 'LOSTLINK_SQLITE')) and k not in ('DATABASE_URL', 'RENDER')}
    env.update({k: v for k, v in overrides.items() if v is not None})
    return subprocess.run([sys.executable, '-c', probe], cwd=settings.BASE_DIR, env=env,
                          capture_output=True, text=True, timeout=60)


class ProductionSettingsTests(TestCase):
    def test_valid_production_environment(self):
        result = load_settings(**PROD_ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         "False True True True True ['lostlink.example.edu'] ['https://lostlink.example.edu']")

    def test_unsafe_production_environments_refuse_to_start(self):
        cases = {
            'missing key': {'DJANGO_SECRET_KEY': None},
            'short key': {'DJANGO_SECRET_KEY': 'too-short'},
            'insecure dev key': {'DJANGO_SECRET_KEY': 'django-insecure-' + 'a1b2c3d4e5' * 5},
            'no database': {'DATABASE_URL': None},
            'no hosts': {'DJANGO_ALLOWED_HOSTS': None},
            'wildcard host': {'DJANGO_ALLOWED_HOSTS': '*'},
            'http origin': {'DJANGO_CSRF_TRUSTED_ORIGINS': 'http://lostlink.example.edu'},
        }
        for label, change in cases.items():
            with self.subTest(label):
                env = {**PROD_ENV, **change}
                result = load_settings(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('RuntimeError', result.stderr)

    def test_debug_must_be_explicit_on_render(self):
        probe = 'import config.settings as s; print(s.DEBUG)'
        result = load_settings(probe, RENDER='true')
        self.assertIn('Set DJANGO_DEBUG=false on Render', result.stderr)
        result = load_settings(probe, RENDER='true', DJANGO_DEBUG='true')
        self.assertEqual((result.returncode, result.stdout.strip()), (0, 'True'), result.stderr)

    def test_sqlite_path_override(self):
        probe = 'import config.settings as s; print(s.DATABASES["default"]["NAME"])'
        env = {k: v for k, v in os.environ.items() if k not in ('DATABASE_URL', 'DJANGO_DEBUG')}
        env['LOSTLINK_SQLITE_PATH'] = os.path.join(str(settings.BASE_DIR), 'tmp-smoke.sqlite3')
        result = subprocess.run([sys.executable, '-c', probe], cwd=settings.BASE_DIR, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertTrue(result.stdout.strip().endswith('tmp-smoke.sqlite3'), result.stderr)


class ErrorPageTests(TestCase):
    def test_custom_error_pages_render_without_internals(self):
        from django.contrib.auth.models import AnonymousUser
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        for view, status, text in ((defaults.bad_request, 400, "didn't make sense"),
                                   (defaults.page_not_found, 404, "isn't here"),
                                   (defaults.server_error, 500, 'went wrong on our side')):
            with self.subTest(status=status):
                if view is defaults.server_error:
                    response = view(request)
                else:
                    response = view(request, Exception('internal detail /srv/app/secret.py'))
                body = response.content.decode()
                self.assertEqual(response.status_code, status)
                self.assertIn(text, body)
                self.assertIn('error-page', body)
                self.assertNotIn('secret.py', body)
                self.assertNotIn('Traceback', body)

    def test_csrf_failure_page(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post('/accounts/login/', {'username': 'x', 'password': 'y'})
        self.assertEqual(response.status_code, 403)
        self.assertIn('That form has expired', response.content.decode())
        self.assertNotIn('CSRF cookie not set', response.content.decode())

    def test_403_page_for_permission_denied(self):
        from config.testing import make_taxonomy, make_users
        from items.models import Item
        tax, users = make_taxonomy(), make_users()
        item = Item.objects.create(reporter=users['owner'], report_type='lost', title='Wallet',
                                   description='Black wallet', category=tax['bags'], location=tax['library'])
        self.client.force_login(users['other'])
        response = self.client.get(item.get_absolute_url())
        self.assertEqual(response.status_code, 403)
        self.assertIn('error-page', response.content.decode())


class MobileNavigationCssTests(TestCase):
    """The base `.app main` rule must not come after the tab-bar media query,
    or (same specificity) it cancels the mobile bottom clearance."""

    def test_tabbar_clearance_wins_the_cascade(self):
        css = (settings.BASE_DIR / 'static/css/layout.css').read_text(encoding='utf-8')
        css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
        media_start = css.index('@media (max-width: 1023px)', css.index('.tabbar { display: none; }'))
        base_rules = [m.start() for m in re.finditer(r'(?m)^\.app main\s*\{', css)]
        self.assertEqual(len(base_rules), 1, 'exactly one top-level .app main rule expected')
        self.assertLess(base_rules[0], media_start)
        block = css[media_start:css.index('\n}', media_start)]
        self.assertRegex(block, r'\.app main\s*\{\s*padding-bottom:\s*calc\(var\(--tabbar-h\)')
        for name in ('base.css', 'components.css', 'landing.css', 'tokens.css'):
            other = (settings.BASE_DIR / 'static/css' / name).read_text(encoding='utf-8')
            self.assertNotRegex(other, r'\.app main\s*\{[^}]*padding-bottom', name)
