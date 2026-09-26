# 03 · Assumptions and decision log

## Source of the business context

Everything below was **authored** for a synthetic case study by the AI implementer, under Herui's
direction. **No stakeholder interviews were held.** Each "assumption" is a statement a real BA would
validate with real stakeholders before relying on it. The "Would validate with" column names the
fictional stakeholder role that would own the answer.

## Assumptions (authored, unvalidated)

| ID | Assumption | Why it matters | Would validate with |
|---|---|---|---|
| A-01 | Corporate merchant onboarding at the fictional provider is a two-role flow: an onboarding specialist prepares the case and a risk reviewer decides it. | It drives the role matrix and the self-review rule. | Head of Onboarding Ops (fictional) |
| A-02 | Reviewers work from a snapshot of the application. Changes after submission must produce a new revision, not overwrite. | It drives immutable revisions (REQ-INT-04/05). | Risk Review Lead (fictional) |
| A-03 | Deferred-delivery products (subscriptions, pre-paid services delivered later) carry refund exposure that grows with volume. | It motivates CR-001 / EV-05. | Credit Risk Policy Owner (fictional) |
| A-04 | Merchants already approved under v1 should not be re-papered because of CR-001. | It drives grandfathering (REQ-POL-04). | Policy Owner + Relationship Management (fictional) |
| A-05 | Cases already under review on the effective date must meet v2 before any decision, because a decision made on or after 2026-07-01 is a v2-era decision. | It drives the in-flight migration rule (REQ-POL-05). | Policy Owner (fictional) |
| A-06 | Declared monthly volume bands (V1–V4) are good enough to scope the rule. Actual processing data is not available at onboarding. | The rule keys on the declared band. | Risk Analytics (fictional) |
| A-07 | Document metadata is enough to practise the workflow. Real document inspection is out of scope. | It avoids handling real documents. | — (scope decision) |

## Decision log

| ID | Date (lab) | Decision | Alternatives considered | Trade-off / consequence |
|---|---|---|---|---|
| DEC-01 | 2026-06 | **Policy version is chosen by the server business date at submission.** Each revision is evaluated at submission under the policy effective on that date. | Choose by application creation date; or evaluate lazily at decision time. | The evaluation a reviewer sees is fixed and auditable. It needs an explicit migration rule for cases already in flight on the effective date (DEC-03). |
| DEC-02 | 2026-06 | **Grandfather v1 approvals** with a frozen decision snapshot. Migration never re-evaluates terminal cases. | Re-review every approved merchant under v2. | No re-papering effort, but some approved merchants (MOB-0002) knowingly lack EV-05 evidence. The impact view shows that gap as "not applied" so the business can accept it knowingly. |
| DEC-03 | 2026-06 | **Explicit, visible migration of in-flight cases** by the policy owner, on or after 2026-07-01. Until migration runs, decisions on v1-evaluated revisions are blocked (`POLICY_MIGRATION_REQUIRED`). Submitted cases with v2 gaps move to `needs_information` with system reason `POLICY_CHANGE_EVIDENCE_REQUIRED`. | Silent automatic re-evaluation when the clock passes the date; or allow v1 decisions until a grace date. | The change is visible and attributable, and history is kept. The cost is one operational step, and decisions pause between the effective date and the migration run. |
| DEC-04 | 2026-06 | **Evidence age (EV-05's 90 days) is measured as of the revision's original submission date, including during migration re-evaluation** (example PE-24). A stored evaluation never changes when the clock moves. If the business wants a more recent statement, the reviewer requests information and the specialist submits a **new revision**. | Measure age at decision date; or at migration date. | **Pro:** deterministic and reproducible. The same revision always gives the same result, and a reviewer's decision context can't change underneath them. **Con:** a statement 85 days old at submission may be about 150 days old when a slow case is finally approved. The rule therefore does **not** guarantee freshness at decision time, and the UI says "age N days as of submitted YYYY-MM-DD" rather than implying decision-date freshness. A follow-up option (not built): a maximum review duration after which a new revision is required. |
| DEC-05 | 2026-06 | **Idempotency keys bind actor, operation, application, revision, policy version, evaluation and payload hash.** Only successful results are stored. | Keys scoped per actor only; or storing failed responses too. | A retry can never replay another case's decision. A failed attempt does not reserve the key, so the caller may correct the payload and retry with the same key. |
| DEC-06 | 2026-06 | **Failed commands write nothing.** Each command runs in one SQLite `BEGIN IMMEDIATE` transaction and rolls back on any error. Refused attempts are not written to the audit log. | Record refused attempts in an "attempt log". | No partial state or misleading success events. Refused attempts are visible only in HTTP responses. A real system might add a separate security log. |
| DEC-07 | 2026-06 | **The server lab clock is the single date authority.** It moves forward only, through a labelled simulation control. | Let callers pass an "as of" date. | Stale or forged dates can't select old rules. The clock is a demo control that a production system would not have. |
| DEC-08 | 2026-06 | **Rejections can be reopened with a reason; approvals cannot.** | No reopen at all; or reopen both. | This covers the realistic "merchant fixes the problem" path without mutating the original decision. Post-approval changes (for example, a volume increase) are out of scope. |
| DEC-09 | 2026-06 | **Identical resubmissions are refused.** | Allow them. | This stops no-op cycles. The specialist must change content and explain the change in the response note. |
| DEC-10 | 2026-06 | **Synthetic identifiers only** (`SYN-######`, `SYN-DOC-…`). | Free-form identifiers. | Real registry numbers can't be entered by mistake. |
| DEC-11 | 2026-06 | **Migration intervenes only for gaps the policy change creates.** A submitted case moves to `needs_information` only if a rule unmet under v2 was not already unmet under its previous evaluation (a "new gap"). Pre-existing gaps stay with the reviewer's normal process. | Move every incomplete in-flight case to `needs_information`. | The system reason `POLICY_CHANGE_EVIDENCE_REQUIRED` is only used when the policy change actually caused the gap. A case already missing baseline evidence keeps its existing reviewer-led path, and approval stays blocked by `EVIDENCE_INCOMPLETE`. |
| DEC-12 | 2026-06 | **Self-review scope is exactly "created or submitted".** A reviewer who created the application, or submitted any of its revisions, cannot request information on it, approve it, reject it or reopen it. Draft editors who never submitted are **not** excluded. | Exclude everyone who ever edited the draft. | This is simple and auditable from the revision table. The known gap is that a reviewer-specialist who only edited a draft (without submitting) could still review it. A real system would need a fuller involvement record. |

## Open questions a real BA would take to stakeholders

1. Should approval also require that the EV-05 statement is at most N days old on the **decision** date
   (see DEC-04 con)? That would be a v3 change request, not a silent reinterpretation of v2.
2. Is a pause in decisions between 2026-07-01 and the migration run acceptable operationally, or
   should migration be scheduled for 00:00 on the effective date?
3. Should grandfathered merchants be re-papered at their next periodic review?
