"""Seed LostLink with demo categories, locations, users, and matching item pairs.

Usage:  python manage.py seed_demo [--fresh] [--allow-production]

Development and demonstration only. It creates accounts with published
passwords, so it refuses to run when DEBUG is off unless explicitly forced
with ``--allow-production`` (for a disposable demo instance only). Real
deployments need just ``python manage.py seed_taxonomy``.

Generates a small, self-consistent demo dataset — including simple colour-coded
placeholder photographs so the image-similarity component of the matching engine
has real pixels to work with — then runs the matching engine so potential matches
exist immediately.
"""
import io
import random

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from claims.models import Claim, VerificationQuestion
from items.models import Item
from items.taxonomy import default_categories, default_locations, ensure_default_taxonomy
from matching.engine import extract_image_vector
from matching.models import Match
from matching.services import run_matching
from notifications.models import Notification

User = get_user_model()

COLOR_MAP = {
    'black': (35, 35, 40), 'blue': (40, 90, 200), 'navy': (25, 40, 90),
    'red': (200, 55, 55), 'silver': (170, 175, 180), 'brown': (110, 70, 40),
    'green': (45, 140, 90), 'grey': (120, 120, 125), 'white': (235, 235, 235),
    'purple': (120, 70, 180),
}

# (report_type, title, category, location, days_ago, brand, color, shape)
PAIRS = [
    # matching lost/found pairs — same colour + shape -> strong image signal
    [('lost', 'Black leather wallet', 'Bags & Wallets', 'Central Library', 4, '', 'black', 'rect'),
     ('found', 'Black wallet made of leather with silver logo', 'Bags & Wallets', 'Central Library', 3, '', 'black', 'rect')],
    [('lost', 'iPhone 13 with blue case', 'Electronics', 'Cafeteria', 2, 'Apple', 'blue', 'rect'),
     ('found', 'Blue Apple phone found on table', 'Electronics', 'Cafeteria', 2, 'Apple', 'blue', 'rect')],
    [('lost', 'Silver steel water bottle', 'Water Bottles', 'Sports Complex', 6, 'Milton', 'silver', 'circle'),
     ('found', 'Metal water bottle, steel colour', 'Water Bottles', 'Sports Complex', 5, '', 'silver', 'circle')],
    [('lost', 'Student ID card', 'ID & Cards', 'Academic Block A', 1, '', 'white', 'rect'),
     ('found', 'College identity card near class', 'ID & Cards', 'Academic Block B', 1, '', 'white', 'rect')],
    [('lost', 'Navy blue backpack', 'Bags & Wallets', 'Academic Block B', 8, 'Wildcraft', 'navy', 'circle'),
     ('found', 'Dark blue bag left in classroom', 'Bags & Wallets', 'Academic Block B', 7, '', 'navy', 'circle')],
    [('lost', 'Bunch of keys with red tag', 'Keys', 'Parking Lot', 3, '', 'red', 'circle'),
     ('found', 'Keys with a red keychain', 'Keys', 'Main Gate', 2, '', 'red', 'circle')],
]

# unmatched singletons for variety
SINGLES = [
    ('lost', 'Black spectacles in a case', 'Eyewear', 'Auditorium', 5, '', 'black', 'rect'),
    ('found', 'Brown leather diary', 'Books & Stationery', 'Central Library', 2, '', 'brown', 'rect'),
    ('lost', 'Silver ring', 'Jewellery', 'Hostel', 9, '', 'silver', 'circle'),
    ('found', 'Green hoodie, size M', 'Clothing', 'Cafeteria', 4, '', 'green', 'rect'),
]


def _emoji_font(size):
    from PIL import ImageFont
    for path in ('C:/Windows/Fonts/seguiemj.ttf',
                 '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf'):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return None


