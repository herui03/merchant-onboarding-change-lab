# 00 · Scope and truthfulness statement

**Project:** Merchant Onboarding Policy Change Lab — Business Analyst & UAT Case Study
**Status:** independent, synthetic learning prototype. Private repository. Not deployed.

## What this is

A small, runnable workflow prototype for **corporate merchant onboarding at a fictional
payment provider ("Fernhill Payments", invented)**. It shows one coherent business change,
end to end:

1. An onboarding specialist prepares an application (business identifier, product category,
   declared monthly volume band, and **document metadata only**).
2. A risk reviewer inspects an immutable submitted revision against the policy evaluation,
   and either requests information, approves or rejects it.
3. The fictional evidence policy changes from **v1** to **v2** on **2026-07-01** (change request
   CR-001): designated products in elevated volume bands must also provide a service-scope
   statement issued within 90 days before the revision's submission date.
4. The change is handled deliberately: approved v1 cases are grandfathered with frozen
   snapshots, in-flight cases follow an explicit migration rule, and stale reviewer actions fail.

## What this is not

| Not claimed | Why it matters |
|---|---|
| Real KYC/KYB, AML, sanctions or legal/compliance assessment | Policies, thresholds and evidence types are invented for practice. The 90-day window and V3/V4 bands are **not** legal or regulatory thresholds. |
| Real merchant eligibility | All companies, identifiers (`SYN-######`) and documents are synthetic. No real IDs or documents may be entered; the identifier format rejects them. |
| Document handling | Only metadata (type, title, issue date, page count, synthetic reference) is stored. No files are uploaded. |
| Enterprise authentication | "Acting as" a synthetic actor is a lab switch. Role and self-review rules are enforced in the service layer, but anyone at the keyboard can switch actor. |
| External messaging | Nothing is emailed or sent anywhere. The app binds to `127.0.0.1` only. |
| Stakeholder interviews | Business context, pain points and assumptions were **authored** for the case study. None come from interviews. |
| External UAT | Acceptance checks in this repo are **developer acceptance checks** (automated service and browser tests). No external business tester has executed UAT and no signoff exists. |

## Who did what (attribution)

| Contributor | Role in this project |
|---|---|
| **Herui** | Directs the project, sets scope and acceptance intent, and is learning BA/UAT practice through it. |
| **Claude (AI, Claude Code)** | Designed and implemented the code, wrote documents, authored the expected examples, ran the tests and browser checks, and captured the screenshots. |
| **Codex (AI)** | Independent reviewer. Its pre-implementation review added four edge cases (idempotency binding, no partial writes on failure, business-date authority, no in-place evidence edits). Any further review findings are recorded only when they actually happen. |

Herui has not personally calculated, implemented or verified the AI-produced work unless a
later document records that they reproduced it. The CV templates in
`docs/09_cv_templates.md` are conditional on that personal reproduction.

## Business-date authority (demo)

Time matters for an effective-dated policy, and today's real date is after 2026-07-01, so the
lab uses a **server-side simulated business date** stored in the database (`meta.business_date`):

- It is the **only** date used to choose the effective policy, stamp submissions and decisions,
  and to decide whether a migration may run.
- API callers and HTML forms **cannot** supply a submission, decision or effective date. Unknown
  fields such as `business_date` or `submitted_on` are rejected with `422`.
- A `policy_version` sent with a decision is a **binding assertion** ("this is what I inspected").
  It never selects the rule set. If it disagrees with the server's state, the request fails.
- The lab clock only moves forward and is changed through a labelled simulation control. In a
  real system, time is not user-controlled.

## Fixed scope decisions

- Stack: Python 3.11+, SQLite, Flask/Jinja. Runs offline with no credentials on `127.0.0.1:5058`.
  Other portfolio apps use ports 5057 and 5059.
- English UI, and a Chinese learning/interview guide in `docs/zh/`.
- Expected outcomes (`acceptance/`) were committed **before** the policy engine and workflow code.
  The git history shows the order.
