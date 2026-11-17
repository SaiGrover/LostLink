# Implementation status

Last verified: 17 November 2026, on Windows 11 with Python 3.13, Django 6.1
and SQLite. PostgreSQL 17 is exercised by the GitHub Actions workflow on push
(no local PostgreSQL server was available for this round).

## Verification results

| Check | Result |
| --- | --- |
| `python manage.py check` | No issues |
| `python manage.py makemigrations --check --dry-run` | No changes |
| `python manage.py check --deploy` (DEBUG off, production env) | Only `accounts.W001`: email delivery not configured in that test environment (expected until SMTP is set) |
| `python manage.py migrate` on a copy of the populated demo database | All new migrations applied; data kept; legacy disputes and inactive reports migrated; existing accounts marked verified (legacy) |
| `python manage.py test` on SQLite | 394 tests, all passing, 1 skipped (pretrained smoke test, opt-in) |
| `scripts/smoke_first_run.py` on an empty database | 11 steps passed (registration + email verification, taxonomy seeding, reports (one published automatically, one held for a moderator), moderation, matching, checklist approval, one-time-code handover, notifications); real SMTP delivery not run |
| `scripts/e2e_workflow.py` | Full journey through the real UI passes: the ticked found report waits for a moderator, the plain lost report is published at once, ending with the owner's one-time code entered by the finder |
| `scripts/browser_check.py` (4 roles, 24 routes, 390/768/1440 px, light and dark) | 318 page checks, no HTTP errors, overflow, JS errors or heading faults |
| `scripts/mobile_nav_check.py` (320–767 px, 7 pages) | 65 checks, content and last controls clear of the tab bar |
| `rebuild_matches` (ResNet-50, engine `synopsis-v3`) on a copy of the demo database | 6 true lost/found pairs scored 71–92 (2 strong, 4 moderate), evidence coverage 1.0; second run changed nothing |

The end-to-end script signs in as two students and a moderator and, in a real
browser: reports a found and a lost item with photos, approves both, confirms
the owner sees the scored match and a notification, starts a claim from the
match, asks and answers a private question, checks contact details are hidden
before approval, approves with the evidence checklist (item reserved, not
returned), has the owner create the one-time handover code, checks the
moderator never sees the code, has the finder enter it, and checks the
timeline and decision log.

## Remediation round (November 2026)

| Area | Change | Where |
| --- | --- | --- |
| Campus identity | Expiring single-use email verification, institutional-domain policy, no enumeration, legacy accounts grandfathered; unverified accounts cannot report or claim | `accounts/`, [migrations 0002–0003] |
| Password recovery | Reset by email with rate limits and session invalidation; production email backend from environment | `accounts/`, settings |
| Auth hardening | Login rate limiting, POST-only logout, admin role-escalation fix | `accounts/` |
| Independent moderation | No self-review of reports, claims, disputes or handover exceptions; own reports listed separately | `dashboard/views.py`, `claims/services.py` |
| Report lifecycle | One validated transition function, explicit withdrawn/removed states, audited from/to state | `items/lifecycle.py` |
| Ownership | Approved claim = reservation; disputes are separate records and never release it; revocation is explicit and audited | `claims/models.py`, `claims/services.py` |
| Reconsideration | Limited, rate-limited appeals with immutable statement history | `ClaimRevision` |
| Verification evidence | Moderator evidence checklist; private expected answers; reporter's private details; private re-encoded evidence photos with retention | `claims/`, `items/forms.py` |
| Handover | Owner-held one-time code (hashed, 10 min, single use, attempt lock, rate limits, suspended by disputes); documented moderator exception | `HandoverSession`, [docs/CLAIMS_AND_HANDOVER.md](CLAIMS_AND_HANDOVER.md) |
| Matching | Stable versioned text model with titles, colour and chronology rules, evidence coverage, retired match history, taxonomy rescoring, feature status | `matching/`, [EVALUATION.md](EVALUATION.md) |
| Media | Upload sanitising, deny-by-default media access, sensitive-photo restriction | `items/media.py` |
| Production | `seed_taxonomy`, startup checks for secrets/hosts/origins, error pages, smoke test | settings, `scripts/` |
| UI | Status-aware actions (Handover pending), mobile tab-bar clearance, similarity wording | templates, `static/css/layout.css` |

