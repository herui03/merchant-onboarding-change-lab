# 08 · Three-minute demo script

## Before you start (about 1 minute, not part of the 3 minutes)

1. If the lab is already running, stop it: press `Ctrl+C` in its terminal window. `--reset` refuses to run while
   port 5058 is busy or while any lab server holds the database, and in that case nothing is changed.
2. Run `./launch_demo.command --reset`. It backs up the current database into `instance/backups/`, reseeds,
   and starts the server.
3. Open `http://127.0.0.1:5058/` in **one** browser window.

If the live app can't run, open `replay/case_study_replay.html` and say clearly that it is a **recorded replay**.

> **Why one window?** The synthetic actor ("Acting as") is stored in the browser's session cookie.
> Every tab and window of the same browser profile shares it, so switching actor in one tab switches it
> everywhere. The main path below therefore uses one window and switches actor explicitly. The stale-tab
> refusal needs two *separate* cookie jars and is an optional exercise at the end.

## Main path (single window)

| Time | Acting as → screen and action | What to say |
|---|---|---|
| 0:00–0:20 | (no actor) **Overview**. Point at the yellow banner. | "This is a synthetic BA case study: a merchant onboarding workflow at a fictional payment provider. The policies, companies and documents are invented, so it is not a KYC or legal tool. The business problem is how to change an evidence rule while cases are already in the pipeline." |
| 0:20–0:45 | Select **Sam Okafor — Risk Reviewer** → *Switch* → **Cases → MOB-0003**. Point at the evaluation table and the blue binding box. | "Today is 24 June in the lab, so policy v1 applies. This subscription merchant's revision 1 is complete under v1. My decision would be bound to exactly revision 1, policy v1 and this evaluation id." |
| 0:45–1:05 | Still Sam → **Lab controls** → date `2026-07-01` → *Set lab date*. Then **Cases → MOB-0003** → *Approve revision 1 under v1*. | "The lab clock is a simulation control that any actor except the auditor may move. The effective date arrives, and nothing changes silently. If I approve now, the server refuses with POLICY_MIGRATION_REQUIRED, because a decision made on or after 1 July is a v2-era decision." |
| 1:05–1:35 | Select **Morgan Blake — Policy Owner** → *Switch* → **Policy change**. Scroll: rule diff → matrix → impact. Tick the confirmation box and click *Apply v2 to in-flight applications*. | "The comparison is computed from the rule data. EV-05 is added for subscriptions and future delivery in bands V3/V4. The impact preview is read-only: MOB-0003 gets a new gap, and MOB-0002 was approved under v1, so it is grandfathered. The gap is shown but not applied. Applying v2 is recorded as a run." |
| 1:35–2:10 | Select **Alex Rivera — Onboarding Specialist** → *Switch* → **MOB-0003**. Under *Add document metadata* enter: type `SERVICE_SCOPE`, title `Service-scope statement`, issued `2026-06-28`, pages `3`, reference `SYN-DOC-LSC001` → *Add document*. Enter a response note → *Resubmit as revision 2*. | "MOB-0003 now needs information, with a system reason naming the missing statement. I add the document metadata and resubmit. That creates revision 2, evaluated under v2. The age is measured as of the submission date, which is a documented trade-off." |
| 2:10–2:35 | Select **Sam Okafor** → *Switch* → **MOB-0003** → *Approve revision 2 under v2*. Then open **MOB-0002**. | "Now I approve the current revision under v2, and a frozen snapshot is stored. MOB-0002 is still approved under v1, unchanged, and marked grandfathered." |
| 2:35–3:00 | **UAT evidence** page. | "The scenarios were written before the code. These are developer acceptance checks run by automated tests and a Chromium run. External UAT has not been done and there is no signoff. That would be the next step." |

## Optional exercise: stale tab refused (about 3 minutes, needs two cookie jars)

Use **two separate browser contexts**: a normal window (**A**) and a private/incognito window (**B**) of the
same browser. Private windows keep their own cookies. Two separate browser profiles, or two different browsers,
also work. Start from a fresh `--reset`.

1. **Window A:** open `http://127.0.0.1:5058/`, select **Sam Okafor** → *Switch* → open **MOB-0003**. You see
   "You are inspecting: revision 1 … under v1". **Leave window A untouched from here on. Do not refresh it and do not switch its actor.**
2. **Window B (private):** open `http://127.0.0.1:5058/`, select **Morgan Blake** → *Switch* → **Lab controls** →
   `2026-07-01` → **Policy change** → tick the box → *Apply v2*.
3. **Window B:** select **Alex Rivera** → *Switch* → **MOB-0003** → add the `SERVICE_SCOPE` document (as above) →
   *Resubmit as revision 2*.
4. **Window A:** still showing Sam's revision-1 form, click *Approve revision 1 under v1*.
   Expected: a red banner **`STALE_REVISION`** ("You inspected revision 1, but MOB-0003 is now at revision 2 …"),
   with nothing written. The session in window A is still Sam, because window B's actor switches never touched it.
5. **Window A:** now refresh. The binding box shows revision 2 · v2, and approval succeeds.

Other optional checks, which work in one window:

- **Self-review:** select **Kai Nakamura** and approve **MOB-0008**. Expected: `SELF_REVIEW_FORBIDDEN`.
- **Rejected case:** as Sam, open **MOB-0007**. There is no approve form, only *Reopen with reason*.

**Honesty lines to keep:** "AI implemented and tested it; another AI reviewed it; I directed the work and
I'm learning the BA practice through it." Only claim what you have personally reproduced (see `09_cv_templates.md`).

**Likely follow-ups:** why measure age at submission (DEC-04), why block decisions instead of auto-migrating
(DEC-03), how idempotency works (DEC-05), what "self-review" covers (DEC-12). See the 15 Q&As in the Chinese
guide (`docs/zh/learning_guide_zh.md`).
