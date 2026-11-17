"""First-run deployment smoke test against a brand-new, empty database.

Proves that a fresh install is usable without ``seed_demo``: migrate,
``seed_taxonomy``, accounts, a lost and a found report (category and campus
zone required), moderator approval, matching, a verified claim through to the
recorded handover, notifications and photo access rules.

    python scripts/smoke_first_run.py           # temporary SQLite + media, removed afterwards
    python scripts/smoke_first_run.py --keep    # keep the temporary directory for inspection

It never touches ``db.sqlite3`` or a ``DATABASE_URL`` database: it refuses to
run while DATABASE_URL is set. Lightweight models (TF-IDF + colour histogram)
are used unless ``--profile-models`` is given. Outgoing mail goes to Django's
in-memory backend, so no SMTP server is contacted.

Every step is a function in ``STEPS``; ``status`` is one of
  ok        - ran and passed
  pending   - owned by another workstream and not built yet (listed, not run)
  external  - needs a real external service (SMTP); not run here
To add a step (e.g. registration with e-mail verification once it exists),
write ``def step_x(ctx)`` and insert it into ``STEPS`` at the right point.
The test suite runs the same flow (items/test_first_run.py).
"""
import argparse
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PASSWORD = 'Smoke-test-pass-9241'


class SmokeFailure(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise SmokeFailure(message)


def _photo(color, size=(320, 240)):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image, ImageDraw
    img = Image.new('RGB', size, color)
    ImageDraw.Draw(img).rectangle([90, 60, 230, 180], fill=(230, 200, 40))
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return SimpleUploadedFile('phone-photo.jpg', buf.getvalue(), content_type='image/jpeg')


def _client(user=None):
    from django.test import Client
    client = Client()
    if user is not None:
        client.force_login(user)
    return client


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def step_seed_taxonomy(ctx):
    from django.core.management import call_command

    from items.models import Category, Location
    call_command('seed_taxonomy', stdout=io.StringIO())
    call_command('seed_taxonomy', stdout=io.StringIO())  # idempotent
    check(Category.objects.count() >= 10 and Location.objects.count() >= 10, 'default taxonomy missing')
    ctx['category'] = Category.objects.get(slug='electronics')
    ctx['zone'] = Location.objects.get(slug='cafeteria')
    return f'{Category.objects.count()} categories, {Location.objects.count()} zones'


def step_register_and_verify_email(ctx):
    return None  # pending: self-service registration + e-mail verification (accounts workstream)


def step_create_accounts(ctx):
    from accounts.models import User
    ctx['finder'] = User.objects.create_user('smoke_finder', 'finder@campus.test', PASSWORD, first_name='Fin')
    ctx['owner'] = User.objects.create_user('smoke_owner', 'owner@campus.test', PASSWORD, first_name='Olu')
    ctx['moderator'] = User.objects.create_user('smoke_mod', 'mod@campus.test', PASSWORD, role='moderator')
    client = _client()
    check(client.login(username='smoke_owner', password=PASSWORD), 'password login failed')
    return 'student x2 + moderator created; password login works'


def step_report_requires_taxonomy(ctx):
    from items.models import Item
    client = _client(ctx['owner'])
    response = client.post('/report/', {'report_type': 'lost', 'title': 'Phone', 'event_date': '',
                                        'description': 'Blue phone with a cracked case'})
    check(response.status_code == 200 and Item.objects.count() == 0,
          'a report without category/zone was accepted')
    return 'report without category/zone rejected'


def _report(ctx, user, rtype, title, photo_color):
    from django.utils import timezone

    from items.models import Item
    client = _client(user)
    response = client.post('/report/', {
        'report_type': rtype, 'title': title, 'category': ctx['category'].pk,
        'location': ctx['zone'].pk, 'event_date': timezone.localdate().isoformat(),
        'description': 'Blue Apple iPhone in a clear case with a cracked corner and a sticker',
        'brand': 'Apple', 'color': 'Blue', 'image': _photo(photo_color)})
    item = Item.objects.filter(reporter=user, title=title).first()
    check(response.status_code == 302 and item is not None, f'{rtype} report was not created')
    check(item.status == Item.Status.PENDING, 'new report is not pending review')
    check(item.image and item.image.name.endswith('.jpg') and 'phone-photo' not in item.image.name,
          'uploaded photo was not re-encoded under a random name')
    return item


def step_submit_reports(ctx):
    ctx['found'] = _report(ctx, ctx['finder'], 'found', 'Blue iPhone found in cafeteria', (40, 90, 200))
    ctx['lost'] = _report(ctx, ctx['owner'], 'lost', 'Lost my blue iPhone', (42, 92, 198))
    anon = _client()
    check(anon.get(ctx['found'].image.url).status_code == 403, 'pending photo visible to the public')
    return 'found + lost reports pending review, photos private'


def step_moderation_approval(ctx):
    from items.models import Item
    mod = _client(ctx['moderator'])
    for item in (ctx['found'], ctx['lost']):
        response = mod.post(f'/manage/moderation/report/{item.pk}/approve/', {'note': ''})
        check(response.status_code == 302, 'approval request failed')
        item.refresh_from_db()
        check(item.status == Item.Status.APPROVED, 'report not approved')
    check(_client().get(ctx['found'].image.url).status_code == 200, 'approved photo not public')
    return 'both reports approved and public'


def step_matching(ctx):
    from matching.models import Match
    match = Match.objects.filter(lost_item=ctx['lost'], found_item=ctx['found']).first()
    check(match is not None, 'no match stored between the two reports')
    ctx['match'] = match
    return f'match score {match.score:.2f} ({match.tier})'


def step_claim_and_handover(ctx):
    from claims.models import Claim
    from items.models import Item
    owner, finder, mod = _client(ctx['owner']), _client(ctx['finder']), _client(ctx['moderator'])
    response = owner.post(f'/claims/start/{ctx["found"].pk}/?match={ctx["match"].pk}',
                          {'statement': 'It is mine: the sticker is a red fox and the lock screen is a beach.',
                           'contact_phone': '', 'contact_email': ''})
    claim = Claim.objects.filter(item=ctx['found'], claimant=ctx['owner']).first()
    check(response.status_code == 302 and claim is not None, 'claim not created')
    finder.post(f'/claims/{claim.pk}/question/', {'text': 'What colour is the sticker?'})
    question = claim.questions.first()
    check(question is not None, 'verification question not saved')
    owner.post(f'/claims/{claim.pk}/answer/{question.pk}/', {'answer': 'Red, a fox'})
    mod.post(f'/claims/{claim.pk}/decide/approve/', {'note': 'Answers match.'})
    claim.refresh_from_db()
    check(claim.status == Claim.Status.APPROVED, f'claim not approved (status {claim.status})')
    mod.post(f'/claims/{claim.pk}/return/', {'note': ''})
    claim.refresh_from_db()
    ctx['found'].refresh_from_db()
    check(claim.status == Claim.Status.RETURNED and ctx['found'].status == Item.Status.RETURNED,
          'handover not recorded')
    ctx['claim'] = claim
    return 'claim questioned, answered, approved and handed over'


def step_handover_otp(ctx):
    return None  # pending: one-time handover code (claims workstream)


def step_notifications(ctx):
    from notifications.models import Notification
    kinds = set(Notification.objects.filter(user=ctx['finder']).values_list('kind', flat=True))
    check({'report', 'claim'} <= kinds, f'finder notifications incomplete: {sorted(kinds)}')
    check(Notification.objects.filter(user=ctx['owner'], kind='match').exists()
          or Notification.objects.filter(user=ctx['finder'], kind='match').exists(),
          'no match notification')
    count = _client(ctx['owner']).get('/notifications/unread-count/')
    check(count.status_code == 200 and set(count.json()) == {'unread'}, 'unread-count endpoint failed')
    return f'{Notification.objects.count()} notifications recorded'


def step_email_delivery(ctx):
    return None  # external: real SMTP delivery (verification, password reset) needs a mail server


STEPS = [
    ('seed_taxonomy', step_seed_taxonomy, None),
    ('register_and_verify_email', step_register_and_verify_email, 'pending'),
    ('create_accounts', step_create_accounts, None),
    ('report_requires_taxonomy', step_report_requires_taxonomy, None),
    ('submit_reports', step_submit_reports, None),
    ('moderation_approval', step_moderation_approval, None),
    ('matching', step_matching, None),
    ('claim_and_handover', step_claim_and_handover, None),
    ('handover_otp', step_handover_otp, 'pending'),
    ('notifications', step_notifications, None),
    ('password_reset_email', step_email_delivery, 'external'),
]


def run_flow(out=None):
    """Run every runnable step in order; returns [(name, status, detail)].
    Raises SmokeFailure naming the first step that fails."""
    ctx, results = {}, []
    for name, func, fixed_status in STEPS:
        if fixed_status:
            results.append((name, fixed_status, ''))
        else:
            try:
                detail = func(ctx)
            except SmokeFailure as exc:
                raise SmokeFailure(f'{name}: {exc}') from exc
            results.append((name, 'ok', detail or ''))
        if out is not None:
            out.write(f'  {results[-1][1]:<9} {name:<28} {results[-1][2]}\n')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--keep', action='store_true', help='Keep the temporary database and media.')
    parser.add_argument('--profile-models', action='store_true',
                        help='Use the configured matching models instead of TF-IDF + histogram.')
    args = parser.parse_args()
    if os.environ.get('DATABASE_URL'):
        sys.exit('DATABASE_URL is set; unset it so the smoke test cannot touch a real database.')

    workdir = Path(tempfile.mkdtemp(prefix='lostlink-smoke-'))
    os.environ['LOSTLINK_SQLITE_PATH'] = str(workdir / 'db.sqlite3')
    os.environ['LOSTLINK_MEDIA_ROOT'] = str(workdir / 'media')
    os.environ.setdefault('DJANGO_DEBUG', 'true')
    if not args.profile_models:
        os.environ['LOSTLINK_TEXT_MODEL'] = 'tfidf'
        os.environ['LOSTLINK_IMAGE_MODEL'] = 'histogram'
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    sys.path.insert(0, str(ROOT))

    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection
    from django.test.utils import setup_test_environment
    setup_test_environment()  # in-memory mail backend + 'testserver' host

    print(f'LostLink first-run smoke test in {workdir}')
    try:
        call_command('migrate', verbosity=0)
        print('  ok        migrate')
        results = run_flow(sys.stdout)
    except SmokeFailure as exc:
        print(f'FAILED: {exc}')
        return 1
    finally:
        connection.close()
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
    pending = [name for name, status, _ in results if status != 'ok']
    print(f'PASSED: {sum(1 for r in results if r[1] == "ok") + 1} steps ok; '
          f'not run here: {", ".join(pending) or "none"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
