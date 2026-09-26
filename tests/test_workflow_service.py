"""Service-level developer acceptance checks against a real SQLite file (no mocks).

Every refused command is checked with Lab.refused(), which also asserts the database
fingerprint is unchanged — i.e. no partial decision, evaluation or success event (REQ-DEC-07).
"""
from __future__ import annotations

import json
import sqlite3
import tomllib

import pytest

from moblab.db import row_counts
from moblab.errors import DomainError
from moblab.paths import ACCEPTANCE_DIR

SCENARIOS = tomllib.loads((ACCEPTANCE_DIR / "acceptance_scenarios.toml").read_text(encoding="utf-8"))
SEED_CASES = {c["reference"]: c for c in SCENARIOS["seed_case"]}


# ----------------------------------------------------------------------------- AC-01 happy path
@pytest.mark.req("REQ-INT-01", "REQ-INT-02", "REQ-INT-04", "REQ-DEC-01", "REQ-DEC-05", "REQ-WF-05")
@pytest.mark.ac("AC-01")
def test_happy_path_v1(empty_lab):
    lab = empty_lab
    ref = lab.create(name="Happy Path Cafe Ltd")
    lab.base(ref)
    sub = lab.submit(ref)
    assert sub["revision_no"] == 1 and sub["policy_version"] == "v1" and sub["complete"] is True
    rev = lab.one("SELECT * FROM application_revision WHERE id = ?", sub["revision_id"])
    assert len(rev["content_sha256"]) == 64

    res = lab.decide(ref, actor="sam")
    assert res.http_status == 201 and res.body["status"] == "approved"
    assert lab.status(ref) == "approved"
    d = lab.one("SELECT * FROM decision WHERE application_id = (SELECT id FROM application WHERE reference = ?)", ref)
    assert (d["revision_id"], d["policy_version"], d["evaluation_id"]) == (sub["revision_id"], "v1", sub["evaluation_id"])
    snap = json.loads(d["snapshot_json"])
    assert snap["policy"]["version"] == "v1" and [r["id"] for r in snap["policy"]["rules"]] == ["EV-01", "EV-02", "EV-03", "EV-04"]
    assert snap["revision"]["content"]["legal_name"] == "Happy Path Cafe Ltd"
    assert snap["evaluation"]["result"]["complete"] is True
    from moblab.policy_engine import sha256_json
    assert sha256_json(snap) == d["snapshot_sha256"]

    events = [r["event_type"] for r in lab.conn.execute(
        "SELECT event_type FROM event WHERE application_id = (SELECT id FROM application WHERE reference = ?) ORDER BY id", (ref,))]
    assert events == ["CREATED", "DOCUMENT_ADDED", "DOCUMENT_ADDED", "DOCUMENT_ADDED", "SUBMITTED", "APPROVED"]


# ----------------------------------------------------------------------------- AC-02 missing evidence
@pytest.mark.req("REQ-DEC-02", "REQ-WF-03", "REQ-DEC-07")
@pytest.mark.ac("AC-02", "AC-23")
def test_missing_evidence_blocks_approval_but_not_info_request_or_rejection(empty_lab):
    lab = empty_lab
    ref = lab.create()
    lab.base(ref, skip=("BANK_CONFIRM",))
    sub = lab.submit(ref)
    assert sub["complete"] is False and sub["unmet"] == [{"rule_id": "EV-03", "doc_type": "BANK_CONFIRM", "status": "MISSING"}]
    err = lab.refused("EVIDENCE_INCOMPLETE", lab.decide, ref)
    assert err.details["unmet"][0]["doc_type"] == "BANK_CONFIRM"
    assert lab.status(ref) == "submitted" and lab.count("decision") == 0
    assert lab.count("event", "event_type = 'APPROVED'") == 0

    lab.refused("VALIDATION_FAILED", lab.svc.request_information, "sam", ref, {
        "request_key": lab.key(), "revision_id": sub["revision_id"], "reason_code": "MISSING_EVIDENCE",
        "message": "too short"})
    res = lab.svc.request_information("sam", ref, {
        "request_key": lab.key(), "revision_id": sub["revision_id"], "reason_code": "MISSING_EVIDENCE",
        "message": "Please add the settlement bank account confirmation.", "requested_doc_types": ["BANK_CONFIRM"]})
    assert res.body["status"] == "needs_information" and lab.status(ref) == "needs_information"

    other = lab.create()
    lab.base(other, skip=("BANK_CONFIRM",))
    lab.submit(other)
    lab.refused("VALIDATION_FAILED", lab.decide, other, actor="sam", outcome="reject", reason_text="too short reason")
    assert lab.decide(other, actor="sam", outcome="reject").body["status"] == "rejected"


