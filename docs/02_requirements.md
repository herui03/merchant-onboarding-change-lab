# 02 · Requirements (stable IDs, observable acceptance criteria)

All requirements describe a **synthetic** prototype. "Observable" means a tester can see the result
in the UI, the JSON API or the database without reading code. Scenario IDs (`AC-xx`) refer to
`acceptance/acceptance_scenarios.toml`, which was written before implementation. The
requirement-to-test mapping, with actual results, is generated into `docs/06_test_evidence.md`.

Priority: **M** = must for the release recommendation, **S** = should.

## Intake (INT)

| ID | Pri | Requirement | Observable acceptance criteria |
|---|---|---|---|
| REQ-INT-01 | M | An onboarding specialist can create an application with legal name, synthetic business identifier, product category, volume band and optional activity summary, validated on the server. | Valid input → 201 and status `draft`. Invalid input → 422 `VALIDATION_FAILED` with per-field messages, and no row is created. Limits are in `docs/04_data_api_contract.md`. |
| REQ-INT-02 | M | Evidence is document **metadata only**: type, title, issue date, page count, synthetic reference. Maximum 12 per draft. | No file field exists anywhere. A 13th document → 422 `DOCUMENT_LIMIT_REACHED`. |
| REQ-INT-03 | M | The business identifier matches `SYN-` followed by six digits, is unique, and cannot change after creation. | Duplicate → 409 `DUPLICATE_BUSINESS_IDENTIFIER`. The draft update contract has no identifier field; sending one → 422. |
| REQ-INT-04 | M | Submission freezes the draft into an immutable, numbered revision with a content sha256. Documents issued after the submission business date are refused. | Revision `n` appears in the revision list with its hash. Future-dated document → 422 `FUTURE_DATED_DOCUMENT`, nothing is created. |
| REQ-INT-05 | M | Evidence on a submitted revision cannot be edited in place. Changes require the needs-information → new revision path. | Draft edits while `submitted` → 409 `INVALID_TRANSITION`. Direct SQL UPDATE/DELETE on revision, document, evaluation, decision or event rows is aborted by triggers. Tampered content → approval 409 `REVISION_INTEGRITY_FAILURE`. |

## Workflow (WF)

| ID | Pri | Requirement | Observable acceptance criteria |
|---|---|---|---|
| REQ-WF-01 | M | Status changes only through named commands. Allowed: `draft→submitted`, `submitted→needs_information`, `needs_information→submitted`, `submitted→approved`, `submitted→rejected`, `rejected→draft` (reopen), and system `submitted→needs_information` (migration). | Any other command/state pair → 409 `INVALID_TRANSITION` or `ALREADY_DECIDED`. |
| REQ-WF-02 | M | There is no generic status update. | `PATCH`/`PUT` on an application → 405. A `status` field in any payload → 422. |
| REQ-WF-03 | M | A request for information needs a reason code and a 20–1000 character message, and binds the current revision. | Status becomes `needs_information`. The reason and message appear on the case and in the timeline. |
| REQ-WF-04 | M | A resubmission creates revision `n+1` with a 10–1000 character response note. Content identical to the previous revision is refused. Earlier revisions stay retrievable and unchanged. | Revision list shows both. Identical content → 422 `NO_CHANGES_SINCE_LAST_REVISION`. |
| REQ-WF-05 | M | Every successful command appends a timeline event (actor, business date, wall-clock time, reason). Events are append-only. | Case timeline lists events in order. UPDATE/DELETE on `event` is aborted. |

## Decisions (DEC)

| ID | Pri | Requirement | Observable acceptance criteria |
|---|---|---|---|
| REQ-DEC-01 | M | Approve and reject cite `revision_id`, `policy_version` and `evaluation_id`. They are accepted only when the revision belongs to the application and is current, its latest evaluation is under the policy effective on the business date, and the cited version and evaluation match that. | Other application's revision → 422 `REVISION_APPLICATION_MISMATCH`. Old revision → 409 `STALE_REVISION`. Evaluated under a superseded policy → 409 `POLICY_MIGRATION_REQUIRED`. Wrong label or evaluation → 409 `STALE_POLICY_EVALUATION`. |
| REQ-DEC-02 | M | Approval requires a complete evaluation. Rejection needs a reason code and a 20+ character reason. | Incomplete → 422 `EVIDENCE_INCOMPLETE` listing the unmet rules. |
| REQ-DEC-03 | M | Terminal decisions are immutable. At most one terminal decision can win per revision, even under concurrency. | Eight concurrent attempts → one 201 and the rest 409. One decision row per revision (unique index). |
| REQ-DEC-04 | M | Mutating requests carry a request key. A retry with the same key **and** the same binding (actor, operation, application, revision, policy version, evaluation, payload hash) replays the stored result without writing. Any other reuse → 409. | Retry → 200 with `replayed: true` and the same ids. Reuse on another case, actor, revision or payload → 409 `IDEMPOTENCY_KEY_CONFLICT`. |
| REQ-DEC-05 | M | Each decision stores a frozen snapshot (revision content, policy rules, evaluation) and its sha256. | The snapshot is visible on the case and in the JSON export. Its hash is recomputable. |
| REQ-DEC-06 | M | A rejected application returns to work only through a reviewer's reasoned reopen (20+ characters), which starts a new draft cycle. The rejection stays unchanged. Approved applications cannot be reopened. | Reopen of approved → 409. After reopen, the old rejection row is unchanged and approval citing the rejected revision → 409. |
| REQ-DEC-07 | M | A failed command writes nothing: no partial decision, evaluation, migration or success event. | Row counts are unchanged after each failure, including a migration that fails part-way through. |

