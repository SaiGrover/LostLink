# 🔗 LostLink

[![Verify LostLink](https://github.com/SaiGrover/LostLink/actions/workflows/test.yml/badge.svg)](https://github.com/SaiGrover/LostLink/actions/workflows/test.yml)

**An AI-Enabled Full-Stack Platform for Intelligent Lost-and-Found Item Matching and Recovery**

LostLink centralises campus lost-and-found reporting and uses a **hybrid AI
matching engine** — text-description similarity + image similarity + structured
metadata — to automatically surface likely matches between lost and found
reports, gated by a human-in-the-loop **claim-verification workflow** so an item
is never handed over on a guess.

Built with Django for the JIIT Full-Stack Development Lab (PBL).

---

## ✨ Features

| Area | What it does |
|------|--------------|
| **Role-based access** | Student, Moderator, Administrator — each sees only what its role needs. |
| **Report lost/found** | Photo, category, campus zone, date, approximate time, brand, colour. |
| **Hybrid matching** | Versioned text, pretrained image, and metadata scores with explainable High / Medium / Low tiers. |
| **Explainable matches** | Every match shows a full per-component breakdown, not one opaque number. |
| **Live AJAX search** | Results filter as you type — no page reloads. |
| **Correct lost/found semantics** | Found items can be **claimed** ("this is mine"); lost items can be **returned** ("I found this"). Separate Lost and Found browse pages. |
| **Verified claims + contact exchange** | Private verification questions + moderator approval; once approved, both parties' contact details are revealed to arrange the handover. |
| **Human verification** | Every approval requires answered private questions and a moderator decision. |
| **Dark mode** | Theme toggle in the top bar; remembers your choice and respects the OS preference. |
| **In-app notifications** | Match alerts and claim/report updates with item thumbnail context. |
| **Admin analytics** | Categories, locations, report time gaps, wording variation, photo coverage, confidence tiers, CSV and PDF exports. |

---

## 🎨 Design

An editorial campus identity: warm paper, forest green, Newsreader and Manrope typography, with clear responsive layouts and visible focus states.

---

## 🚀 Quick start

```bash
# 1. (optional) create a virtualenv
python -m venv .venv && .venv\Scripts\activate      # Windows
# source .venv/bin/activate                          # macOS/Linux

# 2. install dependencies
pip install -r requirements-ml.txt

# 3. set up the database
python manage.py migrate

# 4. load demo data (users, items, matches, a claim in progress)
python manage.py seed_demo --fresh
python manage.py upgrade_demo_photos  # real object photos for 14 demo reports
python manage.py rebuild_matches

# 5. run
python manage.py runserver
```

Open **http://127.0.0.1:8000/**

For PostgreSQL, including a standalone setup when Docker is unavailable, follow
[the local PostgreSQL guide](docs/POSTGRESQL.md). Start the verified local setup
with `python scripts/local_postgres.py start`, then run the app through
`python scripts/local_postgres.py manage runserver`.

### Demo logins

| Role | Username | Password |
|------|----------|----------|
| Administrator | `admin` | `admin123` |
| Moderator | `moderator` | `mod12345` |
| Student | `saanvi` (or `prankit`, `aditya`, `aaradhya`) | `student123` |

> The Django admin site is at `/django-admin/` (log in as `admin`).

---

## 🧠 The matching engine

Lives in [`matching/engine.py`](matching/engine.py) and
[`matching/services.py`](matching/services.py).

* **Text** — TF-IDF or pinned Sentence-BERT embeddings.
* **Image** — pinned pretrained ResNet or CLIP embeddings; a Pillow histogram is available for a lightweight demo.
* **Metadata** — exact/adjacent location, exact category, date-decay, and
  brand/colour attributes.
* **Pretrained models are used only as fixed feature extractors — nothing is
  trained here.**

The active configuration is recorded in `matching/profile.json`. The constructed 180-pair pilot and comparison results are in `evaluation/`; read `docs/EVALUATION.md` before interpreting scores. Pilot results are not estimates of real campus accuracy.

Demo reports can use representative photographs of real objects. Run
`python manage.py upgrade_demo_photos` after seeding; it changes only generated
`demo_*.jpg` placeholders and preserves user uploads. [Photo credits and licences](docs/DEMO_IMAGE_CREDITS.json)
are recorded per report. The two identity-card demos retain abstract placeholders
to avoid displaying someone's real ID. These illustrative photos are not
independently captured lost/found evidence.

Research-ready datasets are checked separately from engineering fixtures:

```bash
python manage.py validate_research_dataset path/to/manually-reviewed-pairs.json
python manage.py evaluate_hybrid path/to/manually-reviewed-pairs.json
python manage.py evaluate_discovery_study evaluation/studies/discovery-study.csv
```

Run operational monitoring once with `python manage.py run_maintenance_scheduler --once`,
or keep the same command running without `--once` as a host-managed daily service.

---

## 🗂️ Project structure

```
config/         project settings, root URLs
accounts/       custom User model (roles), auth, RBAC decorators
items/          Category, Location, Item; report/search/detail views; seed_demo
matching/       hybrid engine, Match model, orchestration service
claims/         Claim + VerificationQuestion, verification workflow
notifications/  in-app notifications
dashboard/      moderation queue, admin analytics, CSV + PDF export
templates/      design-system templates (base + app shell)
static/         main.css design system, AJAX search JS
```

---

## 🔒 Security

- **CSRF protection** on every form (Django middleware) and **session auth** with
  http-only cookies.
- **Role-based access control** — `role_required` / `moderator_required` /
  `admin_required` decorators gate every privileged view; unauthorised access
  renders a 403.
- **No raw SQL** — all data access goes through the Django ORM (no injection).
- **Password validation** (length, common-password, numeric, similarity).
- **Upload hardening** — item images are size- (≤5 MB) and content-type-checked;
  request bodies are capped.
- **Production headers** activate automatically when `DEBUG=false`: HTTPS redirect,
  HSTS, secure cookies, `nosniff`, `X-Frame-Options: DENY`.

Configure via environment variables for deployment:

```bash
DJANGO_SECRET_KEY=<random-50-chars>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=lostlink.example.edu
DJANGO_CSRF_TRUSTED_ORIGINS=https://lostlink.example.edu
```

AI auto-approval is removed. Similarity scores never authorize ownership.
See `docs/EVALUATION.md` for model setup, benchmark requirements and deployment configuration.

---

## 🔐 Ethics & trust

The AI only ranks and surfaces potential matches — it **never** transfers an
item. Every claim passes through verification questions and moderator approval,
keeping the consequential decision human. Role-based access control enforces
data minimisation across the three roles.

---

*LostLink · Reuniting campus, one item at a time.*
