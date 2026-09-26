"""Per-database advisory lock so a reset can never run under a live server (DEF-004, Codex R3-03).

`python -m moblab run` holds a SHARED lock on `<db>.runlock` for its whole lifetime; `reset` needs an
EXCLUSIVE lock and refuses if any server — on any port — has that database open. The OS releases
the lock when a process exits, so a crashed server leaves no stale lock behind.

Supported platforms: macOS and Linux (POSIX `fcntl.flock`). The launcher is bash, so Windows is
not a supported host for this lab.
"""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class DatabaseInUse(RuntimeError):
    pass


def lock_path(db: Path) -> Path:
    return Path(str(db) + ".runlock")


def hold_shared(db: Path):
    """Acquire and return an open file that holds the shared (server) lock until closed."""
    db.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path(db), "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise DatabaseInUse(f"{db} is being reset right now; try again in a moment.") from None
    return fh


@contextmanager
def exclusive(db: Path) -> Iterator[None]:
    """Exclusive lock for destructive operations (reset). Fails fast if a server holds the DB."""
    db.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path(db), "a+")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DatabaseInUse(
                f"A lab server is running on {db}. Stop it (Ctrl+C in its terminal) before resetting."
            ) from None
        yield
    finally:
        fh.close()   # closing the descriptor releases the lock


__all__ = ["DatabaseInUse", "exclusive", "hold_shared", "lock_path"]