# ----------------------------------------------------------------------------- AC-03 boundary through the workflow
@pytest.mark.req("REQ-POL-01", "REQ-POL-02", "REQ-POL-09")
@pytest.mark.ac("AC-03")
def test_effective_date_boundary_through_workflow(empty_lab):
    lab = empty_lab
    lab.date("2026-06-30")
    a = lab.create(product="SUBSCRIPTION", band="V3")
    lab.base(a)
    before = lab.submit(a)
    assert before["policy_version"] == "v1" and before["complete"] is True

    b = lab.create(product="SUBSCRIPTION", band="V3")
    lab.base(b)
    lab.date("2026-07-01")
    after = lab.submit(b)
    assert after["policy_version"] == "v2" and after["complete"] is False
    assert after["unmet"] == [{"rule_id": "EV-05", "doc_type": "SERVICE_SCOPE", "status": "MISSING"}]

    c = lab.create(product="SUBSCRIPTION", band="V2")
    lab.base(c)
    assert lab.submit(c)["complete"] is True  # V2 is below the EV-05 bands


@pytest.mark.req("REQ-POL-01", "REQ-NFR-06")
@pytest.mark.ac("AC-03", "AC-16")
def test_no_policy_before_first_effective_date(tmp_path):
    from moblab.seed import init_database
    from tests.conftest import Lab
    path = tmp_path / "early.db"
    init_database(path, with_demo_cases=False, business_date="2025-12-20")
    lab = Lab(path)
    ref = lab.create()
    lab.refused("NO_EFFECTIVE_POLICY", lab.submit, ref)
    lab.date("2026-01-01")
    assert lab.submit(ref)["policy_version"] == "v1"
    lab.conn.close()


# ----------------------------------------------------------------------------- seed expectations
@pytest.mark.req("REQ-POL-04", "REQ-POL-05")
@pytest.mark.ac("AC-04", "AC-05")
def test_seed_matches_hand_authored_expectations(seeded_lab):
    lab = seeded_lab
    assert lab.svc.business_date() == SCENARIOS["meta"]["lab_business_date_after_seed"]
    for ref, exp in SEED_CASES.items():
        app, ev = lab.current(ref)
        assert app["business_identifier"] == exp["business_identifier"], ref
        assert app["draft_legal_name"] == exp["legal_name"], ref
        assert app["status"] == exp["status"], ref
        if "policy_version" in exp:
            assert ev["policy_version"] == exp["policy_version"], ref
            assert bool(ev["complete"]) == exp["evaluation_complete"], ref


# ----------------------------------------------------------------------------- AC-04 / AC-05 migration
@pytest.mark.req("REQ-POL-04", "REQ-POL-05", "REQ-POL-07", "REQ-DEC-05", "REQ-WF-05")
@pytest.mark.ac("AC-04", "AC-05")
def test_policy_migration_grandfathers_and_migrates_in_flight(seeded_lab):
    lab = seeded_lab
    frozen = {r["application_id"]: tuple(r) for r in lab.conn.execute("SELECT * FROM decision")}
    v1_evals = {r["id"]: tuple(r) for r in lab.conn.execute("SELECT * FROM policy_evaluation")}

    lab.refused("POLICY_NOT_YET_EFFECTIVE", lab.migrate)

    lab.date("2026-07-01")
    # Before migration: decisions on v1-evaluated revisions are blocked.
    lab.refused("POLICY_MIGRATION_REQUIRED", lab.decide, "MOB-0003", actor="sam")
    lab.refused("POLICY_MIGRATION_REQUIRED", lab.decide, "MOB-0003", actor="sam", policy_version="v2")

    # Preview is read-only and predicts the hand-authored classifications.
    fp = lab.fp()
    preview = {i["reference"]: i for i in lab.svc.migration_preview("v2")["items"]}
    assert lab.fp() == fp
    for ref, exp in SEED_CASES.items():
        assert preview[ref]["classification"] == exp["expected_migration"], ref
        if "v2_hypothetical_unmet" in exp:
            assert [f"{u['doc_type']}:{u['status']}" for u in preview[ref]["target_unmet"]] == exp["v2_hypothetical_unmet"], ref

    run = lab.migrate()
    assert run.http_status == 201
    items = {i["reference"]: i for i in run.body["items"]}
    for ref, exp in SEED_CASES.items():
        assert items[ref]["classification"] == exp["expected_migration"], ref
        assert lab.status(ref) == exp.get("status_after_migration", exp["status"]), ref

    # Grandfathered decisions unchanged byte for byte; no v2 evaluation for them.
    assert {r["application_id"]: tuple(r) for r in lab.conn.execute("SELECT * FROM decision")} == frozen
    for ref in ("MOB-0001", "MOB-0002"):
        assert lab.count("policy_evaluation", "policy_version = 'v2' AND revision_id = "
                         "(SELECT current_revision_id FROM application WHERE reference = ?)", ref) == 0
    # v1 evaluations retained, v2 evaluations appended for in-flight cases.
    for eid, row in v1_evals.items():
        assert tuple(lab.one("SELECT * FROM policy_evaluation WHERE id = ?", eid)) == row
    for ref in ("MOB-0003", "MOB-0004", "MOB-0005", "MOB-0008"):
        _, ev = lab.current(ref)
        assert ev["policy_version"] == "v2" and ev["trigger"] == "migration"
    ir = lab.one("SELECT * FROM info_request WHERE source = 'policy_migration'")
    assert ir["reason_code"] == "POLICY_CHANGE_EVIDENCE_REQUIRED" and json.loads(ir["requested_doc_types_json"]) == ["SERVICE_SCOPE"]
    assert lab.count("info_request", "source = 'policy_migration'") == 1
    assert lab.count("event", "event_type = 'POLICY_MIGRATED'") == 4

    # Second run changes no business state.
    before = row_counts(lab.conn)
    second = lab.migrate()
    classes = {i["reference"]: i["classification"] for i in second.body["items"]}
    assert all(classes[r] == "ALREADY_ON_TARGET" for r in ("MOB-0003", "MOB-0004", "MOB-0005", "MOB-0008"))
    after = row_counts(lab.conn)
    assert after["policy_evaluation"] == before["policy_evaluation"]
    assert after["info_request"] == before["info_request"] and after["decision"] == before["decision"]
    assert after["event"] == before["event"] + 1  # only the POLICY_MIGRATION_RUN record


