# 09 · Honest CV / interview templates (conditional)

These templates are **conditional**. Use a line only after **you personally** have done what its
condition says. The AI implemented the code, tests and documents. Codex (AI) reviewed them.
Herui directed the work. Do not present AI work as your own.

## Conditions checklist (tick before using any line)

| # | Condition you must personally meet | How to meet it |
|---|---|---|
| C1 | You ran the app yourself from a fresh clone | Clone, `./launch_demo.command`, open http://127.0.0.1:5058/ |
| C2 | You walked the demo end to end yourself | Follow `08_demo_script.md` without help |
| C3 | You ran the test suite yourself and read the result | `.venv/bin/python -m pytest -q tests`; note the pass/fail/skip counts **you** saw |
| C4 | You can explain every decision in `03_assumptions_decisions.md` without notes | Rehearse the 15 Q&As in `docs/zh/learning_guide_zh.md` |
| C5 | You changed something yourself and saw a test catch it | For example, change `max_age_days` to 60 in `policies/v2.toml`, see PE-15 fail, then revert |
| C6 | You wrote or rewrote an artefact yourself | For example, your own version of the CR-001 impact matrix or a new acceptance scenario |

## Templates

**Project line (needs C1 + C2 + C4):**
> Directed an AI-built synthetic merchant-onboarding prototype (Python/Flask/SQLite) as a BA/UAT learning
> case study. Defined the policy-change scope with the AI and reviewed its requirements, acceptance scenarios and change impact.

**Requirements and UAT line (needs C4 + C6):**
> Worked through stable requirement IDs, acceptance scenarios written before implementation, and a
> requirement-to-test evidence map for a fictional effective-dated policy change, including grandfathering and in-flight migration rules.

**Testing line (needs C3 + C5):**
> Re-ran the automated acceptance suite (service, HTTP and Chromium browser checks) and verified that
> changing a policy boundary makes the corresponding hand-authored example fail.

**Interview framing (always true):**
> "It's a synthetic learning project. An AI implemented it under my direction and another AI reviewed it.
> My learning focus was the BA side: how to specify an effective-dated rule change, handle cases already in flight,
> and prove it with acceptance evidence. External UAT was never performed."

## Never claim

- That you wrote the code, tests or documents (unless C6 covers a specific artefact you rewrote).
- Stakeholder interviews, real users, real merchants, or real KYC/AML/compliance experience from this project.
- That UAT was performed or signed off, or that anything was deployed or used in production.
- Test numbers you have not seen yourself (C3). Quote your own run, not the recorded one.
- That the 90-day or V3/V4 thresholds reflect any real regulation.