def make_image(color_name, shape, emoji='📦'):
    """Render a clean, recognisable placeholder: a diagonal gradient tinted by
    the item's colour with the category emoji centred on a soft card."""
    from PIL import Image, ImageDraw
    W = H = 320
    base = COLOR_MAP.get(color_name, (150, 150, 150))
    top = tuple(min(255, int(c * .55 + 90)) for c in base)
    bot = tuple(max(0, int(c * .75 + 20)) for c in base)

    img = Image.new('RGB', (W, H))
    for y in range(H):
        t = y / H
        row = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        for x in range(W):
            img.putpixel((x, y), row)
    d = ImageDraw.Draw(img, 'RGBA')
    # soft translucent card behind the glyph
    d.rounded_rectangle([56, 56, W - 56, H - 56], radius=28, fill=(255, 255, 255, 40))

    fnt = _emoji_font(150)
    if fnt is not None:
        try:
            bbox = d.textbbox((0, 0), emoji, font=fnt, embedded_color=True)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            d.text(((W - tw) / 2 - bbox[0], (H - th) / 2 - bbox[1]),
                   emoji, font=fnt, embedded_color=True)
        except Exception:
            fnt = None
    if fnt is None:  # fallback: simple shape
        if shape == 'circle':
            d.ellipse([90, 90, W - 90, H - 90], fill=base)
        else:
            d.rounded_rectangle([80, 110, W - 80, H - 110], radius=18, fill=base)

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=88)
    return ContentFile(buf.getvalue())


