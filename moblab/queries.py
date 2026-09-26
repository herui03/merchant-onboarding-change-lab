"""Read models for pages, API and exports. Nothing here writes to the database."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from .contracts import ID_MAX, INFO_REASON_CODES, REJECT_REASON_CODES, APPROVE_REASON_CODES
from .paths import ACCEPTANCE_DIR, EVIDENCE_DIR
from .policy_engine import (DOC_TYPES, PRODUCT_CATEGORIES, VOLUME_BANDS, effective_policy, evaluate,
                            parse_iso_date, required_doc_types)
from .service import TERMINAL, WorkflowService, load_db_policies

STATUS_LABELS = {
    "draft": "Draft",
    "submitted": "Submitted — in review",
    "needs_information": "Needs information",
    "approved": "Approved",
    "rejected": "Rejected",
}
CLASSIFICATION_LABELS = {
    "GRANDFATHERED_APPROVED": "Grandfathered — approved under earlier policy, snapshot frozen",
    "APPROVED_UNDER_TARGET": "Approved under the target policy",
    "TERMINAL_REJECTED_UNCHANGED": "Rejected — unchanged",
    "DRAFT_NOT_EVALUATED": "Draft — evaluated at submission under the policy then effective",
    "ALREADY_ON_TARGET": "Already evaluated under the target policy",
    "MIGRATED_NO_NEW_EVIDENCE": "Re-evaluated — no new evidence required",
    "MIGRATED_EVIDENCE_REQUIRED": "Re-evaluated — new evidence required → needs information",
    "MIGRATED_PENDING_RESUBMISSION": "Re-evaluated — still awaiting the specialist's resubmission",
}
REASON_LABELS = {**INFO_REASON_CODES, **APPROVE_REASON_CODES, **REJECT_REASON_CODES,
                 "POLICY_CHANGE_EVIDENCE_REQUIRED": "Policy change requires additional evidence",
                 "REOPEN": "Reopened with reason"}


def bounded_id(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= ID_MAX


def actors(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [{**dict(r), "roles": json.loads(r["roles_json"])} for r in conn.execute("SELECT * FROM actor ORDER BY rowid")]


def actor_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {a["id"]: a for a in actors(conn)}


def lab_state(conn: sqlite3.Connection) -> dict[str, Any]:
    bd = conn.execute("SELECT value FROM meta WHERE key='business_date'").fetchone()[0]
    policies = load_db_policies(conn)
    eff = effective_policy(policies.values(), parse_iso_date(bd))
    runs = conn.execute("SELECT * FROM migration_run WHERE target_version = ? ORDER BY id DESC LIMIT 1",
                        (eff.version if eff else "",)).fetchone()
    return {"business_date": bd, "effective_policy": eff.version if eff else None,
            "effective_from": eff.effective_from.isoformat() if eff else None,
            "policies": policies, "latest_migration_run": dict(runs) if runs else None}


def _latest_eval(conn, revision_id):
    if revision_id is None:
        return None
    row = conn.execute("SELECT * FROM policy_evaluation WHERE revision_id = ? ORDER BY id DESC LIMIT 1",
                       (revision_id,)).fetchone()
    return _eval_dict(row) if row else None


def _eval_dict(row) -> dict[str, Any]:
    d = dict(row)
    d["result"] = json.loads(d.pop("result_json"))
    d["complete"] = bool(d["complete"])
    return d


def list_applications(conn: sqlite3.Connection, *, status: str = "", product: str = "", policy: str = "") -> list[dict]:
    state = lab_state(conn)
    rows = []
    for app in conn.execute("SELECT * FROM application ORDER BY id"):
        rev = conn.execute("SELECT * FROM application_revision WHERE id = ?", (app["current_revision_id"],)).fetchone() \
            if app["current_revision_id"] else None
        ev = _latest_eval(conn, app["current_revision_id"])
        decision = conn.execute("SELECT * FROM decision WHERE revision_id = ?", (app["current_revision_id"],)).fetchone() \
            if app["current_revision_id"] else None
        in_flight = app["status"] in ("submitted", "needs_information")
        item = {
            "reference": app["reference"],
            "legal_name": app["draft_legal_name"],
            "business_identifier": app["business_identifier"],
            "product_category": app["draft_product_category"],
            "volume_band": app["draft_volume_band"],
            "status": app["status"],
            "status_label": STATUS_LABELS[app["status"]],
            "revision_no": rev["revision_no"] if rev else None,
            "policy_version": (decision["policy_version"] if decision and app["status"] in TERMINAL
                               else ev["policy_version"] if ev and app["status"] != "draft" else None),
            "complete": ev["complete"] if ev and app["status"] != "draft" else None,
            "created_by": app["created_by"],
            "migration_required": bool(in_flight and ev and state["effective_policy"]
                                       and ev["policy_version"] != state["effective_policy"]),
            "grandfathered": bool(app["status"] == "approved" and decision and state["effective_policy"]
                                  and decision["policy_version"] != state["effective_policy"]),
        }
        if status and item["status"] != status:
            continue
        if product and item["product_category"] != product:
            continue
        if policy and item["policy_version"] != policy:
            continue
        rows.append(item)
    return rows


def application_detail(conn: sqlite3.Connection, reference: str) -> dict[str, Any] | None:
    app = conn.execute("SELECT * FROM application WHERE reference = ?", (reference,)).fetchone()
    if app is None:
        return None
    state = lab_state(conn)
    revisions = []
    for rev in conn.execute("SELECT * FROM application_revision WHERE application_id = ? ORDER BY revision_no", (app["id"],)):
        docs = [dict(d) for d in conn.execute("SELECT * FROM revision_document WHERE revision_id = ? ORDER BY position", (rev["id"],))]
        evals = [_eval_dict(e) for e in conn.execute("SELECT * FROM policy_evaluation WHERE revision_id = ? ORDER BY id", (rev["id"],))]
        decision = conn.execute("SELECT * FROM decision WHERE revision_id = ?", (rev["id"],)).fetchone()
        infos = [_info_dict(i) for i in conn.execute("SELECT * FROM info_request WHERE revision_id = ? ORDER BY id", (rev["id"],))]
        revisions.append({**dict(rev), "documents": docs, "evaluations": evals,
                          "decision": _decision_dict(decision) if decision else None, "info_requests": infos,
                          "is_current": rev["id"] == app["current_revision_id"]})
    current = next((r for r in revisions if r["is_current"]), None)
    current_eval = current["evaluations"][-1] if current and current["evaluations"] else None
    open_info = current["info_requests"] if current and app["status"] == "needs_information" else []
    draft_docs = [dict(d) for d in conn.execute("SELECT * FROM draft_document WHERE application_id = ? ORDER BY id", (app["id"],))]
    events = [{**dict(e), "details": json.loads(e["details_json"])} for e in
              conn.execute("SELECT * FROM event WHERE application_id = ? ORDER BY id", (app["id"],))]
    migration_items = [{**dict(m), "new_gaps": json.loads(m["new_gaps_json"]), "target_unmet": json.loads(m["target_unmet_json"]),
                        "run": dict(conn.execute("SELECT * FROM migration_run WHERE id = ?", (m["run_id"],)).fetchone())}
                       for m in conn.execute("SELECT * FROM migration_item WHERE application_id = ? ORDER BY id", (app["id"],))]
    in_flight = app["status"] in ("submitted", "needs_information")
    decision = current["decision"] if current else None
    return {
        "app": dict(app),
        "status_label": STATUS_LABELS[app["status"]],
        "revisions": revisions,
        "current": current,
        "current_eval": current_eval,
        "open_info_requests": open_info,
        "draft_documents": draft_docs,
        "events": events,
        "migration_items": migration_items,
        "decisions": [r["decision"] for r in revisions if r["decision"]],
        "migration_required": bool(in_flight and current_eval and state["effective_policy"]
                                   and current_eval["policy_version"] != state["effective_policy"]),
        "grandfathered": bool(app["status"] == "approved" and decision and state["effective_policy"]
                              and decision["policy_version"] != state["effective_policy"]),
        "draft_required_doc_types": _draft_requirements(state, app),
    }


def _draft_requirements(state, app) -> dict[str, Any] | None:
    """Preview of what the policy effective today would require for the draft (display only)."""
    if app["status"] not in ("draft", "needs_information") or not state["effective_policy"]:
        return None
    policy = state["policies"][state["effective_policy"]]
    return {"policy_version": policy.version,
            "doc_types": required_doc_types(policy, app["draft_product_category"], app["draft_volume_band"])}


def _info_dict(row) -> dict[str, Any]:
    d = dict(row)
    d["requested_doc_types"] = json.loads(d.pop("requested_doc_types_json"))
    return d


def _decision_dict(row) -> dict[str, Any]:
    d = dict(row)
    d["snapshot"] = json.loads(d.pop("snapshot_json"))
    return d


def revision_view(conn: sqlite3.Connection, reference: str, revision_no: int) -> dict[str, Any] | None:
    if not bounded_id(revision_no):
        return None
    detail = application_detail(conn, reference)
    if detail is None:
        return None
    rev = next((r for r in detail["revisions"] if r["revision_no"] == revision_no), None)
    if rev is None:
        return None
    prev = next((r for r in detail["revisions"] if r["revision_no"] == revision_no - 1), None)
    return {"detail": detail, "revision": rev, "previous": prev, "diff": _diff(prev, rev) if prev else None}


def _diff(prev: dict, rev: dict) -> dict[str, Any]:
    fields = ["legal_name", "product_category", "volume_band", "activity_summary"]
    changed = [{"field": f, "from": prev[f], "to": rev[f]} for f in fields if prev[f] != rev[f]]

    def key(d):
        return (d["doc_type"], d["reference"], d["issued_on"], d["title"], d["page_count"])

    before = {key(d) for d in prev["documents"]}
    after = {key(d) for d in rev["documents"]}
    return {"fields": changed,
            "added": [d for d in rev["documents"] if key(d) not in before],
            "removed": [d for d in prev["documents"] if key(d) not in after]}


def global_events(conn: sqlite3.Connection, limit: int = 300) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT e.*, a.reference FROM event e LEFT JOIN application a ON a.id = e.application_id ORDER BY e.id DESC LIMIT ?",
        (limit,),
    )
    return [{**dict(r), "details": json.loads(r["details_json"])} for r in rows]


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out = {s: 0 for s in STATUS_LABELS}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM application GROUP BY status"):
        out[row["status"]] = row["n"]
    out["total"] = sum(out[s] for s in STATUS_LABELS)
    return out


# ----------------------------------------------------------------------------- policy comparison
def policy_comparison(conn: sqlite3.Connection) -> dict[str, Any]:
    policies = load_db_policies(conn)
    ordered = sorted(policies.values(), key=lambda p: p.effective_from)
    old, new = ordered[-2], ordered[-1]
    old_rules = {r.id: r for r in old.rules}
    new_rules = {r.id: r for r in new.rules}
    diff = []
    for rule_id in sorted(set(old_rules) | set(new_rules)):
        o, n = old_rules.get(rule_id), new_rules.get(rule_id)
        change = "Added" if o is None else "Removed" if n is None else "Unchanged" if o.as_dict() == n.as_dict() else "Modified"
        diff.append({"rule_id": rule_id, "old": o.as_dict() if o else None, "new": n.as_dict() if n else None, "change": change})
    matrix = []
    for product in PRODUCT_CATEGORIES:
        cells = []
        for band in VOLUME_BANDS:
            before = required_doc_types(old, product, band)
            after = required_doc_types(new, product, band)
            cells.append({"band": band, "old": before, "new": after, "added": [d for d in after if d not in before]})
        matrix.append({"product": product, "cells": cells})
    return {"old": old, "new": new, "diff": diff, "matrix": matrix,
            "rows_sha": {p.version: p.rules_sha256() for p in ordered}}


def example_results(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Hand-authored examples (committed before the engine) vs the live engine."""
    policies = load_db_policies(conn)
    out = []
    path = ACCEPTANCE_DIR / "policy_examples.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            docs = []
            for i, part in enumerate(p for p in row["documents"].split(";") if p):
                doc_type, issued = part.split("@")
                docs.append({"doc_type": doc_type, "issued_on": issued, "reference": f"EX{i}"})
            result = evaluate(policies[row["policy_version"]], row["product_category"], row["volume_band"], docs,
                              parse_iso_date(row["reference_date"]))
            actual_unmet = ";".join(f"{u['doc_type']}:{u['status']}" for u in result["unmet"])
            actual_outcome = "COMPLETE" if result["complete"] else "INCOMPLETE"
            out.append({**row, "actual_unmet": actual_unmet, "actual_outcome": actual_outcome,
                        "actual_required": ";".join(result["required"]),
                        "match": (actual_unmet == row["expected_unmet"] and actual_outcome == row["expected_outcome"]
                                  and ";".join(result["required"]) == row["expected_required"])})
    return out


