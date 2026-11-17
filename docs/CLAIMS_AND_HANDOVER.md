# Claims, ownership and handover

How LostLink decides who an item belongs to and records its return. The rules
live in `claims/services.py` (claims) and `items/lifecycle.py` (reports); views
only authenticate, lock rows and call them.

## Four separate concerns

| Concern | Where | Meaning |
| --- | --- | --- |
| Verification status | `Claim.status` | submitted → questions → answered → approved / rejected; withdrawn by the claimant; returned after the handover |
| Ownership reservation | an `approved` claim | at most one per item (database constraint `one_verified_claim_per_item`) |
| Dispute | `ClaimDispute` | a concern raised alongside a claim; never changes its status |
| Physical handover | `HandoverSession` | the owner's one-time code; only a verified code (or a documented exception) returns the item |

**Claim approved ≠ item returned.**

## Lifecycle

```
Report:  pending → approved ⇄ claimed → returned
           │  ╲        │                 (approved → returned when the counterpart
           │   rejected ─→ pending        report is handed over)
           ▼                ▼
        withdrawn (reporter)   removed (moderator)
```

* Every report status change goes through `items.lifecycle.transition`, which
  validates the move, locks the row, records the previous and new state in the
  audit log and retires or reruns matching.
* Returned, withdrawn and removed are terminal and inactive: the report leaves
  search, matching and claim intake.
* A new report is published at once (audited as "published automatically",
  with no human actor) unless it may show personal details: the reporter
  ticked "This report shows personal details", or `media.suggests_sensitive`
  detects an ID card, bank card or personal document from the category, title
  or description. Those stay `pending` for a moderator, with the reasons stored
  on the report (`Item.review_reasons`) and shown in the queue. See
  `items/publishing.py`; `LOSTLINK_AUTO_PUBLISH=false` reviews every report.
* Editing a report applies the same rule: it stays published, or goes back to
  `pending` (audited as "resubmitted") if it now may show personal details. A
  report a moderator rejected always goes back to a moderator.

## Independent review

* Nobody approves, rejects or removes their own report, decides a claim they
  submitted or on a report they filed, resolves a dispute they raised or is a
  party to, or records a handover exception on their own case. The moderation
  queue lists a moderator's own pending reports separately as non-actionable.
* Approving a claim requires every private question answered and an evidence
  checklist: statement vs. report, private verification details, answers, and
  supporting evidence. Approval is refused if any check contradicts the claim
  or if none supports it. A reason is required for every decision.
* Expected answers (written by whoever asks a question) and the reporter's
  private verification details are shown only to the reporter and moderators.
  They guide the reviewer; nothing is approved by automatic comparison.

## Disputes and reassignment

* The claimant, the reporter or a moderator can raise a dispute while a claim is
  open or approved. An approved claim **keeps its reservation**; the handover is
  suspended (an active code becomes `suspended`).
* An independent moderator resolves it: **dismiss** (the claim continues; the
  owner creates a new code), **revoke** (an approved claim becomes rejected, the
  reservation is released and matching resumes) or **reject** (an open claim).
* Reassignment is therefore always two explicit, audited steps: revoke, then
  approve another claim.

## The claimant's other report

A claim is often started from a matching suggestion between the claimant's own
report and the item. A suggestion is not proof that both reports describe the
same object (a student can lose two similar wallets), so the handover never
closes the claimant's other report on its own. That report is closed only when
the link is **explicitly confirmed**:

