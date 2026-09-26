"""Pure, deterministic evaluation of a fictional evidence policy against one revision.

Nothing here touches the database or the clock. The same (revision content, reference date,
policy) always gives the same result (REQ-POL-03). The reference date is the revision's
original submission date, including during migration re-evaluation (DEC-04 / REQ-POL-08).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PRODUCT_CATEGORIES = {
    "CARD_PRESENT": "In-store card acceptance",
    "ECOMMERCE": "Online checkout, goods shipped",
    "SUBSCRIPTION": "Recurring subscription billing",
    "FUTURE_DELIVERY": "Pre-paid services delivered later",
}
VOLUME_BANDS = {
    "V1": "Under 50,000 / month",
    "V2": "50,000 – 249,999 / month",
    "V3": "250,000 – 999,999 / month",
    "V4": "1,000,000+ / month",
}
DOC_TYPES = {
    "REG_EXTRACT": "Business registration extract",
    "OWNERSHIP_DECL": "Beneficial ownership declaration",
    "BANK_CONFIRM": "Settlement bank account confirmation",
    "PROCESSING_HISTORY": "Prior processing history",
    "SERVICE_SCOPE": "Service-scope statement",
    "OTHER": "Other supporting document",
}

# Rule outcome statuses.
SATISFIED = "SATISFIED"
MISSING = "MISSING"
OUT_OF_WINDOW = "OUT_OF_WINDOW"
NOT_APPLICABLE = "NOT_APPLICABLE"


class InvalidDate(ValueError):
    """Raised for anything that is not a real, zero-padded ISO calendar date."""


def parse_iso_date(value: Any) -> date:
    if not isinstance(value, str) or not ISO_DATE_RE.match(value):
        raise InvalidDate(f"not an ISO date (YYYY-MM-DD): {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:  # e.g. 2026-02-30
        raise InvalidDate(f"not a real calendar date: {value!r}") from exc


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    doc_type: str
    rationale: str = ""
    product_categories: tuple[str, ...] = ()
    volume_bands: tuple[str, ...] = ()
    max_age_days: int | None = None

    def applies_to(self, product: str | None, band: str | None) -> bool:
        if self.product_categories and product not in self.product_categories:
            return False
        if self.volume_bands and band not in self.volume_bands:
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "doc_type": self.doc_type,
            "rationale": self.rationale,
            "product_categories": list(self.product_categories),
            "volume_bands": list(self.volume_bands),
            "max_age_days": self.max_age_days,
        }


@dataclass(frozen=True)
class Policy:
    version: str
    title: str
    effective_from: date
    rules: tuple[Rule, ...]
    change_request: str = ""
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "title": self.title,
            "effective_from": self.effective_from.isoformat(),
            "change_request": self.change_request,
            "summary": self.summary,
            "rules": [r.as_dict() for r in self.rules],
        }

    def rules_sha256(self) -> str:
        return sha256_json(self.as_dict())


def policy_from_mapping(data: Mapping[str, Any]) -> Policy:
    """Build a Policy from parsed TOML/JSON, validating the rule vocabulary strictly."""
    rules = []
    seen = set()
    for raw in data["rules"]:
        allowed = {"id", "name", "doc_type", "rationale", "product_categories", "volume_bands", "max_age_days"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown rule keys {sorted(unknown)} in {raw.get('id')}")
        if raw["id"] in seen:
            raise ValueError(f"duplicate rule id {raw['id']}")
        seen.add(raw["id"])
        if raw["doc_type"] not in DOC_TYPES or raw["doc_type"] == "OTHER":
            raise ValueError(f"rule {raw['id']} has unusable doc_type {raw['doc_type']}")
        for p in raw.get("product_categories", []):
            if p not in PRODUCT_CATEGORIES:
                raise ValueError(f"rule {raw['id']} unknown product {p}")
        for b in raw.get("volume_bands", []):
            if b not in VOLUME_BANDS:
                raise ValueError(f"rule {raw['id']} unknown band {b}")
        max_age = raw.get("max_age_days")
        if max_age is not None and (not isinstance(max_age, int) or max_age < 0):
            raise ValueError(f"rule {raw['id']} bad max_age_days")
        rules.append(
            Rule(
                id=raw["id"],
                name=raw["name"],
                doc_type=raw["doc_type"],
                rationale=raw.get("rationale", ""),
                product_categories=tuple(raw.get("product_categories", [])),
                volume_bands=tuple(raw.get("volume_bands", [])),
                max_age_days=max_age,
            )
        )
    rules.sort(key=lambda r: r.id)
    eff = data["effective_from"]
    return Policy(
        version=data["version"],
        title=data["title"],
        effective_from=eff if isinstance(eff, date) else parse_iso_date(eff),
        rules=tuple(rules),
        change_request=data.get("change_request", ""),
        summary=data.get("summary", ""),
    )


def effective_policy(policies: Iterable[Policy], on: date) -> Policy | None:
    """Latest policy whose effective_from <= on (REQ-POL-01). None before the first one."""
    candidates = [p for p in policies if p.effective_from <= on]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.effective_from)


def evaluate(
    policy: Policy,
    product: str | None,
    band: str | None,
    documents: Sequence[Mapping[str, Any]],
    reference_date: date,
) -> dict[str, Any]:
    """Evaluate one revision. `documents` need `doc_type` and `issued_on` (ISO string).

    Age of a document = (reference_date - issued_on) in days. A rule with max_age_days is
    satisfied by any document of its type with 0 <= age <= max_age_days (inclusive both ends).
    """
    outcomes = []
    required: list[str] = []
    unmet: list[dict[str, str]] = []
    for rule in policy.rules:
        if not rule.applies_to(product, band):
            outcomes.append({"rule_id": rule.id, "doc_type": rule.doc_type, "status": NOT_APPLICABLE, "details": []})
            continue
        required.append(rule.doc_type)
        matching = [d for d in documents if d.get("doc_type") == rule.doc_type]
        details = []
        status = MISSING
        if matching:
            status = OUT_OF_WINDOW
            for doc in matching:
                issued = parse_iso_date(doc["issued_on"])
                age = (reference_date - issued).days
                ok = age >= 0 and (rule.max_age_days is None or age <= rule.max_age_days)
                details.append(
                    {
                        "reference": doc.get("reference", ""),
                        "issued_on": issued.isoformat(),
                        "age_days": age,
                        "counts": ok,
                        "why": _age_reason(age, rule.max_age_days),
                    }
                )
                if ok:
                    status = SATISFIED
        if status != SATISFIED:
            unmet.append({"rule_id": rule.id, "doc_type": rule.doc_type, "status": status})
        outcomes.append({"rule_id": rule.id, "doc_type": rule.doc_type, "status": status, "details": details})
    return {
        "policy_version": policy.version,
        "reference_date": reference_date.isoformat(),
        "product_category": product,
        "volume_band": band,
        "required": required,
        "unmet": unmet,
        "complete": not unmet,
        "rules": outcomes,
    }


def _age_reason(age: int, max_age: int | None) -> str:
    if age < 0:
        return f"issued {-age} day(s) after the submission date"
    if max_age is None:
        return "no age limit"
    if age <= max_age:
        return f"{age} day(s) old as of submission (limit {max_age})"
    return f"{age} day(s) old as of submission exceeds the {max_age}-day limit"


def required_doc_types(policy: Policy, product: str, band: str) -> list[str]:
    return [r.doc_type for r in policy.rules if r.applies_to(product, band)]


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