## Defects found and fixed

| Defect | Fix |
| --- | --- |
| 10 of the 35 original tests errored: templates required a `collectstatic` manifest even in tests. | Hashed manifest storage only when `DJANGO_DEBUG=false`. |
| The active profile had replaced the synopsis weights and tiers (0.2/0.4 text/image, tiers 0.8/0.9) using a pilot that the project's own gate marks as not research-valid. | Restored the synopsis weights 0.30/0.30/0.15/0.10/0.10/0.05 and tiers (>85 high, 70–85 medium); pilot variant kept as a recorded experiment. |
| Tier boundary used `>=` for high, contradicting “> 85%”. | Exact boundaries with tests at 0.70, 0.85 and just above. |
| Missing brand/colour scored as 0 (negative evidence). | Dropped and renormalised like a missing photo; stored per match. |
| Found-before-lost pairs were scored by absolute day gap. | Directional rule; impossible pairs never stored. |
| Category comparison was exact only. | Administrator-defined compatible categories earn 0.5. |
| TF-IDF was fitted per pair, so IDF weighting did nothing. | One fit over the whole candidate pool (superseded in `synopsis-v3`, see below). |
| Stale matches survived edits, rejections, withdrawals and drops below the minimum score. | Matches are cleared or deleted in each case. |
| Rescores never re-notified a pair that rose from low to medium/high; re-runs could duplicate notifications. | Notify on first entry into a surfaced tier; identical unread notifications de-duplicated. |
| Model or inference failure raised inside the request. | Logged; text and metadata matching continue; features retried later. |
| Rival claims were rejected without notifying their claimants; moderators could decide their own claims; answers could be rewritten. | Rival claimants notified; self-decision forbidden; answers immutable. |
| No database guarantee against two verified owners. | Partial unique constraint on approved/returned claims per item. |
| No record of moderation decisions; no user flags; no dispute notification. | `ModerationDecision` audit log, `ReportFlag`, notifications on disputes. |
| CSV exposed usernames; PDF was all-time only. | CSV uses reporter ID and date filters; PDF by month or range. |
| `review_report` accepted an arbitrary `next` URL. | Restricted to same-host URLs. |
| Demo credentials always shown on the sign-in page. | Shown only while `DEBUG` is on. |
| Demo photo credits keyed by database ID, duplicated on every reseed. | Keyed by the stable demo title. |

### Matching remediation (engine `synopsis-v3`)

| Defect | Fix |
| --- | --- |
| TF-IDF was fitted on the query plus its candidate pool: a pair's text score changed when unrelated reports were added and differed by which side started matching. | Fixed-vocabulary lexical release `lexical-v1` (title + description, concept normalisation, fixed term-class weights), built reproducibly by `build_text_model`; pairwise, symmetric, pool-independent; release id in every fingerprint and manifest. |
| Titles were ignored; paraphrases ("cellphone with damaged display") scored as unrelated. | Title + description representation; synonyms, paraphrases, brands, spelling variants, abbreviations, campus terms; identifiers keep exact lexical weight. |
| The percentage was presented as "confidence". | Relabelled everywhere as a weighted similarity score; tiers are strong / moderate / weak similarity, described as heuristic thresholds. |
| Sparse-evidence pairs looked as strong as rich-evidence pairs. | Evidence coverage stored and shown; coverage-based tier caps; missing signals listed with their reason and weight redistribution. |
| "Red and blue" vs "red" counted as full colour agreement. | Colour families, shades, neighbours and multicolour sets (full / partial / conflict). |
| Time of day was ignored; found-before-lost beyond one day was silently never stored. | Graded chronology over date + time (plausible / approximate / suspicious / contradictory), penalties, tier caps and moderator warnings. |
| Taxonomy edits left stale category/zone scores. | `m2m_changed` signals rescore exactly the affected pairs after commit, coalesced per transaction; taxonomy fingerprint stored per match. |
| Obsolete matches were deleted (losing claim evidence) or, for counterparts that left the pool, left active. | Matches are retired with a reason; default manager shows active rows only; stale counterparts retired on every run; `reconcile_matches` in rebuilds. |
| Notifications could repeat after a pair dropped and re-rose or after rebuilds. | Exactly-once per pair (`notified_at`); existing surfaced pairs back-filled by the migration. |
| Feature failures were only logged; the histogram baseline was not distinguishable in diagnostics. | `ItemFeatures` per report (state, model actually used, attempts, error for staff), bounded retries, `retry_features`, `matching_status`. |
| Evaluation reimplemented scoring and multiplied `None` (missing attributes) by a weight. | Evaluation calls the production `score_evidence`; undefined metrics handled; provenance recorded; validation-only tuning; ablation without images; results regenerated. |
| `evaluate_hybrid --apply` overwrote the profile and dropped the image calibration. | `--apply` merges into the existing profile. |

