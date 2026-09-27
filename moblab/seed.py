"""Reproducible synthetic seed. Every business action goes through WorkflowService, so the seeded
history (revisions, evaluations, decisions, events) is produced by the same rules as live use.

Two shortcuts, both seed-only and documented: the lab date is stepped directly in `meta`
(to simulate June history without CLOCK_ADVANCED noise), and wall-clock stamps are
deterministic so two seeds produce identical databases.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import connect, create_schema, transaction
from .service import ROLE_AUDITOR, ROLE_POLICY_OWNER, ROLE_REVIEWER, ROLE_SPECIALIST, WorkflowService

SEED_START_DATE = "2026-06-01"
SEED_END_DATE = "2026-06-24"

ACTORS = [
    ("alex", "Alex Rivera", "Onboarding Specialist", [ROLE_SPECIALIST]),
    ("jordan", "Jordan Lee", "Onboarding Specialist", [ROLE_SPECIALIST]),
    ("sam", "Sam Okafor", "Risk Reviewer", [ROLE_REVIEWER]),
    ("dana", "Dana Morales", "Senior Risk Reviewer", [ROLE_REVIEWER]),
    ("kai", "Kai Nakamura", "Specialist and Reviewer (dual role)", [ROLE_SPECIALIST, ROLE_REVIEWER]),
    ("morgan", "Morgan Blake", "Policy Owner", [ROLE_POLICY_OWNER]),
    ("riley", "Riley Park", "Auditor", [ROLE_AUDITOR]),
]


class SeedClock:
    """Deterministic wall-clock stamps: 09:00:00Z + one minute per write on each lab date."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.counter: dict[str, int] = {}

    def __call__(self) -> str:
        bd = self.conn.execute("SELECT value FROM meta WHERE key='business_date'").fetchone()[0]
        n = self.counter.get(bd, 0)
        self.counter[bd] = n + 1
        return f"{bd}T{9 + n // 60:02d}:{n % 60:02d}:00Z"


