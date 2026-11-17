# LostLink

[![Verify LostLink](https://github.com/SaiGrover/LostLink/actions/workflows/test.yml/badge.svg)](https://github.com/SaiGrover/LostLink/actions/workflows/test.yml)

**An AI-enabled full-stack platform for intelligent lost-and-found item matching and recovery.**

LostLink replaces scattered campus group chats and notice boards with one
searchable record. A hybrid matching engine compares every approved lost
report with every approved found report using text, photographs and
structured metadata, and explains each score. A human-in-the-loop claim
workflow (private verification questions, a moderator decision, and a
separately confirmed handover) decides who gets the item. Similarity scores
never approve anything.

Built with Django for the JIIT Full Stack Development Lab (project-based learning).

## Features

| Area | What is implemented |
| --- | --- |
| Roles | Student, Moderator, Administrator. Server-side checks on every restricted view and action; self-registration is always Student. |
| Reporting | Lost or found; title, category, campus zone, event date, approximate time, description, brand, colour, one photo. Validation of dates, text length and real image content (format, size, pixel count). Edit sends the report back to review; withdraw removes it from search and matching. |
| Moderation | Approve or reject reports with a reason, possible-duplicate warnings, user flags (duplicate, suspicious, inappropriate), dispute review, handover confirmation, and an append-only decision log. |
| Discovery | AJAX search with keyword, type, category, zone, status, date range, sort and pagination; shareable URLs; a no-JavaScript fallback and a `?study=manual` baseline for the usability study. Only approved, active reports are public. |
| Matching | TF-IDF or Sentence-BERT text, ResNet-50 or CLIP image embeddings, zone adjacency, compatible categories, date decay, brand/colour. Synopsis weights 0.30/0.30/0.15/0.10/0.10/0.05; tiers above 85% high, 70–85% medium, below 70% low. |
| Explanations | Every match stores its component scores, the components that had no evidence, the model manifest and weight version. The UI shows each signal's similarity, effective weight and points. |
| Claims | Owners claim found items; finders offer to return lost items. Private questions and answers, moderator approval (never by the claimant or the person who reported the item), contact details revealed only after approval, disputes, and handover confirmation. A confirmed handover also closes the claimant's own matching report. Removing a report closes its open claims and tells each claimant. A database constraint allows one verified claimant per item. |
| Notifications | Persistent in-app notifications for matches, claims, questions, answers, decisions, disputes and handovers; de-duplicated across matching runs; mark read/unread without reload; live unread badge. |
| Analytics | Users, lost/found counts, pending moderation, match tiers and score histogram, claim outcomes, returns and recovery rate, categories, zones, weekly trends, photo coverage, lost-to-found day gaps, wording variation, monitoring snapshots. Optional date range. |
| Exports | CSV of reports (no usernames, contacts or answers; formula-injection safe) and PDF summaries for a month or a date range. |
| Interface | Black + Vermilion design system: warm off-white light mode and layered charcoal dark mode, with Light, Dark and System choices that persist and never flash. Vermilion marks the primary action, lost reports, active navigation and match scores; statuses pair a coloured dot with a word. Each match shows the two reports overlapping by the real similarity score. Unbounded and Hanken Grotesk type, an animated landing page, reduced-motion support. See [docs/FRONTEND.md](docs/FRONTEND.md). |

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; use: source .venv/bin/activate on macOS/Linux
pip install -r requirements-ml.txt
python manage.py migrate
python manage.py seed_demo --fresh
python manage.py upgrade_demo_photos
python manage.py rebuild_matches
python manage.py runserver
```

Open http://127.0.0.1:8000/. `upgrade_demo_photos` downloads credited
Wikimedia Commons photographs for 14 demo reports; skip it offline.
`rebuild_matches` downloads the pinned ResNet-50 weights on first use.

For a lightweight setup without PyTorch, install `requirements.txt` only and
set `LOSTLINK_IMAGE_MODEL=histogram`. That baseline is labelled as such in every
match explanation and is never substituted silently.

### First run without demo data (production)

`seed_demo` is for local development only: it creates accounts with the
published passwords below and refuses to run when `DEBUG` is off (unless
forced with `--allow-production` for a disposable demo). A real installation
needs only:

```bash
python manage.py migrate
python manage.py seed_taxonomy --if-empty   # default categories and campus zones
python manage.py createsuperuser             # your own strong password
```

`seed_taxonomy` is idempotent and creates no accounts or reports. It never
edits existing categories or zones; without `--if-empty` it only adds defaults
that are missing (matched by name or slug), and it never re-adds a related
category or adjacent zone an administrator removed. Report forms need at least
one category and one zone, so run it (or add them in *Categories and zones*)
before inviting users.

`python scripts/smoke_first_run.py` checks this whole first-run path against a
temporary empty database (migrate, taxonomy, accounts, reports, moderation,
matching, claim to handover, notifications). Photos stored before upload
sanitisation existed can be cleaned with `python manage.py sanitize_photos`
(`--dry-run` first; `--restrict-suggested` also blurs existing ID-card-like
photos for the public until a moderator decides).

### Demo accounts (local only)

| Role | Username | Password |
| --- | --- | --- |
| Administrator | `admin` | `admin123` |
| Moderator | `moderator` | `mod12345` |
| Student | `saanvi`, `prankit`, `aditya`, `aaradhya` | `student123` |

These are created by `seed_demo` and shown on the sign-in page only while
`DEBUG` is on. Never seed a production database. The Django admin is at
`/django-admin/`.

## Configuration

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required when `DJANGO_DEBUG=false`. |
| `DJANGO_DEBUG` | `true` for development (default), `false` in production. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated host names. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated HTTPS origins in production. |
| `DATABASE_URL` | `postgresql://user:pass@host:port/db`; SQLite is used when unset. Required in production. |
| `LOSTLINK_MEDIA_ROOT` | Upload directory (default `media/`). Uploads are served only through the permission-checked `/media/` view. |
| `LOSTLINK_TEXT_MODEL` | Override `tfidf` or `sbert`. |
| `LOSTLINK_IMAGE_MODEL` | Override `resnet`, `clip` or `histogram`. |
| `LOSTLINK_LOG_LEVEL` | Log level for the `lostlink` logger (default `INFO`). |
| `LOSTLINK_UPLOAD_MAX_MB` / `LOSTLINK_UPLOAD_MAX_PIXELS` / `LOSTLINK_UPLOAD_MAX_EDGE` | Upload limits: 5 MB, 40 megapixels, and the long edge photos are scaled to (2048 px). |
| `LOSTLINK_SQLITE_PATH` | Alternative SQLite file for local runs (ignored when `DATABASE_URL` is set). |
| `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_HSTS_SECONDS` | Production only; default `true` and one year. |

With `DJANGO_DEBUG=false` the app refuses to start unless the secret key is a
private value of at least 50 characters, `DJANGO_ALLOWED_HOSTS` names the real
host(s) (no `*`), CSRF origins are `https://`, `DATABASE_URL` is set and the
media directory is outside the static directories. On Render (`RENDER` set),
`DJANGO_DEBUG` must be given explicitly.

### PostgreSQL

`docker compose up -d db` starts PostgreSQL 17 on `127.0.0.1:54329`:

```bash
export DATABASE_URL=postgresql://lostlink:local-development-only@127.0.0.1:54329/lostlink
python manage.py migrate
python manage.py test
```

Without Docker, see [docs/POSTGRESQL.md](docs/POSTGRESQL.md). GitHub Actions
runs the suite against PostgreSQL on every push. Deployment notes are in
[docs/DEPLOY_RENDER.md](docs/DEPLOY_RENDER.md).

## Matching engine

Code: [matching/engine.py](matching/engine.py) (scores),
[matching/services.py](matching/services.py) (eligibility, persistence,
notifications), [matching/features.py](matching/features.py) (cached,
versioned embeddings), [matching/embeddings.py](matching/embeddings.py)
(pinned pretrained models). The active profile is
[matching/profile.json](matching/profile.json).

```
score = Σ weight_c × similarity_c   over components with evidence, weights renormalised to 1
```

- **Missing evidence is not negative evidence.** If either report has no photo,
  or neither attribute (brand, colour) is present on both, that component is
  dropped and the others are renormalised. The dropped components are stored
  on the match and shown as “Not compared”.
- **Temporal rule.** A found report dated more than one day before the lost
  report is impossible; it is never stored as a match.
- **Image calibration.** Pretrained image features never put unrelated photos
  at zero: on the pilot set, photos of different items have a median cosine of
  0.275 (ResNet-50), 0.568 (CLIP) and 0.574 (histogram). Image similarity is
  rescaled above that chance level, `(cos - chance) / (1 - chance)`, so
  unrelated photos add no points and identical photos still score 1. The
  baselines and their provenance are in `matching/profile.json`.
- **Brand and colour.** Brands compare without case, spaces or punctuation;
  colours agree when they share a colour word after normalising spellings and
  dropping modifiers ("Navy blue" matches "blue", "grey" matches "gray").
- **Live matches only.** Pairs whose report was returned, withdrawn or removed
  stay stored as claim evidence but are not counted as open matches.
- **Partial credit.** Adjacent zones score 0.5; compatible categories (set by an
  administrator) score 0.5.
- **Eligibility.** Only active, moderator-approved reports of the opposite type
  by a different person are compared. Editing, rejecting or withdrawing a
  report removes its stored matches.
- **Versioning.** Each match records the engine version, models, model
  revisions and weights. Pages show only current-version matches;
  `python manage.py rebuild_matches` recomputes after a configuration change.
- **Robustness.** Embeddings are computed once per report and cached by a
  fingerprint. A model or inference failure is logged; the report keeps
  working with metadata and text, and features are retried later.
- **Notifications.** Both reporters are told once when a pair first reaches
  the medium or high tier.

Pretrained models are fixed feature extractors. Nothing is trained.

## Evaluation

```bash
python manage.py evaluate_hybrid evaluation/pilot/dataset.json   # TF-IDF/SBERT × ResNet/CLIP, weights, thresholds
python manage.py validate_research_dataset path/to/reviewed-pairs.json
python manage.py evaluate_matches path/to/pairs.csv --split test  # text-only retrieval metrics
python manage.py monitor_matching                                  # score-distribution snapshot
python manage.py evaluate_discovery_study path/to/study.csv        # AJAX vs manual search study
```

The bundled 180-pair pilot is a **constructed engineering fixture**: positive
pairs reuse a transformed copy of the same Commons photo and descriptions come
from source titles. Its near-perfect scores in
[evaluation/results.json](evaluation/results.json) are not evidence of
real-world accuracy, and the dataset deliberately fails the research-readiness
check. The deployed weights are the synopsis defaults, not the pilot-tuned
variant. Read [docs/EVALUATION.md](docs/EVALUATION.md) before quoting any
number.

## Tests

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test                       # 118 tests; uses TF-IDF + histogram, no downloads
LOSTLINK_TEST_PRETRAINED=1 python manage.py test matching.tests.PretrainedSmokeTests
python scripts/browser_check.py --base http://127.0.0.1:8000   # needs: pip install playwright
```

The browser sweep signs in as each role and opens every main page at 390, 768
and 1440 px in light and dark themes, failing on HTTP errors, horizontal
overflow, JavaScript errors or heading structure.

## Project structure

```
config/         settings, URLs, shared test helpers
accounts/       custom User with roles, auth views, RBAC decorators
items/          Category, Location, Item, ReportFlag; reporting, search, detail; seed commands; UI template tags
matching/       engine, Match and ScoreSnapshot, services, features, evaluation commands
claims/         Claim and VerificationQuestion; verification, decision, dispute and handover views
notifications/  Notification model, inbox, read state, unread count
dashboard/      ModerationDecision audit log; moderation, analytics, people, taxonomy, CSV and PDF exports
templates/      base, app shell, partials and pages
static/         tokens.css, base.css, components.css, layout.css; app.js, search.js
evaluation/     pilot dataset, results, research and study templates
docs/           evaluation, frontend, PostgreSQL, deployment, study protocol, implementation status
```

## Security and privacy

- Django CSRF protection, session authentication, password validators (minimum 8 characters).
- Role checks in decorators and object-level checks in views: users can only
  edit or withdraw their own reports, only claim participants and staff can
  open a claim, only the claimant answers, and only staff decide.
- Private answers and contact details are never in public pages or exports;
  contacts appear to the other party only after approval.
- Media are deny-by-default (`items/media.py`): only `/media/items/...` has a
  rule, and a file is served only while it is the current photo of a report
  the viewer may see. Photos of pending, rejected or withdrawn reports go only
  to the reporter, claim participants and staff. Every media response is
  `Cache-Control: private, no-store`, `nosniff`, sandboxed, with its type taken
  from the file's signature. A new private upload type (e.g. claim evidence)
  stays unreachable until its app registers an authorisation rule.
- Uploads are decoded, size- and pixel-limited (decompression bombs refused),
  checked for HTML/SVG/script polyglots and re-encoded as JPEG under a random
  name: EXIF (including GPS), XMP, comments and ICC are removed after the
  orientation is applied. Originals are never stored.
- Sensitive photos (ID or bank cards, numbers, QR codes): reporters can declare
  them, ID-card-like reports are restricted as a precaution (a hint, not a
  guarantee) and moderators decide in the review queue. Everyone except the
  reporter, staff and a verified claimant gets a blurred low-resolution preview.
- ORM only; templates auto-escape; CSV cells are protected from formula injection.
- With `DJANGO_DEBUG=false`: HTTPS redirect, HSTS, secure cookies, and required
  secret key, hosts and database URL. `X-Frame-Options: DENY` and `nosniff`
  always. Custom 400/403/404/500 and CSRF-failure pages show no internals.

## Known limitations

- Matching accuracy on real campus reports is unmeasured. No independently
  photographed, human-reviewed pair set or participant study exists yet.
- One photo per report.
- Matching runs synchronously when a moderator approves a report. With
  pretrained models on CPU this adds noticeable latency per approval at scale.
- Email and SMS notifications, GPS, payments and native apps are out of scope (synopsis Section 3.3).
