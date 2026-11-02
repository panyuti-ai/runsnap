"""Snapshots on disk: one SQLite file per recorded run."""
from __future__ import annotations

import datetime
import os
import platform
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS calls (
  test TEXT, seq INTEGER, func TEXT, depth INTEGER,
  args TEXT, result TEXT, exc TEXT, duration_ns INTEGER
);
CREATE INDEX IF NOT EXISTS calls_func ON calls (func);
CREATE INDEX IF NOT EXISTS calls_test ON calls (test);
"""


def snap_dir() -> Path:
    return Path(os.environ.get("RUNSNAP_DIR", ".runsnap"))


def path_for(label: str) -> Path:
    if label.endswith(".sqlite") or os.sep in label or "/" in label:
        return Path(label)
    return snap_dir() / f"{label}.sqlite"


def write(label: str, calls, targets, truncated) -> Path:
    path = path_for(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        meta = {
            "label": label,
            "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "targets": ",".join(targets),
            "truncated_tests": "\n".join(sorted(truncated)),
        }
        db.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
        db.executemany(
            "INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(c.test, c.seq, c.func, c.depth, c.args, c.result, c.exc, c.duration_ns) for c in calls],
        )
    return path


def open_snapshot(label: str) -> sqlite3.Connection:
    path = path_for(label)
    if not path.exists():
        raise FileNotFoundError(f"no snapshot named {label!r} (looked for {path})")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def list_snapshots() -> list[dict]:
    out = []
    for path in sorted(snap_dir().glob("*.sqlite")):
        with sqlite3.connect(path) as db:
            meta = dict(db.execute("SELECT key, value FROM meta"))
            count = db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        out.append({"label": path.stem, "created": meta.get("created"), "targets": meta.get("targets"), "calls": count})
    return out
