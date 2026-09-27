"""Workflow commands — the only code path that changes business state.

Every command follows the same order (documented in docs/04_data_api_contract.md):
  1. validate the payload against its contract (422, before touching the database);
  2. BEGIN IMMEDIATE (one write transaction; any error rolls everything back);
  3. resolve actor → role check (403) → application (404) → self-review (403);
  4. idempotency: same key + same binding → replay stored result; other binding → 409;
  5. state and binding checks, then writes, then one timeline event per changed case.

Dates: the server lab clock (meta.business_date) is the only business-date authority.
No command accepts a caller-supplied business, submission, decision or effective date.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from . import contracts as C
from .db import transaction
from .errors import DomainError
from .policy_engine import Policy, effective_policy, evaluate, parse_iso_date, policy_from_mapping, sha256_json

ROLE_SPECIALIST = "onboarding_specialist"
ROLE_REVIEWER = "risk_reviewer"
ROLE_POLICY_OWNER = "policy_owner"
ROLE_AUDITOR = "auditor"
ROLE_LABELS = {
    ROLE_SPECIALIST: "Onboarding specialist",
    ROLE_REVIEWER: "Risk reviewer",
    ROLE_POLICY_OWNER: "Policy owner",
    ROLE_AUDITOR: "Auditor (read-only)",
}

EDITABLE = ("draft", "needs_information")
TERMINAL = ("approved", "rejected")

# Migration classifications (REQ-POL-04/05).
GRANDFATHERED_APPROVED = "GRANDFATHERED_APPROVED"
APPROVED_UNDER_TARGET = "APPROVED_UNDER_TARGET"
TERMINAL_REJECTED_UNCHANGED = "TERMINAL_REJECTED_UNCHANGED"
DRAFT_NOT_EVALUATED = "DRAFT_NOT_EVALUATED"
ALREADY_ON_TARGET = "ALREADY_ON_TARGET"
MIGRATED_NO_NEW_EVIDENCE = "MIGRATED_NO_NEW_EVIDENCE"
MIGRATED_EVIDENCE_REQUIRED = "MIGRATED_EVIDENCE_REQUIRED"
MIGRATED_PENDING_RESUBMISSION = "MIGRATED_PENDING_RESUBMISSION"
POLICY_CHANGE_REASON = "POLICY_CHANGE_EVIDENCE_REQUIRED"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class CommandResult:
    http_status: int
    body: dict[str, Any]
    replayed: bool = False


@dataclass
class _Ctx:
    actor: sqlite3.Row
    roles: frozenset[str]
    app: sqlite3.Row | None
    business_date: str
    data: dict[str, Any] = field(default_factory=dict)


def load_db_policies(conn: sqlite3.Connection) -> dict[str, Policy]:
    out = {}
    for row in conn.execute("SELECT * FROM policy_version ORDER BY effective_from"):
        out[row["version"]] = policy_from_mapping(
            {
                "version": row["version"],
                "title": row["title"],
                "effective_from": row["effective_from"],
                "change_request": row["change_request"],
                "summary": row["summary"],
                "rules": json.loads(row["rules_json"]),
            }
        )
    return out


def revision_content(app_reference: str, rev: Mapping[str, Any], docs: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The exact content covered by a revision's sha256 (REQ-INT-04, REQ-INT-05)."""
    return {
        "application_reference": app_reference,
        "revision_no": rev["revision_no"],
        "legal_name": rev["legal_name"],
        "business_identifier": rev["business_identifier"],
        "product_category": rev["product_category"],
        "volume_band": rev["volume_band"],
        "activity_summary": rev["activity_summary"],
        "response_note": rev["response_note"],
        "submitted_by": rev["submitted_by"],
        "submitted_on": rev["submitted_on"],
        "documents": [
            {k: d[k] for k in ("doc_type", "title", "issued_on", "page_count", "reference")} for d in docs
        ],
    }


def _comparable(content: Mapping[str, Any]) -> tuple:
    """Business content only, documents as a multiset — used for the identical-resubmission rule."""
    docs = sorted(tuple(d[k] for k in ("doc_type", "title", "issued_on", "page_count", "reference")) for d in content["documents"])
    return (
        content["legal_name"],
        content["product_category"],
        content["volume_band"],
        content["activity_summary"],
        tuple(docs),
    )


