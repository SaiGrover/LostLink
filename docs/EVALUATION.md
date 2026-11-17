# Matching engine and evaluation

This document describes how LostLink scores lost/found pairs (engine version
`synopsis-v3`), what the score means, and how the matcher is evaluated. Read it
before quoting any number.

## 1. What the score is

```
similarity = Σ w_c · s_c  over compared components c,  weights renormalised to sum to 1
```

Components and base weights (synopsis Section 4.9): title + description text
0.30, photograph 0.30, campus location 0.15, category 0.10, date and time 0.10,
brand / colour 0.05.

The percentage shown to users is a **weighted similarity (compatibility)
score**, not a probability that two reports describe the same object. The
weights are design choices and the score has never been calibrated against
real outcomes. Tiers are **heuristic display thresholds**: above 85 *strong*,
70–85 *moderate*, below 70 *weak similarity* (stored as `high` / `medium` /
`low`). Strong and moderate pairs are surfaced to students and notified once;
weak pairs are visible only to the reporter and staff. A moderator, not the
score, decides ownership.

The single implementation is `matching.engine.score_evidence`. The live
service (`matching.services`) and the evaluation tooling
(`matching.evaluation`) both build `Evidence` records and call it, so
normalisation, calibration, chronology, missing-evidence handling, coverage
and tiers are identical in production and evaluation.

## 2. Missing versus contradictory evidence and evidence coverage

| Situation | Treatment |
| --- | --- |
| No photo on one or both reports | image *missing*: dropped, weights renormalised; UI says which report has no photo |
| Photo supplied but processing failed / pending | image *missing* (`photo_not_processed` / `photo_pending`); staff see the error, students see "a photo could not be compared" |
| Photos processed by different models | image *missing* (`incompatible_models`), warning |
| Brand and colour not both given | attribute *missing* |
| Date missing (evaluation data only) | date *missing* |
| Photos both present but unlike | image *scored* (near 0: little resemblance) |
| Different category / zone, conflicting colour or brand | *scored* 0: contradicts |
| Found before lost | *scored* with a chronology penalty (section 4) |

**Evidence coverage** = share of the *base* weight whose component was
compared on both reports (1.0 = everything compared; 0.65 = no photo and no
brand/colour). Renormalisation keeps the *strength* of sparse pairs comparable,
but a 0.80 built on 65% of the evidence is less well supported than 0.80 on
100%. Coverage is stored on every match, shown next to the score, and caps the
tier (heuristic rules, `MATCHING['EVIDENCE_COVERAGE']`): below 0.70 a pair is
shown as moderate at most, below 0.50 as weak. The match explanation lists
each compared signal with its similarity, base and effective weight and
points, and each missing signal with the reason and "base weight 30% → 0%".

## 3. Text: stable, pairwise, versioned (`lexical-v1`)

The old text component fitted TF-IDF on the active report *plus its
candidate pool*, so a pair's score changed when unrelated reports were added
and depended on which side started matching. It also ignored titles.

The text model is now a **fixed-vocabulary weighted lexical model**:

```
vector(report) = L2-normalise({term: class_weight(term) · (1 + ln tf)})
text similarity = cosine(vector(lost), vector(found))
```

* `tf` counts canonical terms over **title + description** together.
* Canonical terms come from a curated normaliser: spelling variants
  (grey/gray, colour/color), abbreviations (blk, specs, lib), multi-word
  phrases (cell phone, ID card, power bank), synonyms and paraphrases mapped
  to shared concepts (mobile/cellphone/smartphone → phone; cracked/shattered/
  damaged → damaged; display → screen), an object hierarchy (backpack → bag),
  brands (iphone → iphone + apple + phone), campus terms, conservative plural
  folding. "Black mobile phone with cracked screen" vs "Dark cellphone with
  damaged display" scores 0.95 instead of near zero.
* Identifiers keep their exact characters and the highest weight, so model
  and serial numbers keep their lexical strength (`fx-991ES` vs `FX991ES`
  0.85, vs `fx-82MS` 0.56).
