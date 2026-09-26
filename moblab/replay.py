"""Build replay/case_study_replay.html: a self-contained, offline page made ONLY from recorded
outputs (evidence/*.json) and the screenshots that run captured. It is a recorded replay, not the
live workflow; every piece of text is HTML-escaped."""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path

from .evidence import requirement_titles
from .paths import DOCS_DIR, REPO_ROOT
from .queries import evidence_by_requirement, load_evidence

OUT = REPO_ROOT / "replay" / "case_study_replay.html"
SHOTS = DOCS_DIR / "screenshots"
EXTRA = {
    "02-guided-demo": "Guided demo page (live checklist read from the database)",
    "15-revision-2-diff": "Revision view: revision 2 vs revision 1 (added SERVICE_SCOPE)",
    "16-uat-evidence": "UAT evidence page (developer checks; external UAT not performed)",
    "17-audit-timeline": "Append-only audit timeline",
}
NARROW_FOR = {"05-approval-blocked-migration-required", "07-v2-applied", "09-resubmitted-revision-2-under-v2"}


def _img(name: str, alt: str) -> str:
    path = SHOTS / name
    if not path.exists():
        return f'<p class="missing">Screenshot {escape(name)} is missing from docs/screenshots/.</p>'
    mime = "image/jpeg" if path.suffix == ".jpg" else "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<img loading="lazy" alt="{escape(alt)}" src="data:{mime};base64,{data}">'


CSS = """
:root{--bg:#f5f6f8;--surface:#fff;--text:#1c2430;--muted:#5a6573;--border:#d9dee5;--brand:#1f4e79;--warn-bg:#fff4dc;--warn:#8a5a00;--ok:#1d7a46;--ok-bg:#e6f4ec;--bad:#a4262c}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#11151b;--surface:#182029;--text:#e6ebf1;--muted:#9aa7b6;--border:#2d3a48;--brand:#3d7ab8;--warn-bg:#33280f;--warn:#f0b95a;--ok:#5cc78c;--ok-bg:#16301f;--bad:#f08a8f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;overflow-wrap:break-word}
.banner{position:sticky;top:0;z-index:5;background:var(--warn-bg);color:var(--warn);font-weight:700;padding:10px 16px;text-align:center;border-bottom:1px solid var(--border)}
main{max-width:1080px;margin:0 auto;padding:20px 16px 60px}h1{font-size:1.7rem;margin:.2em 0}h2{margin-top:1.6em}.card>h2:first-child,.card>h3:first-child{margin-top:0}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin:0 0 16px}
.muted{color:var(--muted)}code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.88em}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left;vertical-align:top}
.wrap{overflow-x:auto}.step h3{margin:.1em 0 .4em}.kv{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 12px}
.kv dt{color:var(--muted)}.kv dd{margin:0}img{display:block;max-width:100%;height:auto;border:1px solid var(--border);border-radius:6px;margin-top:10px}
details summary{cursor:pointer;font-weight:600;margin-top:8px}.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}
nav ol{columns:2;padding-left:1.2em}@media(max-width:640px){nav ol{columns:1}.kv{grid-template-columns:minmax(0,1fr)}}
.missing{color:var(--bad)}
"""


