"""SQLite connection handling, schema creation and a content fingerprint for read-only checks."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .policies import load_policy_files
from .policy_engine import canonical_json

SCHEMA_VERSION = "1"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Append-only / write-once history tables (REQ-WF-05, REQ-INT-05, REQ-DEC-03).
IMMUTABLE_TABLES = (
    "policy_version",
    "application_revision",
    "revision_document",
    "policy_evaluation",
    "decision",
    "event",
    "info_request",
    "migration_run",
    "migration_item",
    "idempotency_record",
)

DATA_TABLES = (
    "meta",
    "actor",
    "policy_version",
    "application",
    "draft_document",
    "application_revision",
    "revision_document",
    "policy_evaluation",
    "info_request",
    "decision",
    "event",
    "idempotency_record",
    "migration_run",
    "migration_item",
)


def connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a connection in autocommit mode; commands manage BEGIN IMMEDIATE themselves.

    read_only=True sets PRAGMA query_only so any accidental write from a GET handler raises
    instead of silently mutating (REQ-NFR-05).
    """
    conn = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    if read_only:
        conn.execute("PRAGMA query_only = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write transaction. BEGIN IMMEDIATE takes the write lock up front, so two commands
    can never interleave their check-then-write steps (REQ-DEC-03). Any exception rolls back
    everything, so a failed command leaves no partial rows (REQ-DEC-07)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def is_initialised(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
    return row is not None


def create_schema(conn: sqlite3.Connection, business_date: str) -> None:
    """Create tables, triggers and the immutable policy rows in a fresh database."""
    if is_initialised(conn):
        raise RuntimeError("database already initialised")
    conn.execute("PRAGMA journal_mode = WAL")
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    for table in IMMUTABLE_TABLES:
        script += (
            f"\nCREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: {table} rows cannot be updated'); END;"
            f"\nCREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: {table} rows cannot be deleted'); END;"
        )
    conn.executescript("BEGIN;\n" + script + "\nCOMMIT;")
    with transaction(conn):
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        conn.execute("INSERT INTO meta(key, value) VALUES ('business_date', ?)", (business_date,))
        for policy in load_policy_files():
            data = policy.as_dict()
            conn.execute(
                "INSERT INTO policy_version(version, title, effective_from, change_request, summary, rules_json, rules_sha256)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    policy.version,
                    policy.title,
                    policy.effective_from.isoformat(),
                    policy.change_request,
                    policy.summary,
                    canonical_json(data["rules"]),
                    policy.rules_sha256(),
                ),
            )


def fingerprint(conn: sqlite3.Connection) -> str:
    """sha256 over every row of every data table — used to prove GET routes do not write."""
    digest = hashlib.sha256()
    for table in DATA_TABLES:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        digest.update(table.encode())
        for row in rows:
            digest.update(json.dumps(list(row), default=str).encode())
    return digest.hexdigest()


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in DATA_TABLES}