## Roles (ROLE) — synthetic, not enterprise authentication

| ID | Pri | Requirement | Observable acceptance criteria |
|---|---|---|---|
| REQ-ROLE-01 | M | The role matrix in `docs/04_data_api_contract.md` is enforced in the service layer, so the UI and API share one check. | Wrong role → 403 `FORBIDDEN_ROLE`. The UI hides actions the actor cannot take, but the server check does not depend on that. |
| REQ-ROLE-02 | M | Self-review: an actor who created the application or submitted any of its revisions cannot request information on it, approve it or reject it. | 403 `SELF_REVIEW_FORBIDDEN` for dual-role actor Kai on MOB-0008. |
| REQ-ROLE-03 | M | The auditor is read-only. | Any POST by the auditor except the lab actor switch → 403. |

## Policy change (POL)

| ID | Pri | Requirement | Observable acceptance criteria |
|---|---|---|---|
| REQ-POL-01 | M | Policy versions are immutable data. The effective version for date D is the latest with `effective_from ≤ D`. Before 2026-01-01 no version is effective. | Examples PA-01..PA-10 hold. Submission with no effective policy → 409 `NO_EFFECTIVE_POLICY`. |
| REQ-POL-02 | M | v2 (effective 2026-07-01, CR-001) adds EV-05. SUBSCRIPTION or FUTURE_DELIVERY in band V3 or V4 require a SERVICE_SCOPE statement issued 0–90 days before the revision's submission date (both ends inclusive). | Examples PE-07..PE-20, PE-23 and PE-24 hold. |
| REQ-POL-03 | M | Evaluation is deterministic: the same revision content, submission date and policy always give the same result. | Engine output equals every hand-authored PE example. |
| REQ-POL-04 | M | Applications approved under v1 are grandfathered. Their frozen snapshot stays authoritative and migration never re-evaluates them. | After migration, MOB-0001 and MOB-0002 are unchanged byte for byte and classified `GRANDFATHERED_APPROVED`. |
| REQ-POL-05 | M | In-flight rule: on or after the effective date, decisions on revisions evaluated under a superseded version are blocked until the policy owner applies v2. Migration re-evaluates each in-flight current revision. A submitted case with unmet v2 rules moves to `needs_information` with system reason `POLICY_CHANGE_EVIDENCE_REQUIRED`. Earlier evaluations are kept. | Classifications match `acceptance_scenarios.toml` `seed_case.expected_migration`. |
| REQ-POL-06 | M | The policy comparison page is built from the implemented rule data and engine. It shows a rule diff, a product × band requirement matrix, live per-application impact and the linked requirement and test evidence. | Changing a rule file changes the page. There is no hard-coded comparison text. |
| REQ-POL-07 | M | The migration preview is read-only. Migration is idempotent: a second run changes nothing. | Database fingerprint unchanged by the preview. Second run → every in-flight case `ALREADY_ON_TARGET`. |
| REQ-POL-08 | M | Evidence age is measured **as of the revision's submission date**, including during migration re-evaluation. A stored evaluation never changes as the clock moves. A more recent document requires a new revision. | The UI labels ages "as of submitted YYYY-MM-DD". Scenario AC-25 holds. |
| REQ-POL-09 | M | The server-side lab business date is the only date authority. Callers cannot supply submission, decision or effective dates, and a caller's `policy_version` is a binding assertion only. | Unknown date fields → 422. A `policy_version` of `v1` after v2 is applied → 409 and never selects v1 rules. |

## Non-functional and controls (NFR)

| ID | Pri | Requirement | Observable acceptance criteria |
|---|---|---|---|
| REQ-NFR-01 | M | State persists in the named SQLite database across restarts. | A restarted app shows the same cases, revisions, decisions and clock. |
| REQ-NFR-02 | M | The app binds `127.0.0.1:5058` and accepts only localhost Host headers. | Foreign Host → 400 `HOST_NOT_ALLOWED`. |
| REQ-NFR-03 | M | Every POST requires the session CSRF token. A foreign Origin is refused. | Missing or wrong token → 403 `CSRF_FAILED`. Foreign Origin → 403 `ORIGIN_NOT_ALLOWED`. |
| REQ-NFR-04 | M | User text is always escaped. CSV export neutralises spreadsheet formulas. | `<script>` shows as text. Cells starting with `= + - @`, tab or CR get a `'` prefix. |
| REQ-NFR-05 | M | GET pages and API reads never write to the database. | Fingerprint unchanged across all GET routes. |
| REQ-NFR-06 | M | Invalid identifiers and dates return 404 or 422 with a JSON or HTML explanation, never 500. | Covered by AC-16. |
| REQ-NFR-07 | M | `launch_demo.command` checks dependencies without installing anything, preserves an existing named database, seeds only a missing one, and resets only on explicit request with a backup. | Covered by AC-15. |
| REQ-NFR-08 | S | Request bodies are limited to 64 KB, and field lengths as per the contract. | Larger body → 413. |
| REQ-NFR-09 | S | Pages are usable at desktop (1440 px) and narrow (390 px) widths without horizontal page overflow or console errors. | Browser check measures `scrollWidth ≤ innerWidth` and the console error count. |
