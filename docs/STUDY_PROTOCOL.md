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
reviewer identifier, `status: approved`, and `independent_photos: true` for positives. Check it
before making an accuracy claim:

```bash
python manage.py validate_research_dataset path/to/research-pairs.json
python manage.py evaluate_hybrid path/to/research-pairs.json
```

The bundled Commons pilot remains an engineering sanity check and is expected to fail the
research-readiness command.