@pytest.mark.req("REQ-POL-05", "REQ-WF-04", "REQ-DEC-01")
@pytest.mark.ac("AC-05", "AC-06", "AC-21")
def test_before_after_story_service_level(seeded_lab):
    lab = seeded_lab
    lab.date("2026-07-01")
    lab.migrate()
    assert lab.status("MOB-0003") == "needs_information"
    lab.refused("NO_CHANGES_SINCE_LAST_REVISION", lab.submit, "MOB-0003", note="Resubmitting without changes.")
    lab.add("MOB-0003", "SERVICE_SCOPE", "2026-06-28")
    sub = lab.submit("MOB-0003", note="Added the service-scope statement requested under policy v2.")
    assert (sub["revision_no"], sub["policy_version"], sub["complete"]) == (2, "v2", True)
    res = lab.decide("MOB-0003", actor="sam")
    assert res.body["status"] == "approved" and res.body["policy_version"] == "v2" and res.body["revision_no"] == 2
    assert lab.status("MOB-0002") == "approved"


# ----------------------------------------------------------------------------- AC-06 resubmission
@pytest.mark.req("REQ-WF-03", "REQ-WF-04", "REQ-INT-04")
@pytest.mark.ac("AC-06")
def test_needs_information_and_resubmission_keep_history(seeded_lab):
    lab = seeded_lab
    app, ev = lab.current("MOB-0005")
    rev1 = tuple(lab.one("SELECT * FROM application_revision WHERE id = ?", app["current_revision_id"]))
    docs1 = [tuple(r) for r in lab.conn.execute("SELECT * FROM revision_document WHERE revision_id = ?", (app["current_revision_id"],))]
    lab.refused("VALIDATION_FAILED", lab.submit, "MOB-0005", actor="jordan")  # note required
    lab.refused("NO_CHANGES_SINCE_LAST_REVISION", lab.submit, "MOB-0005", actor="jordan", note="No changes yet at all.")
    lab.add("MOB-0005", "BANK_CONFIRM", "2026-06-20", actor="jordan")
    sub = lab.submit("MOB-0005", actor="jordan", note="Added the bank confirmation letter.")
    assert sub["revision_no"] == 2 and sub["complete"] is True
    assert tuple(lab.one("SELECT * FROM application_revision WHERE id = ?", app["current_revision_id"])) == rev1
    assert [tuple(r) for r in lab.conn.execute("SELECT * FROM revision_document WHERE revision_id = ?",
                                                (app["current_revision_id"],))] == docs1
    assert lab.count("application_revision", "application_id = ?", app["id"]) == 2
    assert lab.one("SELECT response_note FROM application_revision WHERE id = ?", sub["revision_id"])[0].startswith("Added")


# ----------------------------------------------------------------------------- AC-07 stale approval
@pytest.mark.req("REQ-DEC-01", "REQ-POL-05", "REQ-POL-09", "REQ-DEC-07")
@pytest.mark.ac("AC-07", "AC-23")
def test_stale_revision_and_stale_policy_are_refused(seeded_lab):
    lab = seeded_lab
    inspected = lab.decision_payload("MOB-0003")           # reviewer's view at 2026-06-24: rev 1, v1
    lab.date("2026-07-01")
    lab.migrate()
    lab.add("MOB-0003", "SERVICE_SCOPE", "2026-06-28")
    lab.submit("MOB-0003", note="Added the service-scope statement under v2.")
    lab.refused("STALE_REVISION", lab.svc.decide, "sam", "MOB-0003", inspected)

    # Correct revision, but an old label/evaluation: never selects old rules.
    app, ev = lab.current("MOB-0003")
    lab.refused("STALE_POLICY_EVALUATION", lab.decide, "MOB-0003", policy_version="v1")
    lab.refused("STALE_POLICY_EVALUATION", lab.decide, "MOB-0003", evaluation_id=ev["id"] - 1)
    assert lab.count("decision", "application_id = ?", app["id"]) == 0
    assert lab.count("event", "application_id = ? AND event_type = 'APPROVED'", app["id"]) == 0

    # In-flight v1 evaluation after migration → stale evaluation (MOB-0004 stayed submitted, now v2).
    lab.refused("STALE_POLICY_EVALUATION", lab.svc.decide, "sam", "MOB-0004",
                {**lab.decision_payload("MOB-0004"), "policy_version": "v1",
                 "evaluation_id": lab.one("SELECT id FROM policy_evaluation WHERE policy_version='v1' AND revision_id = ?",
                                          lab.current("MOB-0004")[0]["current_revision_id"])[0]})
    assert lab.decide("MOB-0004", actor="sam").body["policy_version"] == "v2"


