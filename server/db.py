"""Tiny SQLite store for sensor readings. Owner: Mech B."""
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

# Vercel's filesystem is read-only apart from /tmp, so the database lives there when deployed.
# That copy is wiped whenever the server instance restarts: fine for the demo, but the deployed
# Live tab only keeps readings sent since the last restart. SHIPLESS_DB overrides both.
if os.environ.get("SHIPLESS_DB"):
    DB_PATH = Path(os.environ["SHIPLESS_DB"])
elif os.environ.get("VERCEL"):
    DB_PATH = Path(tempfile.gettempdir()) / "shipless.db"
else:
    DB_PATH = Path(__file__).resolve().parent.parent / "data" / "shipless.db"
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT, site_id TEXT, ts TEXT, received_at TEXT,
            amps REAL, watts REAL, seq INTEGER)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ts ON readings(ts)")


def insert(rows):
    with _lock, _conn() as c:
        c.executemany("INSERT INTO readings (device_id, site_id, ts, received_at, amps, watts, seq) "
                      "VALUES (:device_id, :site_id, :ts, :received_at, :amps, :watts, :seq)", rows)


def since(ts_iso, limit=20000):
    with _lock, _conn() as c:
        cur = c.execute("SELECT device_id, site_id, ts, amps, watts, seq FROM readings "
                        "WHERE ts >= ? ORDER BY ts ASC LIMIT ?", (ts_iso, limit))
        return [dict(r) for r in cur.fetchall()]


def latest():
    with _lock, _conn() as c:
        r = c.execute("SELECT device_id, site_id, ts, amps, watts, seq FROM readings "
                      "ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(r) if r else None


def clear():
    with _lock, _conn() as c:
        c.execute("DELETE FROM readings")
