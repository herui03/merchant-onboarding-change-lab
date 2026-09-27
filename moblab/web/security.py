"""Local-only request controls: Host allowlist (DNS-rebinding defence), Origin check and CSRF.

This is a localhost lab, not enterprise authentication. "Acting as" an actor is a session value
that anyone at the keyboard can change; role rules are enforced in the service layer.
"""
from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit

from flask import Flask, request, session

from ..errors import DomainError

ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def _hostname(value: str) -> str:
    try:
        return (urlsplit(f"//{value}").hostname or "").lower()
    except ValueError:
        return ""


def install(app: Flask) -> None:
    @app.before_request
    def _guard():
        if _hostname(request.host) not in ALLOWED_HOSTNAMES:
            raise DomainError("HOST_NOT_ALLOWED", "This lab only answers on 127.0.0.1 / localhost.", 400)
        if request.method in SAFE_METHODS:
            return None
        origin = request.headers.get("Origin")
        if origin and origin != "null":
            parts = urlsplit(origin)
            if (parts.hostname or "").lower() not in ALLOWED_HOSTNAMES or parts.netloc != request.host:
                raise DomainError("ORIGIN_NOT_ALLOWED", "Cross-origin requests are refused.", 403)
        elif origin == "null":
            raise DomainError("ORIGIN_NOT_ALLOWED", "Cross-origin requests are refused.", 403)
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf", "")
        if not expected or not sent or not hmac.compare_digest(str(sent), str(expected)):
            raise DomainError("CSRF_FAILED", "Missing or invalid CSRF token. Reload the page and try again.", 403)
        return None

    @app.after_request
    def _headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response