@pytest.mark.req("REQ-POL-09", "REQ-WF-02")
@pytest.mark.ac("AC-07", "AC-16")
def test_callers_cannot_supply_dates(seeded_lab):
    lab = seeded_lab
    for field, value in (("business_date", "2026-06-01"), ("submitted_on", "2026-06-01"), ("decided_on", "2026-06-01"),
                         ("effective_from", "2026-01-01")):
        err = lab.refused("VALIDATION_FAILED", lab.decide, "MOB-0003", **{field: value})
        assert field in err.details["fields"]
    err = lab.refused("VALIDATION_FAILED", lab.svc.submit, "jordan", "MOB-0006",
                      {"request_key": lab.key(), "business_date": "2026-06-30"})
    assert "business_date" in err.details["fields"]


# ----------------------------------------------------------------------------- AC-08 self review
@pytest.mark.req("REQ-ROLE-02")
@pytest.mark.ac("AC-08")
def test_self_review_is_blocked(seeded_lab):
    lab = seeded_lab
    app, _ = lab.current("MOB-0008")
    lab.refused("SELF_REVIEW_FORBIDDEN", lab.decide, "MOB-0008", actor="kai")
    lab.refused("SELF_REVIEW_FORBIDDEN", lab.decide, "MOB-0008", actor="kai", outcome="reject")
    lab.refused("SELF_REVIEW_FORBIDDEN", lab.svc.request_information, "kai", "MOB-0008", {
        "request_key": lab.key(), "revision_id": app["current_revision_id"], "reason_code": "OTHER",
        "message": "Trying to review my own application."})
    assert lab.decide("MOB-0008", actor="sam").body["status"] == "approved"


@pytest.mark.req("REQ-ROLE-02")
@pytest.mark.ac("AC-08")
def test_submitter_of_any_revision_is_excluded(empty_lab):
    lab = empty_lab
    ref = lab.create(actor="alex")
    lab.base(ref)
    lab.submit(ref, actor="alex")
    app, _ = lab.current(ref)
    lab.svc.request_information("sam", ref, {"request_key": lab.key(), "revision_id": app["current_revision_id"],
                                             "reason_code": "OTHER", "message": "Please confirm the trading name."})
    lab.svc.update_draft("kai", ref, {"request_key": lab.key(), "legal_name": "Test Merchant Trading Ltd"})
    lab.submit(ref, actor="kai", note="Trading name confirmed and updated.")
    lab.refused("SELF_REVIEW_FORBIDDEN", lab.decide, ref, actor="kai")


# ----------------------------------------------------------------------------- AC-09 roles
@pytest.mark.req("REQ-ROLE-01", "REQ-ROLE-03")
@pytest.mark.ac("AC-09")
def test_wrong_roles_are_refused(seeded_lab):
    lab = seeded_lab
    lab.refused("FORBIDDEN_ROLE", lab.decide, "MOB-0003", actor="alex")
    lab.refused("FORBIDDEN_ROLE", lab.decide, "MOB-0003", actor="morgan")
    lab.refused("FORBIDDEN_ROLE", lab.svc.create_application, "sam", {
        "request_key": lab.key(), "legal_name": "Reviewer Made Ltd", "business_identifier": "SYN-999001",
        "product_category": "ECOMMERCE", "volume_band": "V1"})
    lab.refused("FORBIDDEN_ROLE", lab.svc.run_migration, "sam", {"request_key": lab.key(), "target_version": "v2"})
    lab.refused("FORBIDDEN_ROLE", lab.svc.submit, "sam", "MOB-0006", {"request_key": lab.key()})
    lab.refused("FORBIDDEN_ROLE", lab.svc.create_application, "riley", {
        "request_key": lab.key(), "legal_name": "Auditor Ltd", "business_identifier": "SYN-999002",
        "product_category": "ECOMMERCE", "volume_band": "V1"})
    lab.refused("FORBIDDEN_ROLE", lab.decide, "MOB-0003", actor="riley")
    lab.refused("FORBIDDEN_ROLE", lab.svc.set_business_date, "riley", {"business_date": "2026-06-25"})
    lab.refused("UNKNOWN_ACTOR", lab.decide, "MOB-0003", actor="mallory")