def init_database(path: str | Path, *, with_demo_cases: bool = True, business_date: str = SEED_START_DATE) -> None:
    """Create a new database file. Refuses to touch an existing one (callers back up first)."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} already exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        create_schema(conn, business_date)
        with transaction(conn):
            for actor_id, name, title, roles in ACTORS:
                conn.execute("INSERT INTO actor(id, display_name, title, roles_json) VALUES (?,?,?,?)",
                             (actor_id, name, title, json.dumps(roles)))
        if with_demo_cases:
            _seed_cases(conn)
    finally:
        conn.close()


def _set_date(conn: sqlite3.Connection, business_date: str) -> None:
    with transaction(conn):
        conn.execute("UPDATE meta SET value = ? WHERE key = 'business_date'", (business_date,))


def _seed_cases(conn: sqlite3.Connection) -> None:
    svc = WorkflowService(conn, now=SeedClock(conn))
    seq = iter(range(1, 10_000))

    def key() -> str:
        return f"seed-{next(seq):04d}"

    def ok(result):
        assert result.http_status in (200, 201), result.body
        return result.body

    def create(actor, name, ident, product, band, summary):
        return ok(svc.create_application(actor, {
            "request_key": key(), "legal_name": name, "business_identifier": ident,
            "product_category": product, "volume_band": band, "activity_summary": summary,
        }))["reference"]

    def doc(actor, ref, doc_type, issued_on, code, title, pages=2):
        ok(svc.add_document(actor, ref, {
            "request_key": key(), "doc_type": doc_type, "title": title, "issued_on": issued_on,
            "page_count": pages, "reference": f"SYN-DOC-{code}",
        }))

    def base(actor, ref, n, name, *, bank=True):
        doc(actor, ref, "REG_EXTRACT", "2026-05-18", f"{n:04d}REG", f"Registration extract — {name}", 3)
        doc(actor, ref, "OWNERSHIP_DECL", "2026-05-19", f"{n:04d}OWN", f"Ownership declaration — {name}", 2)
        if bank:
            doc(actor, ref, "BANK_CONFIRM", "2026-05-20", f"{n:04d}BNK", f"Settlement account letter — {name}", 1)

    def submit(actor, ref):
        return ok(svc.submit(actor, ref, {"request_key": key()}))

    def decide(actor, ref, outcome, code, text):
        app = conn.execute("SELECT current_revision_id FROM application WHERE reference = ?", (ref,)).fetchone()
        ev = conn.execute("SELECT id, policy_version FROM policy_evaluation WHERE revision_id = ? ORDER BY id DESC LIMIT 1",
                          (app[0],)).fetchone()
        ok(svc.decide(actor, ref, {"request_key": key(), "outcome": outcome, "revision_id": app[0],
                                   "policy_version": ev[1], "evaluation_id": ev[0],
                                   "reason_code": code, "reason_text": text}))

    _set_date(conn, "2026-06-01")
    m1 = create("alex", "Alder Street Bakery Ltd", "SYN-100001", "CARD_PRESENT", "V1",
                "Neighbourhood bakery with two counters; card payments in store only.")
    m2 = create("alex", "Quillon Travel Experiences Ltd", "SYN-100002", "FUTURE_DELIVERY", "V3",
                "Pre-paid guided walking tours booked up to six months ahead.")
    m3 = create("alex", "Larkspire Streaming Club Ltd", "SYN-100003", "SUBSCRIPTION", "V3",
                "Monthly streaming membership for independent film clubs; billed on the 1st.")
    m4 = create("jordan", "Tidewell Event Passes Ltd", "SYN-100004", "FUTURE_DELIVERY", "V4",
                "Season passes for coastal music events, sold before each season starts.")
    m5 = create("jordan", "Wrenfield Home Goods Ltd", "SYN-100005", "ECOMMERCE", "V2",
                "Online shop for kitchenware; goods shipped within three days.")
    m6 = create("jordan", "Nettlefield Fitness Memberships Ltd", "SYN-100006", "SUBSCRIPTION", "V2",
                "Gym memberships billed monthly across three sites.")
    m7 = create("alex", "Vantory Gadgets Ltd", "SYN-100007", "ECOMMERCE", "V4",
                "Consumer electronics marketplace storefront.")
    m8 = create("kai", "Kestrelmoor Language Club Ltd", "SYN-100008", "SUBSCRIPTION", "V1",
                "Weekly online language conversation classes, billed monthly.")
    base("alex", m1, 1, "Alder Street Bakery Ltd")
    base("alex", m2, 2, "Quillon Travel Experiences Ltd")
    base("alex", m3, 3, "Larkspire Streaming Club Ltd")
    base("jordan", m4, 4, "Tidewell Event Passes Ltd")
    doc("jordan", m4, "PROCESSING_HISTORY", "2026-05-29", "0004PRH", "Six-month processing statements — Tidewell", 12)
    base("jordan", m5, 5, "Wrenfield Home Goods Ltd", bank=False)
    doc("jordan", m6, "REG_EXTRACT", "2026-05-18", "0006REG", "Registration extract — Nettlefield Fitness Memberships Ltd", 3)
    doc("jordan", m6, "OWNERSHIP_DECL", "2026-05-19", "0006OWN", "Ownership declaration — Nettlefield Fitness Memberships Ltd", 2)
    base("alex", m7, 7, "Vantory Gadgets Ltd")
    doc("alex", m7, "PROCESSING_HISTORY", "2026-05-29", "0007PRH", "Six-month processing statements — Vantory", 10)
    base("kai", m8, 8, "Kestrelmoor Language Club Ltd")

    _set_date(conn, "2026-06-05")
    doc("jordan", m4, "SERVICE_SCOPE", "2026-06-05", "0004SCP", "Service-scope statement — season pass delivery calendar", 4)

    _set_date(conn, "2026-06-08")
    submit("alex", m1)
    submit("alex", m7)

    _set_date(conn, "2026-06-10")
    decide("sam", m1, "approve", "EVIDENCE_VERIFIED", "Registration, ownership and settlement evidence consistent.")

    _set_date(conn, "2026-06-11")
    decide("dana", m7, "reject", "EVIDENCE_INCONSISTENT",
           "Processing statements name a different trading entity than the registration extract.")

    _set_date(conn, "2026-06-12")
    submit("jordan", m5)

    _set_date(conn, "2026-06-15")
    rev5 = conn.execute("SELECT current_revision_id FROM application WHERE reference = ?", (m5,)).fetchone()[0]
    ok(svc.request_information("sam", m5, {
        "request_key": key(), "revision_id": rev5, "reason_code": "MISSING_EVIDENCE",
        "message": "The settlement bank account confirmation is missing. Please add it and resubmit.",
        "requested_doc_types": ["BANK_CONFIRM"],
    }))

    _set_date(conn, "2026-06-16")
    submit("alex", m2)

    _set_date(conn, "2026-06-18")
    decide("dana", m2, "approve", "EVIDENCE_VERIFIED", "All v1 evidence present; tour operator registration verified.")

    _set_date(conn, "2026-06-22")
    submit("alex", m3)

    _set_date(conn, "2026-06-23")
    submit("jordan", m4)
    submit("kai", m8)

    _set_date(conn, SEED_END_DATE)
