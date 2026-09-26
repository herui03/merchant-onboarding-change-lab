# 05 · Change request CR-001 — service-scope evidence for deferred-delivery merchants

| Field | Value |
|---|---|
| Change request | CR-001 (fictional) |
| Requested by | Policy owner (fictional role; Morgan Blake in the lab) |
| Policy versions | v1 (effective 2026-01-01) → v2 (effective **2026-07-01**) |
| Status in this lab | Implemented in the prototype and verified by developer acceptance checks. **External UAT not performed.** |
| Not a legal requirement | The 90-day window and the V3/V4 bands are invented for practice. |

## 1. The change

**New rule EV-05 (v2 only):** a SUBSCRIPTION or FUTURE_DELIVERY application in declared volume band
V3 or V4 must include a `SERVICE_SCOPE` document (service-scope statement) issued **0–90 days before the
revision's submission date**. Both ends are inclusive. EV-01 to EV-04 are unchanged.

Exact boundary behaviour (implemented in `moblab/policy_engine.py`, matching the hand-authored examples in
`acceptance/policy_examples.csv`):

| Dimension | Boundary | Examples |
|---|---|---|
| Effective date | Version effective on D = latest with `effective_from ≤ D`. 2026-06-30 → v1, 2026-07-01 → v2 | PA-03, PA-04 |
| Product | SUBSCRIPTION and FUTURE_DELIVERY are designated. CARD_PRESENT and ECOMMERCE are not | PE-13, PE-14 |
| Band | V3 and V4 are elevated. V1 and V2 are not | PE-09, PE-22 |
| Age window | age = submission date − issue date. Accepted when 0 ≤ age ≤ 90 | PE-15 (90 ✓), PE-16 (91 ✗), PE-17 (0 ✓), PE-18 (−1 ✗), PE-23 (89 ✓) |
| Several statements | One valid statement is enough | PE-19 |
| Substitutes | An `OTHER` document never satisfies EV-05 | PE-20 |
| Reference date during migration | The **original submission date** of the revision, not the migration or decision date (DEC-04) | PE-24, AC-25 |

## 2. Transition rules

| Case state on/after 2026-07-01 | Rule | Implemented as |
|---|---|---|
| Approved under v1 | **Grandfathered.** No re-evaluation. The frozen v1 snapshot stays authoritative. The hypothetical v2 gap is shown as "not applied". | `GRANDFATHERED_APPROVED` |
| Rejected | Unchanged. It can return only through a reasoned reopen, whose new revision is evaluated under the policy then effective. | `TERMINAL_REJECTED_UNCHANGED` |
| Draft (never submitted) | Nothing to migrate. It is evaluated at submission under the policy effective on that date. | `DRAFT_NOT_EVALUATED` |
| Submitted, evaluated under v1 | **Decisions blocked** (`POLICY_MIGRATION_REQUIRED`) until the policy owner applies v2. Then it is re-evaluated under v2. A **new** gap → `needs_information` with system reason `POLICY_CHANGE_EVIDENCE_REQUIRED`. No new gap → stays submitted under v2. | `MIGRATED_EVIDENCE_REQUIRED` / `MIGRATED_NO_NEW_EVIDENCE` |
| Needs information, evaluated under v1 | Re-evaluated under v2 and stays `needs_information`. A new gap adds a system information request. | `MIGRATED_PENDING_RESUBMISSION` |
| Submitted on/after 2026-07-01 | Evaluated under v2 at submission. No migration is needed. | — |
| Running v2 again | Changes no business state. In-flight cases show `ALREADY_ON_TARGET`, and only the run itself is recorded. | `ALREADY_ON_TARGET` |

Any reviewer form rendered before a resubmission or migration becomes stale. Submitting it fails with
`STALE_REVISION` or `STALE_POLICY_EVALUATION`, and nothing is written.

## 3. Portfolio impact (synthetic seed)

The expected column was written **before** implementation (`acceptance/acceptance_scenarios.toml`,
`seed_case.expected_migration`). The observed column is what the implemented migration produced. The test
`test_policy_migration_grandfathers_and_migrates_in_flight` asserts they are equal, and its result
is in `06_test_evidence.md`.