# ----------------------------------------------------------------------------- AC-10 / AC-11 idempotency
@pytest.mark.req("REQ-DEC-04")
@pytest.mark.ac("AC-10")
def test_retry_with_same_key_replays_without_duplicates(seeded_lab):
    lab = seeded_lab
    payload = lab.decision_payload("MOB-0003")
    first = lab.svc.decide("sam", "MOB-0003", payload)
    fp = lab.fp()
    retry = lab.svc.decide("sam", "MOB-0003", dict(payload))
    assert first.http_status == 201 and retry.http_status == 200
    assert retry.replayed and retry.body["replayed"] is True
    assert retry.body["decision_id"] == first.body["decision_id"]
    assert lab.fp() == fp
    app, _ = lab.current("MOB-0003")
    assert lab.count("decision", "application_id = ?", app["id"]) == 1
    assert lab.count("event", "application_id = ? AND event_type = 'APPROVED'", app["id"]) == 1


@pytest.mark.req("REQ-DEC-04", "REQ-DEC-07")
@pytest.mark.ac("AC-11", "AC-23")
def test_key_reuse_with_any_different_binding_conflicts(seeded_lab):
    lab = seeded_lab
    payload = lab.decision_payload("MOB-0003")
    lab.svc.decide("sam", "MOB-0003", payload)
    key = payload["request_key"]
    cases = {
        "payload": ("sam", "MOB-0003", {**payload, "reason_text": "A different reason for the same key."}),
        "actor": ("dana", "MOB-0003", payload),
        "application": ("sam", "MOB-0004", {**lab.decision_payload("MOB-0004"), "request_key": key}),
        "outcome": ("sam", "MOB-0003", {**payload, "outcome": "reject", "reason_code": "OTHER",
                                        "reason_text": "Rejecting with a reused request key."}),
    }
    for name, (actor, ref, body) in cases.items():
        err = lab.refused("IDEMPOTENCY_KEY_CONFLICT", lab.svc.decide, actor, ref, body)
        assert err.details["differs_in"], name
    # Another case's decision is never replayed: MOB-0004 is untouched.
    assert lab.status("MOB-0004") == "submitted"
    # The same key on a different operation also conflicts.
    lab.refused("IDEMPOTENCY_KEY_CONFLICT", lab.svc.add_document, "jordan", "MOB-0006", {
        "request_key": key, "doc_type": "OTHER", "title": "Reused key", "issued_on": "2026-06-01",
        "page_count": 1, "reference": "SYN-DOC-REUSE1"})


@pytest.mark.req("REQ-DEC-04")
@pytest.mark.ac("AC-11")
def test_key_binds_revision_even_with_identical_payload_fields(seeded_lab):
    lab = seeded_lab
    lab.date("2026-07-01")
    lab.migrate()
    lab.add("MOB-0003", "SERVICE_SCOPE", "2026-06-28")
    lab.submit("MOB-0003", note="Added the service-scope statement under v2.")
    payload = lab.decision_payload("MOB-0003")
    app, _ = lab.current("MOB-0003")
    info_key = lab.key()
    lab.svc.request_information("sam", "MOB-0003", {
        "request_key": info_key, "revision_id": app["current_revision_id"], "reason_code": "EVIDENCE_UNCLEAR",
        "message": "The service-scope statement is illegible; please resend it."})
    lab.add("MOB-0003", "OTHER", "2026-06-30", title="Legible copy")
    lab.submit("MOB-0003", note="Added a legible copy of the statement.")
    app, _ = lab.current("MOB-0003")
    err = lab.refused("IDEMPOTENCY_KEY_CONFLICT", lab.svc.request_information, "sam", "MOB-0003", {
        "request_key": info_key, "revision_id": app["current_revision_id"], "reason_code": "EVIDENCE_UNCLEAR",
        "message": "The service-scope statement is illegible; please resend it."})
    assert "revision" in err.details["differs_in"]
    # A retried stale approval (old revision in payload) is refused, not replayed.
    lab.refused("STALE_REVISION", lab.svc.decide, "sam", "MOB-0003", payload)


# ----------------------------------------------------------------------------- AC-12 cross-application ids
@pytest.mark.req("REQ-DEC-01", "REQ-DEC-07")
@pytest.mark.ac("AC-12", "AC-23")
def test_cross_application_revision_and_document_ids(seeded_lab):
    lab = seeded_lab
    other_app, other_ev = lab.current("MOB-0004")
    lab.refused("REVISION_APPLICATION_MISMATCH", lab.decide, "MOB-0003",
                revision_id=other_app["current_revision_id"], evaluation_id=other_ev["id"])
    lab.refused("REVISION_APPLICATION_MISMATCH", lab.decide, "MOB-0003", revision_id=99999)
    foreign_doc = lab.one("SELECT id FROM draft_document WHERE application_id = "
                          "(SELECT id FROM application WHERE reference = 'MOB-0005')")[0]
    lab.refused("NOT_FOUND", lab.svc.remove_document, "jordan", "MOB-0006", foreign_doc, {"request_key": lab.key()})
    lab.refused("NOT_FOUND", lab.svc.remove_document, "jordan", "MOB-0006", True, {"request_key": lab.key()})


