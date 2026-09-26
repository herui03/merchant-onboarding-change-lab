"""HTML pages and form posts. Forms call the same WorkflowService as the API.

Successful posts redirect (POST/redirect/GET) with a flash message. Refused posts re-render the
page with the error code, message and the real HTTP status, so a stale or forbidden action is
visible and the database is untouched (the service rolled the transaction back).
"""
from __future__ import annotations

import json
import secrets
import tomllib

from flask import (Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for)

from ..contracts import APPROVE_REASON_CODES, INFO_REASON_CODES, REJECT_REASON_CODES
from ..errors import DomainError
from ..paths import ACCEPTANCE_DIR
from ..queries import (actor_map, application_detail, case_export, cases_csv, counts, evidence_by_requirement,
                       evidence_by_scenario, example_results, global_events, impact, lab_state, list_applications,
                       load_evidence, policy_comparison, revision_view)
from ..service import ROLE_POLICY_OWNER, ROLE_REVIEWER, ROLE_SPECIALIST, WorkflowService

bp = Blueprint("ui", __name__)

INT_FIELDS = {"revision_id", "evaluation_id", "page_count"}
LIST_FIELDS = {"requested_doc_types"}


def new_key() -> str:
    return secrets.token_urlsafe(18)


def _actor_id() -> str:
    return session.get("actor_id", "")


def _form_payload(*, drop=()) -> dict:
    """Form strings → service payload. Digit-only strings become ints for integer fields; anything
    else is passed through unchanged so the service contract reports it (no silent coercion)."""
    payload = {}
    for name in request.form:
        if name == "csrf_token" or name in drop:
            continue
        if name in LIST_FIELDS:
            payload[name] = request.form.getlist(name)
            continue
        value = request.form.get(name, "")
        if name in INT_FIELDS and value.isascii() and value.isdigit() and len(value) <= 12:
            value = int(value)
        payload[name] = value
    return payload


def _scenarios() -> dict:
    return tomllib.loads((ACCEPTANCE_DIR / "acceptance_scenarios.toml").read_text(encoding="utf-8"))


def _viewer(detail) -> dict:
    """UI hints only — the service enforces every rule regardless of what is shown."""
    actor = actor_map(g.db).get(_actor_id())
    roles = set(actor["roles"]) if actor else set()
    app = detail["app"] if detail else None
    involved = False
    if actor and app:
        involved = app["created_by"] == actor["id"] or any(r["submitted_by"] == actor["id"] for r in detail["revisions"])
    return {"actor": actor, "is_specialist": ROLE_SPECIALIST in roles, "is_reviewer": ROLE_REVIEWER in roles,
            "is_policy_owner": ROLE_POLICY_OWNER in roles, "involved": involved}


# ----------------------------------------------------------------------------- pages
@bp.get("/")
def home():
    return render_template("home.html", counts=counts(g.db), apps=list_applications(g.db))


@bp.get("/demo")
def demo():
    apps = {a["reference"]: a for a in list_applications(g.db)}
    detail3 = application_detail(g.db, "MOB-0003")
    return render_template("demo.html", apps=apps, detail3=detail3, key=new_key())


@bp.get("/cases")
def cases():
    status = request.args.get("status", "")
    product = request.args.get("product", "")
    policy = request.args.get("policy", "")
    return render_template("cases.html", apps=list_applications(g.db, status=status, product=product, policy=policy),
                           filters={"status": status, "product": product, "policy": policy})


@bp.get("/cases/new")
def new_case():
    return render_template("new_case.html", key=new_key(), form={}, error=None)


@bp.get("/cases/<reference>")
def case(reference):
    return _render_case(reference)


def _render_case(reference, *, error: DomainError | None = None, status: int = 200, form: dict | None = None):
    detail = application_detail(g.db, reference)
    if detail is None:
        abort(404)
    return render_template(
        "case.html", d=detail, v=_viewer(detail), error=error, form=form or {},
        keys={name: new_key() for name in ("draft", "doc", "submit", "info", "approve", "reject", "reopen", "remove")},
        info_codes=INFO_REASON_CODES, approve_codes=APPROVE_REASON_CODES, reject_codes=REJECT_REASON_CODES,
    ), status


@bp.get("/cases/<reference>/revisions/<int:revision_no>")
def revision(reference, revision_no):
    view = revision_view(g.db, reference, revision_no)
    if view is None:
        abort(404)
    return render_template("revision.html", r=view)


@bp.get("/cases/<reference>/export.json")
def case_json(reference):
    data = case_export(g.db, reference)
    if data is None:
        raise DomainError("NOT_FOUND", "No such application.", 404)
    body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{data["application"]["reference"]}.json"'})


