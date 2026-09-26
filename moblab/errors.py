"""One error type for every refused command. `code` is stable and documented in the API contract."""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, message: str, http_status: int, details: dict[str, Any] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}
