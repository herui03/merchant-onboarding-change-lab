"""Real Chromium against a real server process (python -m moblab run) on a fresh seeded database.

Recording (optional): set MOBLAB_SCREENSHOT_DIR to keep screenshots, and MOBLAB_E2E_STORY_OUT to
write the observed step log (used to build the offline recorded replay).
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed (pip install -r requirements-dev.txt)")

from moblab.seed import init_database  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DESKTOP = {"width": 1440, "height": 900}
NARROW = {"width": 390, "height": 844}
pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening_addresses(port: int) -> set[str]:
    """Local addresses with a LISTEN socket on `port`, from /proc/net/tcp{,6} (Linux only)."""
    found = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            addr, state = parts[1], parts[3]
            ip_hex, port_hex = addr.split(":")
            if state == "0A" and int(port_hex, 16) == port:
                found.add(ip_hex)
    return found


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium could not be launched: {exc}")
        yield b
        b.close()


@pytest.fixture
def server(tmp_path):
    db = tmp_path / "inst" / "merchant_onboarding_lab.db"
    init_database(db)
    port = _free_port()
    log = (tmp_path / "server.log").open("w")
    proc = subprocess.Popen([sys.executable, "-m", "moblab", "run", "--db", str(db), "--port", str(port)],
                            cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while True:
        try:
            with urllib.request.urlopen(base + "/", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            if time.time() > deadline or proc.poll() is not None:
                proc.terminate()
                raise RuntimeError((tmp_path / "server.log").read_text())
            time.sleep(0.2)
    yield {"base": base, "db": db, "port": port, "proc": proc}
    proc.terminate()
    proc.wait(timeout=10)
    log.close()


class Recorder:
    def __init__(self, tmp_path):
        out = os.environ.get("MOBLAB_SCREENSHOT_DIR")
        self.dir = Path(out) if out else tmp_path / "shots"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ext = os.environ.get("MOBLAB_SCREENSHOT_FORMAT", "png")
        self.steps: list[dict] = []
        self.shots: list[dict] = []

    def shot(self, page, name: str, *, full_page: bool = True):
        """Desktop and narrow screenshots, each with a horizontal-overflow measurement."""
        results = {}
        for label, size in (("desktop", DESKTOP), ("narrow", NARROW)):
            page.set_viewport_size(size)
            page.wait_for_timeout(50)
            problems = _layout_problems(page)
            path = self.dir / f"{name}-{label}.{self.ext}"
            if self.ext == "jpg":
                page.screenshot(path=str(path), full_page=full_page, type="jpeg", quality=62)
            else:
                page.screenshot(path=str(path), full_page=full_page)
            results[label] = {"file": path.name, **problems}
            _assert_layout(problems, name, size["width"])
        page.set_viewport_size(DESKTOP)
        self.shots.append({"name": name, **results})
        return results

    def step(self, actor, action, expected, observed, shot=None):
        assert observed, f"no observation for step: {action}"
        self.steps.append({"actor": actor, "action": action, "expected": expected, "observed": observed,
                           "screenshot": f"{shot}-desktop.{self.ext}" if shot else None})


SQUASHED_HEADERS_JS = """
() => [...document.querySelectorAll('th')]
  .filter(th => th.offsetParent !== null && th.scrollWidth > th.clientWidth + 1)
  .map(th => th.textContent.trim())
"""


SCROLLING_TABLES_JS = """
() => [...document.querySelectorAll('.table-wrap')]
  .filter(w => w.offsetParent !== null && w.scrollWidth > w.clientWidth + 1)
  .map(w => (w.querySelector('table') || w).getAttribute('data-testid') || 'table')