class WorkflowService:
    def __init__(self, conn: sqlite3.Connection, now: Callable[[], str] = utc_now):
        self.conn = conn
        self.now = now

    # ------------------------------------------------------------------ public commands
    def create_application(self, actor_id: str, payload: Any) -> CommandResult:
        return self._command(actor_id, "create_application", None, payload, C.CREATE_APPLICATION,
                             ROLE_SPECIALIST, self._do_create)

    def update_draft(self, actor_id: str, reference: str, payload: Any) -> CommandResult:
        return self._command(actor_id, "update_draft", reference, payload, C.UPDATE_DRAFT,
                             ROLE_SPECIALIST, self._do_update_draft)

    def add_document(self, actor_id: str, reference: str, payload: Any) -> CommandResult:
        return self._command(actor_id, "add_document", reference, payload, C.ADD_DOCUMENT,
                             ROLE_SPECIALIST, self._do_add_document)

    def remove_document(self, actor_id: str, reference: str, document_id: Any, payload: Any) -> CommandResult:
        if not C.is_valid_id(document_id):  # DEF-001 (Codex R3-01): unbounded ints overflowed SQLite
            raise DomainError("NOT_FOUND", "No such draft document.", 404)
        return self._command(actor_id, "remove_document", reference, payload, C.REMOVE_DOCUMENT,
                             ROLE_SPECIALIST, self._do_remove_document, extra={"document_id": document_id})

    def submit(self, actor_id: str, reference: str, payload: Any) -> CommandResult:
        return self._command(actor_id, "submit", reference, payload, C.SUBMIT, ROLE_SPECIALIST, self._do_submit)

    def request_information(self, actor_id: str, reference: str, payload: Any) -> CommandResult:
        return self._command(actor_id, "request_information", reference, payload, C.REQUEST_INFORMATION,
                             ROLE_REVIEWER, self._do_request_information, self_review=True)

    def decide(self, actor_id: str, reference: str, payload: Any) -> CommandResult:
        return self._command(actor_id, "decide", reference, payload, C.DECIDE, ROLE_REVIEWER,
                             self._do_decide, self_review=True)

    def reopen(self, actor_id: str, reference: str, payload: Any) -> CommandResult:
        return self._command(actor_id, "reopen", reference, payload, C.REOPEN, ROLE_REVIEWER,
                             self._do_reopen, self_review=True)

    def run_migration(self, actor_id: str, payload: Any) -> CommandResult:
        return self._command(actor_id, "run_migration", None, payload, C.RUN_MIGRATION, ROLE_POLICY_OWNER,
                             self._do_run_migration)

    def set_business_date(self, actor_id: str, payload: Any) -> CommandResult:
        """Lab simulation control (not a business permission). Forward-only; auditor excluded."""
        clean = C.validate(payload, C.SET_BUSINESS_DATE)
        with transaction(self.conn):
            actor, roles = self._actor(actor_id)
            if not roles - {ROLE_AUDITOR}:
                raise DomainError("FORBIDDEN_ROLE", "The auditor is read-only and cannot move the lab clock.", 403)
            current = self.business_date()
            new = clean["business_date"]
            if new < current:
                raise DomainError("CLOCK_NOT_FORWARD", f"The lab clock only moves forward (currently {current}).", 422,
                                  {"current": current, "requested": new})
            if new == current:
                return CommandResult(200, {"business_date": current, "changed": False})
            policies = load_db_policies(self.conn)
            before = effective_policy(policies.values(), parse_iso_date(current))
            after = effective_policy(policies.values(), parse_iso_date(new))
            self.conn.execute("UPDATE meta SET value = ? WHERE key = 'business_date'", (new,))
            self._event(None, None, "CLOCK_ADVANCED", actor["id"], new, message=f"Lab clock {current} → {new}",
                        details={"from": current, "to": new,
                                 "effective_policy_before": before.version if before else None,
                                 "effective_policy_after": after.version if after else None})
            return CommandResult(200, {"business_date": new, "changed": True,
                                       "effective_policy": after.version if after else None})

    # ------------------------------------------------------------------ read helpers
    def business_date(self) -> str:
        return self.conn.execute("SELECT value FROM meta WHERE key = 'business_date'").fetchone()[0]

    def migration_preview(self, target_version: str) -> dict[str, Any]:
        """Read-only: what a migration run would do right now (REQ-POL-07)."""
        policies = load_db_policies(self.conn)
        target = policies.get(target_version)
        if target is None:
            raise DomainError("NOT_FOUND", f"Unknown policy version {target_version!r}.", 404)
        bd = self.business_date()
        return {
            "target_version": target.version,
            "business_date": bd,
            "effective": target.effective_from.isoformat() <= bd,
            "items": self._migration_plan(target, policies),
        }

    # ------------------------------------------------------------------ command skeleton
    def _command(self, actor_id, operation, reference, payload, contract, role, body, *, self_review=False, extra=None):
        clean = C.validate(payload, contract)
        if reference is not None:
            C.check_reference(reference)
        with transaction(self.conn):
            actor, roles = self._actor(actor_id)
            if role not in roles:
                raise DomainError("FORBIDDEN_ROLE", f"This action needs the role '{ROLE_LABELS[role]}'.", 403,
                                  {"required_role": role, "actor_roles": sorted(roles)})
            app = self._app(reference) if reference is not None else None
            if self_review:
                self._not_self_review(actor, app)
            key = clean.pop("request_key")
            if extra:
                clean.update(extra)
            binding = {
                "actor_id": actor["id"],
                "operation": operation,
                "application_ref": reference or "",
                "revision_id": clean.get("revision_id"),
                "policy_version": clean.get("policy_version") or clean.get("target_version") or "",
                "evaluation_id": clean.get("evaluation_id"),
                "payload_sha256": sha256_json(clean),
            }
            replay = self._replay_or_conflict(key, binding)
            if replay is not None:
                return replay
            ctx = _Ctx(actor=actor, roles=roles, app=app, business_date=self.business_date())
            status, result = body(ctx, clean)
            result = {**result, "replayed": False}
            self.conn.execute(
                "INSERT INTO idempotency_record(request_key, actor_id, operation, application_ref, revision_id,"
                " policy_version, evaluation_id, payload_sha256, binding_sha256, http_status, response_json, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, binding["actor_id"], operation, binding["application_ref"], binding["revision_id"],
                 binding["policy_version"], binding["evaluation_id"], binding["payload_sha256"],
                 sha256_json(binding), status, json.dumps(result, sort_keys=True), self.now()),
            )
            return CommandResult(status, result)

    def _replay_or_conflict(self, key: str, binding: dict[str, Any]) -> CommandResult | None:
        row = self.conn.execute("SELECT * FROM idempotency_record WHERE request_key = ?", (key,)).fetchone()
        if row is None:
            return None
        if row["binding_sha256"] == sha256_json(binding):
            stored = json.loads(row["response_json"])
            return CommandResult(200, {**stored, "replayed": True}, replayed=True)
        differs = [
            name
            for name, column in (
                ("actor", "actor_id"), ("operation", "operation"), ("application", "application_ref"),
                ("revision", "revision_id"), ("policy_version", "policy_version"),
                ("evaluation", "evaluation_id"), ("payload", "payload_sha256"),
            )
            if row[column] != binding[column]
        ]
        raise DomainError(
            "IDEMPOTENCY_KEY_CONFLICT",
            "This request key was already used for a different request. Use a new key for a new request.",
            409,
            {"differs_in": differs},
        )

    # ------------------------------------------------------------------ lookups and guards
    def _actor(self, actor_id: str) -> tuple[sqlite3.Row, frozenset[str]]:
        row = self.conn.execute("SELECT * FROM actor WHERE id = ?", (actor_id,)).fetchone() if isinstance(actor_id, str) else None
        if row is None:
            raise DomainError("UNKNOWN_ACTOR", "Choose a synthetic actor first.", 403)
        return row, frozenset(json.loads(row["roles_json"]))

    def _app(self, reference: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM application WHERE reference = ?", (reference,)).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", f"No application {reference}.", 404)
        return row

    def _not_self_review(self, actor: sqlite3.Row, app: sqlite3.Row) -> None:
        involved = app["created_by"] == actor["id"] or self.conn.execute(
            "SELECT 1 FROM application_revision WHERE application_id = ? AND submitted_by = ? LIMIT 1",
            (app["id"], actor["id"]),
        ).fetchone()
        if involved:
            raise DomainError(
                "SELF_REVIEW_FORBIDDEN",
                f"{actor['display_name']} created or submitted {app['reference']} and cannot review it.",
                403,
            )

    @staticmethod
    def _require_status(app: sqlite3.Row, allowed: tuple[str, ...], action: str) -> None:
        if app["status"] in allowed:
            return
        if app["status"] in TERMINAL:
            raise DomainError("ALREADY_DECIDED",
                              f"{app['reference']} is already {app['status']}; its decision cannot change.", 409,
                              {"status": app["status"]})
        raise DomainError("INVALID_TRANSITION", f"Cannot {action} while {app['reference']} is {app['status']}.", 409,
                          {"status": app["status"], "allowed_from": list(allowed)})

    def _set_status(self, app: sqlite3.Row, new_status: str, **columns: Any) -> None:
        """Compare-and-set on row_version: a second writer can never overwrite the first."""
        sets = ["status = ?", "row_version = row_version + 1"] + [f"{c} = ?" for c in columns]
        cur = self.conn.execute(
            f"UPDATE application SET {', '.join(sets)} WHERE id = ? AND row_version = ?",
            [new_status, *columns.values(), app["id"], app["row_version"]],
        )
        if cur.rowcount != 1:
            raise DomainError("CONCURRENT_MODIFICATION", "The application changed; reload and try again.", 409)

    def _bound_revision(self, app: sqlite3.Row, revision_id: int) -> sqlite3.Row:
        rev = self.conn.execute("SELECT * FROM application_revision WHERE id = ?", (revision_id,)).fetchone()
        if rev is None or rev["application_id"] != app["id"]:
            raise DomainError("REVISION_APPLICATION_MISMATCH",
                              f"Revision id {revision_id} is not a revision of {app['reference']}.", 422)
        if rev["id"] != app["current_revision_id"]:
            current = self.conn.execute("SELECT revision_no FROM application_revision WHERE id = ?",
                                        (app["current_revision_id"],)).fetchone()
            raise DomainError(
                "STALE_REVISION",
                f"You inspected revision {rev['revision_no']}, but {app['reference']} is now at revision "
                f"{current['revision_no']}. Reload and review the current revision.",
                409,
                {"inspected_revision_id": rev["id"], "current_revision_id": app["current_revision_id"]},
            )
        return rev

    def _latest_evaluation(self, revision_id: int) -> sqlite3.Row:
        return self.conn.execute(
            "SELECT * FROM policy_evaluation WHERE revision_id = ? ORDER BY id DESC LIMIT 1", (revision_id,)
        ).fetchone()

    def _revision_docs(self, revision_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM revision_document WHERE revision_id = ? ORDER BY position", (revision_id,)
        ).fetchall()

    def _event(self, app_id, revision_id, event_type, actor_id, business_date, *, reason_code="", message="", details=None):
        self.conn.execute(
            "INSERT INTO event(application_id, revision_id, event_type, actor_id, business_date, recorded_at,"
            " reason_code, message, details_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (app_id, revision_id, event_type, actor_id, business_date, self.now(), reason_code, message,
             json.dumps(details or {}, sort_keys=True)),
        )

    # ------------------------------------------------------------------ command bodies
    def _do_create(self, ctx: _Ctx, d: dict[str, Any]):
        if self.conn.execute("SELECT 1 FROM application WHERE business_identifier = ?",
                             (d["business_identifier"],)).fetchone():
            raise DomainError("DUPLICATE_BUSINESS_IDENTIFIER",
                              f"An application for {d['business_identifier']} already exists.", 409)
        next_id = self.conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM application").fetchone()[0]
        if next_id > 9999:
            raise DomainError("LAB_CAPACITY_REACHED", "The lab holds at most 9999 applications.", 409)
        reference = f"MOB-{next_id:04d}"
        self.conn.execute(
            "INSERT INTO application(id, reference, business_identifier, status, created_by, created_on, created_at,"
            " draft_legal_name, draft_product_category, draft_volume_band, draft_activity_summary)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (next_id, reference, d["business_identifier"], "draft", ctx.actor["id"], ctx.business_date, self.now(),
             d["legal_name"], d["product_category"], d["volume_band"], d["activity_summary"]),
        )
        self._event(next_id, None, "CREATED", ctx.actor["id"], ctx.business_date,
                    message=f"Draft created for {d['legal_name']}",
                    details={k: d[k] for k in ("legal_name", "business_identifier", "product_category", "volume_band")})
        return 201, {"reference": reference, "status": "draft"}

    def _do_update_draft(self, ctx: _Ctx, d: dict[str, Any]):
        app = ctx.app
        self._require_status(app, EDITABLE, "edit the draft")
        mapping = {"legal_name": "draft_legal_name", "product_category": "draft_product_category",
                   "volume_band": "draft_volume_band", "activity_summary": "draft_activity_summary"}
        provided = {k: v for k, v in d.items() if k in mapping and v is not None}
        if not provided:
            raise DomainError("VALIDATION_FAILED", "Provide at least one draft field to change.", 422)
        changes = {k: {"from": app[mapping[k]], "to": v} for k, v in provided.items() if app[mapping[k]] != v}
        if changes:
            cur = self.conn.execute(
                f"UPDATE application SET {', '.join(f'{mapping[k]} = ?' for k in changes)}, row_version = row_version + 1"
                " WHERE id = ? AND row_version = ?",
                [v["to"] for v in changes.values()] + [app["id"], app["row_version"]],
            )
            if cur.rowcount != 1:
                raise DomainError("CONCURRENT_MODIFICATION", "The application changed; reload and try again.", 409)
            self._event(app["id"], None, "DRAFT_UPDATED", ctx.actor["id"], ctx.business_date,
                        message="Draft fields changed: " + ", ".join(changes), details=changes)
        return 200, {"reference": app["reference"], "changed": sorted(changes)}

    def _do_add_document(self, ctx: _Ctx, d: dict[str, Any]):
        app = ctx.app
        self._require_status(app, EDITABLE, "change draft evidence")
        count = self.conn.execute("SELECT COUNT(*) FROM draft_document WHERE application_id = ?", (app["id"],)).fetchone()[0]
        if count >= C.MAX_DOCUMENTS:
            raise DomainError("DOCUMENT_LIMIT_REACHED", f"A draft holds at most {C.MAX_DOCUMENTS} documents.", 422)
        if d["issued_on"] > ctx.business_date:
            raise DomainError("FUTURE_DATED_DOCUMENT",
                              f"Issued {d['issued_on']} is after the lab business date {ctx.business_date}.", 422,
                              {"fields": {"issued_on": "Cannot be after the business date."}})
        if self.conn.execute("SELECT 1 FROM draft_document WHERE application_id = ? AND reference = ?",
                             (app["id"], d["reference"])).fetchone():
            raise DomainError("VALIDATION_FAILED", "Some fields are invalid.", 422,
                              {"fields": {"reference": "Already used in this draft."}})
        cur = self.conn.execute(
            "INSERT INTO draft_document(application_id, doc_type, title, issued_on, page_count, reference, added_by, added_on)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (app["id"], d["doc_type"], d["title"], d["issued_on"], d["page_count"], d["reference"],
             ctx.actor["id"], ctx.business_date),
        )
        self._event(app["id"], None, "DOCUMENT_ADDED", ctx.actor["id"], ctx.business_date,
                    message=f"{d['doc_type']} {d['reference']} (issued {d['issued_on']})",
                    details={k: d[k] for k in ("doc_type", "title", "issued_on", "page_count", "reference")})
        return 201, {"reference": app["reference"], "document_id": cur.lastrowid}

    def _do_remove_document(self, ctx: _Ctx, d: dict[str, Any]):
        app = ctx.app
        doc = self.conn.execute("SELECT * FROM draft_document WHERE id = ? AND application_id = ?",
                                (d["document_id"], app["id"])).fetchone()
        if doc is None:
            raise DomainError("NOT_FOUND", f"No draft document {d['document_id']} on {app['reference']}.", 404)
        self._require_status(app, EDITABLE, "change draft evidence")
        self.conn.execute("DELETE FROM draft_document WHERE id = ?", (doc["id"],))
        self._event(app["id"], None, "DOCUMENT_REMOVED", ctx.actor["id"], ctx.business_date,
                    message=f"{doc['doc_type']} {doc['reference']} removed",
                    details={"document_id": doc["id"], "doc_type": doc["doc_type"], "reference": doc["reference"]})
        return 200, {"reference": app["reference"], "removed_document_id": doc["id"]}

    def _do_submit(self, ctx: _Ctx, d: dict[str, Any]):
        app = ctx.app
        self._require_status(app, EDITABLE, "submit")
        bd = ctx.business_date
        policies = load_db_policies(self.conn)
        policy = effective_policy(policies.values(), parse_iso_date(bd))
        if policy is None:
            raise DomainError("NO_EFFECTIVE_POLICY", f"No policy version is effective on {bd}.", 409)
        prev = self.conn.execute(
            "SELECT * FROM application_revision WHERE application_id = ? ORDER BY revision_no DESC LIMIT 1", (app["id"],)
        ).fetchone()
        note = d["response_note"]
        if prev is not None and len(note) < 10:
            raise DomainError("VALIDATION_FAILED", "Some fields are invalid.", 422,
                              {"fields": {"response_note": "Required when resubmitting (10–1000 characters): say what changed."}})
        if prev is None and note:
            raise DomainError("VALIDATION_FAILED", "Some fields are invalid.", 422,
                              {"fields": {"response_note": "Only used when resubmitting."}})
        docs = [dict(r) for r in self.conn.execute(
            "SELECT * FROM draft_document WHERE application_id = ? ORDER BY id", (app["id"],))]
        future = [doc["reference"] for doc in docs if doc["issued_on"] > bd]
        if future:
            raise DomainError("FUTURE_DATED_DOCUMENT", "Documents cannot be issued after the submission date.", 422,
                              {"documents": future})
        rev = {
            "revision_no": (prev["revision_no"] + 1) if prev else 1,
            "legal_name": app["draft_legal_name"],
            "business_identifier": app["business_identifier"],
            "product_category": app["draft_product_category"],
            "volume_band": app["draft_volume_band"],
            "activity_summary": app["draft_activity_summary"],
            "response_note": note,
            "submitted_by": ctx.actor["id"],
            "submitted_on": bd,
        }
        content = revision_content(app["reference"], rev, docs)
        if prev is not None:
            prev_content = revision_content(app["reference"], prev, [dict(r) for r in self._revision_docs(prev["id"])])
            if _comparable(prev_content) == _comparable(content):
                raise DomainError("NO_CHANGES_SINCE_LAST_REVISION",
                                  f"Nothing changed since revision {prev['revision_no']}. Change the draft before resubmitting.",
                                  422)
        content_sha = sha256_json(content)
        cur = self.conn.execute(
            "INSERT INTO application_revision(application_id, revision_no, cycle, legal_name, business_identifier,"
            " product_category, volume_band, activity_summary, response_note, submitted_by, submitted_on, submitted_at,"
            " content_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (app["id"], rev["revision_no"], app["cycle"], rev["legal_name"], rev["business_identifier"],
             rev["product_category"], rev["volume_band"], rev["activity_summary"], note, ctx.actor["id"], bd,
             self.now(), content_sha),
        )
        revision_id = cur.lastrowid
        for position, doc in enumerate(docs, start=1):
            self.conn.execute(
                "INSERT INTO revision_document(revision_id, position, doc_type, title, issued_on, page_count, reference)"
                " VALUES (?,?,?,?,?,?,?)",
                (revision_id, position, doc["doc_type"], doc["title"], doc["issued_on"], doc["page_count"], doc["reference"]),
            )
        result = evaluate(policy, rev["product_category"], rev["volume_band"], docs, parse_iso_date(bd))
        evaluation_id = self._insert_evaluation(revision_id, policy.version, "submission", None, bd, bd, content_sha, result)
        self._set_status(app, "submitted", current_revision_id=revision_id)
        self._event(app["id"], revision_id, "RESUBMITTED" if prev else "SUBMITTED", ctx.actor["id"], bd,
                    message=note or f"Revision {rev['revision_no']} submitted",
                    details={"revision_no": rev["revision_no"], "policy_version": policy.version,
                             "complete": result["complete"], "unmet": result["unmet"]})
        return 201, {"reference": app["reference"], "status": "submitted", "revision_id": revision_id,
                     "revision_no": rev["revision_no"], "policy_version": policy.version,
                     "evaluation_id": evaluation_id, "complete": result["complete"], "unmet": result["unmet"]}

    def _insert_evaluation(self, revision_id, version, trigger, run_id, reference_date, evaluated_on, content_sha, result):
        cur = self.conn.execute(
            "INSERT INTO policy_evaluation(revision_id, policy_version, trigger, migration_run_id, reference_date,"
            " evaluated_on, evaluated_at, revision_content_sha256, complete, result_json, result_sha256)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (revision_id, version, trigger, run_id, reference_date, evaluated_on, self.now(), content_sha,
             1 if result["complete"] else 0, json.dumps(result, sort_keys=True), sha256_json(result)),
        )
        return cur.lastrowid

    def _do_request_information(self, ctx: _Ctx, d: dict[str, Any]):
        app = ctx.app
        self._require_status(app, ("submitted",), "request information")
        rev = self._bound_revision(app, d["revision_id"])
        cur = self.conn.execute(
            "INSERT INTO info_request(application_id, revision_id, source, requested_by, reason_code, message,"
            " requested_doc_types_json, requested_on, requested_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (app["id"], rev["id"], "reviewer", ctx.actor["id"], d["reason_code"], d["message"],
             json.dumps(d["requested_doc_types"]), ctx.business_date, self.now()),
        )
        self._set_status(app, "needs_information")
        self._event(app["id"], rev["id"], "INFORMATION_REQUESTED", ctx.actor["id"], ctx.business_date,
                    reason_code=d["reason_code"], message=d["message"],
                    details={"requested_doc_types": d["requested_doc_types"], "revision_no": rev["revision_no"]})
        return 201, {"reference": app["reference"], "status": "needs_information", "info_request_id": cur.lastrowid}

    def _do_decide(self, ctx: _Ctx, d: dict[str, Any]):
        app = ctx.app
        outcome = C.OUTCOMES[d["outcome"]]
        codes = C.APPROVE_REASON_CODES if outcome == "approved" else C.REJECT_REASON_CODES
        if d["reason_code"] not in codes:
            raise DomainError("VALIDATION_FAILED", "Some fields are invalid.", 422,
                              {"fields": {"reason_code": f"Use one of: {', '.join(sorted(codes))}."}})
        if outcome == "rejected" and len(d["reason_text"]) < 20:
            raise DomainError("VALIDATION_FAILED", "Some fields are invalid.", 422,
                              {"fields": {"reason_text": "A rejection needs at least 20 characters of reasoning."}})
        self._require_status(app, ("submitted",), "decide")
        rev = self._bound_revision(app, d["revision_id"])
        evaluation = self._latest_evaluation(rev["id"])
        policies = load_db_policies(self.conn)
        effective = effective_policy(policies.values(), parse_iso_date(ctx.business_date))
        if effective is None:
            raise DomainError("NO_EFFECTIVE_POLICY", f"No policy version is effective on {ctx.business_date}.", 409)
        if evaluation["policy_version"] != effective.version:
            raise DomainError(
                "POLICY_MIGRATION_REQUIRED",
                f"Revision {rev['revision_no']} was evaluated under {evaluation['policy_version']}, but "
                f"{effective.version} is effective since {effective.effective_from.isoformat()}. The policy owner must "
                f"apply {effective.version} before any decision.",
                409,
                {"evaluated_under": evaluation["policy_version"], "effective": effective.version},
            )
        if d["policy_version"] != evaluation["policy_version"] or d["evaluation_id"] != evaluation["id"]:
            raise DomainError(
                "STALE_POLICY_EVALUATION",
                f"You inspected evaluation {d['evaluation_id']} under {d['policy_version']}, but the current evaluation "
                f"of revision {rev['revision_no']} is {evaluation['id']} under {evaluation['policy_version']}. Reload.",
                409,
                {"inspected": {"evaluation_id": d["evaluation_id"], "policy_version": d["policy_version"]},
                 "current": {"evaluation_id": evaluation["id"], "policy_version": evaluation["policy_version"]}},
            )
        docs = [dict(r) for r in self._revision_docs(rev["id"])]
        content = revision_content(app["reference"], rev, docs)
        content_sha = sha256_json(content)
        if content_sha != rev["content_sha256"] or content_sha != evaluation["revision_content_sha256"]:
            raise DomainError("REVISION_INTEGRITY_FAILURE",
                              "The stored revision no longer matches what was evaluated. No decision was recorded.", 409)
        result = json.loads(evaluation["result_json"])
        if outcome == "approved" and not evaluation["complete"]:
            raise DomainError("EVIDENCE_INCOMPLETE",
                              "Approval needs every applicable policy rule satisfied. Request information instead.",
                              422, {"unmet": result["unmet"]})
        policy_row = self.conn.execute("SELECT * FROM policy_version WHERE version = ?",
                                       (evaluation["policy_version"],)).fetchone()
        snapshot = {
            "revision": {"id": rev["id"], "content": content, "content_sha256": rev["content_sha256"]},
            "policy": {"version": policy_row["version"], "effective_from": policy_row["effective_from"],
                       "rules": json.loads(policy_row["rules_json"]), "rules_sha256": policy_row["rules_sha256"]},
            "evaluation": {"id": evaluation["id"], "trigger": evaluation["trigger"], "result": result,
                           "result_sha256": evaluation["result_sha256"]},
            "decision": {"outcome": outcome, "reason_code": d["reason_code"], "reason_text": d["reason_text"],
                         "decided_by": ctx.actor["id"], "decided_on": ctx.business_date},
        }
        snapshot_sha = sha256_json(snapshot)
        cur = self.conn.execute(
            "INSERT INTO decision(application_id, revision_id, evaluation_id, policy_version, outcome, reason_code,"
            " reason_text, decided_by, decided_on, decided_at, snapshot_json, snapshot_sha256)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (app["id"], rev["id"], evaluation["id"], evaluation["policy_version"], outcome, d["reason_code"],
             d["reason_text"], ctx.actor["id"], ctx.business_date, self.now(),
             json.dumps(snapshot, sort_keys=True), snapshot_sha),
        )
        self._set_status(app, outcome)
        self._event(app["id"], rev["id"], outcome.upper(), ctx.actor["id"], ctx.business_date,
                    reason_code=d["reason_code"], message=d["reason_text"],
                    details={"decision_id": cur.lastrowid, "revision_no": rev["revision_no"],
                             "policy_version": evaluation["policy_version"], "evaluation_id": evaluation["id"],
                             "snapshot_sha256": snapshot_sha})
        return 201, {"reference": app["reference"], "status": outcome, "decision_id": cur.lastrowid,
                     "revision_id": rev["id"], "revision_no": rev["revision_no"],
                     "policy_version": evaluation["policy_version"], "evaluation_id": evaluation["id"],
                     "snapshot_sha256": snapshot_sha}

    def _do_reopen(self, ctx: _Ctx, d: dict[str, Any]):
        app = ctx.app
        if app["status"] == "approved":
            raise DomainError("INVALID_TRANSITION",
                              "Approved applications cannot be reopened (DEC-08); post-approval changes are out of scope.",
                              409, {"status": "approved"})
        if app["status"] != "rejected":
            raise DomainError("INVALID_TRANSITION", f"Only rejected applications can be reopened; {app['reference']} "
                              f"is {app['status']}.", 409, {"status": app["status"]})
        decision = self.conn.execute("SELECT id FROM decision WHERE revision_id = ?", (app["current_revision_id"],)).fetchone()
        self._set_status(app, "draft", cycle=app["cycle"] + 1)
        self._event(app["id"], app["current_revision_id"], "REOPENED", ctx.actor["id"], ctx.business_date,
                    reason_code="REOPEN", message=d["reason_text"],
                    details={"new_cycle": app["cycle"] + 1, "preserved_decision_id": decision["id"]})
        return 200, {"reference": app["reference"], "status": "draft", "cycle": app["cycle"] + 1,
                     "preserved_decision_id": decision["id"]}

    # ------------------------------------------------------------------ migration
    def _migration_plan(self, target: Policy, policies: dict[str, Policy]) -> list[dict[str, Any]]:
        plan = []
        for app in self.conn.execute("SELECT * FROM application ORDER BY id").fetchall():
            item: dict[str, Any] = {
                "application_id": app["id"], "reference": app["reference"], "legal_name": app["draft_legal_name"],
                "status_before": app["status"], "status_after": app["status"], "revision_id": app["current_revision_id"],
                "prior_policy_version": "", "new_gaps": [], "target_unmet": [], "evaluate": False, "result": None,
            }
            rev = None
            if app["current_revision_id"] is not None:
                rev = self.conn.execute("SELECT * FROM application_revision WHERE id = ?",
                                        (app["current_revision_id"],)).fetchone()
                item["product_category"], item["volume_band"] = rev["product_category"], rev["volume_band"]
                item["submitted_on"] = rev["submitted_on"]
            else:
                item["product_category"], item["volume_band"] = app["draft_product_category"], app["draft_volume_band"]
            if app["status"] == "draft":
                item["classification"] = DRAFT_NOT_EVALUATED
                plan.append(item)
                continue
            docs = [dict(r) for r in self._revision_docs(rev["id"])]
            # Age is measured as of the revision's ORIGINAL submission date (DEC-04, PE-24).
            target_result = evaluate(target, rev["product_category"], rev["volume_band"], docs,
                                     parse_iso_date(rev["submitted_on"]))
            item["target_unmet"] = target_result["unmet"]
            if app["status"] == "approved":
                decision = self.conn.execute("SELECT * FROM decision WHERE revision_id = ?", (rev["id"],)).fetchone()
                item["prior_policy_version"] = decision["policy_version"]
                item["classification"] = (APPROVED_UNDER_TARGET if decision["policy_version"] == target.version
                                          else GRANDFATHERED_APPROVED)
            elif app["status"] == "rejected":
                decision = self.conn.execute("SELECT * FROM decision WHERE revision_id = ?", (rev["id"],)).fetchone()
                item["prior_policy_version"] = decision["policy_version"]
                item["classification"] = TERMINAL_REJECTED_UNCHANGED
            else:
                latest = self._latest_evaluation(rev["id"])
                item["prior_policy_version"] = latest["policy_version"]
                if latest["policy_version"] == target.version:
                    item["classification"] = ALREADY_ON_TARGET
                    item["target_unmet"] = json.loads(latest["result_json"])["unmet"]
                else:
                    prior_unmet = {u["rule_id"] for u in json.loads(latest["result_json"])["unmet"]}
                    item["new_gaps"] = [u for u in target_result["unmet"] if u["rule_id"] not in prior_unmet]
                    item["evaluate"] = True
                    item["result"] = target_result
                    item["content_sha256"] = rev["content_sha256"]
                    if app["status"] == "submitted":
                        if item["new_gaps"]:
                            item["classification"] = MIGRATED_EVIDENCE_REQUIRED
                            item["status_after"] = "needs_information"
                        else:
                            item["classification"] = MIGRATED_NO_NEW_EVIDENCE
                    else:
                        item["classification"] = MIGRATED_PENDING_RESUBMISSION
            plan.append(item)
        return plan

    def _do_run_migration(self, ctx: _Ctx, d: dict[str, Any]):
        policies = load_db_policies(self.conn)
        target = policies.get(d["target_version"])
        if target is None:
            raise DomainError("VALIDATION_FAILED", "Some fields are invalid.", 422,
                              {"fields": {"target_version": f"Unknown policy version. Known: {', '.join(policies)}."}})
        bd = ctx.business_date
        if target.effective_from.isoformat() > bd:
            raise DomainError("POLICY_NOT_YET_EFFECTIVE",
                              f"{target.version} is effective from {target.effective_from.isoformat()}; "
                              f"the lab date is {bd}.", 409)
        effective = effective_policy(policies.values(), parse_iso_date(bd))
        if effective.version != target.version:
            raise DomainError("VALIDATION_FAILED", "Some fields are invalid.", 422,
                              {"fields": {"target_version": f"Only the currently effective version ({effective.version}) can be applied."}})
        plan = self._migration_plan(target, policies)
        counts: dict[str, int] = {}
        for item in plan:
            counts[item["classification"]] = counts.get(item["classification"], 0) + 1
        run_id = self.conn.execute(
            "INSERT INTO migration_run(target_version, run_by, run_on, run_at, summary_json) VALUES (?,?,?,?,?)",
            (target.version, ctx.actor["id"], bd, self.now(), json.dumps({"counts": counts}, sort_keys=True)),
        ).lastrowid
        for item in plan:
            evaluation_id = None
            if item["evaluate"]:
                evaluation_id = self._insert_evaluation(item["revision_id"], target.version, "migration", run_id,
                                                        item["submitted_on"], bd, item["content_sha256"], item["result"])
                app = self.conn.execute("SELECT * FROM application WHERE id = ?", (item["application_id"],)).fetchone()
                if item["new_gaps"]:
                    gap_types = [g["doc_type"] for g in item["new_gaps"]]
                    gap_desc = ", ".join(f"{g['doc_type']} ({g['rule_id']}, {g['status']})" for g in item["new_gaps"])
                    message = (
                        f"Policy {target.version} ({target.change_request or 'policy change'}) is effective from "
                        f"{target.effective_from.isoformat()}. Revision submitted {item['submitted_on']} was evaluated "
                        f"under {item['prior_policy_version']}; under {target.version} it lacks: {gap_desc}. Submit a new "
                        f"revision that includes it. Evidence age is measured as of the new revision's submission date."
                    )
                    self.conn.execute(
                        "INSERT INTO info_request(application_id, revision_id, source, requested_by, reason_code, message,"
                        " requested_doc_types_json, migration_run_id, requested_on, requested_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (app["id"], item["revision_id"], "policy_migration", ctx.actor["id"], POLICY_CHANGE_REASON,
                         message, json.dumps(gap_types), run_id, bd, self.now()),
                    )
                if item["status_after"] != item["status_before"]:
                    self._set_status(app, item["status_after"])
                self._event(app["id"], item["revision_id"], "POLICY_MIGRATED", ctx.actor["id"], bd,
                            reason_code=POLICY_CHANGE_REASON if item["new_gaps"] else "",
                            message=f"{item['prior_policy_version']} → {target.version}: {item['classification']}",
                            details={"run_id": run_id, "classification": item["classification"],
                                     "evaluation_id": evaluation_id, "new_gaps": item["new_gaps"],
                                     "status_before": item["status_before"], "status_after": item["status_after"]})
            self.conn.execute(
                "INSERT INTO migration_item(run_id, application_id, revision_id, classification, status_before,"
                " status_after, prior_policy_version, evaluation_id, new_gaps_json, target_unmet_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, item["application_id"], item["revision_id"], item["classification"], item["status_before"],
                 item["status_after"], item["prior_policy_version"], evaluation_id,
                 json.dumps(item["new_gaps"]), json.dumps(item["target_unmet"])),
            )
        self._event(None, None, "POLICY_MIGRATION_RUN", ctx.actor["id"], bd,
                    message=f"Applied {target.version} to in-flight applications (run {run_id})",
                    details={"run_id": run_id, "counts": counts})
        return 201, {"run_id": run_id, "target_version": target.version, "counts": counts,
                     "items": [{"reference": i["reference"], "classification": i["classification"],
                                "status_before": i["status_before"], "status_after": i["status_after"]} for i in plan]}