def impact(conn: sqlite3.Connection) -> dict[str, Any]:
    """Live impact: the read-only preview plus the latest recorded migration run."""
    state = lab_state(conn)
    target = sorted(state["policies"].values(), key=lambda p: p.effective_from)[-1]
    preview = WorkflowService(conn).migration_preview(target.version)
    runs = [dict(r) for r in conn.execute("SELECT * FROM migration_run WHERE target_version = ? ORDER BY id", (target.version,))]
    latest_items = {}
    if runs:
        for m in conn.execute("SELECT mi.*, a.reference FROM migration_item mi JOIN application a ON a.id = mi.application_id "
                              "WHERE run_id = ? ORDER BY mi.id", (runs[0]["id"],)):
            latest_items[m["reference"]] = {**dict(m), "new_gaps": json.loads(m["new_gaps_json"]),
                                            "target_unmet": json.loads(m["target_unmet_json"])}
    return {"target": target, "preview": preview, "runs": runs, "first_run_items": latest_items}


# ----------------------------------------------------------------------------- evidence files
def load_evidence(name: str = "test_results.json") -> dict[str, Any] | None:
    path = EVIDENCE_DIR / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def evidence_by_requirement(evidence: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not evidence:
        return out
    for r in evidence.get("results", []):
        for req in r.get("req", []):
            slot = out.setdefault(req, {"passed": 0, "failed": 0, "skipped": 0, "tests": []})
            slot[r["outcome"]] = slot.get(r["outcome"], 0) + 1
            slot["tests"].append(r)
    return out


def evidence_by_scenario(evidence: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not evidence:
        return out
    for r in evidence.get("results", []):
        for ac in r.get("ac", []):
            slot = out.setdefault(ac, {"passed": 0, "failed": 0, "skipped": 0, "tests": []})
            slot[r["outcome"]] = slot.get(r["outcome"], 0) + 1
            slot["tests"].append(r)
    return out


# ----------------------------------------------------------------------------- exports
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> str:
    """Neutralise spreadsheet formula injection (REQ-NFR-04)."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def cases_csv(conn: sqlite3.Connection) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["reference", "legal_name", "business_identifier", "product_category", "volume_band", "status",
                     "current_revision_no", "policy_version", "evidence_complete", "activity_summary", "document_titles"])
    for item in list_applications(conn):
        app = conn.execute("SELECT * FROM application WHERE reference = ?", (item["reference"],)).fetchone()
        titles = " | ".join(r["title"] for r in conn.execute(
            "SELECT title FROM draft_document WHERE application_id = ? ORDER BY id", (app["id"],)))
        writer.writerow([csv_safe(v) for v in (
            item["reference"], item["legal_name"], item["business_identifier"], item["product_category"],
            item["volume_band"], item["status"], item["revision_no"] or "", item["policy_version"] or "",
            "" if item["complete"] is None else ("yes" if item["complete"] else "no"),
            app["draft_activity_summary"], titles)])
    return buf.getvalue()


def case_export(conn: sqlite3.Connection, reference: str) -> dict[str, Any] | None:
    detail = application_detail(conn, reference)
    if detail is None:
        return None
    return {
        "notice": "Synthetic data from the Merchant Onboarding Policy Change Lab. Not a real merchant record.",
        "application": detail["app"],
        "revisions": detail["revisions"],
        "draft_documents": detail["draft_documents"],
        "events": detail["events"],
        "migration_items": detail["migration_items"],
    }


LABELS = {"doc_types": DOC_TYPES, "products": PRODUCT_CATEGORIES, "bands": VOLUME_BANDS,
          "status": STATUS_LABELS, "classification": CLASSIFICATION_LABELS, "reasons": REASON_LABELS}