* Class weights (identifier 2.5, brand 2.0, object 2.0, feature 1.5, material
  1.2, colour 1.0, unknown 1.0, descriptor 0.6, place 0.5) are transparent
  heuristics shipped with the release. They replace corpus IDF deliberately:
  the only corpora available were the live pool (the source of instability)
  and the evaluation data (leakage). A future release can re-estimate weights
  from an independent reviewed corpus.

**Guarantees** (tests in `matching/test_stability.py`): the same pair gets the
same score under the same release; scores are symmetric (lost→found =
found→lost); adding, removing, approving or reordering other reports cannot
change a pair; candidate retrieval does not influence pairwise similarity.

**Release control.** `matching/lexicon.py` is the human-edited source; it is
never read at run time. `python manage.py build_text_model --release lexical-v1`
compiles it deterministically into `matching/text_model/lexical-v1.json`
(23.8 KB), the only file the matcher loads. Releases are immutable (the
command refuses to overwrite a release with different content) and a test
checks that the committed release equals a fresh build. The active release is
named in `matching/profile.json` (`text_release`) and recorded in every
feature fingerprint and match manifest.

Optional Sentence-BERT (`LOSTLINK_TEXT_MODEL=sbert`) embeds title +
description per report; embeddings are per-report and therefore also
pairwise-stable. If an embedding fails, the report falls back to the lexical
release and its feature state records the failure.

## 4. Date and time chronology (graded, never a hard exclusion)

Previously a found report dated more than one day before the lost report was
never stored. Students enter dates and times from memory, so this excluded
genuine pairs with a typo. Chronology is now graded
(`matching/chronology.py`, settings `MATCHING['CHRONOLOGY']`):

| Status | Rule (defaults) | Date score | Effect |
| --- | --- | --- | --- |
| plausible | found on/after lost | proximity | — |
| approximate | dates only: found ≤1 day earlier; both times known: ≤3 h earlier (works across midnight) | proximity × 0.9 | — |
| suspicious | earlier than that, up to 7 days | proximity × 0.5 | tier ≤ moderate, moderator warning |
| contradictory | > 7 days earlier | 0 | tier ≤ weak (not surfaced), warning |
| unknown | a date missing | — | component missing |

`proximity = 0.5 ** (calendar_day_gap / 10)`. Times decide the ordering
status but do not shrink same-day proximity, so adding a time never lowers a
consistent pair. A missing time is independent of a missing date: with dates
on both reports and a time on one or none, calendar days are compared and the
pair is fully scored. "Lost 18:00, found 09:00 the same day" is *suspicious*
(date score 0.5, capped at moderate, warning) rather than fully compatible;
"lost 18:00, found 16:30" is *approximate* and keeps its tier.

## 5. Colours and brands

Colours are parsed into families with optional shades (`matching/colour.py`).
Same family and compatible shade → 1.0 (navy blue = blue, dark red = maroon,
grey = gray); same family, opposite shade → 0.75; neighbouring families easily
confused by lighting or wording (grey/silver, beige/brown, ...) → 0.5;
different families → 0 (*conflict*). Multicolour descriptions are compared as
sets with a soft F1, so "red and blue" vs "red" is 0.67 (*partial*), never
full agreement; an unnamed "multicolour" vs one named colour is 0.5. Brands
compare without case, spaces or punctuation, with a few aliases (HP /
Hewlett-Packard, Redmi / Xiaomi). The attribute component is the mean of the
comparable brand and colour checks.

## 6. Lifecycle, versioning and taxonomy changes

* **One row per pair**, updated in place; identical reruns write nothing.
* **Retirement instead of deletion.** Matches that stop being valid are
  retired (`status='retired'`, `retired_reason`, `retired_at`): report
  withdrawn / rejected / returned / sent back to review / no longer eligible,
  counterpart left the candidate pool, rescored below `MIN_SCORE` (0.40),
  superseded engine version. `Match.objects` returns active rows only, so no
  page or count can show a stale score; `Match.all_objects` and `claim.match`
  keep the history and claim evidence. Moderators see retired matches with
  their reason on the report page. `matching.services.clear_matches(item)`
  keeps its contract and retires instead of deleting.
* **Eligibility** is one rule, `Item.objects.matchable()`, used for the report
  (`is_match_eligible`) and its candidates (`candidate_queryset`); never the
  same report, never the same reporter, opposite type only.
