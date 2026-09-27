"""Command line: python -m moblab <command>.

  run            serve on 127.0.0.1 (default port 5058) — never on another interface
  seed           create the named database with the synthetic seed (refuses if it exists)
  reset --yes    back up the existing database into <db dir>/backups/, then reseed
  check          verify schema, policy rule hashes and print a short status
  evidence       regenerate docs/06_test_evidence.md from evidence/*.json
  build-replay   build the offline recorded-replay HTML from evidence + screenshots
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .paths import DEFAULT_DB_PATH


def _db(args) -> Path:
    return Path(args.db).expanduser().resolve()


def cmd_seed(args) -> int:
    from .seed import init_database
    db = _db(args)
    if db.exists():
        print(f"Database already exists, left untouched: {db}")
        return 0 if args.if_missing else 1
    init_database(db)
    print(f"Created synthetic seed database: {db}")
    return 0


def cmd_reset(args) -> int:
    from .runlock import DatabaseInUse, exclusive
    db = _db(args)
    if not args.yes:
        print("Refusing to reset without --yes. The current database would be backed up first.")
        return 2
    try:
        with exclusive(db):   # precondition: no server has this database open (DEF-004)
            return _reset_locked(db)
    except DatabaseInUse as exc:
        print(f"ERROR: {exc} Nothing was changed.")
        return 3


def _reset_locked(db: Path) -> int:
    from .seed import init_database
    backup = None
    if db.exists():
        backups = db.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = backups / f"{db.stem}-{stamp}.db"
        n = 1
        while backup.exists():
            backup = backups / f"{db.stem}-{stamp}-{n}.db"
            n += 1
        import sqlite3
        src = sqlite3.connect(str(db))
        dst = sqlite3.connect(str(backup))
        with dst:
            src.backup(dst)   # consistent copy including WAL contents
        src.close()
        dst.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db) + suffix)
            if p.exists():
                p.unlink()
        print(f"Backed up previous database to: {backup}")
    init_database(db)
    print(f"Created fresh synthetic seed database: {db}")
    return 0


def cmd_check(args) -> int:
    from .db import SCHEMA_VERSION, connect, is_initialised
    from .policies import load_policy_files
    db = _db(args)
    if not db.exists():
        print(f"MISSING database: {db}")
        return 1
    conn = connect(db, read_only=True)
    try:
        if not is_initialised(conn):
            print(f"NOT A LAB DATABASE: {db}")
            return 1
        version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        bd = conn.execute("SELECT value FROM meta WHERE key='business_date'").fetchone()[0]
        stored = {r["version"]: r["rules_sha256"] for r in conn.execute("SELECT version, rules_sha256 FROM policy_version")}
        files = {p.version: p.rules_sha256() for p in load_policy_files()}
        n_apps = conn.execute("SELECT COUNT(*) FROM application").fetchone()[0]
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    ok = version == SCHEMA_VERSION and stored == files and quick == "ok"
    print(f"database        {db}")
    print(f"schema version  {version} (expected {SCHEMA_VERSION})")
    print(f"integrity       {quick}")
    print(f"policy rules    {'match policies/*.toml' if stored == files else 'DIFFER from policies/*.toml'}")
    print(f"lab date        {bd}")
    print(f"applications    {n_apps}")
    return 0 if ok else 1


def cmd_run(args) -> int:
    from .runlock import DatabaseInUse, hold_shared
    from .web import HOST, create_app
    db = _db(args)
    try:
        lock = hold_shared(db)   # held for the server's lifetime; blocks `reset` on this database
    except DatabaseInUse as exc:
        print(f"ERROR: {exc}")
        return 3
    try:
        app = create_app(db, instance_dir=db.parent)
        print(f"Merchant Onboarding Policy Change Lab — http://{HOST}:{args.port}/  (database {db})")
        print("Local development server for a synthetic lab; bound to 127.0.0.1 only. Press Ctrl+C to stop.")
        app.run(host=HOST, port=args.port, debug=False, use_reloader=False, threaded=True)
    finally:
        lock.close()
    return 0


def cmd_evidence(args) -> int:
    from .evidence import write_evidence_doc
    path = write_evidence_doc()
    print(f"Wrote {path}")
    return 0


def cmd_build_replay(args) -> int:
    from .replay import build_replay
    path = build_replay()
    print(f"Wrote {path} ({path.stat().st_size // 1024} KB)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m moblab", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("run", cmd_run), ("seed", cmd_seed), ("reset", cmd_reset), ("check", cmd_check)):
        p = sub.add_parser(name)
        p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
        p.set_defaults(func=fn)
        if name == "run":
            p.add_argument("--port", type=int, default=5058)
        if name == "seed":
            p.add_argument("--if-missing", action="store_true", help="exit 0 when the database already exists")
        if name == "reset":
            p.add_argument("--yes", action="store_true", help="confirm: back up and reseed")
    sub.add_parser("evidence").set_defaults(func=cmd_evidence)
    sub.add_parser("build-replay").set_defaults(func=cmd_build_replay)
    args = parser.parse_args(argv)
    if getattr(args, "port", 5058) is not None and not (1024 <= getattr(args, "port", 5058) <= 65535):
        parser.error("--port must be 1024–65535")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