@bp.get("/export/cases.csv")
def export_csv():
    return Response(cases_csv(g.db), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="merchant_onboarding_cases.csv"'})


@bp.get("/policy")
def policy():
    evidence = load_evidence()
    return render_template("policy.html", cmp=policy_comparison(g.db), examples=example_results(g.db),
                           impact=impact(g.db), req_evidence=evidence_by_requirement(evidence), evidence=evidence,
                           key=new_key(), error=None, v=_viewer(None)), 200


@bp.get("/uat")
def uat():
    evidence = load_evidence()
    e2e = load_evidence("e2e_story.json")
    return render_template("uat.html", scenarios=_scenarios(), evidence=evidence, e2e=e2e,
                           by_ac=evidence_by_scenario(evidence), by_req=evidence_by_requirement(evidence))


@bp.get("/timeline")
def timeline():
    return render_template("timeline.html", events=global_events(g.db))


@bp.get("/lab")
def lab():
    return render_template("lab.html", key=new_key(), error=None)


# ----------------------------------------------------------------------------- lab controls
@bp.post("/lab/actor")
def set_actor():
    actor_id = request.form.get("actor_id", "")
    if actor_id not in actor_map(g.db):
        raise DomainError("VALIDATION_FAILED", "Unknown synthetic actor.", 422)
    session["actor_id"] = actor_id
    flash(f"Now acting as {actor_map(g.db)[actor_id]['display_name']} (synthetic actor).", "info")
    return redirect(_safe_next())


def _safe_next() -> str:
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("ui.home")


@bp.post("/lab/clock")
def set_clock():
    try:
        result = WorkflowService(g.db).set_business_date(_actor_id(), _form_payload(drop=("next",)))
    except DomainError as exc:
        return render_template("lab.html", key=new_key(), error=exc), exc.http_status
    if result.body.get("changed"):
        flash(f"Lab business date is now {result.body['business_date']} "
              f"(effective policy {result.body.get('effective_policy')}).", "success")
    else:
        flash("Lab business date unchanged.", "info")
    return redirect(_safe_next())


# ----------------------------------------------------------------------------- case commands
@bp.post("/cases")
def create_case():
    svc = WorkflowService(g.db)
    try:
        result = svc.create_application(_actor_id(), _form_payload())
    except DomainError as exc:
        return render_template("new_case.html", key=request.form.get("request_key") or new_key(),
                               form=request.form, error=exc), exc.http_status
    flash(f"Created {result.body['reference']} as a draft.", "success")
    return redirect(url_for("ui.case", reference=result.body["reference"]), 303)


def _case_command(reference, call, success_message):
    try:
        result = call()
    except DomainError as exc:
        if exc.code == "NOT_FOUND" and application_detail(g.db, reference) is None:
            raise
        return _render_case(reference, error=exc, status=exc.http_status, form=request.form)
    msg = success_message(result.body)
    if result.replayed:
        msg += " (duplicate request detected — original result shown, nothing changed)"
    flash(msg, "success")
    return redirect(url_for("ui.case", reference=reference), 303)


@bp.post("/cases/<reference>/draft")
def update_draft(reference):
    return _case_command(reference, lambda: WorkflowService(g.db).update_draft(_actor_id(), reference, _form_payload()),
                         lambda b: "Draft saved." if b.get("changed") else "No draft changes.")


@bp.post("/cases/<reference>/documents")
def add_document(reference):
    return _case_command(reference, lambda: WorkflowService(g.db).add_document(_actor_id(), reference, _form_payload()),
                         lambda b: "Document metadata added to the draft.")


@bp.post("/cases/<reference>/documents/<int:document_id>/remove")
def remove_document(reference, document_id):
    return _case_command(reference, lambda: WorkflowService(g.db).remove_document(
        _actor_id(), reference, document_id, _form_payload()), lambda b: "Document removed from the draft.")


@bp.post("/cases/<reference>/submit")
def submit(reference):
    return _case_command(reference, lambda: WorkflowService(g.db).submit(_actor_id(), reference, _form_payload()),
                         lambda b: f"Submitted revision {b['revision_no']} — evaluated under policy {b['policy_version']}.")


@bp.post("/cases/<reference>/request-information")
def request_information(reference):
    return _case_command(reference, lambda: WorkflowService(g.db).request_information(
        _actor_id(), reference, _form_payload()), lambda b: "Information requested; case is now waiting on the specialist.")


@bp.post("/cases/<reference>/decision")
def decide(reference):
    return _case_command(reference, lambda: WorkflowService(g.db).decide(_actor_id(), reference, _form_payload()),
                         lambda b: f"Decision recorded: {b['status']} (revision {b['revision_no']}, policy {b['policy_version']}).")


@bp.post("/cases/<reference>/reopen")
def reopen(reference):
    return _case_command(reference, lambda: WorkflowService(g.db).reopen(_actor_id(), reference, _form_payload()),
                         lambda b: f"Reopened as cycle {b['cycle']}; the original rejection is preserved.")


@bp.post("/policy/migrations")
def run_migration():
    svc = WorkflowService(g.db)
    try:
        if request.form.get("confirm") != "yes":
            raise DomainError("VALIDATION_FAILED", "Tick the confirmation box to apply the policy.", 422)
        result = svc.run_migration(_actor_id(), _form_payload(drop=("confirm",)))
    except DomainError as exc:
        evidence = load_evidence()
        return render_template("policy.html", cmp=policy_comparison(g.db), examples=example_results(g.db),
                               impact=impact(g.db), req_evidence=evidence_by_requirement(evidence), evidence=evidence,
                               key=new_key(), error=exc, v=_viewer(None)), exc.http_status
    c = result.body["counts"]
    flash(f"Applied {result.body['target_version']} (run {result.body['run_id']}): "
          + ", ".join(f"{k} × {n}" for k, n in sorted(c.items())), "success")
    return redirect(url_for("ui.policy") + "#impact", 303)
