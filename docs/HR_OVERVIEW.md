# Merchant Onboarding Policy Change Lab — overview

A payment provider must check a new business customer's documents before the customer can take card payments. This
project is a small web app that runs that review and shows how a rule change is rolled out while applications are still
being reviewed. It is a Business Analyst case study with automated acceptance checks, built on synthetic data.

## What the app does

- **Review workflow.** A specialist prepares an application, and a reviewer approves it, rejects it or asks for more information.
  Each submission becomes a numbered, read-only version, and every step is logged.
- **Rules with a start date.** From 1 July 2026 the new rules (v2) also require a service-scope statement, issued within the
  90 days before submission, from subscription and pay-in-advance businesses in the two highest volume bands.
- **Controlled rollout.** The policy owner previews the effect of the new rules on every case before applying them. Earlier
  approvals stay in place, and cases in review that now lack the document go back with a reason.
- **Protected decisions.** Every decision is tied to the exact version the reviewer saw. Decisions from an out-of-date screen,
  reviews of one's own case and actions outside a person's role are refused, and nothing is saved.

## Example: a rule change during review

1. On 22 June, *Larkspire Streaming Club Ltd*, a subscription business, is submitted and meets the old rules. On
   24 June, reviewer Sam opens it and leaves the page open.
2. The new rules take effect on 1 July. The policy owner applies them, and Larkspire is sent back for the missing
   service-scope statement. A customer approved in June stays approved.
3. The specialist adds the statement and resubmits. The new version meets the new rules.
4. Sam clicks *Approve* on the page opened on 24 June. The app refuses because that page shows an old version, and saves
   nothing. Sam reloads and approves the new version.

## See it

- **Demo:** the [three-minute demo script](08_demo_script.md) walks through this example in the running app.
- **Replay:** the [recorded replay](../replay/case_study_replay.html) is an offline recording of the same steps, not the
  live app (download it and open it in a browser).
- **Screenshots:** [rules compared](screenshots/06-policy-comparison-and-preview-desktop.jpg) ·
  [case sent back](screenshots/08-needs-information-after-migration-desktop.jpg) ·
  [out-of-date approval refused](screenshots/10-stale-approval-refused-desktop.jpg) ·
  [earlier approval unchanged](screenshots/12-grandfathered-v1-approval-desktop.jpg) ·
  [all screenshots](screenshots/)

## Built with

Python and Flask, a SQLite database file, and plain HTML/CSS. Tests use pytest, and Playwright drives a Chromium browser at
desktop and phone widths, and GitHub Actions runs them automatically. The app runs on macOS or Linux with Python 3.11+
([setup](../README.md#run-it-locally)).

## Testing

Expected results were written first: 24 rule examples, 10 date examples and 25 acceptance scenarios. In the
recorded run on code commit `6a6be96`, 117 automated checks passed, 0 failed and 0 skipped ([test evidence](06_test_evidence.md)).
The six product defects found during development were fixed and covered by tests ([defect log](defect_log.md)).

## Limits

- **Data:** the company, customers, people and documents are invented, and only document details are stored.
- **Scope:** this is not a Know-Your-Customer, legal or compliance check. The rules and thresholds are made up, and the
  business background was written for the case study rather than taken from interviews.
- **Validation:** the checks are automated developer tests. External user acceptance testing (UAT) has not been performed,
  and there is no business sign-off.
- **Use:** it runs locally only and has not been deployed. "Acting as" a person is a demo switch, not a login, and the
  date is simulated.
