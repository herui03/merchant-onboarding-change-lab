# HR overview — Merchant Onboarding Policy Change Lab

*A plain-English summary for recruiters and hiring managers. The technical detail is in the [README](../README.md).*

## In one paragraph

This is a small, working **practice project** that models how a payment company might check new business customers
("merchants") before letting them take card payments, and what happens **when the checking rules change on a set
date while applications are already being reviewed**. It is a Business Analyst and testing (UAT) case study: it
contains written requirements, a documented rule change, expected results written before any code, a runnable
web app on a laptop, and recorded test results. **Everything in it is invented**: the company, the policy, the
merchants and the people. It was never used by a real business.

## Who did what

| Person / tool | Role |
|---|---|
| **Herui** | Set the direction, scope and acceptance goals, and is using the project to learn Business Analyst and testing practice. |
| **Claude** (AI coding assistant) | Designed and wrote the code, tests and documents, ran the checks and captured the screenshots. |
| **Codex** (AI reviewer) | Reviewed the work independently and found real problems, which were then fixed. They are listed in the [defect log](defect_log.md). |

This repository does **not** claim that Herui wrote the code. Herui's own understanding of the business problem,
the design decisions and the evidence is meant to be shown directly: by running the demo and answering
questions about it, not by what this page says.

## The problem it looks at

A payment company checks documents before approving a new business customer. Suppose the rules change on
**1 July 2026**: some higher-risk merchants (subscription businesses, and businesses paid in advance for things
delivered later) must now also provide a **"service-scope statement"**. That raises practical questions:

- Do customers who were **already approved** under the old rules need to be checked again? *(Here: no. Their
  approval is kept, and the gap is shown but not acted on.)*
- What happens to applications that are **half-way through review** on 1 July? *(Here: decisions pause until the
  policy owner applies the new rules, and cases missing the new document are sent back with a clear reason.)*
- How do we stop a reviewer approving something based on **an out-of-date screen**? *(Here: the system refuses it
  and records nothing.)*

## A normal flow (concrete example)

1. An onboarding specialist enters a fictional merchant, *Larkspire Streaming Club Ltd* (a subscription business),
   with the details of its documents (type, title, date issued, number of pages). The app stores only these
   details, never real document files.
2. They submit it. The app freezes that version as **revision 1** and checks it against the rules in force that
   day (the old rules, v1). Everything required is present.
3. The rules change on 1 July. The policy owner reviews an **impact preview** and applies the new rules (v2).
   The Larkspire case is sent back ("needs information") with a system reason, `POLICY_CHANGE_EVIDENCE_REQUIRED`,
   that names the missing service-scope statement.
4. The specialist adds the statement and resubmits. This becomes **revision 2**, checked under v2.
5. A risk reviewer who did not prepare the case approves revision 2. The decision records exactly which
   revision and which rule version it was based on.

## A failure flow (concrete example)

