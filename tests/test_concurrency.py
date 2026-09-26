"""AC-13: concurrent terminal decisions on separate SQLite connections (real threads, real file)."""
from __future__ import annotations

import threading

import pytest

from moblab.db import connect
from moblab.errors import DomainError
from moblab.service import WorkflowService


def _race(path, ref, payloads):
    barrier = threading.Barrier(len(payloads))
    results = [None] * len(payloads)

    def worker(i, actor, payload):
        conn = connect(path)
        try:
            svc = WorkflowService(conn)
            barrier.wait()
            try:
                results[i] = ("ok", svc.decide(actor, ref, payload).body)
            except DomainError as exc:
                results[i] = ("err", exc.code)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i, a, p)) for i, (a, p) in enumerate(payloads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


@pytest.mark.req("REQ-DEC-03")
@pytest.mark.ac("AC-13")
@pytest.mark.parametrize("round_no", range(3))
def test_eight_concurrent_decisions_one_winner(seeded_lab, round_no):
    lab = seeded_lab
    payloads = []
    for i in range(8):
        outcome = "approve" if i % 2 == 0 else "reject"
        payloads.append(("sam" if i % 3 else "dana", lab.decision_payload("MOB-0003", outcome)))
    results = _race(lab.path, "MOB-0003", payloads)
    winners = [r for r in results if r[0] == "ok"]
    losers = [r for r in results if r[0] == "err"]
    assert len(winners) == 1, results
    assert {code for _, code in losers} == {"ALREADY_DECIDED"}, results
    app, _ = lab.current("MOB-0003")
    assert lab.count("decision", "application_id = ?", app["id"]) == 1
    assert lab.count("event", "application_id = ? AND event_type IN ('APPROVED','REJECTED')", app["id"]) == 1
    assert app["status"] == winners[0][1]["status"]


@pytest.mark.req("REQ-DEC-03", "REQ-DEC-04")
@pytest.mark.ac("AC-10", "AC-13")
def test_concurrent_retries_with_same_key_yield_one_decision(seeded_lab):
    lab = seeded_lab
    payload = lab.decision_payload("MOB-0004")
    results = _race(lab.path, "MOB-0004", [("sam", dict(payload)) for _ in range(6)])
    assert all(r[0] == "ok" for r in results), results
    assert len({r[1]["decision_id"] for r in results}) == 1
    assert sum(1 for r in results if r[1]["replayed"] is False) == 1
    app, _ = lab.current("MOB-0004")
    assert lab.count("decision", "application_id = ?", app["id"]) == 1