class Command(BaseCommand):
    help = 'Seed the database with demo data for LostLink.'

    def add_arguments(self, parser):
        parser.add_argument('--fresh', action='store_true',
                            help='Delete existing demo items/matches first.')
        parser.add_argument('--allow-production', action='store_true',
                            help='Run even though DEBUG is off (disposable demo instances only).')

    def handle(self, *args, **options):
        if not settings.DEBUG and not options['allow_production']:
            raise CommandError(
                'seed_demo creates demo accounts with published passwords and is for local '
                'development only; refusing to run with DEBUG off. Use '
                '"python manage.py seed_taxonomy" to initialise a real deployment, or pass '
                '--allow-production for a disposable demo instance.')
        if options['fresh']:
            self.stdout.write('Clearing existing items, matches, claims…')
            Notification.objects.all().delete()
            VerificationQuestion.objects.all().delete()
            Claim.objects.all().delete()
            Match.objects.all().delete()
            Item.objects.all().delete()

        # Categories and zones come from the production-safe seed_taxonomy
        # vocabulary; existing (possibly customised) rows are left unchanged.
        ensure_default_taxonomy()
        cats = default_categories()
        locs = default_locations()
        self.stdout.write(self.style.SUCCESS(
            f'{len(cats)} categories, {len(locs)} locations ready.'))

        # --- users -------------------------------------------------------
        admin, created = User.objects.get_or_create(
            username='admin',
            defaults={'first_name': 'Aditi', 'last_name': 'Rao',
                      'email': 'admin@lostlink.edu', 'role': User.Role.ADMIN,
                      'is_staff': True, 'is_superuser': True})
        if created:
            admin.set_password('admin123')
            admin.avatar_color = '#7C5CFF'
            admin.save()

        mod, created = User.objects.get_or_create(
            username='moderator',
            defaults={'first_name': 'Rohan', 'last_name': 'Mehta',
                      'email': 'mod@lostlink.edu', 'role': User.Role.MODERATOR})
        if created:
            mod.set_password('mod12345')
            mod.avatar_color = '#12A594'
            mod.save()

        students_data = [
            ('saanvi', 'Saanvi', 'Grover', '#E8623C', '+91 98110 20010'),
            ('prankit', 'Prankit', 'Goel', '#E0398B', '+91 98110 20011'),
            ('aditya', 'Aditya', 'Sharma', '#F2A63B', '+91 98110 20019'),
            ('aaradhya', 'Aaradhya', 'Kulashri', '#2FA36B', '+91 98110 20026'),
        ]
        students = []
        for uname, fn, ln, color, phone in students_data:
            s, created = User.objects.get_or_create(
                username=uname,
                defaults={'first_name': fn, 'last_name': ln,
                          'email': f'{uname}@lostlink.edu', 'phone': phone,
                          'role': User.Role.STUDENT, 'avatar_color': color})
            if created:
                s.set_password('student123')
                s.save()
            students.append(s)
        self.stdout.write(self.style.SUCCESS('Users ready (admin/moderator/4 students).'))

        # --- items -------------------------------------------------------
        today = timezone.now().date()
        rng = random.Random(42)

        def create_item(spec, reporter, status=Item.Status.APPROVED):
            rtype, title, cat, loc, days, brand, color, shape = spec
            item = Item.objects.create(
                reporter=reporter, report_type=rtype, title=title,
                description=f'{title}. Reported {color} {cat.lower()} around {loc}.',
                category=cats[cat], location=locs[loc],
                event_date=today - timezone.timedelta(days=days),
                brand=brand, color=color, status=status)
            item.image.save(f'demo_{item.pk}.jpg',
                            make_image(color, shape, cats[cat].icon), save=False)
            item.image_vector = extract_image_vector(item.image.path)
            item.save()
            return item

        created_items = []
        for pair in PAIRS:
            lost_spec, found_spec = pair
            lost_reporter = rng.choice(students)
            found_reporter = rng.choice([s for s in students if s != lost_reporter])
            i1 = create_item(lost_spec, lost_reporter)
            i2 = create_item(found_spec, found_reporter)
            created_items += [i1, i2]

        # singletons — leave two pending to populate the moderation queue
        for idx, spec in enumerate(SINGLES):
            status = Item.Status.PENDING if idx < 2 else Item.Status.APPROVED
            created_items.append(create_item(spec, rng.choice(students), status))

        self.stdout.write(self.style.SUCCESS(f'{len(created_items)} items created.'))

        # --- run the matching engine ------------------------------------
        Match.objects.all().delete()
        total_matches = 0
        for item in created_items:
            if item.is_matchable:
                total_matches += len(run_matching(item, notify=False))
        # de-dup count (matches are symmetric upserts)
        self.stdout.write(self.style.SUCCESS(
            f'{Match.objects.count()} potential matches computed.'))

        # --- one demo claim in progress ---------------------------------
        wallet_found = Item.objects.filter(
            title__icontains='wallet', report_type='found').first()
        if wallet_found:
            claimant = next((s for s in students
                             if s != wallet_found.reporter), students[0])
            claim, _ = Claim.objects.get_or_create(
                item=wallet_found, claimant=claimant,
                defaults={'statement': 'It is my wallet — black leather with a '
                          'silver logo, has my library card inside.',
                          'contact_phone': claimant.phone,
                          'contact_email': claimant.email,
                          'status': Claim.Status.QUESTIONS})
            VerificationQuestion.objects.get_or_create(
                claim=claim, text='What exactly was inside the wallet?',
                defaults={'asked_by': mod})
            if wallet_found.status == Item.Status.APPROVED:
                wallet_found.status = Item.Status.CLAIMED
                wallet_found.save(update_fields=['status'])

        # --- a user flag and an audit record so the moderator queue is populated
        from dashboard.models import ModerationDecision, record_decision
        from items.models import ReportFlag
        hoodie = Item.objects.filter(title__icontains='hoodie').first()
        if hoodie and hoodie.status == Item.Status.APPROVED:
            flagger = next((s for s in students if s != hoodie.reporter), students[0])
            ReportFlag.objects.get_or_create(
                item=hoodie, reporter=flagger, resolved_at=None,
                defaults={'reason': ReportFlag.Reason.SUSPICIOUS,
                          'note': 'The photo looks like a stock image, not the hoodie.'})
        for item in Item.objects.filter(status__in=[Item.Status.APPROVED, Item.Status.CLAIMED])[:6]:
            if not item.moderation_decisions.exists():
                record_decision(mod, ModerationDecision.Action.REPORT_APPROVED, item=item,
                                note='Seeded demo approval')

        self.stdout.write(self.style.SUCCESS('\n[OK] Demo seed complete.'))
        self.stdout.write('  Admin:      admin / admin123')
        self.stdout.write('  Moderator:  moderator / mod12345')
        self.stdout.write('  Students:   saanvi | prankit | aditya | aaradhya  (student123)')