"""


def _layout_problems(page) -> dict:
    """Horizontal page overflow, table headers squeezed narrower than their text (DEF-005), and
    tables that need sideways scrolling (allowed on a phone, not at desktop width)."""
    return {"overflow_px": page.evaluate("document.documentElement.scrollWidth - window.innerWidth"),
            "squashed_headers": page.evaluate(SQUASHED_HEADERS_JS),
            "scrolling_tables": page.evaluate(SCROLLING_TABLES_JS)}


def _assert_layout(problems, where, width):
    assert problems["overflow_px"] <= 0, f"{where} at {width}px overflows: {problems}"
    assert not problems["squashed_headers"], f"{where} at {width}px: {problems}"
    if width >= DESKTOP["width"]:
        assert not problems["scrolling_tables"], f"{where} at {width}px needs sideways table scrolling: {problems}"


def _watch(page, errors):
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))


def _context(browser, base, actor, errors):
    ctx = browser.new_context(viewport=DESKTOP, base_url=base)
    page = ctx.new_page()
    _watch(page, errors)
    page.goto("/")
    page.select_option("[data-testid=actor-select]", actor)
    page.click("[data-testid=actor-switch]")
    page.wait_for_selector("[data-testid=flash]")
    return ctx, page


def _db_one(db, sql, *params):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _error_code(page) -> str:
    return page.locator("[data-testid=error-code]").inner_text().strip()


# Chromium logs one console error per document answered with a 4xx status. A refused action is
# answered with its real status (409/403), so each intentional refusal adds exactly one of these.
RESOURCE_ERROR = "console.error: Failed to load resource: the server responded with a status of {}"
REFUSAL_STATUS = {"POLICY_MIGRATION_REQUIRED": "409 (CONFLICT)", "STALE_REVISION": "409 (CONFLICT)",
                  "ALREADY_DECIDED": "409 (CONFLICT)", "SELF_REVIEW_FORBIDDEN": "403 (FORBIDDEN)"}


@pytest.mark.req("REQ-POL-04", "REQ-POL-05", "REQ-POL-06", "REQ-WF-04", "REQ-DEC-01", "REQ-DEC-03", "REQ-ROLE-02",
                 "REQ-NFR-09", "REQ-NFR-02", "REQ-POL-08")
@pytest.mark.ac("AC-21", "AC-22", "AC-08")
def test_before_after_story_in_chromium(browser, server, tmp_path):
    base, db = server["base"], server["db"]
    listening = _listening_addresses(server["port"])
    if listening:  # Linux: prove the server listens on 127.0.0.1 only
        assert listening == {"0100007F"}, listening
    rec = Recorder(tmp_path)
    errors: list[str] = []
    expected_errors: list[str] = []

    def refused(page, code):
        page.wait_for_selector("[data-testid=error-banner]")
        assert _error_code(page) == code
        expected_errors.append(RESOURCE_ERROR.format(REFUSAL_STATUS[code]))

    anon = browser.new_context(viewport=DESKTOP, base_url=base)
    page = anon.new_page()
    _watch(page, errors)
    page.goto("/")
    rec.shot(page, "01-overview")
    rec.step("Visitor", "Open the overview", "Scope banner and synthetic disclaimer visible; 8 seeded cases",
             f"count={page.locator('[data-testid=count-total]').inner_text()}; banner present="
             f"{page.locator('.synthetic-banner').count() == 1}", "01-overview")
    page.goto("/demo")
    rec.shot(page, "02-guided-demo")
    page.goto("/cases")
    rec.shot(page, "03-case-list")
    rec.step("Visitor", "Open the case list", "MOB-0001..MOB-0008 with status and policy columns",
             f"rows={page.locator('[data-testid=case-table] tbody tr').count()}", "03-case-list")

    # --- Before: Sam inspects MOB-0003 under v1 and keeps that tab open (it will go stale).
    sam_ctx, sam = _context(browser, base, "sam", errors)
    stale_tab = sam_ctx.new_page()
    _watch(stale_tab, errors)
    stale_tab.goto("/cases/MOB-0003")
    heading = stale_tab.locator("[data-testid=evaluation-heading]").inner_text()
    binding = stale_tab.locator("[data-testid=binding-box]").inner_text()
    assert "v1" in heading and "complete" in heading and "revision 1" in binding
    rec.shot(stale_tab, "04-mob0003-inspected-under-v1")
    rec.step("Sam Okafor (Risk Reviewer)", "Inspect MOB-0003 (SUBSCRIPTION/V3) on 2026-06-24",
             "Revision 1 evaluated under v1, complete; decision panel bound to revision 1 · v1",
             f"{heading.strip()} | {binding.strip()[:90]}", "04-mob0003-inspected-under-v1")

    # --- The effective date arrives.
    morgan_ctx, morgan = _context(browser, base, "morgan", errors)
    morgan.goto("/lab")
    morgan.fill("[data-testid=clock-input]", "2026-07-01")
    morgan.click("[data-testid=clock-submit]")
    morgan.wait_for_selector("[data-testid=flash]")
    assert morgan.locator("[data-testid=business-date]").inner_text() == "2026-07-01"
    assert morgan.locator("[data-testid=effective-policy]").inner_text() == "v2"
    rec.step("Morgan Blake (Policy Owner)", "Move the lab date to 2026-07-01", "Effective policy becomes v2",
             f"business date {morgan.locator('[data-testid=business-date]').inner_text()}, effective "
             f"{morgan.locator('[data-testid=effective-policy]').inner_text()}")

    # --- Sam's approval is now blocked until migration.
    sam.goto("/cases/MOB-0003")
    assert sam.locator("[data-testid=migration-required-banner]").is_visible()
    sam.click("[data-testid=approve-button]")
    refused(sam, "POLICY_MIGRATION_REQUIRED")
    assert _db_one(db, "SELECT COUNT(*) FROM decision WHERE application_id = 3")[0] == 0
    rec.shot(sam, "05-approval-blocked-migration-required")
    rec.step("Sam Okafor (Risk Reviewer)", "Try to approve MOB-0003 after 2026-07-01 without migration",
             "Refused: POLICY_MIGRATION_REQUIRED; nothing written", f"error {_error_code(sam)}; decisions for MOB-0003 = 0",
             "05-approval-blocked-migration-required")

    # --- Policy owner previews and applies v2.
    morgan.goto("/policy")
    row3 = morgan.locator("[data-testid=impact-table] tr[data-ref=MOB-0003] [data-testid=classification]")
    row2 = morgan.locator("[data-testid=impact-table] tr[data-ref=MOB-0002] [data-testid=classification]")
    assert row3.inner_text() == "MIGRATED_EVIDENCE_REQUIRED" and row2.inner_text() == "GRANDFATHERED_APPROVED"
    assert morgan.locator("[data-testid=examples-match]").inner_text().startswith("24/24")
    rec.shot(morgan, "06-policy-comparison-and-preview")
    rec.step("Morgan Blake (Policy Owner)", "Open Policy change: rule diff, product×band matrix, examples, impact preview",
             "Preview: MOB-0003 MIGRATED_EVIDENCE_REQUIRED, MOB-0002 GRANDFATHERED_APPROVED; 24/24 examples match",
             f"MOB-0003 {row3.inner_text()}, MOB-0002 {row2.inner_text()}, examples "
             f"{morgan.locator('[data-testid=examples-match]').inner_text()}", "06-policy-comparison-and-preview")
    morgan.check("[data-testid=migration-confirm]")
    morgan.click("[data-testid=migration-submit]")
    morgan.wait_for_selector("[data-testid=flash]")
    flash = morgan.locator("[data-testid=flash]").inner_text()
    assert "MIGRATED_EVIDENCE_REQUIRED × 1" in flash and "GRANDFATHERED_APPROVED × 2" in flash
    morgan.locator("#impact").scroll_into_view_if_needed()
    rec.shot(morgan, "07-v2-applied")
    rec.step("Morgan Blake (Policy Owner)", "Apply v2 to in-flight applications", "One migration run recorded with per-case classifications",
             flash, "07-v2-applied")

    # --- Specialist responds with a new revision.
    alex_ctx, alex = _context(browser, base, "alex", errors)
    alex.goto("/cases/MOB-0003")
    info = alex.locator("[data-testid=info-request]").inner_text()
    assert "POLICY_CHANGE_EVIDENCE_REQUIRED" in info and "SERVICE_SCOPE" in info
    assert "Needs information" in alex.locator("[data-testid=case-header] [data-testid=status-badge]").inner_text()
    rec.shot(alex, "08-needs-information-after-migration")
    rec.step("Alex Rivera (Onboarding Specialist)", "Open MOB-0003 after migration",
             "Needs information; system request POLICY_CHANGE_EVIDENCE_REQUIRED for SERVICE_SCOPE", info.replace("\n", " ")[:160],
             "08-needs-information-after-migration")
    alex.select_option("#doc-type", "SERVICE_SCOPE")
    alex.fill("#doc-title", "Service-scope statement — membership tiers and billing dates")
    alex.fill("#doc-issued", "2026-06-28")
    alex.fill("#doc-pages", "3")
    alex.fill("#doc-ref", "SYN-DOC-LSC001")
    alex.click("[data-testid=add-document]")
    alex.wait_for_selector("[data-testid=flash]")
    alex.fill("#response_note", "Added the service-scope statement required by policy v2 (CR-001).")
    alex.click("[data-testid=submit-button]")
    alex.wait_for_selector("[data-testid=flash]")
    flash = alex.locator("[data-testid=flash]").inner_text()
    assert "revision 2" in flash and "policy v2" in flash
    ev_heading = alex.locator("[data-testid=evaluation-heading]").inner_text()
    assert "v2" in ev_heading and "complete" in ev_heading
    age_text = alex.locator("[data-testid=evaluation-table]").inner_text()
    assert "as of submitted 2026-07-01" in age_text
    rec.shot(alex, "09-resubmitted-revision-2-under-v2")
    rec.step("Alex Rivera (Onboarding Specialist)", "Add SERVICE_SCOPE metadata (issued 2026-06-28) and resubmit with a note",
             "Revision 2 evaluated under v2, complete; age shown as of the submission date",
             f"{flash} | {ev_heading.strip()}", "09-resubmitted-revision-2-under-v2")

    # --- AC-22: the tab Sam opened before the change is now stale.
    stale_tab.fill("#a-text", "Approving what I inspected on 2026-06-24.")
    stale_tab.click("[data-testid=approve-button]")
    refused(stale_tab, "STALE_REVISION")
    assert _db_one(db, "SELECT COUNT(*) FROM decision WHERE application_id = 3")[0] == 0
    rec.shot(stale_tab, "10-stale-approval-refused")
    rec.step("Sam Okafor (Risk Reviewer)", "Submit the approval form from the tab opened before the change",
             "Refused: STALE_REVISION; nothing written", f"error {_error_code(stale_tab)}; decisions for MOB-0003 = 0",
             "10-stale-approval-refused")

    # --- Sam approves the current revision under v2.
    sam.goto("/cases/MOB-0003")
    binding = sam.locator("[data-testid=binding-box]").inner_text()
    assert "revision 2" in binding and "v2" in binding
    sam.click("[data-testid=approve-button]")
    sam.wait_for_selector("[data-testid=flash]")
    flash = sam.locator("[data-testid=flash]").inner_text()
    assert "approved (revision 2, policy v2)" in flash
    row = _db_one(db, "SELECT outcome, policy_version, (SELECT revision_no FROM application_revision r WHERE r.id = d.revision_id)"
                      " FROM decision d WHERE application_id = 3")
    assert row == ("approved", "v2", 2)
    rec.shot(sam, "11-approved-under-v2")
    rec.step("Sam Okafor (Risk Reviewer)", "Approve MOB-0003 revision 2", "Approved; decision bound to revision 2 · v2 with frozen snapshot",
             f"{flash}; DB decision = {row}", "11-approved-under-v2")

    # --- Grandfathering is visible and unchanged.
    sam.goto("/cases/MOB-0002")
    banner = sam.locator("[data-testid=grandfathered-banner]").inner_text()
    assert "Grandfathered" in banner and "v1" in banner
    rec.shot(sam, "12-grandfathered-v1-approval")
    rec.step("Sam Okafor (Risk Reviewer)", "Open MOB-0002 (approved under v1, no service-scope statement)",
             "Still approved; grandfathered banner; migration record shows the v2 gap as not applied",
             banner.replace("\n", " ")[:160], "12-grandfathered-v1-approval")

    # --- AC-22: another reviewer's decision makes an open form stale.
    dana_ctx, dana = _context(browser, base, "dana", errors)
    dana.goto("/cases/MOB-0004")
    sam.goto("/cases/MOB-0004")
    sam.fill("#r-text", "Season-pass delivery calendar does not match the processing history.")
    sam.click("[data-testid=reject-button]")
    sam.wait_for_selector("[data-testid=flash]")
    dana.click("[data-testid=approve-button]")
    refused(dana, "ALREADY_DECIDED")
    assert _db_one(db, "SELECT status FROM application WHERE reference = 'MOB-0004'")[0] == "rejected"
    assert _db_one(db, "SELECT COUNT(*) FROM decision WHERE application_id = 4")[0] == 1
    rec.shot(dana, "13-already-decided-refused")
    rec.step("Dana Morales (Senior Risk Reviewer)", "Approve MOB-0004 from a form opened before Sam rejected it",
             "Refused: ALREADY_DECIDED; the rejection stands", f"error {_error_code(dana)}; MOB-0004 status rejected, 1 decision",
             "13-already-decided-refused")

    # --- Self-review is blocked for the dual-role actor.
    kai_ctx, kai = _context(browser, base, "kai", errors)
    kai.goto("/cases/MOB-0008")
    assert kai.locator("[data-testid=self-review-warning]").is_visible()
    kai.click("[data-testid=approve-button]")
    refused(kai, "SELF_REVIEW_FORBIDDEN")
    rec.shot(kai, "14-self-review-blocked")
    rec.step("Kai Nakamura (dual role)", "Try to approve MOB-0008, which Kai created and submitted",
             "Refused: SELF_REVIEW_FORBIDDEN", f"error {_error_code(kai)}", "14-self-review-blocked")

    # --- Evidence views.
    sam.goto("/cases/MOB-0003/revisions/2")
    assert "SERVICE_SCOPE" in sam.locator("[data-testid=revision-diff]").inner_text()
    rec.shot(sam, "15-revision-2-diff")
    sam.goto("/uat")
    assert "not external UAT" in sam.locator("[data-testid=uat-disclaimer]").inner_text()
    rec.shot(sam, "16-uat-evidence")
    sam.goto("/timeline")
    rec.shot(sam, "17-audit-timeline")

    assert sorted(errors) == sorted(expected_errors), errors   # nothing but the four intentional refusals
    for ctx in (anon, sam_ctx, morgan_ctx, alex_ctx, dana_ctx, kai_ctx):
        ctx.close()

    story_out = os.environ.get("MOBLAB_E2E_STORY_OUT")
    if story_out:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout.strip())
        Path(story_out).parent.mkdir(parents=True, exist_ok=True)
        Path(story_out).write_text(json.dumps({
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_head": head, "git_worktree_dirty": dirty,
            "browser": f"Chromium {browser.version} (Playwright, headless)",
            "viewports": ["1440x900", "390x844"],
            "server": "python -m moblab run on 127.0.0.1 (ephemeral port), fresh seeded database",
            "console_errors": errors, "console_errors_expected": expected_errors,
            "steps": rec.steps, "screenshots": rec.shots,
        }, indent=2), encoding="utf-8")


@pytest.mark.req("REQ-NFR-09", "REQ-NFR-05")
@pytest.mark.ac("AC-21", "AC-18")
def test_every_page_fits_narrow_and_desktop_without_console_errors(browser, server):
    base, db = server["base"], server["db"]
    before = _db_one(db, "SELECT COUNT(*) FROM event")[0]
    errors: list[str] = []
    pages = ["/", "/demo", "/cases", "/cases/new", "/policy", "/uat", "/timeline", "/lab", "/cases/MOB-9999"] + \
            [f"/cases/MOB-000{i}" for i in range(1, 9)] + ["/cases/MOB-0001/revisions/1", "/cases/MOB-0005/revisions/1"]
    for actor in ("sam", "alex", "morgan", "kai"):
        ctx, page = _context(browser, base, actor, errors)
        for size in (NARROW, DESKTOP):
            page.set_viewport_size(size)
            for url in pages:
                resp = page.goto(url)
                assert resp.status in (200, 404), (url, resp.status)
                _assert_layout(_layout_problems(page), f"{url} as {actor}", size["width"])
        ctx.close()
    # The deliberate /cases/MOB-9999 visit is a real 404 (one resource error per visit); nothing else.
    assert errors == [RESOURCE_ERROR.format("404 (NOT FOUND)")] * 8, errors
    assert _db_one(db, "SELECT COUNT(*) FROM event")[0] == before
