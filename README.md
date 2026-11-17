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

Code: [matching/engine.py](matching/engine.py) (the single scoring function
`score_evidence`), [matching/textmodel.py](matching/textmodel.py) (stable
text representation), [matching/chronology.py](matching/chronology.py),
[matching/colour.py](matching/colour.py),
[matching/services.py](matching/services.py) (eligibility, persistence,
retirement, notifications), [matching/signals.py](matching/signals.py)
(taxonomy, report and claim changes), [matching/features.py](matching/features.py)
(cached, versioned features and their processing state),
[matching/embeddings.py](matching/embeddings.py) (pinned pretrained models).
The active profile is [matching/profile.json](matching/profile.json) (engine
`synopsis-v3`). Full description: [docs/EVALUATION.md](docs/EVALUATION.md).

```
similarity = Σ weight_c × similarity_c   over compared components, weights renormalised to 1
```

- **A similarity score, not a probability.** The percentage is a weighted
  compatibility score. Tiers (above 85 strong, 70–85 moderate, below 70 weak
  similarity) are heuristic display thresholds, not confidence levels.
- **Missing is not contradictory.** A photo, brand/colour, date or text that
  is absent (or a photo that could not be processed) is dropped and the other
  weights renormalised; disagreeing evidence is scored. **Evidence coverage**
  (share of base weight compared) is stored and shown with every score; below
  70% a pair is shown as moderate at most, below 50% as weak.
- **Stable text.** Title + description, normalised by a curated, versioned
  lexicon (synonyms, paraphrases, brands, spelling variants, abbreviations,
  campus terms; identifiers kept exact) and weighted by fixed term classes
  from the release `matching/text_model/lexical-v1.json`
  (`manage.py build_text_model`). A pair's score never depends on other
  reports or on which side started matching. Sentence-BERT is optional.
- **Graded chronology.** Date and optional time; found-before-lost is
  approximate, suspicious or contradictory (penalised, warned, tier capped),
  never silently excluded. Same-day contradictory times are not fully
  compatible; approximate student input is tolerated, across midnight too.
- **Colours** are compared as families with shades and multicolour sets:
  "red and blue" vs "red" is partial, never full agreement.
- **Image calibration.** Image cosine is rescaled above each model's chance
  level (ResNet-50 0.275, CLIP 0.568, histogram 0.574) so unrelated photos add
  no points.
- **Partial credit.** Adjacent zones and compatible categories score 0.5;
  editing them in the dashboard rescores the affected pairs automatically.
- **Eligibility.** One rule, `Item.objects.matchable()`, for a report and its
  candidates; never the same report or reporter. Matches that stop being
  valid are retired with a reason (kept as history and claim evidence),
  never left active.
- **Versioning.** Each match stores the engine version, model manifest, text
  release and taxonomy fingerprint; `python manage.py rebuild_matches`
  rescores in place after any change and is idempotent.
- **Robustness.** Per-report feature state (processed / missing / failed /
  retrying, and the model actually used) is recorded; failures are logged,
  matching continues on the other signals, and `retry_features` retries.
  `matching_status` prints a health report.
- **Notifications.** Both reporters are told once per pair, when it first
  reaches moderate or strong similarity.

Pretrained models are fixed feature extractors. Nothing is trained.

## Evaluation

```bash
python manage.py evaluate_hybrid evaluation/pilot/dataset.json --image resnet clip histogram none
python manage.py validate_research_dataset path/to/reviewed-pairs.json
python manage.py evaluate_matches path/to/pairs.csv --split test  # text-only retrieval metrics
python manage.py monitor_matching                                  # score-distribution snapshot
python manage.py evaluate_discovery_study path/to/study.csv        # AJAX vs manual search study
```

Evaluation uses the production scoring function, tunes on the validation
split only and records dataset hash, provenance, models, versions, weights,
thresholds and limitations with every result. The bundled 180-pair pilot is a
**constructed engineering fixture** (positives reuse a transformed copy of
the same Commons photo; descriptions come from source titles); its results
in [evaluation/results.json](evaluation/results.json) are labelled as an
engineering check and are not evidence of real-world accuracy. The format
for independently collected, manually reviewed pairs is in
[docs/EVALUATION.md](docs/EVALUATION.md).

## Tests

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test                       # uses the lexical text release + histogram, no downloads
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
- Photos of pending, rejected or withdrawn reports are served only to the
  reporter, claim participants and staff (`Cache-Control: private, no-store`).
- Upload checks on declared type, decoded format, file size and pixel count.
- ORM only; templates auto-escape; CSV cells are protected from formula injection.
- With `DJANGO_DEBUG=false`: HTTPS redirect, HSTS, secure cookies, and required
  secret key and database URL. `X-Frame-Options: DENY` and `nosniff` always.

## Known limitations

- Matching accuracy on real campus reports is unmeasured. No independently
  photographed, human-reviewed pair set or participant study exists yet.
- One photo per report.
- Matching runs synchronously when a moderator approves a report. With
  pretrained models on CPU this adds noticeable latency per approval at scale.
- Email and SMS notifications, GPS, payments and native apps are out of scope (synopsis Section 3.3).
