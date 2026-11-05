# Implementation status

Last verified: 5 November 2026, on Windows 11 with Python 3.13, Django 6.1,
SQLite and PostgreSQL 17.11 (Docker).

## Verification results

| Check | Result |
| --- | --- |
| `python manage.py check` | No issues |
| `python manage.py makemigrations --check --dry-run` | No changes |
| `python manage.py migrate` on an empty PostgreSQL 17.11 database | All migrations applied |
| `python manage.py migrate` on the existing populated SQLite database | 4 new additive migrations applied, data kept |
| `python manage.py test` on SQLite | 118 tests, all passing, 1 skipped (pretrained smoke test, opt-in) |
| `python manage.py test` on PostgreSQL 17.11 | 118 tests, all passing, 1 skipped |
| `scripts/browser_check.py` (4 roles, 24 routes, 390/768/1440 px, light and dark) | 318 page checks, no HTTP errors, overflow, JS errors or heading faults |
| `scripts/e2e_workflow.py` | Full journey through the real UI passes (below) |
| `seed_demo`, `upgrade_demo_photos`, `rebuild_matches` (ResNet-50) | 6 true lost/found pairs scored 72–87% (1 high, 5 medium) |
| `monitor_matching` | Snapshot recorded |

The end-to-end script signs in as two students and a moderator and, in a real
browser: reports a found and a lost item with photos, approves both through
the confirmation dialog, confirms the owner sees the scored match and a
notification, starts a claim from the match, asks and answers a private
question, checks contact details are hidden before approval and visible
after, approves, confirms the handover, and checks the timeline and decision
log. Analytics then showed the returned item.

## Defects found and fixed

| Defect | Fix |
| --- | --- |
| 10 of the 35 original tests errored: templates required a `collectstatic` manifest even in tests. | Hashed manifest storage only when `DJANGO_DEBUG=false`. |
| The active profile had replaced the synopsis weights and tiers (0.2/0.4 text/image, tiers 0.8/0.9) using a pilot that the project's own gate marks as not research-valid. | Restored the synopsis weights 0.30/0.30/0.15/0.10/0.10/0.05 and tiers (>85 high, 70–85 medium); pilot variant kept as a recorded experiment. |
| Tier boundary used `>=` for high, contradicting “> 85%”. | Exact boundaries with tests at 0.70, 0.85 and just above. |
| Missing brand/colour scored as 0 (negative evidence). | Dropped and renormalised like a missing photo; stored per match. |
| Found-before-lost pairs were scored by absolute day gap. | Directional rule; impossible pairs never stored. |
| Category comparison was exact only. | Administrator-defined compatible categories earn 0.5. |
| TF-IDF was fitted per pair, so IDF weighting did nothing. | One fit over the whole candidate pool. |
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

## Requirement coverage

| Synopsis requirement | Status |
| --- | --- |
| Roles with server-side checks (Student, Moderator, Administrator) | Implemented and tested |
| Report lifecycle: create, view, edit, withdraw, flag, moderate, return | Implemented and tested |
| AJAX search: keyword, type, category, zone, status, dates, sort, pagination | Implemented and tested |
| Hybrid matching with synopsis weights, explanations, versioning | Implemented and tested |
| TF-IDF vs Sentence-BERT, ResNet vs CLIP comparison | Implemented (`evaluate_hybrid`); run on the constructed pilot only |
| Claim workflow with private questions, moderator approval, handover | Implemented and tested end to end |
| In-app notifications | Implemented and tested |
| Analytics, CSV and PDF | Implemented and tested |
| SQLite and PostgreSQL | Both verified |
| Responsive, accessible interface with a token-based design system | Implemented; see `docs/FRONTEND.md` |

## Remaining limitations

- **Accuracy is unmeasured on real data.** The 180-pair pilot is constructed
  (transformed copies of the same photo; titles as descriptions) and fails the
  research-readiness gate by design. Its results are engineering checks only.
  A manually reviewed, independently photographed set of 150–200 pairs is
  still needed, as is the AJAX-versus-manual participant study (DQ4).
- Pretrained inference was exercised for ResNet-50 on the demo data. CLIP and
  Sentence-BERT run through the same code but were not re-run this session;
  `LOSTLINK_TEST_PRETRAINED=1` enables their smoke test.
- Matching runs synchronously on moderator approval; at large scale this
  would need a background queue.
- One photo per report.
- The visual palette is provisional by request; see `docs/FRONTEND.md`.
- No hosted deployment has been made.
