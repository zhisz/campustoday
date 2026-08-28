import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def db_path() -> str:
    return os.getenv("DATABASE_PATH", "/data/campustoday.db")


@contextmanager
def connect():
    connection = sqlite3.connect(db_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def migrate():
    os.makedirs(os.path.dirname(db_path()), exist_ok=True)
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS accounts (
          id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL,
          password_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL UNIQUE,
          task_name TEXT NOT NULL, start_time TEXT, end_time TEXT,
          status TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS checkins (
          id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, task_id TEXT,
          task_name TEXT, start_time TEXT, end_time TEXT, submit_time TEXT,
          status TEXT NOT NULL, response_code TEXT, response_message TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS locations (
          id INTEGER PRIMARY KEY AUTOINCREMENT, latitude REAL NOT NULL,
          longitude REAL NOT NULL, accuracy REAL, observed_at TEXT NOT NULL,
          received_at TEXT NOT NULL, verified INTEGER NOT NULL,
          reason TEXT NOT NULL, source TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT NOT NULL,
          event TEXT NOT NULL, message TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, CURRENT_TIMESTAMP);
        """)
        location_columns = {row["name"] for row in db.execute("PRAGMA table_info(locations)")}
        for name, definition in (
            ("proof_id", "TEXT"),
            ("device_id", "TEXT"),
            ("address", "TEXT"),
            ("coordinate_system", "TEXT"),
        ):
            if name not in location_columns:
                db.execute(f"ALTER TABLE locations ADD COLUMN {name} {definition}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_locations_proof_id ON locations(proof_id) WHERE proof_id IS NOT NULL")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (2, CURRENT_TIMESTAMP)")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (3, CURRENT_TIMESTAMP)")


def log_event(event: str, message: str, level: str = "INFO", metadata=None):
    safe = _redact(message)
    with connect() as db:
        db.execute(
            "INSERT INTO logs(level,event,message,metadata_json,created_at) VALUES(?,?,?,?,?)",
            (level, event, safe, json.dumps(metadata or {}, ensure_ascii=False), now_iso()),
        )


def _redact(value: str) -> str:
    text = str(value)
    for marker in ("password", "authorization", "cookie", "token", "session"):
        if marker in text.lower():
            return "[sensitive value redacted]"
    return text[:2000]


def get_setting(key: str, default=None):
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_settings(values: dict[str, str]):
    at = now_iso()
    with connect() as db:
        db.executemany(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            [(key, value, at) for key, value in values.items()],
        )
