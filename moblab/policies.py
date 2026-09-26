"""Load the fictional policy versions from policies/*.toml (the committed source of truth)."""
from __future__ import annotations

import tomllib
from pathlib import Path

from .paths import POLICY_DIR
from .policy_engine import Policy, policy_from_mapping


def load_policy_files(directory: Path = POLICY_DIR) -> list[Policy]:
    policies = []
    for path in sorted(directory.glob("v*.toml")):
        with path.open("rb") as fh:
            policies.append(policy_from_mapping(tomllib.load(fh)))
    versions = [p.version for p in policies]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate policy versions: {versions}")
    dates = [p.effective_from for p in policies]
    if len(set(dates)) != len(dates):
        raise ValueError("two policy versions share an effective date")
    return sorted(policies, key=lambda p: p.effective_from)