# ----------------------------------------------------------------------------- AC-14 terminal protection + reopen
@pytest.mark.req("REQ-DEC-03", "REQ-DEC-06", "REQ-WF-01", "REQ-DEC-07")
@pytest.mark.ac("AC-14", "AC-23")
def test_terminal_states_are_protected_and_reopen_preserves_decision(seeded_lab):
    lab = seeded_lab
    for ref in ("MOB-0001", "MOB-0007"):
        app, _ = lab.current(ref)
        lab.refused("ALREADY_DECIDED", lab.decide, ref, actor="sam")
        lab.refused("ALREADY_DECIDED", lab.decide, ref, actor="sam", outcome="reject")
        lab.refused("ALREADY_DECIDED", lab.svc.request_information, "sam", ref, {
            "request_key": lab.key(), "revision_id": app["current_revision_id"], "reason_code": "OTHER",
            "message": "Asking for information on a decided case."})
        lab.refused("ALREADY_DECIDED", lab.svc.submit, "alex", ref, {"request_key": lab.key(),
                                                                     "response_note": "Trying to resubmit."})
        lab.refused("ALREADY_DECIDED", lab.svc.update_draft, "alex", ref, {"request_key": lab.key(),
                                                                           "legal_name": "Renamed Ltd"})
        lab.refused("ALREADY_DECIDED", lab.add, ref, "OTHER", "2026-06-01")

    lab.refused("INVALID_TRANSITION", lab.svc.reopen, "sam", "MOB-0001",
                {"request_key": lab.key(), "reason_text": "Trying to reopen an approved case."})
    lab.refused("VALIDATION_FAILED", lab.svc.reopen, "sam", "MOB-0007", {"request_key": lab.key(), "reason_text": "short"})
    rejection = tuple(lab.one("SELECT * FROM decision WHERE outcome = 'rejected'"))
    res = lab.svc.reopen("sam", "MOB-0007", {"request_key": lab.key(),
                                             "reason_text": "Merchant supplied corrected processing statements."})
    assert res.body["status"] == "draft" and res.body["cycle"] == 2
    assert tuple(lab.one("SELECT * FROM decision WHERE outcome = 'rejected'")) == rejection
    # The rejected revision can never be approved.
    rejected_rev = rejection[2]
    lab.refused("INVALID_TRANSITION", lab.decide, "MOB-0007", actor="sam", revision_id=rejected_rev)
    lab.add("MOB-0007", "PROCESSING_HISTORY", "2026-06-20", title="Corrected processing statements")
    sub = lab.submit("MOB-0007", note="Corrected processing statements attached.")
    lab.refused("STALE_REVISION", lab.decide, "MOB-0007", actor="sam", revision_id=rejected_rev)
    assert lab.decide("MOB-0007", actor="sam").body["revision_id"] == sub["revision_id"]
    assert lab.count("decision", "application_id = (SELECT id FROM application WHERE reference='MOB-0007')") == 2


# ----------------------------------------------------------------------------- AC-16 invalid inputs
@pytest.mark.req("REQ-NFR-06", "REQ-INT-01", "REQ-INT-02", "REQ-INT-03", "REQ-INT-04")
@pytest.mark.ac("AC-16")
def test_invalid_dates_ids_and_contracts(seeded_lab):
    lab = seeded_lab
    for bad in ("2026-02-30", "2026-7-1", "tomorrow", "", "2026-07-01T00:00", 20260701):
        lab.refused("VALIDATION_FAILED", lab.svc.set_business_date, "morgan", {"business_date": bad})
    lab.refused("CLOCK_NOT_FORWARD", lab.date, "2026-06-01")
    assert lab.svc.business_date() == "2026-06-24"
    assert lab.date("2026-06-24").body["changed"] is False

    for bad in ("2026-02-30", "30/06/2026", "1999-12-31"):
        lab.refused("VALIDATION_FAILED", lab.add, "MOB-0006", "OTHER", bad, actor="jordan")
    lab.refused("FUTURE_DATED_DOCUMENT", lab.add, "MOB-0006", "OTHER", "2026-06-25", actor="jordan")

    valid_payload = lab.decision_payload("MOB-0003")
    for ref in ("MOB-9999", "mob-0001", "MOB-1", "../etc", "MOB-0001;DROP"):
        lab.refused("NOT_FOUND", lab.svc.decide, "sam", ref, {**valid_payload, "request_key": lab.key()})
    lab.refused("VALIDATION_FAILED", lab.decide, "MOB-0003", revision_id="7")
    lab.refused("VALIDATION_FAILED", lab.decide, "MOB-0003", revision_id=True)
    lab.refused("VALIDATION_FAILED", lab.decide, "MOB-0003", request_key="short")

    base = {"legal_name": "Valid Name Ltd", "product_category": "ECOMMERCE", "volume_band": "V1"}
    for ident in ("12345678", "SYN-12345", "SYN-1234567", "GB-123456", "syn-123456"):
        lab.refused("VALIDATION_FAILED", lab.svc.create_application, "alex",
                    {**base, "request_key": lab.key(), "business_identifier": ident})
    lab.refused("DUPLICATE_BUSINESS_IDENTIFIER", lab.svc.create_application, "alex",
                {**base, "request_key": lab.key(), "business_identifier": "SYN-100001"})
    for name in ("=HYPERLINK(1)", "<b>Bold</b> Ltd", "A", "Name\x07Ltd", "x" * 121):
        lab.refused("VALIDATION_FAILED", lab.svc.create_application, "alex",
                    {**base, "request_key": lab.key(), "business_identifier": "SYN-777001", "legal_name": name})
    err = lab.refused("VALIDATION_FAILED", lab.svc.create_application, "alex",
                      {**base, "request_key": lab.key(), "business_identifier": "SYN-777002", "status": "approved"})
    assert "status" in err.details["fields"]
    lab.refused("VALIDATION_FAILED", lab.svc.update_draft, "jordan", "MOB-0006",
                {"request_key": lab.key(), "business_identifier": "SYN-000001"})
    lab.refused("VALIDATION_FAILED", lab.svc.create_application, "alex", ["not", "an", "object"])

    # Document limit.
    for i in range(10):
        lab.add("MOB-0006", "OTHER", "2026-06-01", actor="jordan")
    lab.refused("DOCUMENT_LIMIT_REACHED", lab.add, "MOB-0006", "OTHER", "2026-06-01", actor="jordan")


