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

Verification: 28 regression tests passed; Django checks and migration checks
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

Research limitation: source-identity labels are automatic and positive pairs
reuse a transformed copy of one photo. Human review and an independently
photographed/worded holdout set remain necessary before claiming real campus
matching accuracy. The project now refuses to classify that fixture as
research-ready and includes the collection, validation and analysis workflow.
Production hosting is optional in the synopsis and no deployment has been made.

Git is initialized locally. No remote repository was created or code published.
