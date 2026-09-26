"""HTTP-level developer acceptance checks through the real Flask app and a real SQLite file."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from moblab.db import connect, fingerprint
from moblab.seed import init_database
from moblab.web import HOST, PORT, create_app

REPO = Path(__file__).resolve().parent.parent


class Api:
    """JSON client: fetches the CSRF token from /api/session, as a real client would."""

    def __init__(self, app):
        self.c = app.test_client()
        self.token = self.c.get("/api/session").get_json()["csrf_token"]
        self.n = 0

    def key(self):
        self.n += 1
        return f"http-key-{self.n:05d}-{id(self) % 10000:04d}"

    def act(self, actor):
        r = self.post("/api/session/actor", {"actor_id": actor})
        assert r.status_code == 200, r.get_json()
        return self

    def post(self, path, body, **headers):
        return self.c.post(path, json=body, headers={"X-CSRF-Token": self.token, **headers})

    def get(self, path):
        return self.c.get(path)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "http.db"
    init_database(path)
    return path


@pytest.fixture
def app(db_path, tmp_path):
    return create_app(db_path, secret_key="test-secret-" + "x" * 20, instance_dir=tmp_path)


@pytest.fixture
def api(app):
    return Api(app)


def fp(db_path):
    conn = connect(db_path)
    try:
        return fingerprint(conn)
    finally:
        conn.close()


def decision_body(api, ref, outcome="approve", **over):
    detail = api.get(f"/api/applications/{ref}").get_json()
    body = {"request_key": api.key(), "outcome": outcome, "revision_id": detail["current"]["id"],
            "policy_version": detail["current_eval"]["policy_version"], "evaluation_id": detail["current_eval"]["id"],
            "reason_code": "EVIDENCE_VERIFIED" if outcome == "approve" else "EVIDENCE_INCONSISTENT",
            "reason_text": "Evidence reviewed against the policy and found consistent." if outcome == "approve"
            else "Evidence contradicts the declared business activity."}
    body.update(over)
    return body


# ----------------------------------------------------------------------------- AC-19 controls
@pytest.mark.req("REQ-NFR-03", "REQ-DEC-07")
@pytest.mark.ac("AC-19")
def test_csrf_required_on_every_post(app, api, db_path):
    api.act("sam")
    before = fp(db_path)
    body = decision_body(api, "MOB-0003")
    raw = app.test_client()
    raw.get("/api/session")
    for headers in ({}, {"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": ""}):
        r = raw.post("/api/applications/MOB-0003/decisions", json=body, headers=headers)
        assert r.status_code == 403 and r.get_json()["error"]["code"] == "CSRF_FAILED"
    r = api.c.post("/cases/MOB-0003/decision", data={"outcome": "approve"})
    assert r.status_code == 403 and b"CSRF_FAILED" in r.data
    r = api.c.post("/api/applications/MOB-0003/decisions", json=body, headers={"X-CSRF-Token": "x" * 43})
    assert r.status_code == 403
    assert fp(db_path) == before


@pytest.mark.req("REQ-NFR-02", "REQ-NFR-03", "REQ-NFR-08")
@pytest.mark.ac("AC-19")
def test_host_origin_and_size_controls(app, api, db_path):
    api.act("sam")
    before = fp(db_path)
    body = decision_body(api, "MOB-0003")
    for host in ("evil.example", "127.0.0.1.evil.example", "192.168.1.5:5058", "0.0.0.0:5058"):
        r = api.c.get("/", headers={"Host": host})
        assert r.status_code == 400 and b"HOST_NOT_ALLOWED" in r.data
        r = api.post("/api/applications/MOB-0003/decisions", body, Host=host)
        assert r.status_code == 400
    for origin in ("http://evil.example", "null", "http://localhost.evil.example", "http://127.0.0.1:9999"):
        r = api.post("/api/applications/MOB-0003/decisions", body, Origin=origin)
        assert r.status_code == 403 and r.get_json()["error"]["code"] == "ORIGIN_NOT_ALLOWED", origin
    r = api.post("/api/applications/MOB-0003/decisions", {**body, "reason_text": "x" * (70 * 1024)})
    assert r.status_code == 413 and r.get_json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    r = api.c.post("/api/applications/MOB-0003/decisions", data="a=b", headers={"X-CSRF-Token": api.token})
    assert r.status_code == 415
    assert fp(db_path) == before
    # Same-origin request with a matching Origin header is accepted.
    r = api.post("/api/applications/MOB-0003/decisions", body, Origin="http://localhost")
    assert r.status_code == 201, r.get_json()


@pytest.mark.req("REQ-NFR-02")
@pytest.mark.ac("AC-19")
def test_server_binds_localhost_5058_only():
    assert (HOST, PORT) == ("127.0.0.1", 5058)
    main_src = (REPO / "moblab" / "__main__.py").read_text(encoding="utf-8")
    assert "app.run(host=HOST" in main_src and "0.0.0.0" not in main_src
    launcher = (REPO / "launch_demo.command").read_text(encoding="utf-8")
    assert "PORT=5058" in launcher and "0.0.0.0" not in launcher


# ----------------------------------------------------------------------------- AC-20 no status bypass
@pytest.mark.req("REQ-WF-01", "REQ-WF-02")
@pytest.mark.ac("AC-20")
def test_no_generic_status_update(api, db_path):
    api.act("sam")
    before = fp(db_path)
    for method in ("patch", "put", "delete"):
        r = getattr(api.c, method)("/api/applications/MOB-0003", json={"status": "approved"},
                                   headers={"X-CSRF-Token": api.token})
        assert r.status_code == 405 and r.get_json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    r = api.post("/api/applications/MOB-0003/status", {"status": "approved"})
    assert r.status_code == 405
    r = api.post("/api/applications/MOB-0003/decisions", {**decision_body(api, "MOB-0003"), "status": "approved"})
    assert r.status_code == 422 and "status" in r.get_json()["error"]["details"]["fields"]
    api.act("alex")
    r = api.post("/api/applications/MOB-0006/draft", {"request_key": api.key(), "status": "submitted"})
    assert r.status_code == 422
    assert fp(db_path) == before
    assert api.get("/api/applications/MOB-0003").get_json()["app"]["status"] == "submitted"


# ----------------------------------------------------------------------------- AC-18 read-only pages
def _all_get_urls(app):
    samples = {"reference": "MOB-0003", "revision_no": 1, "document_id": 1}
    urls = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        urls.append(re.sub(r"<(?:[a-z]+:)?([a-z_]+)>", lambda m: str(samples[m.group(1)]), rule.rule))
    for ref in ("MOB-0001", "MOB-0002", "MOB-0005", "MOB-0006", "MOB-0007", "MOB-0008", "MOB-9999"):
        urls += [f"/cases/{ref}", f"/api/applications/{ref}", f"/cases/{ref}/export.json"]
    urls += ["/cases?status=approved&product=SUBSCRIPTION&policy=v1", "/cases?status=<script>", "/static/style.css",
             "/cases/MOB-0003/revisions/99999999999999999999999", "/api/applications?status=nope"]
    return urls


@pytest.mark.req("REQ-NFR-05", "REQ-POL-07")
@pytest.mark.ac("AC-18")
def test_every_get_route_leaves_the_database_unchanged(app, api, db_path):
    urls = _all_get_urls(app)
    assert len(urls) >= 30
    before = fp(db_path)
    for actor in (None, "sam", "morgan", "alex", "riley"):
        if actor:
            api.act(actor)
        after_actor = fp(db_path)
        for url in urls:
            r = api.get(url)
            assert r.status_code in (200, 404, 405), (url, r.status_code)
        assert fp(db_path) == after_actor == before
    # And in a state where the migration preview has real work to show.
    api.act("morgan")
    assert api.post("/api/lab/clock", {"business_date": "2026-07-01"}).status_code == 200
    moved = fp(db_path)
    for url in urls:
        api.get(url)
    assert fp(db_path) == moved


@pytest.mark.req("REQ-NFR-05")
@pytest.mark.ac("AC-18")
def test_get_requests_use_a_query_only_connection(app):
    from flask import g
    with app.test_request_context("/", method="GET"):
        app.preprocess_request()
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            g.db.execute("UPDATE meta SET value = '2030-01-01' WHERE key = 'business_date'")
        app.do_teardown_request()


# ----------------------------------------------------------------------------- AC-17 safe rendering and export
@pytest.mark.req("REQ-NFR-04")
@pytest.mark.ac("AC-17")
def test_hostile_text_is_escaped_and_exports_are_safe(api, db_path):
    api.act("alex")
    evil_summary = '<script>alert("x")</script><img src=x onerror=alert(1)> =HYPERLINK("http://evil")'
    r = api.post("/api/applications", {"request_key": api.key(), "legal_name": "Safe Render Test Ltd",
                                       "business_identifier": "SYN-424242", "product_category": "ECOMMERCE",
                                       "volume_band": "V1", "activity_summary": evil_summary})
    assert r.status_code == 201
    ref = r.get_json()["reference"]
    r = api.post(f"/api/applications/{ref}/documents", {"request_key": api.key(), "doc_type": "OTHER",
                                                        "title": "=cmd|' /C calc'!A0 <b>bold</b>", "issued_on": "2026-06-01",
                                                        "page_count": 1, "reference": "SYN-DOC-XSS1"})
    assert r.status_code == 201
    for d in ("REG_EXTRACT", "OWNERSHIP_DECL", "BANK_CONFIRM"):
        api.post(f"/api/applications/{ref}/documents", {"request_key": api.key(), "doc_type": d, "title": f"{d} doc",
                                                        "issued_on": "2026-06-01", "page_count": 1,
                                                        "reference": f"SYN-DOC-{d[:4]}9"})
    assert api.post(f"/api/applications/{ref}/submit", {"request_key": api.key()}).status_code == 201

    for url in (f"/cases/{ref}", f"/cases/{ref}/revisions/1", "/cases", "/timeline"):
        html = api.get(url).get_data(as_text=True)
        assert "<script>alert" not in html and "<img src=x" not in html and "<b>bold</b>" not in html, url
    html = api.get(f"/cases/{ref}").get_data(as_text=True)
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in api.get(f"/cases/{ref}/revisions/1").get_data(as_text=True)

    r = api.get("/export/cases.csv")
    assert r.mimetype == "text/csv" and "attachment" in r.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
    row = next(x for x in rows if x[0] == ref)
    header = rows[0]
    assert row[header.index("activity_summary")] == evil_summary      # does not start with a formula char
    assert row[header.index("document_titles")].startswith("'=cmd")
    from moblab.queries import csv_safe
    for dangerous in ("=1+1", "+1", "-1", "@SUM(A1)", "\t=1", "\r=1"):
        assert csv_safe(dangerous) == "'" + dangerous
    assert csv_safe("Normal Ltd") == "Normal Ltd"

    r = api.get(f"/cases/{ref}/export.json")
    assert r.mimetype == "application/json"
    data = json.loads(r.get_data(as_text=True))
    assert data["revisions"][0]["activity_summary"] == evil_summary
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]


# ----------------------------------------------------------------------------- API flows
@pytest.mark.req("REQ-DEC-04", "REQ-ROLE-01", "REQ-DEC-01", "REQ-ROLE-03")
@pytest.mark.ac("AC-09", "AC-10", "AC-11", "AC-12")
def test_api_roles_idempotency_and_cross_application(api, db_path):
    api.act("alex")
    body = decision_body(api, "MOB-0003")
    r = api.post("/api/applications/MOB-0003/decisions", body)
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "FORBIDDEN_ROLE"
    api.act("riley")
    assert api.post("/api/lab/clock", {"business_date": "2026-06-25"}).status_code == 403
    assert api.post("/api/applications/MOB-0003/decisions", body).status_code == 403

    api.act("sam")
    other = api.get("/api/applications/MOB-0004").get_json()
    r = api.post("/api/applications/MOB-0003/decisions", {**body, "request_key": api.key(),
                                                           "revision_id": other["current"]["id"]})
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "REVISION_APPLICATION_MISMATCH"

    first = api.post("/api/applications/MOB-0003/decisions", body)
    assert first.status_code == 201
    retry = api.post("/api/applications/MOB-0003/decisions", body)
    assert retry.status_code == 200 and retry.get_json()["replayed"] is True
    assert retry.get_json()["decision_id"] == first.get_json()["decision_id"]
    conflict = api.post("/api/applications/MOB-0004/decisions", {**decision_body(api, "MOB-0004"),
                                                                  "request_key": body["request_key"]})
    assert conflict.status_code == 409 and conflict.get_json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert api.get("/api/applications/MOB-0004").get_json()["app"]["status"] == "submitted"

    api2 = Api(api.c.application)
    r = api2.post("/api/applications/MOB-0004/decisions", decision_body(api2, "MOB-0004"))
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "UNKNOWN_ACTOR"


@pytest.mark.req("REQ-NFR-06", "REQ-DEC-07")
@pytest.mark.ac("AC-16", "AC-23")
def test_def001_huge_ids_over_http_are_404_not_500(api, db_path):
    """DEF-001 / Codex R3-01, HTTP path."""
    api.act("jordan")
    before = fp(db_path)
    for doc_id in (2**100, 2**31, 99999999999999999999999999):
        r = api.post(f"/api/applications/MOB-0006/documents/{doc_id}/remove", {"request_key": api.key()})
        assert r.status_code == 404 and r.get_json()["error"]["code"] == "NOT_FOUND", doc_id
        r = api.c.post(f"/cases/MOB-0006/documents/{doc_id}/remove",
                       data={"csrf_token": api.token, "request_key": api.key()})
        assert r.status_code == 404, doc_id
    for path in ("/cases/MOB-0003/revisions/99999999999999999999", "/api/applications/MOB-00030",
                 "/api/applications/%00", "/cases/MOB-0003/revisions/0"):
        assert api.get(path).status_code == 404, path
    api.act("sam")
    body = decision_body(api, "MOB-0003", revision_id=2**100)
    r = api.post("/api/applications/MOB-0003/decisions", body)
    assert r.status_code == 422
    r = api.post("/api/lab/clock", {"business_date": "2026-02-30"})
    assert r.status_code == 422
    assert fp(db_path) == before


# ----------------------------------------------------------------------------- UI form flows
def _form_fields(html, testid):
    form = re.search(rf'<form[^>]*data-testid="{testid}"[^>]*>(.*?)</form>', html, re.S).group(1)
    fields = dict(re.findall(r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"', form))
    return fields


@pytest.mark.req("REQ-DEC-01", "REQ-DEC-04", "REQ-DEC-07")
@pytest.mark.ac("AC-22", "AC-10")
def test_stale_and_duplicate_form_posts(app, db_path):
    sam = Api(app).act("sam")
    alex = Api(app).act("alex")
    morgan = Api(app).act("morgan")
    html = sam.get("/cases/MOB-0003").get_data(as_text=True)
    stale = _form_fields(html, "approve-form")
    assert stale["policy_version"] == "v1"
    stale["reason_code"] = "EVIDENCE_VERIFIED"
    stale["reason_text"] = "Approving what I inspected before the change."

    morgan.post("/api/lab/clock", {"business_date": "2026-07-01"})
    before = fp(db_path)
    r = sam.c.post("/cases/MOB-0003/decision", data=stale)
    assert r.status_code == 409 and b"POLICY_MIGRATION_REQUIRED" in r.data
    assert fp(db_path) == before

    assert morgan.post("/api/policy/migrations", {"request_key": morgan.key(), "target_version": "v2"}).status_code == 201
    alex.post("/api/applications/MOB-0003/documents", {"request_key": alex.key(), "doc_type": "SERVICE_SCOPE",
                                                       "title": "Service scope statement", "issued_on": "2026-06-28",
                                                       "page_count": 3, "reference": "SYN-DOC-SCOPE3"})
    assert alex.post("/api/applications/MOB-0003/submit", {"request_key": alex.key(),
                                                           "response_note": "Added the service-scope statement."}).status_code == 201
    before = fp(db_path)
    r = sam.c.post("/cases/MOB-0003/decision", data=stale)
    assert r.status_code == 409 and b"STALE_REVISION" in r.data and b'data-testid="error-banner"' in r.data
    assert fp(db_path) == before

    fresh = _form_fields(sam.get("/cases/MOB-0003").get_data(as_text=True), "approve-form")
    fresh.update(reason_code="EVIDENCE_VERIFIED", reason_text="Revision 2 reviewed under v2; statement present.")
    r1 = sam.c.post("/cases/MOB-0003/decision", data=fresh)
    assert r1.status_code == 303
    r2 = sam.c.post("/cases/MOB-0003/decision", data=fresh, follow_redirects=True)   # double click / refresh
    assert r2.status_code == 200 and b"duplicate request detected" in r2.data
    conn = connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM decision WHERE application_id = 3").fetchone()[0] == 1
    conn.close()


@pytest.mark.req("REQ-ROLE-02", "REQ-DEC-03")
@pytest.mark.ac("AC-08", "AC-22")
def test_self_review_and_rejected_case_via_forms(app, db_path):
    kai = Api(app).act("kai")
    html = kai.get("/cases/MOB-0008").get_data(as_text=True)
    assert 'data-testid="self-review-warning"' in html
    fields = _form_fields(html, "approve-form")
    fields.update(reason_code="EVIDENCE_VERIFIED", reason_text="Trying to approve my own case.")
    before = fp(db_path)
    r = kai.c.post("/cases/MOB-0008/decision", data=fields)
    assert r.status_code == 403 and b"SELF_REVIEW_FORBIDDEN" in r.data
    # A rejected case shows no decision form and a forged post is refused.
    sam = Api(app).act("sam")
    html = sam.get("/cases/MOB-0007").get_data(as_text=True)
    assert 'data-testid="approve-form"' not in html and 'data-testid="reopen-form"' in html
    detail = sam.get("/api/applications/MOB-0007").get_json()
    forged = {"csrf_token": sam.token, "request_key": sam.key(), "outcome": "approve",
              "revision_id": str(detail["current"]["id"]), "policy_version": "v1",
              "evaluation_id": str(detail["current_eval"]["id"]), "reason_code": "EVIDENCE_VERIFIED",
              "reason_text": "Forged approval of a rejected case."}
    r = sam.c.post("/cases/MOB-0007/decision", data=forged)
    assert r.status_code == 409 and b"ALREADY_DECIDED" in r.data
    assert fp(db_path) == before


# ----------------------------------------------------------------------------- AC-15 restart and launcher
@pytest.mark.req("REQ-NFR-01")
@pytest.mark.ac("AC-15")
def test_state_survives_app_restart(db_path, tmp_path):
    app1 = create_app(db_path, secret_key="s" * 32, instance_dir=tmp_path)
    api = Api(app1).act("sam")
    assert api.post("/api/applications/MOB-0008/decisions", decision_body(api, "MOB-0008")).status_code == 201
    api.act("morgan")
    api.post("/api/lab/clock", {"business_date": "2026-07-03"})
    before = fp(db_path)
    del app1, api
    app2 = create_app(db_path, secret_key="other" * 8, instance_dir=tmp_path)
    fresh = Api(app2)
    assert fresh.get("/api/applications/MOB-0008").get_json()["app"]["status"] == "approved"
    assert fresh.get("/api/session").get_json()["business_date"] == "2026-07-03"
    assert fp(db_path) == before


def _launch(tmp_path, *args, python=None):
    env = {**os.environ, "MOBLAB_INSTANCE_DIR": str(tmp_path / "inst")}
    if python:
        env["MOBLAB_PYTHON"] = python
    else:
        env["MOBLAB_PYTHON"] = sys.executable
    return subprocess.run(["bash", str(REPO / "launch_demo.command"), *args], capture_output=True, text=True,
                          env=env, timeout=120)


@pytest.mark.req("REQ-NFR-07", "REQ-NFR-01")
@pytest.mark.ac("AC-15")
def test_launch_script_seeds_once_preserves_and_backs_up(tmp_path):
    db = tmp_path / "inst" / "merchant_onboarding_lab.db"
    r = _launch(tmp_path, "--check-only")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "creating the synthetic seed" in r.stdout and db.exists()
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO meta(key, value) VALUES ('marker', 'kept')")
    conn.commit()
    conn.close()
    r = _launch(tmp_path, "--check-only")
    assert r.returncode == 0 and "preserved" in r.stdout
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT value FROM meta WHERE key = 'marker'").fetchone() == ("kept",)
    conn.close()
    r = _launch(tmp_path, "--reset", "--check-only")
    assert r.returncode == 0 and "Backed up previous database" in r.stdout, r.stdout
    backups = list((tmp_path / "inst" / "backups").glob("*.db"))
    assert len(backups) == 1
    conn = sqlite3.connect(backups[0])
    assert conn.execute("SELECT value FROM meta WHERE key = 'marker'").fetchone() == ("kept",)
    conn.close()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT value FROM meta WHERE key = 'marker'").fetchone() is None
    conn.close()
    r = _launch(tmp_path, "--bogus")
    assert r.returncode == 2


@pytest.mark.req("REQ-NFR-07")
@pytest.mark.ac("AC-15")
def test_launch_script_never_installs_missing_dependencies(tmp_path):
    fake = tmp_path / "python-without-flask"
    fake.write_text("#!/bin/sh\n"
                    "case \"$*\" in *'import flask'*) exit 1;; esac\n"
                    f"exec {sys.executable} \"$@\"\n")
    fake.chmod(0o755)
    r = _launch(tmp_path, "--check-only", python=str(fake))
    assert r.returncode == 1
    assert "Flask is not installed" in r.stdout
    assert "python3 -m venv .venv" in r.stdout and ".venv/bin/python -m pip install -r requirements.txt" in r.stdout
    assert "cd \"" in r.stdout and "./launch_demo.command" in r.stdout
    assert not (tmp_path / "inst").exists()
    assert "pip install" not in (REPO / "launch_demo.command").read_text().split("setup_help()")[0]


@pytest.mark.req("REQ-NFR-01")
@pytest.mark.ac("AC-15")
def test_seed_is_reproducible(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    init_database(a)
    init_database(b)
    assert fp(a) == fp(b)
    with pytest.raises(FileExistsError):
        init_database(a)
