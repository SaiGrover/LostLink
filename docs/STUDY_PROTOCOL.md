# Discovery usability study

This protocol answers synopsis design question DQ4 without inventing user-study evidence.

## Design

Recruit consenting campus participants and use a counterbalanced crossover design. Each
participant completes equivalent item-finding tasks in both conditions:

1. `manual`: use `/search/?study=manual`, which disables live requests and uses
   ordinary form submission and paginated page loads.
2. `ajax`: use LostLink live search and filters.

Alternate which condition each participant sees first. Use different but difficulty-matched
tasks in each condition to reduce learning effects. Record no names, email addresses or item
ownership details; assign opaque participant IDs.

## Measures

- Task success (`0` or `1`).
- Completion time in seconds.
- Optional post-task ease rating, stored separately from the required analysis CSV.

Copy `evaluation/studies/discovery-study-template.csv`, add one row per task, and run:

```bash
python manage.py evaluate_discovery_study evaluation/studies/discovery-study.csv
```

The command requires every participant to complete both modes and reports success rate,
mean and median task time, and median time reduction. Report participant count, recruitment,
counterbalancing and limitations alongside the result. Do not describe the workflow as a
completed user study until real participant rows have been collected with consent.

## Matching comparison

DQ2 requires a separate manually reviewed set of independently photographed lost/found
pairs. Every pair must include a `review` object with `label_source: manual`, an opaque
reviewer identifier, `status: approved`, `independent_descriptions: true`, and
`independent_photos: true` for positives. These are human attestations, not facts
the software can establish. Check it
before making an accuracy claim:

```bash
python manage.py validate_research_dataset path/to/research-pairs.json
python manage.py evaluate_hybrid path/to/research-pairs.json
```

The bundled Commons pilot remains an engineering sanity check and is expected to fail the
research-readiness command.

Each report record must have `id` (report ID), `physical_id` (real object ID),
`description`, `category`, `location`, `date` (YYYY-MM-DD), `image` (path relative
to the JSON file), and string `brand` and `color` fields (which may be empty).
`query_id` must equal `query.id`. Positive pairs share a physical ID but have
different report IDs and independently captured photos. Negative pairs have
different physical IDs. Keep every report, physical object and image entirely
within either validation or test; both splits need both classes.

Start with `evaluation/research/pairs-template.json`. Its placeholders deliberately
fail validation. Assign stable opaque IDs, collect reports written independently
by two people, obtain consent for research use, remove identifying details from
text and images, and have a reviewer approve every label. Collect 150–200 pairs,
including difficult same-category negatives. Check the complete dataset without
`--allow-small`; that option only checks structure during collection and never
marks a dataset research-ready. The validator checks readable images, duplicate
pairs, identical positive image bytes, and report/object/image split leakage.
It cannot detect every transformed duplicate or verify a reviewer's statements.

Exports from completed handovers are restricted review material. Dedicated contact
fields and verification answers are omitted, but descriptions and photographs
can still identify someone. Review and redact them before sharing. Photo and
description independence remain unknown until reviewed. Resolve object identities
and split leakage in sampled negatives before retuning:

```bash
python manage.py retune path/to/reviewed-pairs.json --output evaluation/production/retuning-results.json
```

Retuning and `evaluate_hybrid --apply` enforce the research checks. A normal
`evaluate_hybrid` run still accepts the constructed pilot for engineering checks.
Review the validation-selected profile and holdout results before using `--apply`;
then restart the app and run `rebuild_matches`. Do not repeatedly choose models
based on the test results; use a fresh holdout for subsequent tuning rounds.