# ----------------------------------------------------------------------------- AC-23 no partial writes
@pytest.mark.req("REQ-DEC-07")
@pytest.mark.ac("AC-23")
def test_migration_failing_part_way_leaves_nothing(seeded_lab, monkeypatch):
    lab = seeded_lab
    lab.date("2026-07-01")
    before = lab.fp()
    counts = row_counts(lab.conn)
    original = lab.svc._set_status
    calls = {"n": 0}

    def exploding(app, new_status, **cols):
        calls["n"] += 1
        original(app, new_status, **cols)
        raise RuntimeError("simulated crash after the first status change")

    monkeypatch.setattr(lab.svc, "_set_status", exploding)
    with pytest.raises(RuntimeError):
        lab.migrate()
    assert calls["n"] == 1
    assert lab.fp() == before and row_counts(lab.conn) == counts
    assert lab.status("MOB-0003") == "submitted"
    monkeypatch.undo()
    assert lab.migrate().http_status == 201


@pytest.mark.req("REQ-DEC-07")
@pytest.mark.ac("AC-23")
def test_invalid_migration_requests_write_nothing(seeded_lab):
    lab = seeded_lab
    lab.date("2026-07-01")
    lab.refused("VALIDATION_FAILED", lab.svc.run_migration, "morgan", {"request_key": lab.key(), "target_version": "v9"})
    lab.refused("VALIDATION_FAILED", lab.svc.run_migration, "morgan", {"request_key": lab.key(), "target_version": "v1"})
    lab.refused("VALIDATION_FAILED", lab.svc.run_migration, "morgan", {"request_key": lab.key(), "target_version": "v2",
                                                                        "dry_run": False})
    assert lab.count("migration_run") == 0 and lab.count("event", "event_type LIKE 'POLICY_MIGRAT%'") == 0


# ----------------------------------------------------------------------------- AC-24 no in-place evidence edits
@pytest.mark.req("REQ-INT-05", "REQ-WF-05", "REQ-DEC-07")
@pytest.mark.ac("AC-24", "AC-23")
def test_submitted_evidence_cannot_be_edited_in_place(seeded_lab):
    lab = seeded_lab
    app, ev = lab.current("MOB-0003")
    lab.refused("INVALID_TRANSITION", lab.add, "MOB-0003", "SERVICE_SCOPE", "2026-06-20")
    doc_id = lab.one("SELECT id FROM draft_document WHERE application_id = ?", app["id"])[0]
    lab.refused("INVALID_TRANSITION", lab.svc.remove_document, "alex", "MOB-0003", doc_id, {"request_key": lab.key()})
    lab.refused("INVALID_TRANSITION", lab.svc.update_draft, "alex", "MOB-0003",
                {"request_key": lab.key(), "volume_band": "V2"})

    raw_writes = [
        "UPDATE application_revision SET volume_band = 'V1' WHERE id = {rev}",
        "UPDATE revision_document SET issued_on = '2026-06-23' WHERE revision_id = {rev}",
        "DELETE FROM revision_document WHERE revision_id = {rev}",
        "UPDATE policy_evaluation SET complete = 1 WHERE id = {ev}",
        "UPDATE decision SET outcome = 'approved'",
        "DELETE FROM event",
        "UPDATE event SET actor_id = 'sam'",
        "UPDATE application SET draft_volume_band = 'V1' WHERE id = {app}",
        "INSERT INTO draft_document(application_id, doc_type, title, issued_on, page_count, reference, added_by, added_on)"
        " VALUES ({app}, 'SERVICE_SCOPE', 'Sneaky', '2026-06-20', 1, 'SYN-DOC-SNEAK', 'alex', '2026-06-24')",
        "UPDATE application SET status = 'approved' WHERE id = {app}",
        "UPDATE application SET status = 'approved' WHERE reference = 'MOB-0007'",
        "UPDATE application SET business_identifier = 'SYN-000000' WHERE id = {app}",
        "UPDATE policy_version SET effective_from = '2027-01-01'",
    ]
    before = lab.fp()
    for sql in raw_writes:
        with pytest.raises(sqlite3.IntegrityError):
            lab.conn.execute(sql.format(rev=app["current_revision_id"], ev=ev["id"], app=app["id"]))
    assert lab.fp() == before

    # Even if an attacker drops the trigger and edits the revision, approval refuses.
    lab.conn.execute("DROP TRIGGER revision_document_no_update")
    lab.conn.execute("UPDATE revision_document SET issued_on = '2026-06-01' WHERE revision_id = ?",
                     (app["current_revision_id"],))
    lab.refused("REVISION_INTEGRITY_FAILURE", lab.decide, "MOB-0003", actor="sam")