* **Signals** (`matching/signals.py`): after commit, a report that became
  ineligible has its matches retired; an administrator edit of matching
  content rescores it; a claim change re-checks eligibility; editing
  compatible categories or neighbouring zones (dashboard, admin or shell)
  rescores exactly the affected pairs, coalescing the remove/add events of a
  form save into one recalculation. Each match stores the taxonomy
  fingerprint it was scored under.
* **Notifications** are exactly-once per pair (`Match.notified_at`): reruns,
  rebuilds, taxonomy recalculations and version changes never re-notify.
* **Versioning.** Each match stores the engine version, the full manifest
  (models, revisions, text release, weights, thresholds, coverage rules,
  chronology settings) and the taxonomy fingerprint. Any change to scoring
  semantics must bump `version` in `matching/profile.json` and be followed by
  `python manage.py rebuild_matches`, which rescores every eligible pair in
  place, retires leftovers of older versions and is idempotent (notifications
  off unless `--notify`). Pages show current-version, active matches only.

## 7. Feature processing

`matching.models.ItemFeatures` records, per report: text and image state
(`success`, `pending`, `missing` = no photo supplied, `failed`, `retrying`),
the model that actually produced each vector (`lexical-v1`, `sbert:...`,
`resnet`, `clip`, or `histogram` — always labelled as a lightweight baseline,
never as ResNet/CLIP), attempts, last attempt, and a short error for staff.
Failures are logged to `lostlink.matching`; matching continues on the other
signals. After `FEATURE_MAX_ATTEMPTS` (3) failures the report stops retrying on
every run until `python manage.py retry_features [--item ID]` resets it.
`python manage.py matching_status` prints a read-only health report.

## 8. Evaluation data

**Constructed pilot (engineering check).** `evaluation/pilot/dataset.json`
contains 180 constructed pairs from 60 Wikimedia Commons images in six
categories (source page, artist, licence and hash recorded; source-disjoint
split; rebuild with `python manage.py build_pilot`). Positive pairs reuse a
brightness/compression variant of the same photo and candidate text comes
from source titles. It is an optimistic functional benchmark, it fails the
research-readiness gate by design, and its metrics are not evidence of
real-world accuracy.

**Independently collected, manually reviewed data.** Use the format below
(template: `evaluation/research/pairs-template.json`) and validate with
`python manage.py validate_research_dataset <file>`:

```json
{
  "dataset_kind": "independent_reviewed",
  "provenance": "who collected what, when, where, consent and review procedure",
  "limitations": ["..."],
  "taxonomy": {"related_categories": [["bags", "others"]], "adjacent_locations": [["library", "block-a"]]},
  "pairs": [{
    "query_id": "lost-001", "split": "validation", "label": 1,
    "conditions": ["genuine_positive", "independent_photos", "different_wording"],
    "query":     {"id": "lost-001", "physical_id": "obj-001", "report_type": "lost", "title": "...",
                  "description": "...", "category": "bags", "location": "library",
                  "date": "2026-09-01", "time": "18:30", "brand": "", "color": "black", "image": "img/l1.jpg"},
    "candidate": {"id": "found-001", "physical_id": "obj-001", "report_type": "found", "...": "..."},
    "review": {"label_source": "manual", "status": "approved", "reviewer": "r1",
               "independent_photos": true, "independent_descriptions": true}
  }]
}
```

Required per record: `id`, `physical_id`, `description`. Optional strings
(empty = not given, treated as missing evidence exactly as in production):
`title`, `category`, `location`, `date`, `time`, `brand`, `color`, `image`,
`report_type`. Condition tags document what the set covers:
`genuine_positive`, `hard_negative`, `independent_photos`,
`different_wording`, `missing_photo`, `partial_metadata`, `inaccurate_date`,
`multicolour`, `ambiguous_category`, `lighting`. The validator rejects
unknown tags, tags that contradict the label, identity / photo leakage
between splits, reused photo bytes in positives, unreviewed labels and
unreadable photos; it warns about untagged conditions. `research_ready`
requires 150–200 pairs, every check passing and
`dataset_kind = "independent_reviewed"`. Reviewer attestations cannot be
verified by software.

