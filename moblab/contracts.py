"""Server-side input contracts. Every command payload is validated here before any database
work, and the limits are documented in docs/04_data_api_contract.md.

Rules that apply to every command:
- unknown fields are rejected (so `status`, `business_date`, `submitted_on` … can never sneak in);
- strings are stripped; control characters are rejected (newlines allowed only in long text);
- integers must be real integers (booleans are rejected);
- `request_key` is required: 8–64 characters from [A-Za-z0-9_-].
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .errors import DomainError
from .policy_engine import DOC_TYPES, PRODUCT_CATEGORIES, VOLUME_BANDS, InvalidDate, parse_iso_date

REQUEST_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
BUSINESS_ID_RE = re.compile(r"^SYN-\d{6}$")
DOC_REFERENCE_RE = re.compile(r"^SYN-DOC-[A-Z0-9]{4,12}$")
REFERENCE_RE = re.compile(r"^MOB-\d{4}$")
LEGAL_NAME_EXTRA = set(" &'.,()-/")

MAX_DOCUMENTS = 12
MAX_REQUESTED_DOC_TYPES = 6
DATE_MIN = "2000-01-01"
DATE_MAX = "2099-12-31"

INFO_REASON_CODES = {
    "MISSING_EVIDENCE": "Required evidence is missing",
    "EVIDENCE_UNCLEAR": "Evidence is unclear or illegible",
    "DATA_MISMATCH": "Application data does not match the evidence",
    "OTHER": "Other (explain in the message)",
}
APPROVE_REASON_CODES = {
    "EVIDENCE_VERIFIED": "All required evidence reviewed and consistent",
}
REJECT_REASON_CODES = {
    "EVIDENCE_INCONSISTENT": "Evidence is inconsistent with the application",
    "OUTSIDE_RISK_APPETITE": "Business model outside the fictional risk appetite",
    "UNRESPONSIVE_APPLICANT": "Applicant did not provide requested information",
    "OTHER": "Other (explain in the reason)",
}
OUTCOMES = {"approve": "approved", "reject": "rejected"}


class FieldError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Field:
    check: Callable[[Any], Any]
    required: bool = True
    default: Any = None


def validate(payload: Any, fields: Mapping[str, Field]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise DomainError("VALIDATION_FAILED", "Request body must be a JSON object.", 422)
    errors: dict[str, str] = {}
    for name in payload:
        if name not in fields:
            errors[str(name)[:64]] = "Unknown field. This command does not accept it."
    clean: dict[str, Any] = {}
    for name, spec in fields.items():
        if name not in payload or payload[name] is None:
            if spec.required:
                errors[name] = "Required."
            else:
                clean[name] = list(spec.default) if isinstance(spec.default, list) else spec.default
            continue
        try:
            clean[name] = spec.check(payload[name])
        except FieldError as exc:
            errors[name] = exc.message
    if errors:
        raise DomainError("VALIDATION_FAILED", "Some fields are invalid.", 422, {"fields": errors})
    return clean


# ----------------------------------------------------------------------------- field checks
def text(min_len: int, max_len: int, *, multiline: bool = False) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if not isinstance(value, str):
            raise FieldError("Must be text.")
        value = value.replace("\r\n", "\n").strip()
        for ch in value:
            if ch == "\n" and multiline:
                continue
            if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
                raise FieldError("Contains control or invisible characters.")
        if not (min_len <= len(value) <= max_len):
            raise FieldError(f"Must be {min_len}–{max_len} characters.")
        return value

    return check


def legal_name(value: Any) -> str:
    value = text(2, 120)(value)
    if not value[0].isalnum():
        raise FieldError("Must start with a letter or digit.")
    for ch in value:
        if not (ch.isalnum() or ch in LEGAL_NAME_EXTRA):
            raise FieldError("Only letters, digits, spaces and & ' . , ( ) - / are allowed.")
    return value


def pattern(regex: re.Pattern[str], hint: str) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if not isinstance(value, str) or not regex.match(value.strip()):
            raise FieldError(hint)
        return value.strip()

    return check


def choice(options: Mapping[str, str] | set[str]) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if not isinstance(value, str) or value not in options:
            raise FieldError(f"Must be one of: {', '.join(sorted(options))}.")
        return value

    return check


def integer(lo: int, hi: int) -> Callable[[Any], int]:
    def check(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise FieldError("Must be a whole number.")
        if not (lo <= value <= hi):
            raise FieldError(f"Must be between {lo} and {hi}.")
        return value

    return check


def iso_date(value: Any) -> str:
    try:
        parsed = parse_iso_date(value.strip() if isinstance(value, str) else value)
    except InvalidDate:
        raise FieldError("Must be a real calendar date written YYYY-MM-DD.") from None
    iso = parsed.isoformat()
    if not (DATE_MIN <= iso <= DATE_MAX):
        raise FieldError(f"Must be between {DATE_MIN} and {DATE_MAX}.")
    return iso


def doc_type_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise FieldError("Must be a list of document types.")
    if len(value) > MAX_REQUESTED_DOC_TYPES:
        raise FieldError(f"At most {MAX_REQUESTED_DOC_TYPES} document types.")
    out = []
    for item in value:
        if not isinstance(item, str) or item not in DOC_TYPES:
            raise FieldError(f"Unknown document type: {str(item)[:40]!r}.")
        if item in out:
            raise FieldError(f"Duplicate document type: {item}.")
        out.append(item)
    return out


REQUEST_KEY = Field(pattern(REQUEST_KEY_RE, "8–64 characters: letters, digits, '-' or '_'."))
ID_MAX = 2**31 - 1  # every numeric id (revision, evaluation, document) is 1..ID_MAX
ID = integer(1, ID_MAX)


def is_valid_id(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and 1 <= value <= ID_MAX

# ----------------------------------------------------------------------------- command contracts
CREATE_APPLICATION = {
    "request_key": REQUEST_KEY,
    "legal_name": Field(legal_name),
    "business_identifier": Field(pattern(BUSINESS_ID_RE, "Synthetic identifier: SYN- followed by 6 digits.")),
    "product_category": Field(choice(PRODUCT_CATEGORIES)),
    "volume_band": Field(choice(VOLUME_BANDS)),
    "activity_summary": Field(text(0, 400, multiline=True), required=False, default=""),
}
UPDATE_DRAFT = {
    "request_key": REQUEST_KEY,
    "legal_name": Field(legal_name, required=False),
    "product_category": Field(choice(PRODUCT_CATEGORIES), required=False),
    "volume_band": Field(choice(VOLUME_BANDS), required=False),
    "activity_summary": Field(text(0, 400, multiline=True), required=False),
}
ADD_DOCUMENT = {
    "request_key": REQUEST_KEY,
    "doc_type": Field(choice(DOC_TYPES)),
    "title": Field(text(3, 120)),
    "issued_on": Field(iso_date),
    "page_count": Field(integer(1, 500)),
    "reference": Field(pattern(DOC_REFERENCE_RE, "Synthetic reference: SYN-DOC- followed by 4–12 capitals/digits.")),
}
REMOVE_DOCUMENT = {"request_key": REQUEST_KEY}
SUBMIT = {
    "request_key": REQUEST_KEY,
    "response_note": Field(text(0, 1000, multiline=True), required=False, default=""),
}
REQUEST_INFORMATION = {
    "request_key": REQUEST_KEY,
    "revision_id": Field(ID),
    "reason_code": Field(choice(INFO_REASON_CODES)),
    "message": Field(text(20, 1000, multiline=True)),
    "requested_doc_types": Field(doc_type_list, required=False, default=[]),
}
DECIDE = {
    "request_key": REQUEST_KEY,
    "outcome": Field(choice(OUTCOMES)),
    "revision_id": Field(ID),
    "policy_version": Field(pattern(re.compile(r"^v\d{1,3}$"), "Policy version label such as v2.")),
    "evaluation_id": Field(ID),
    "reason_code": Field(choice(set(APPROVE_REASON_CODES) | set(REJECT_REASON_CODES))),
    "reason_text": Field(text(10, 1000, multiline=True)),
}
REOPEN = {
    "request_key": REQUEST_KEY,
    "reason_text": Field(text(20, 1000, multiline=True)),
}
RUN_MIGRATION = {
    "request_key": REQUEST_KEY,
    "target_version": Field(pattern(re.compile(r"^v\d{1,3}$"), "Policy version label such as v2.")),
}
SET_BUSINESS_DATE = {"business_date": Field(iso_date)}


def check_reference(reference: str) -> str:
    """Case references look like MOB-0001; anything else is a 404, never a 500."""
    if not isinstance(reference, str) or not REFERENCE_RE.match(reference):
        raise DomainError("NOT_FOUND", "No application with that reference.", 404)
    return reference
