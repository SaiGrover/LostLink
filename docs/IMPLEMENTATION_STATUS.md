# Implementation verification

Completed in this implementation pass:
- Report visibility and matching restricted to reviewed reports.
- Validated claim-to-match ownership; removed AI approval code.
- Private answers required for moderator approval; closed claims cannot be reopened by answering.
- Atomic moderator decisions with database row locking (effective on PostgreSQL).
- Match notifications to both parties and dashboard visibility filtering.
- Date filtering with AJAX controls, feedback, reset and error handling.
- Shared editorial theme, new landing page, auth restyling and mobile menu.
- Removed unsupported landing-page accuracy, adoption and recovery statistics.
- PostgreSQL connection configuration, environment example and Git exclusions.
- Pretrained adapters: Sentence-BERT, CLIP, ResNet; explicit baseline modes remain defaults.
- Text evaluation CLI with precision, recall, F1, ROC-AUC, P@3 and MRR.
- Model rebuild CLI and evaluation protocol.
- Approximate event time throughout reporting, detail views and CSV export.
- Conservative noun lemmatization in the deployed text-cleaning pipeline.
- Item thumbnail context in accessible, responsive in-app notifications.
- Ongoing EDA for photo coverage, report-date gaps and description variation.
- Research-readiness validation, discovery-study analysis and a production-confirmed-pair export path.
- A daily host maintenance scheduler for score snapshots and retuning-data export.
- Credited real-object photographs for 14 demo reports, labelled as representative
  in cards and detail views. The two identity-card demos retain abstract images
  to avoid presenting a real person's private card as campus evidence.

Verification: 35 regression tests passed on PostgreSQL 17.11; Django checks and migration checks
passed. Browser checks exercised 87 route, viewport and role combinations at
1440px, 768px and 390px with no horizontal overflow. Landing and report views
were visually reviewed. Sentence-BERT, CLIP and ResNet inference produced
384, 512 and 2048-dimensional vectors respectively.

Implemented since the initial pass: a Commons-sourced 180-pair constructed
pilot with attribution manifest; four-configuration hybrid comparison and
validation-only selection; verified ResNet and CLIP inference; active versioned
TF-IDF + ResNet profile; operational feature rebuilding; score snapshots;
moderator-approved claim and separate physical-handover confirmation; dispute
review; protected pending media; report editing; pagination; duplicate flags;
PostgreSQL CI and local Compose configuration. The browser sweep covers all
three roles at desktop, tablet and phone widths.

PostgreSQL local verification (4 November 2026): the standalone server runs on
127.0.0.1:54329 using SCRAM authentication, with all migrations applied. All 109
records copied from SQLite matched exactly when re-exported (including six users,
16 reports, 15 stored matches and the existing claim). SQLite remains as a backup.
Browser sign-in, dashboard rendering, live AJAX search and manual-search submission
were verified against the PostgreSQL-backed app. The earlier 87-combination layout
sweep remains the UI baseline. Setup and restart commands are in `docs/POSTGRESQL.md`.

Research tooling now rejects missing holdout splits, missing or unreadable photos,
duplicate pairs, inconsistent labels, and physical-object/image leakage across
splits. Photo and description independence require explicit reviewer attestations.
Retuning and profile activation require these checks. Completed-handover exports
leave research review pending and do not assume independent photographs. The
study analyser also rejects duplicate observations, blank identifiers and
non-finite completion times.

Research limitation: source-identity labels are automatic and positive pairs
reuse a transformed copy of one photo. Human review and an independently
photographed/worded holdout set remain necessary before claiming real campus
matching accuracy. The project now refuses to classify that fixture as
research-ready and includes the collection, validation and analysis workflow.
The new demo photos are presentation fixtures and do not change this limitation;
they must not be counted as independent lost/found research pairs.
Production hosting is optional in the synopsis and no deployment has been made.

Public repository: https://github.com/SaiGrover/LostLink
GitHub Actions runs the Django suite against PostgreSQL on pushes and pull requests.