## Requirement coverage

| Synopsis requirement | Status |
| --- | --- |
| Roles with server-side checks (Student, Moderator, Administrator) | Implemented and tested |
| Report lifecycle: create, view, edit, withdraw, flag, moderate, return | Implemented and tested |
| AJAX search: keyword, type, category, zone, status, dates, sort, pagination | Implemented and tested |
| Hybrid matching with synopsis weights, explanations, versioning | Implemented and tested (`synopsis-v3`: stable text, coverage, graded chronology, retirement) |
| Lexical vs Sentence-BERT, ResNet vs CLIP comparison | Implemented (`evaluate_hybrid`, production scoring function); run on the constructed pilot only |
| Claim workflow with private questions, evidence checklist, reservation, disputes, appeals and one-time-code handover | Implemented and tested end to end |
| In-app notifications | Implemented and tested |
| Analytics, CSV and PDF | Implemented and tested |
| SQLite and PostgreSQL | SQLite verified locally this round; PostgreSQL 17 verified by CI on push |
| Responsive, accessible interface with a token-based design system | Implemented; see `docs/FRONTEND.md` |

## Remaining limitations

- **Accuracy is unmeasured on real data.** The 180-pair pilot is constructed
  (transformed copies of the same photo; titles as descriptions) and fails the
  research-readiness gate by design. Its results (regenerated for
  `synopsis-v3`) are engineering checks only. The lexical text release, term
  weights, coverage caps and chronology thresholds are documented heuristics,
  not calibrated statistics.
  A manually reviewed, independently photographed set of 150–200 pairs is
  still needed, as is the AJAX-versus-manual participant study (DQ4).
- Pretrained inference was exercised for ResNet-50 on the demo data. CLIP and
  Sentence-BERT run through the same code but were not re-run this session;
  `LOSTLINK_TEST_PRETRAINED=1` enables their smoke test.
- Matching runs synchronously on moderator approval; at large scale this
  would need a background queue.
- One photo per report.
- The visual palette is provisional by request; see `docs/FRONTEND.md`.
- Email delivery needs an SMTP account in production; without it, sign-ups
  cannot verify (the system check warns `accounts.W001`). Live verification
  and password-reset emails are launch checks in `docs/DEPLOY_RENDER.md`.
- Production refuses to start without a campus email domain
  (`accounts.E001`) unless an open deployment opts out explicitly.
- Rate limits need a shared cache (`REDIS_URL` or the database cache) with
  several workers (`accounts.W003`), and `X-Forwarded-For` is trusted only
  after the hosting proxy's behaviour is confirmed (`LOSTLINK_TRUSTED_PROXY_COUNT`).
- Automatic sensitive-photo detection is a keyword/category hint, not a
  guarantee; moderators make the decision.
- A free demonstration deployment exists on a separate branch; no production
  deployment has been made.