# ----------------------------------------------------------------------------- AC-25 age fixed at submission
@pytest.mark.req("REQ-POL-08", "REQ-POL-03")
@pytest.mark.ac("AC-25")
def test_evidence_age_is_fixed_at_submission_date(empty_lab):
    lab = empty_lab
    lab.date("2026-07-02")
    ref = lab.create(product="SUBSCRIPTION", band="V3")
    lab.base(ref)
    lab.add(ref, "SERVICE_SCOPE", "2026-04-10")
    sub = lab.submit(ref)
    assert sub["policy_version"] == "v2" and sub["complete"] is True
    stored = tuple(lab.one("SELECT * FROM policy_evaluation WHERE id = ?", sub["evaluation_id"]))
    result = json.loads(stored[10])
    scope = next(r for r in result["rules"] if r["rule_id"] == "EV-05")
    assert scope["status"] == "SATISFIED" and scope["details"][0]["age_days"] == 83
    assert result["reference_date"] == "2026-07-02"

    lab.date("2026-09-30")   # 173 days after issue, same policy (v2)
    lab.svc.migration_preview("v2")
    assert tuple(lab.one("SELECT * FROM policy_evaluation WHERE id = ?", sub["evaluation_id"])) == stored
    assert lab.count("policy_evaluation", "revision_id = ?", sub["revision_id"]) == 1
    assert lab.decide(ref, actor="sam").body["evaluation_id"] == sub["evaluation_id"]


# ----------------------------------------------------------------------------- DEF-001 regression (Codex R3-01)
@pytest.mark.req("REQ-NFR-06", "REQ-DEC-07")
@pytest.mark.ac("AC-16", "AC-23")
@pytest.mark.parametrize("bad_id", [2**100, 2**31, -1, 0, True, "1", 1.0, None])
def test_out_of_range_document_id_is_a_404_not_a_crash(seeded_lab, bad_id):
    lab = seeded_lab
    err = lab.refused("NOT_FOUND", lab.svc.remove_document, "jordan", "MOB-0006", bad_id, {"request_key": lab.key()})
    assert err.http_status == 404


@pytest.mark.req("REQ-NFR-06")
@pytest.mark.ac("AC-16")
@pytest.mark.parametrize("field", ["revision_id", "evaluation_id"])
@pytest.mark.parametrize("bad_id", [2**100, 2**31])
def test_out_of_range_revision_and_evaluation_ids_are_422(seeded_lab, field, bad_id):
    err = seeded_lab.refused("VALIDATION_FAILED", seeded_lab.decide, "MOB-0003", **{field: bad_id})
    assert field in err.details["fields"]


# ----------------------------------------------------------------------------- DEF-002 regression (Codex R3-02)
@pytest.mark.req("REQ-INT-02", "REQ-DEC-07")
@pytest.mark.ac("AC-12", "AC-23")
def test_stale_document_removal_cannot_delete_a_replacement(seeded_lab):
    lab = seeded_lab
    ref = lab.create(product="ECOMMERCE", band="V1", name="Repro Draft Ltd")   # last-created application
    a = lab.add(ref, "OTHER", "2026-06-01", title="Document A")
    lab.svc.remove_document("alex", ref, a, {"request_key": lab.key()})
    b = lab.add(ref, "OTHER", "2026-06-01", title="Document B (replacement)")
    assert b != a, "a deleted draft document id was reused"
    err = lab.refused("NOT_FOUND", lab.svc.remove_document, "alex", ref, a, {"request_key": lab.key()})
    assert err.http_status == 404
    assert lab.one("SELECT title FROM draft_document WHERE id = ?", b)["title"] == "Document B (replacement)"