| Case | Product / band | Status on 2026-06-24 | v2 gap (as of submission date) | Expected classification | Observed | Status after |
|---|---|---|---|---|---|---|
| MOB-0001 Alder Street Bakery | CARD_PRESENT / V1 | approved (v1) | none | GRANDFATHERED_APPROVED | same | approved |
| MOB-0002 Quillon Travel Experiences | FUTURE_DELIVERY / V3 | approved (v1) | SERVICE_SCOPE:MISSING — **not applied** | GRANDFATHERED_APPROVED | same | approved |
| MOB-0003 Larkspire Streaming Club | SUBSCRIPTION / V3 | submitted (v1) | SERVICE_SCOPE:MISSING (new) | MIGRATED_EVIDENCE_REQUIRED | same | needs_information |
| MOB-0004 Tidewell Event Passes | FUTURE_DELIVERY / V4 | submitted (v1) | none (statement 18 days old at submission) | MIGRATED_NO_NEW_EVIDENCE | same | submitted (v2) |
| MOB-0005 Wrenfield Home Goods | ECOMMERCE / V2 | needs_information (v1) | BANK_CONFIRM:MISSING (pre-existing, not new) | MIGRATED_PENDING_RESUBMISSION | same | needs_information |
| MOB-0006 Nettlefield Fitness | SUBSCRIPTION / V2 | draft | — | DRAFT_NOT_EVALUATED | same | draft |
| MOB-0007 Vantory Gadgets | ECOMMERCE / V4 | rejected (v1) | — | TERMINAL_REJECTED_UNCHANGED | same | rejected |
| MOB-0008 Kestrelmoor Language Club | SUBSCRIPTION / V1 | submitted (v1) | none (V1 not elevated) | MIGRATED_NO_NEW_EVIDENCE | same | submitted (v2) |

The same table is computed live on the **Policy change** page, from the current database.

## 4. Change impact matrix

| Area | Impact | Change made | Evidence |
|---|---|---|---|
| Policy data | Adds rule EV-05, v2 effective 2026-07-01 | `policies/v2.toml`, loaded as an immutable row with rules sha256 | PE-01…PE-24, REQ-POL-02 |
| Evaluation engine | New optional age window and product/band scoping | `policy_engine.evaluate` (pure, deterministic) | `test_policy_engine.py` |
| Specialist process | Designated V3/V4 cases need one more document. Migrated cases get a system information request. | Draft card shows "if submitted today, requires …". The information-request banner names SERVICE_SCOPE. | AC-05, AC-21 |
| Reviewer process | Decisions pause between the effective date and the migration run. Forms are bound to revision + policy + evaluation. | Binding box on the case page. Stale forms are refused. | AC-07, AC-22 |
| Policy owner | Needs a preview and an explicit apply step | Read-only impact preview, confirm checkbox, migration run record | AC-05, REQ-POL-07 |
| Data | New evaluations for in-flight revisions. v1 evaluations kept | `policy_evaluation` unique per (revision, policy); `migration_run` and `migration_item` | AC-04, AC-05 |
| Audit | Must show who applied the change, when, and to which cases | `POLICY_MIGRATED` / `POLICY_MIGRATION_RUN` events and a per-case migration record | Audit timeline page |
| Reporting and export | Policy version per case | Case list policy column. CSV/JSON exports. | AC-17 |
| Controls | Grandfathered approvals must not be altered | Immutable decision rows and snapshot hashes | AC-04, AC-24 |
| Training (fictional) | Specialists: new document type. Reviewers: stale-form refusals. Policy owner: migration step. | Guided demo page and demo script | `08_demo_script.md` |

## 5. Risks and open questions

| # | Risk or question | Current handling |
|---|---|---|
| R1 | A statement valid at submission can be old by decision time (DEC-04). | This is stated explicitly in the UI ("age as of submitted …"). A possible v3 rule is listed as an open question. |
| R2 | Decisions pause until the policy owner runs the migration. | The banner on affected cases links to the impact page. The business must decide the operational timing (open question 2). |
| R3 | Grandfathered merchants such as MOB-0002 lack the new evidence. | Accepted by DEC-02. The gap is visible as "not applied". Re-papering at the next periodic review is an open question. |
| R4 | Self-review covers "created or submitted" only. | DEC-12 documents the gap: a reviewer who only edited a draft is not excluded. |
