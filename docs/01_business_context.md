# 01 · Business context (fictional As-Is / To-Be)

> **Authored scenario.** Fernhill Payments, its teams, volumes and pain points are invented for this
> case study. None of it comes from stakeholder interviews. Each claim below is an assumption (see
> `03_assumptions_decisions.md`) that a real BA would validate.

## Problem statement

Fernhill Payments (fictional) onboards corporate merchants for card acceptance. Its evidence policy
must change on **2026-07-01** (CR-001). Some merchants sell things that are delivered **later**:
subscriptions, or pre-paid services such as tours and event passes. If such a merchant fails, cardholders
who have already paid ask for refunds, and at higher volumes that exposure becomes significant. The policy owner
wants reviewers to see what is sold and when it is delivered — a **service-scope statement** —
before those merchants are approved in the elevated volume bands.

The business question is not only "what is the new rule?" but **"how do we change a rule while cases
are moving through the process, without losing the audit trail or approving on stale information?"**

## Actors (synthetic)

| Role | In this lab | What they need |
|---|---|---|
| Onboarding specialist | Alex Rivera, Jordan Lee | Prepare applications, respond to information requests, and know exactly what evidence is required. |
| Risk reviewer | Sam Okafor, Dana Morales | Decide on a fixed, complete snapshot, and never on something that changed after they looked. |
| Dual-role staff | Kai Nakamura (specialist + reviewer) | Can do both jobs, but must never review a case they prepared themselves. |
| Policy owner | Morgan Blake | Change the rule on a known date, see the impact first, and apply it visibly. |
| Auditor | Riley Park | Read everything, change nothing, and reconstruct who decided what, under which rule. |

## As-Is (fictional, authored)

```
Specialist                     Shared spreadsheet + email                    Reviewer
──────────                     ──────────────────────────                    ────────
collects documents  ──►  row per merchant, status column edited by hand  ──►  opens row, emails questions
edits the row in place ◄──  "latest" attachments overwrite older ones   ◄──  approves by typing "APPROVED"
```

Pain points (authored assumptions, not measured):

| # | Pain point | Consequence |
|---|---|---|
| P1 | Status is a free-text cell that anyone can edit | "Approved" can appear without a reviewer decision, and a rejected case can quietly become approved. |
| P2 | Evidence is overwritten in place | Nobody can prove what the reviewer actually saw when they decided. |
| P3 | Policy changes arrive as an email ("from July, also ask for …") | Nobody knows which open cases the change affects. Some reviewers apply it early and others late. |
| P4 | Approvals are not tied to a policy version | Months later, you can't tell whether a merchant was approved under the old rule or wrongly under the new one. |
| P5 | Double-clicks and retries create duplicate rows or emails | There are conflicting records of the same decision. |
| P6 | People sometimes review their own cases | The four-eyes control is only a convention. |

## To-Be (implemented in this prototype)

```
draft ──submit──► submitted ──request information──► needs_information ──resubmit──► submitted (revision n+1)
                      │                                                                │
                      ├──approve (binds revision + policy + evaluation)──► approved ◄─┘
                      └──reject (reason ≥ 20 chars)──► rejected ──reasoned reopen──► draft (new cycle)

On/after 2026-07-01: policy owner previews impact ──apply v2──► in-flight cases re-evaluated
      submitted + new v2 gap  ─► needs_information (system reason POLICY_CHANGE_EVIDENCE_REQUIRED)
      approved under v1       ─► grandfathered (frozen snapshot, unchanged)
```

| Pain point | To-Be control | Requirement |
|---|---|---|
| P1 | Named commands only, a DB status-transition trigger, and no generic update (405) | REQ-WF-01/02 |
| P2 | Immutable numbered revisions with content hashes. Evidence can only change through a new revision | REQ-INT-04/05, REQ-WF-04 |
| P3 | Versioned policy data with an effective date, a read-only impact preview and a visible migration run | REQ-POL-01/05/06/07 |
| P4 | Each decision binds revision + policy version + evaluation and stores a frozen snapshot | REQ-DEC-01/05 |
| P5 | Request keys: retries replay, and conflicting reuse is refused | REQ-DEC-04 |
| P6 | Server-side self-review rule ("created or submitted") | REQ-ROLE-02 |

## Scope

**In scope:** the corporate merchant application workflow described above, document metadata, a
fictional two-version evidence policy, migration of in-flight cases, auditability, and developer
acceptance evidence.

**Out of scope:** real identity verification, sanctions and PEP screening, document upload and inspection,
pricing, contracts, post-approval changes (for example, volume increases), notifications, enterprise
authentication, deployment and external UAT.