def build_replay() -> Path:
    ev = load_evidence()
    story = load_evidence("e2e_story.json")
    if not ev or not story:
        raise SystemExit("Recorded evidence missing: run the evidence commands in docs/06_test_evidence.md first.")
    counts = ev["counts"]
    reqs = requirement_titles()
    by_req = evidence_by_requirement(ev)
    covered = [r for r in reqs if by_req.get(r, {}).get("passed")]
    h = [f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
         "<title>Onboarding Change Replay</title>",
         "<meta name=\"description\" content=\"Recorded replay of a developer Chromium run of the synthetic Merchant Onboarding Policy Change Lab.\">",
         f"<style>{CSS}</style></head><body>",
         f"<div class=\"banner\">RECORDED REPLAY — static snapshot of a developer Chromium run ({escape(story['generated_at_utc'])}, "
         f"commit {escape(story['git_head'][:12])}). Not the live workflow: nothing here is connected to a database.</div>",
         "<main>",
         "<h1>Merchant Onboarding Policy Change Lab — recorded replay</h1>",
         "<p class=\"muted\">Business Analyst &amp; UAT case study. An independent, synthetic workflow prototype: the policies, companies, "
         "people and documents are fictional. It is not a KYC, legal, compliance or eligibility assessment. To use the live workflow, "
         "clone the repository and run <code>./launch_demo.command</code>.</p>",
         "<div class=\"card\"><h2>What changed (CR-001)</h2><p>From <strong>2026-07-01</strong>, fictional evidence policy <strong>v2</strong> requires a "
         "<em>service-scope statement</em> for SUBSCRIPTION and FUTURE_DELIVERY merchants in declared volume bands V3/V4. The statement must be issued "
         "0–90 days before the revision's submission date. Approved v1 cases are grandfathered with frozen snapshots. In-flight cases "
         "are re-evaluated when the policy owner applies v2, and a case with a new gap moves to <em>needs information</em>. Decisions bind the exact "
         "revision, policy version and evaluation, so a stale action fails.</p></div>",
         "<div class=\"card\"><h2>Recorded evidence</h2><dl class=\"kv\">",
         f"<dt>Automated results</dt><dd><span class=\"ok\">{counts.get('passed', 0)} passed</span>, {counts.get('failed', 0)} failed, "
         f"{counts.get('skipped', 0)} skipped (recorded {escape(ev['generated_at_utc'])}, commit <code>{escape(ev['git_head'][:12])}</code>)</dd>",
         f"<dt>Requirements with passing automated evidence</dt><dd>{len(covered)} of {len(reqs)}</dd>",
         f"<dt>Browser</dt><dd>{escape(story['browser'])}, viewports {escape(', '.join(story['viewports']))}</dd>",
         "<dt>External UAT</dt><dd class=\"bad\">Not performed. There is no business signoff.</dd>",
         "<dt>Who did what</dt><dd>Claude (AI) implemented and tested. Codex (AI) reviewed independently. Herui directed the work and is learning from it.</dd>",
         "</dl></div>", "<nav class=\"card\"><h2>Steps</h2><ol>"]
    for i, st in enumerate(story["steps"], 1):
        h.append(f"<li><a href=\"#step-{i}\">{escape(st['action'])}</a></li>")
    h.append("</ol></nav>")
    for i, st in enumerate(story["steps"], 1):
        h += [f"<section class=\"card step\" id=\"step-{i}\"><h3>{i}. {escape(st['action'])}</h3><dl class=\"kv\">",
              f"<dt>Actor</dt><dd>{escape(st['actor'])}</dd><dt>Expected</dt><dd>{escape(st['expected'])}</dd>",
              f"<dt>Observed</dt><dd>{escape(st['observed'])}</dd></dl>"]
        if st.get("screenshot"):
            h.append(_img(st["screenshot"], f"Step {i} at desktop width"))
            base = st["screenshot"].rsplit("-desktop.", 1)[0]
            if base in NARROW_FOR:
                narrow = st["screenshot"].replace("-desktop.", "-narrow.")
                h.append(f"<details><summary>Same screen at 390 px width</summary>{_img(narrow, f'Step {i} at 390 px width')}</details>")
        h.append("</section>")
    ext = story["screenshots"][0]["desktop"]["file"].rsplit(".", 1)[1] if story["screenshots"] else "png"
    h.append("<h2>Other recorded screens</h2>")
    for base, caption in EXTRA.items():
        h.append(f"<section class=\"card\"><h3>{escape(caption)}</h3>{_img(f'{base}-desktop.{ext}', caption)}</section>")
    h += ["<div class=\"card\"><h2>Limits</h2><ul>",
          "<li>This page replays one recorded run. Numbers and screenshots come from that run's output files.</li>",
          "<li>The acceptance checks are developer checks. The business context and assumptions were authored, not gathered in interviews.</li>",
          "<li>“Acting as” is a lab switch, not authentication. The lab clock is a simulation control.</li>",
          "</ul></div></main></body></html>"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(h), encoding="utf-8")
    return OUT
