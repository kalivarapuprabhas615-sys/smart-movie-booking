"""SQLite connection helpers + database bootstrap (schema + sample data)."""
import os, sqlite3, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "database"))
SCHEMA = os.path.join(ROOT, "database", "sqlite", "schema.sql")


def connect(path):
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)   # autocommit; we manage transactions explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def now_str(dt=None):
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def init_db(path, seed=True):
    """Create a fresh database file with schema and (optionally) the sample data."""
    from pricing import recompute_all
    import seed_data
    d = os.path.dirname(path)
    if d: os.makedirs(d, exist_ok=True)
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(path + ext): os.remove(path + ext)
    conn = connect(path)
    with open(SCHEMA) as fh: conn.executescript(fh.read())
    if seed:
        conn.execute("BEGIN")
        seed_data.load_sqlite(conn, seed_data.build_dataset())
        recompute_all(conn)
    return conn


def has_upcoming_shows(conn):
    row = conn.execute("SELECT COUNT(*) c FROM shows WHERE datetime(show_date || ' ' || show_time) > datetime('now','localtime')").fetchone()
    return row["c"] > 0