* by the reviewing moderator at approval (a checkbox naming the suggested
  report; recorded in the approval's audit metadata), or
* by the claimant after the handover ("It was this item: close it" on the
  claim page).

Without a confirmation the report stays open and keeps matching, and the
claimant is notified that it is still open.

## Reconsideration (appeals)

A rejected claimant can request reconsideration with an updated statement,
what is new, and evidence photos. Limits: `CLAIM_MAX_APPEALS` (default 2) per
claim and `CLAIM_APPEALS_PER_DAY` (3) per user. It is refused while another
claimant holds verified ownership. Every statement is kept as an immutable
`ClaimRevision`, together with the decision it answers.

## Private evidence

Claimants can upload photos (receipt, a hidden mark). Each is decoded,
re-encoded as JPEG with all metadata removed, stored under `claim_evidence/`
(never served by the public media view) and shown only to the claimant and
moderators with `Cache-Control: private, no-store`. Files are deleted
`CLAIM_EVIDENCE_RETENTION_DAYS` (180) after the claim is finalised by
`python manage.py purge_claim_evidence`; the record stays for the audit trail.

## One-time-code handover

1. A moderator approves the claim: the item is reserved and shows **Handover
   pending**; it accepts no new claims and produces no match alerts.
2. The two people meet. The **recipient is always the owner**: the claimant for
   a found report, the reporter for a lost report (the claimant is the finder).
3. The owner opens the claim and creates a code: 6 digits from Python's
   `secrets` module, shown once (no-store page), valid 10 minutes, single use.
4. After receiving and checking the item, the owner tells the code to the
   person handing it over, who enters it on the claim page.
5. The server checks it inside a transaction with the rows locked; on success
   the claim and report become returned, the claimant's own counterpart report
   is resolved, matches are retired, both people are notified and the event is
   logged.

Security properties:

* Only a salted HMAC-SHA-256 of the code is stored; codes never appear in
  notifications, the audit log, URLs, analytics or moderator pages.
* At most one active code per claim (database constraint); a new code
  supersedes the old one.
* `HANDOVER_CODE_MAX_ATTEMPTS` (5) wrong entries lock the code; creation is
  limited to `HANDOVER_CODES_PER_HOUR` (5) per claim and verification to
  `HANDOVER_VERIFY_PER_10_MIN` (10) per user.
* Codes cannot be created before approval and are refused for disputed,
  rejected, withdrawn or returned claims.

### Moderator exception

When the code cannot be used (desk collection with an ID check, the owner
cannot use their account), an independent moderator can record the handover
with a reason and written evidence of at least 20 characters. It is logged as
**Handover recorded by exception**, distinct from a code-confirmed handover,
and analytics count the two separately.

## Audit trail

`ModerationDecision` records actor, action, item, claim, previous and new state,
reason and metadata (checklist, code generation and attempt counts — never the
code). Rows are append-only: `save()` on an existing row and `delete()` raise,
and the Django admin is read-only.

## Settings

| Setting | Env var | Default |
| --- | --- | --- |
| `HANDOVER_CODE_TTL_MINUTES` | `LOSTLINK_HANDOVER_CODE_TTL_MINUTES` | 10 |
| `HANDOVER_CODE_MAX_ATTEMPTS` | `LOSTLINK_HANDOVER_CODE_MAX_ATTEMPTS` | 5 |
| `HANDOVER_CODES_PER_HOUR` | `LOSTLINK_HANDOVER_CODES_PER_HOUR` | 5 |
| `HANDOVER_VERIFY_PER_10_MIN` | `LOSTLINK_HANDOVER_VERIFY_PER_10_MIN` | 10 |
| `CLAIM_MAX_APPEALS` | `LOSTLINK_CLAIM_MAX_APPEALS` | 2 |
| `CLAIM_APPEALS_PER_DAY` | `LOSTLINK_CLAIM_APPEALS_PER_DAY` | 3 |
| `CLAIM_EVIDENCE_MAX_FILES` | `LOSTLINK_CLAIM_EVIDENCE_MAX_FILES` | 5 |
| `CLAIM_EVIDENCE_MAX_BYTES` | `LOSTLINK_CLAIM_EVIDENCE_MAX_BYTES` | 5 MB |
| `CLAIM_EVIDENCE_RETENTION_DAYS` | `LOSTLINK_CLAIM_EVIDENCE_RETENTION_DAYS` | 180 |

Rate limits use Django's cache. With several server processes, configure a
shared cache (e.g. Redis or the database cache) so limits apply across them.

## Migrating existing data

* `claims.0007_migrate_disputed_claims`: each legacy `disputed` claim gets its
  real status back (approved if the audit log shows an unreversed approval and
  no other claim holds the item) and an open `ClaimDispute` with the recorded
  reason.
* `items.0007_explicit_withdrawn_removed`: inactive reports become `withdrawn`,
  or `removed` if they had been rejected and deactivated.