No real-world dataset is bundled and no real-world result is claimed.

## 9. Running an evaluation

```
python manage.py evaluate_hybrid evaluation/pilot/dataset.json --image resnet clip histogram none
python manage.py evaluate_hybrid reviewed.json --text tfidf sbert --image resnet clip
python manage.py evaluate_matches pairs.csv --split test        # text only (query_id,query,candidate,label,split)
```

Protocol: leakage checks (report ids, physical-object ids and image bytes
must be split-disjoint) and both classes per split are enforced before
scoring. For each text × image setting, weight candidates (profile weights
and text/image 0.2/0.4, 0.3/0.3, 0.4/0.2) and thresholds 0.40–0.95 are tuned
on the **validation split only** (F1, then precision, then MRR; ties → lower
threshold); the test split is scored once with the selected setting.
Reported per setting: validation and test precision, recall, F1, ROC-AUC,
MRR, Precision@1/@3, hit rate@1/@3, confusion matrix; test metrics *as
deployed* (profile weights, threshold 0.70); test metrics *without images*
(image component dropped, same weights); test tier distribution and mean
coverage; false-positive / false-negative analysis with each pair's
components, missing evidence and chronology. Undefined metrics are `null`
with a reason (single-class data, no predicted positives, no query with a
positive) instead of a silent zero.

Every report records: schema version, timestamp, code commit and whether
matching code had uncommitted changes, dataset path, SHA-256, kind, evidence
class, provenance, split/label/condition counts, validation errors, engine
and profile version, text release (id and hash) or SBERT revision, image
model and revision, image chance levels, weights, thresholds, coverage
rules, chronology settings, environment versions, protocol and limitations.
Constructed or unreviewed data is labelled "ENGINEERING CHECK ONLY". Reports
for the constructed pilot go to `evaluation/results.json`; others to
`evaluation/research/results-<hash>.json`.

`retune` and `evaluate_hybrid --apply` refuse anything that is not
research-ready; `--apply` merges the selected weights/thresholds into
`matching/profile.json` (keeping calibration and other fields) and requires
a version bump, restart and `rebuild_matches`.

## 10. Current pilot results (engine synopsis-v3, constructed data)

`evaluation/results.json` was regenerated for `synopsis-v3` (text
`lexical-v1`; ResNet-50, CLIP, histogram baseline and text+metadata only).
Earlier results (engine `synopsis-v1`/`pilot-v4`, pool-fitted TF-IDF) are
superseded and must not be quoted for this matcher. On this **constructed**
set (90 test pairs, 30 queries with one positive each), with weights and
threshold chosen on validation:

| Setting | Threshold | Test precision | Recall | F1 | ROC-AUC | MRR | F1 without images |
| --- | --- | --- | --- | --- | --- | --- | --- |
| lexical + ResNet-50 (selected) | 0.75 | 0.83 | 1.00 | 0.91 | 1.00 | 1.00 | 0.76 |
| lexical + CLIP | 0.80 | 0.91 | 1.00 | 0.95 | 1.00 | 1.00 | 0.78 |
| lexical + histogram baseline | 0.75 | 0.77 | 1.00 | 0.87 | 0.97 | 0.97 | 0.78 |
| lexical, text + metadata only | 0.95 | 0.91 | 1.00 | 0.95 | 1.00 | 1.00 | 0.95 |

These numbers only show that the pipeline runs end to end and ranks an
almost-identical photo and title above other items. Positives share the same
source photo and title text, so they say nothing about independently
photographed and worded campus reports. Precision@3 is 1/3 by construction
(one positive among three candidates).

## 11. Monitoring and research exports

`python manage.py monitor_matching` stores a score histogram, mean and tier
distribution with the model manifest and flags total-variation shifts above
0.2 between compatible snapshots (score distribution, not accuracy).
`python manage.py run_maintenance_scheduler` runs it periodically and exports
moderator-confirmed handovers through `export_confirmed_pairs` as restricted
review material (no usernames, contact fields or verification answers;
descriptions and photos may still contain personal data). Exports are
`needs-research-review` until a reviewer completes the fields above. The
AJAX-versus-manual discovery study is described in `docs/STUDY_PROTOCOL.md`.