1. On 24 June (the demo's simulated date), reviewer Sam opens the Larkspire case, sees revision 1, and leaves that
   browser window open.
2. Meanwhile the new rules are applied, and the specialist submits revision 2.
3. Sam returns to the old page and clicks **Approve**.
4. The app **refuses** with `STALE_REVISION` ("You inspected revision 1, but MOB-0003 is now at revision 2 …"), and
   **nothing is saved**. Sam reloads, reviews revision 2 and approves it.

Other refusals shown the same way: approving before the new rules are applied, reviewing your own case,
approving a case that is already decided, or using the wrong role. A double-clicked button is recognised as the same
request, so the first result is shown and nothing is duplicated.

## What can be demonstrated in about 3 minutes

The steps are in the [three-minute demo script](08_demo_script.md). In outline:

1. The overview page and the "synthetic, not a real assessment" banner.
2. A reviewer sees a case under the old rules.
3. The date moves to 1 July, and approval is refused until the new rules are applied.
4. The policy owner compares old and new rules and previews the impact on every case, then applies them.
5. The specialist adds the missing document and resubmits.
6. The reviewer approves the new revision. An already-approved older case is shown as unchanged ("grandfathered").
7. The evidence page shows which checks ran, and states that no real user acceptance testing took place.

## Screenshots (real, from a recorded browser run)

| Step | Screenshot |
|---|---|
| Reviewer inspects a case under the old rules | [04 — inspected under v1](screenshots/04-mob0003-inspected-under-v1-desktop.jpg) |
| Approval refused until the new rules are applied | [05 — approval blocked](screenshots/05-approval-blocked-migration-required-desktop.jpg) |
| Rule comparison and impact preview | [06 — policy comparison](screenshots/06-policy-comparison-and-preview-desktop.jpg) |
| New rules applied to cases in progress | [07 — v2 applied](screenshots/07-v2-applied-desktop.jpg) |
| Case sent back for the missing document | [08 — needs information](screenshots/08-needs-information-after-migration-desktop.jpg) |
| Out-of-date page refused | [10 — stale approval refused](screenshots/10-stale-approval-refused-desktop.jpg) |
| New revision approved | [11 — approved under v2](screenshots/11-approved-under-v2-desktop.jpg) |
| Previously approved case left unchanged | [12 — grandfathered](screenshots/12-grandfathered-v1-approval-desktop.jpg) |
| Same app on a phone-sized screen | [09 — narrow width](screenshots/09-resubmitted-revision-2-under-v2-narrow.jpg) |

![Rule comparison and impact preview](screenshots/06-policy-comparison-and-preview-desktop.jpg)

The same screens are also collected in an offline [recorded replay](../replay/case_study_replay.html). Download it
and open it in a browser. It is a recording, not the live app.

## How it is built

- **Language and web:** Python 3.11+, Flask with Jinja page templates, and plain HTML/CSS. There is no JavaScript framework.
- **Data:** a single local SQLite database file. The database itself blocks edits to past revisions and decisions.
- **Checks:** pytest for the rules, workflow and web layer. Playwright drives a real Chromium browser through the demo at
  desktop and phone widths.
- **Automation:** GitHub Actions runs the checks on each push with read-only permissions.
- **Running it:** only on your own computer (`127.0.0.1`), using `./launch_demo.command`. No accounts, keys or paid
  services are needed. See the [README](../README.md).

## Evidence you can inspect

- **Expected results written first.** Before any code existed, 24 rule examples, 10 date examples and 25 acceptance
  scenarios were written and committed. The history shows they come first (commit `a20b34b`).
- **Recorded test run.** In the recorded run on code commit `6a6be96`, 117 automated checks passed, with 0 failed and 0 skipped.
  The first recording attempt on the same code had 2 failures and 1 skip, caused by the order in which checks
  ran. That is documented and fixed in the procedure. Details: [test evidence](06_test_evidence.md).
- **Real defects, openly logged.** Six product defects were found during development: three by the independent AI
  reviewer, and three by the implementing AI through its own page checks, screenshot review and running the demo script step by step. Each
  was reproduced, fixed and covered by a new check. See the [defect log](defect_log.md).
- **Requirements and change documents.** These are the [requirements](02_requirements.md), the
  [change request and impact](05_change_request_CR-001.md), the [decision log](03_assumptions_decisions.md) and the
  [release recommendation](07_release_recommendation.md).

## Limitations (please read)

- **Invented scenario.** It is not a real Know-Your-Customer, legal, compliance or eligibility check. The rules and
  thresholds are made up.
- **No real business impact.** Nobody used it, and there are no customers, savings or performance figures.
- **No real user acceptance testing.** The acceptance checks were run by the developers' automated tests. No business
  user tested it and nobody signed it off.
- **Not a production system.** It runs only on one computer. "Acting as" a person is a demo switch, not a login, and
  the date can be changed for the demo.
- **Business context written for the exercise.** The background and assumptions were written for the case study,
  not gathered from interviews.
- **Known design limits** are recorded in the [decision log](03_assumptions_decisions.md). For example, document age is
  measured on the submission date rather than the approval date.
