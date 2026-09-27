"""Engine vs the hand-authored examples committed before the engine existed (a20b34b)."""
from __future__ import annotations

import csv
from datetime import date

import pytest

from moblab.paths import ACCEPTANCE_DIR
from moblab.policies import load_policy_files
from moblab.policy_engine import InvalidDate, effective_policy, evaluate, parse_iso_date

POLICIES = {p.version: p for p in load_policy_files()}


def _rows(name):
    with (ACCEPTANCE_DIR / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _split(cell):
    return [part for part in cell.split(";") if part] if cell else []


def _docs(cell):
    docs = []
    for i, part in enumerate(_split(cell)):
        doc_type, issued = part.split("@")
        docs.append({"doc_type": doc_type, "issued_on": issued, "reference": f"SYN-DOC-EX{i:02d}"})
    return docs


POLICY_EXAMPLES = _rows("policy_examples.csv")
APPLICABILITY_EXAMPLES = _rows("policy_applicability_examples.csv")


def test_example_files_are_complete():
    assert [r["example_id"] for r in POLICY_EXAMPLES] == [f"PE-{i:02d}" for i in range(1, 25)]
    assert [r["example_id"] for r in APPLICABILITY_EXAMPLES] == [f"PA-{i:02d}" for i in range(1, 11)]


@pytest.mark.req("REQ-POL-02", "REQ-POL-03", "REQ-POL-08")
@pytest.mark.ac("AC-03")
@pytest.mark.parametrize("row", POLICY_EXAMPLES, ids=[r["example_id"] for r in POLICY_EXAMPLES])
def test_policy_example_matches_hand_authored_expectation(row):
    result = evaluate(
        POLICIES[row["policy_version"]],
        row["product_category"],
        row["volume_band"],
        _docs(row["documents"]),
        parse_iso_date(row["reference_date"]),
    )
    assert result["required"] == _split(row["expected_required"])
    assert [f"{u['doc_type']}:{u['status']}" for u in result["unmet"]] == _split(row["expected_unmet"])
    assert ("COMPLETE" if result["complete"] else "INCOMPLETE") == row["expected_outcome"]


@pytest.mark.req("REQ-POL-01", "REQ-NFR-06")
@pytest.mark.ac("AC-03", "AC-16")
@pytest.mark.parametrize("row", APPLICABILITY_EXAMPLES, ids=[r["example_id"] for r in APPLICABILITY_EXAMPLES])
def test_applicability_example(row):
    expected = row["expected_effective_version"]
    if expected == "INVALID_DATE":
        with pytest.raises(InvalidDate):
            parse_iso_date(row["business_date"])
        return
    policy = effective_policy(POLICIES.values(), parse_iso_date(row["business_date"]))
    assert (policy.version if policy else "NONE") == expected


@pytest.mark.req("REQ-POL-03")
def test_evaluation_is_deterministic_and_ignores_document_order():
    docs = _docs("SERVICE_SCOPE@2026-04-15;REG_EXTRACT@2026-05-20;SERVICE_SCOPE@2026-06-20;OWNERSHIP_DECL@2026-05-20;BANK_CONFIRM@2026-05-21")
    a = evaluate(POLICIES["v2"], "SUBSCRIPTION", "V3", docs, date(2026, 7, 15))
    b = evaluate(POLICIES["v2"], "SUBSCRIPTION", "V3", list(reversed(docs)), date(2026, 7, 15))
    assert a["complete"] and b["complete"]
    assert a["required"] == b["required"] and a["unmet"] == b["unmet"]


@pytest.mark.req("REQ-POL-02")
def test_v2_differs_from_v1_only_by_ev05():
    v1 = {r.id: r.as_dict() for r in POLICIES["v1"].rules}
    v2 = {r.id: r.as_dict() for r in POLICIES["v2"].rules}
    assert set(v2) - set(v1) == {"EV-05"}
    assert all(v1[k] == v2[k] for k in v1)
    assert POLICIES["v2"].effective_from == date(2026, 7, 1)
