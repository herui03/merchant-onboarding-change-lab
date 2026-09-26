"""JSON API. Same service calls as the UI, so role, self-review, binding and idempotency rules are
identical whichever surface is used. See docs/04_data_api_contract.md."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request, session

from ..errors import DomainError
from ..queries import actor_map, application_detail, impact, lab_state, list_applications
from ..service import WorkflowService
from . import security

bp = Blueprint("api", __name__, url_prefix="/api")


def _body() -> dict:
    if not request.is_json:
        raise DomainError("UNSUPPORTED_MEDIA_TYPE", "Send Content-Type: application/json.", 415)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise DomainError("VALIDATION_FAILED", "Request body must be a JSON object.", 422)
    return data


def _actor() -> str:
    return session.get("actor_id", "")


def _reply(result):
    return jsonify(result.body), result.http_status


@bp.get("/session")
def get_session():
    actor = actor_map(g.db).get(_actor())
    state = lab_state(g.db)
    return jsonify({"csrf_token": security.csrf_token(), "actor": actor,
                    "business_date": state["business_date"], "effective_policy": state["effective_policy"]})


@bp.post("/session/actor")
def set_actor():
    data = _body()
    actor_id = data.get("actor_id")
    if set(data) != {"actor_id"} or actor_id not in actor_map(g.db):
        raise DomainError("VALIDATION_FAILED", "Send exactly {\"actor_id\": <synthetic actor id>}.", 422,
                          {"known_actors": sorted(actor_map(g.db))})
    session["actor_id"] = actor_id
    return jsonify({"actor": actor_map(g.db)[actor_id]})


@bp.get("/applications")
def applications():
    return jsonify({"applications": list_applications(
        g.db, status=request.args.get("status", ""), product=request.args.get("product", ""),
        policy=request.args.get("policy", ""))})


@bp.get("/applications/<reference>")
def application(reference):
    detail = application_detail(g.db, reference)
    if detail is None:
        raise DomainError("NOT_FOUND", f"No application {reference[:20]}.", 404)
    return jsonify(detail)


@bp.route("/applications/<reference>", methods=["PATCH", "PUT", "DELETE"])
@bp.route("/applications/<reference>/status", methods=["GET", "POST", "PATCH", "PUT"])
def no_status_bypass(reference):
    raise DomainError("METHOD_NOT_ALLOWED",
                      "There is no generic update. Use the named commands: submit, request-information, decisions, "
                      "reopen or policy migrations.", 405)


@bp.post("/applications")
def create():
    return _reply(WorkflowService(g.db).create_application(_actor(), _body()))


@bp.post("/applications/<reference>/draft")
def update_draft(reference):
    return _reply(WorkflowService(g.db).update_draft(_actor(), reference, _body()))


@bp.post("/applications/<reference>/documents")
def add_document(reference):
    return _reply(WorkflowService(g.db).add_document(_actor(), reference, _body()))


@bp.post("/applications/<reference>/documents/<int:document_id>/remove")
def remove_document(reference, document_id):
    return _reply(WorkflowService(g.db).remove_document(_actor(), reference, document_id, _body()))


@bp.post("/applications/<reference>/submit")
def submit(reference):
    return _reply(WorkflowService(g.db).submit(_actor(), reference, _body()))


@bp.post("/applications/<reference>/request-information")
def request_information(reference):
    return _reply(WorkflowService(g.db).request_information(_actor(), reference, _body()))


@bp.post("/applications/<reference>/decisions")
def decide(reference):
    return _reply(WorkflowService(g.db).decide(_actor(), reference, _body()))


@bp.post("/applications/<reference>/reopen")
def reopen(reference):
    return _reply(WorkflowService(g.db).reopen(_actor(), reference, _body()))


@bp.get("/policy/impact")
def policy_impact():
    data = impact(g.db)
    return jsonify({"target_version": data["target"].version, "preview": data["preview"], "runs": data["runs"]})


@bp.post("/policy/migrations")
def run_migration():
    return _reply(WorkflowService(g.db).run_migration(_actor(), _body()))


@bp.post("/lab/clock")
def set_clock():
    return _reply(WorkflowService(g.db).set_business_date(_actor(), _body()))
