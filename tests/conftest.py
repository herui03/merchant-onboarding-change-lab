"""Shared fixtures and the evidence recorder.

Evidence: when MOBLAB_EVIDENCE_OUT is set, every test's outcome, duration and its `req`/`ac`
markers are written to that JSON file at the end of the session. docs/06_test_evidence.md is
generated from that file, so reported numbers are the real ones from a real run.
"""
from __future__ import annotations

import itertools
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import pytest

from moblab.db import connect, fingerprint
from moblab.errors import DomainError
from moblab.seed import init_database
from moblab.service import WorkflowService

BASE_DOCS = (("REG_EXTRACT", "2026-05-18"), ("OWNERSHIP_DECL", "2026-05-19"), ("BANK_CONFIRM", "2026-05-20"))


class Lab:
    """Thin test driver around the real service and a real SQLite file."""

    _keys = itertools.count(1)
    _idents = itertools.count(500000)

    def __init__(self, path: Path):
        self.path = path
        self.conn = connect(path)
        self.svc = WorkflowService(self.conn)

    # -- plumbing
    def key(self) -> str:
        return f"test-key-{next(self._keys):06d}"

    def fp(self) -> str:
        return fingerprint(self.conn)

    def refused(self, code: str, fn, *args, **kwargs):
        """Assert the command fails with `code` AND the database is byte-for-byte unchanged."""
        before = self.fp()
        with pytest.raises(DomainError) as info:
            fn(*args, **kwargs)
        assert info.value.code == code, (info.value.code, info.value.message, info.value.details)
        assert self.fp() == before, f"{code}: a refused command changed the database"
        return info.value

    def one(self, sql, *params):
        return self.conn.execute(sql, params).fetchone()

    def count(self, table, where="1=1", *params) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]

    # -- domain shortcuts
    def date(self, business_date: str):
        return self.svc.set_business_date("morgan", {"business_date": business_date})

    def create(self, *, actor="alex", product="CARD_PRESENT", band="V1", name="Test Merchant Ltd", ident=None,
               summary=""):
        ident = ident or f"SYN-{next(self._idents):06d}"
        return self.svc.create_application(actor, {
            "request_key": self.key(), "legal_name": name, "business_identifier": ident,
            "product_category": product, "volume_band": band, "activity_summary": summary,
        }).body["reference"]

    def add(self, ref, doc_type, issued_on, *, actor="alex", title=None, ref_code=None):
        code = ref_code or f"T{next(self._keys):06d}"
        return self.svc.add_document(actor, ref, {
            "request_key": self.key(), "doc_type": doc_type, "title": title or f"{doc_type} synthetic",
            "issued_on": issued_on, "page_count": 2, "reference": f"SYN-DOC-{code}",
        }).body["document_id"]

    def base(self, ref, *, actor="alex", skip=()):
        for doc_type, issued in BASE_DOCS:
            if doc_type not in skip:
                self.add(ref, doc_type, issued, actor=actor)

    def submit(self, ref, *, actor="alex", note=""):
        payload = {"request_key": self.key()}
        if note:
            payload["response_note"] = note
        return self.svc.submit(actor, ref, payload).body

    def current(self, ref):
        app = self.one("SELECT * FROM application WHERE reference = ?", ref)
        ev = self.one("SELECT * FROM policy_evaluation WHERE revision_id = ? ORDER BY id DESC LIMIT 1",
                      app["current_revision_id"])
        return app, ev

    def decision_payload(self, ref, outcome="approve", **overrides):
        app, ev = self.current(ref)
        payload = {
            "request_key": self.key(), "outcome": outcome, "revision_id": app["current_revision_id"],
            "policy_version": ev["policy_version"], "evaluation_id": ev["id"],
            "reason_code": "EVIDENCE_VERIFIED" if outcome == "approve" else "EVIDENCE_INCONSISTENT",
            "reason_text": "Evidence reviewed against the policy and found consistent."
            if outcome == "approve" else "Evidence contradicts the declared business activity.",
        }
        payload.update(overrides)
        return payload

    def decide(self, ref, *, actor="sam", outcome="approve", **overrides):
        return self.svc.decide(actor, ref, self.decision_payload(ref, outcome, **overrides))

    def status(self, ref) -> str:
        return self.one("SELECT status FROM application WHERE reference = ?", ref)["status"]

    def migrate(self, actor="morgan", key=None):
        return self.svc.run_migration(actor, {"request_key": key or self.key(), "target_version": "v2"})


@pytest.fixture
def empty_lab(tmp_path) -> Lab:
    """Actors only, lab date 2026-06-01 (v1 effective)."""
    path = tmp_path / "empty.db"
    init_database(path, with_demo_cases=False)
    lab = Lab(path)
    yield lab
    lab.conn.close()


@pytest.fixture
def seeded_lab(tmp_path) -> Lab:
    """The reproducible synthetic seed, lab date 2026-06-24."""
    path = tmp_path / "seeded.db"
    init_database(path)
    lab = Lab(path)
    yield lab
    lab.conn.close()


# ----------------------------------------------------------------------------- evidence recorder
_RESULTS: list[dict] = []


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        item_markers = getattr(report, "_moblab_markers", {})
        _RESULTS.append({
            "nodeid": report.nodeid,
            "outcome": report.outcome,
            "phase": report.when,
            "duration_s": round(report.duration, 4),
            "req": item_markers.get("req", []),
            "ac": item_markers.get("ac", []),
            "e2e": item_markers.get("e2e", False),
            "skip_reason": _skip_reason(report),
        })


def _skip_reason(report):
    if report.outcome == "skipped" and isinstance(report.longrepr, tuple):
        return str(report.longrepr[2])
    return ""


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    markers = {"req": [], "ac": [], "e2e": item.get_closest_marker("e2e") is not None}
    for name in ("req", "ac"):
        for mark in item.iter_markers(name):
            markers[name].extend(a for a in mark.args if a not in markers[name])
    report._moblab_markers = markers


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("MOBLAB_EVIDENCE_OUT")
    if not out:
        return
    root = Path(__file__).resolve().parent.parent

    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""

    counts: dict[str, int] = {}
    for r in _RESULTS:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": git("rev-parse", "HEAD"),
        "git_worktree_dirty": bool(git("status", "--porcelain")),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pytest_exit_status": int(exitstatus),
        "selection": os.environ.get("MOBLAB_EVIDENCE_LABEL", ""),
        "counts": counts,
        "results": _RESULTS,
    }, indent=2), encoding="utf-8")
