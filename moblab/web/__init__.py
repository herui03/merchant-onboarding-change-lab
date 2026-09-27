"""Flask application factory. Binds to 127.0.0.1:5058 via `python -m moblab run`."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request, session
from markupsafe import Markup, escape
from werkzeug.exceptions import HTTPException

from .. import __version__
from ..db import connect, is_initialised
from ..errors import DomainError
from ..paths import INSTANCE_DIR
from ..policy_engine import DOC_TYPES, PRODUCT_CATEGORIES, VOLUME_BANDS
from ..queries import CLASSIFICATION_LABELS, REASON_LABELS, STATUS_LABELS, actor_map, lab_state
from ..service import ROLE_LABELS
from . import security

HOST = "127.0.0.1"
PORT = 5058
MAX_BODY_BYTES = 64 * 1024


def _load_secret(instance_dir: Path) -> str:
    """Per-install session secret, generated locally and never committed (instance/ is ignored)."""
    instance_dir.mkdir(parents=True, exist_ok=True)
    path = instance_dir / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        os.chmod(path, 0o600)
    return path.read_text(encoding="utf-8").strip()


def create_app(db_path: str | Path, *, secret_key: str | None = None, instance_dir: Path = INSTANCE_DIR) -> Flask:
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database {db_path} does not exist. Run `python -m moblab seed` first.")
    probe = connect(db_path, read_only=True)
    try:
        if not is_initialised(probe):
            raise RuntimeError(f"{db_path} is not a Merchant Onboarding Lab database")
    finally:
        probe.close()

    app = Flask(__name__)
    app.config.update(
        DB_PATH=str(db_path),
        SECRET_KEY=secret_key or _load_secret(instance_dir),
        MAX_CONTENT_LENGTH=MAX_BODY_BYTES,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_NAME="moblab_session",
        JSON_SORT_KEYS=False,
    )
    app.json.sort_keys = False
    security.install(app)

    @app.template_filter("wbr")
    def _wbr(value) -> Markup:
        """Escape, then allow line breaks after '_' and ';' in long codes (never mid-word)."""
        return Markup(str(escape("" if value is None else value)).replace("_", "_<wbr>").replace(";", ";<wbr>"))

    @app.template_filter("fromjson_counts")
    def _fromjson_counts(raw: str) -> str:
        import json
        counts = json.loads(raw).get("counts", {})
        return ", ".join(f"{k} × {n}" for k, n in sorted(counts.items()))

    @app.before_request
    def _open_db():
        # GET/HEAD get a query_only connection: a read-only page physically cannot write (REQ-NFR-05).
        g.db = connect(app.config["DB_PATH"], read_only=request.method in security.SAFE_METHODS)

    @app.teardown_request
    def _close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.context_processor
    def _globals():
        db = g.get("db")
        if db is None:
            return {}
        actors = actor_map(db)
        current = actors.get(session.get("actor_id", ""))
        return {
            "lab": lab_state(db),
            "actors": actors,
            "current_actor": current,
            "csrf_token": security.csrf_token,
            "labels": {"doc_types": DOC_TYPES, "products": PRODUCT_CATEGORIES, "bands": VOLUME_BANDS,
                       "status": STATUS_LABELS, "classification": CLASSIFICATION_LABELS,
                       "reasons": REASON_LABELS, "roles": ROLE_LABELS},
            "app_version": __version__,
        }

    def _wants_json() -> bool:
        return request.path.startswith("/api/") or request.path.endswith(".json")

    @app.errorhandler(DomainError)
    def _domain_error(exc: DomainError):
        if _wants_json():
            return jsonify(exc.as_dict()), exc.http_status
        return render_template("error.html", error=exc), exc.http_status

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE",
                415: "UNSUPPORTED_MEDIA_TYPE", 400: "BAD_REQUEST"}.get(exc.code, "HTTP_ERROR")
        message = {
            405: "Method not allowed. Status changes only happen through named commands (submit, request-information, "
                 "decisions, reopen, migrations); there is no generic update.",
            413: f"Request body larger than {MAX_BODY_BYTES // 1024} KB.",
        }.get(exc.code, exc.description)
        err = DomainError(code, message, exc.code or 500)
        if _wants_json():
            resp = jsonify(err.as_dict())
        else:
            resp = render_template("error.html", error=err)
        return resp, exc.code

    from .routes_api import bp as api_bp
    from .routes_ui import bp as ui_bp

    app.register_blueprint(ui_bp)
    app.register_blueprint(api_bp)
    return app
