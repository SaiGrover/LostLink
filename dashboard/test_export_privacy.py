"""Exports and JSON endpoints: formula-injection protection and field privacy."""
import base64
import csv
import io
import re
import zlib

from django.test import TestCase
from django.urls import reverse

from claims.models import Claim, VerificationQuestion
from config.testing import isolated_media, make_taxonomy, make_users, png_upload
from dashboard.views import _csv_safe
from items.models import Item

SECRETS = ('owner@x.edu', 'finder@x.edu', '+91 90000 00001', '98765 43210', 'claimant-secret@mail.test',
           'The answer is a red fox sticker', 'Olive', 'pass-word-123', 'inside: my library card')


def pdf_text(data):
    """Raw PDF bytes plus every inflated content stream, for searching."""
    chunks = [data]
    for stream in re.findall(rb'stream\r?\n(.*?)\s*endstream', data, re.S):
        try:
            if stream.rstrip().endswith(b'~>'):  # ReportLab: /ASCII85Decode /FlateDecode
                stream = base64.a85decode(stream.rstrip(), adobe=True)
            chunks.append(zlib.decompress(stream))
        except (ValueError, zlib.error):
            pass
    return b'\n'.join(chunks).decode('latin-1')


class CsvSafeTests(TestCase):
    def test_formula_prefixes_are_neutralised(self):
        for value in ('=HYPERLINK("http://x","y")', '+1+1', '-2+3', '@SUM(A1)', '\t=1', '\r=1', '\n=1',
                      '  =cmd|" /C calc"!A0', ' @x'):
            with self.subTest(value=value):
                self.assertEqual(_csv_safe(value), "'" + value)

    def test_ordinary_values_are_unchanged(self):
        for value in ('Black wallet', 'iPhone 13', '', 'a=b', 42, -3, None, 'café'):
            self.assertEqual(_csv_safe(value), value)


@isolated_media
class ExportPrivacyTests(TestCase):
    def setUp(self):
        self.tax = make_taxonomy()
        self.users = make_users()
        self.users['owner'].phone = '98765 43210'
        self.users['owner'].save()
        self.item = Item.objects.create(
            reporter=self.users['finder'], report_type='found', title='=HYPERLINK("http://evil","click")',
            description='Wallet, inside: my library card', category=self.tax['bags'],
            location=self.tax['library'], status='claimed', brand='+SUM(1)', color='@black',
            image=png_upload(), photo_sensitive=True)
        claim = Claim.objects.create(item=self.item, claimant=self.users['owner'], statement='It is mine',
                                     contact_phone='98765 43210', contact_email='claimant-secret@mail.test')
        VerificationQuestion.objects.create(claim=claim, text='What is on it?',
                                            answer='The answer is a red fox sticker', asked_by=self.users['mod'])

    def test_exports_are_admin_only(self):
        for user in (None, 'owner', 'mod'):
            self.client.logout()
            if user:
                self.client.force_login(self.users[user])
            for name in ('dashboard:export_csv', 'dashboard:export_pdf'):
                response = self.client.get(reverse(name))
                self.assertIn(response.status_code, (302, 403), (user, name))
                self.assertNotIn('text/csv', response.get('Content-Type', ''))

    def test_csv_is_injection_safe_and_minimal(self):
        self.client.force_login(self.users['admin'])
        response = self.client.get(reverse('dashboard:export_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response['Content-Disposition'])
        body = response.content.decode('utf-8')
        rows = list(csv.reader(io.StringIO(body)))
        header, row = rows[0], dict(zip(rows[0], rows[1]))
        self.assertTrue(row['Title'].startswith("'="))
        self.assertTrue(row['Brand'].startswith("'+"))
        self.assertTrue(row['Color'].startswith("'@"))
        self.assertEqual(row['Reporter ID'], str(self.users['finder'].pk))
        self.assertEqual((row['Has photo'], row['Sensitive photo']), ('yes', 'yes'))
        for column in ('Description', 'Email', 'Phone', 'Reporter', 'Username', 'Statement', 'Answer'):
            self.assertNotIn(column, header)
        for secret in SECRETS + ('finder',):
            self.assertNotIn(secret, body)
        self.assertNotIn('/media/', body)
        for cell in (c for r in rows for c in r):
            self.assertFalse(cell[:1] in ('=', '+', '-', '@', '\t', '\r'), cell)

    def test_pdf_excludes_personal_details(self):
        self.client.force_login(self.users['admin'])
        response = self.client.get(reverse('dashboard:export_pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        text = pdf_text(response.content)
        self.assertIn('Key figures', text)
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def test_bad_pdf_period_gives_a_plain_400(self):
        self.client.force_login(self.users['admin'])
        response = self.client.get(reverse('dashboard:export_pdf'), {'month': '2026-13'})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('Traceback', response.content.decode())

    def test_json_endpoints_carry_no_personal_details(self):
        self.client.force_login(self.users['owner'])
        count = self.client.get(reverse('notifications:unread_count'))
        self.assertEqual(set(count.json()), {'unread'})
        self.client.logout()
        payload = self.client.get(reverse('items:search_api')).content.decode()
        for secret in SECRETS:
            self.assertNotIn(secret, payload)

    def test_analytics_page_shows_aggregates_only(self):
        self.client.force_login(self.users['admin'])
        page = self.client.get(reverse('dashboard:analytics')).content.decode()
        for secret in SECRETS:
            self.assertNotIn(secret, page)
