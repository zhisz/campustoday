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
        db.executescript("""
        CREATE TABLE IF NOT EXISTS campus_accounts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          session_cookie TEXT NOT NULL,
          auto_enabled INTEGER NOT NULL DEFAULT 1,
          session_status TEXT NOT NULL DEFAULT 'UNKNOWN',
          last_checked_at TEXT,
          last_error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """)
        checkin_columns = {row["name"] for row in db.execute("PRAGMA table_info(checkins)")}
        for name, definition in (("account_id", "INTEGER"), ("account_name", "TEXT")):
            if name not in checkin_columns:
                db.execute(f"ALTER TABLE checkins ADD COLUMN {name} {definition}")
        migrated = db.execute("SELECT value FROM settings WHERE key='legacy_cookie_migrated'").fetchone()
        if not migrated:
            legacy_cookie = os.getenv("CPDAILY_SESSION_COOKIE", "").strip()
            if legacy_cookie:
                at = now_iso()
                db.execute(
                    "INSERT INTO campus_accounts(name,session_cookie,auto_enabled,created_at,updated_at) VALUES(?,?,?,?,?)",
                    ("默认账号", legacy_cookie, 1, at, at),
                )
            db.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES('legacy_cookie_migrated','true',?)",
                (now_iso(),),
            )
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (4, CURRENT_TIMESTAMP)")
        account_columns = {row["name"] for row in db.execute("PRAGMA table_info(campus_accounts)")}
        for name in ("device_id", "app_version", "device_model", "system_name", "system_version"):
            if name not in account_columns:
                db.execute(f"ALTER TABLE campus_accounts ADD COLUMN {name} TEXT")
        device_defaults = {
            "device_id": os.getenv("CPDAILY_DEVICE_ID", "").strip(),
            "app_version": os.getenv("CPDAILY_APP_VERSION", "").strip(),
            "device_model": os.getenv("CPDAILY_DEVICE_MODEL", "").strip(),
            "system_name": os.getenv("CPDAILY_SYSTEM_NAME", "").strip(),
            "system_version": os.getenv("CPDAILY_SYSTEM_VERSION", "").strip(),
        }
        for column, value in device_defaults.items():
            if value:
                db.execute(f"UPDATE campus_accounts SET {column}=? WHERE {column} IS NULL OR {column}=''", (value,))
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (5, CURRENT_TIMESTAMP)")
        account_columns = {row["name"] for row in db.execute("PRAGMA table_info(campus_accounts)")}
        for name in ("real_name", "campus_user_id", "identity_verified_at"):
            if name not in account_columns:
                db.execute(f"ALTER TABLE campus_accounts ADD COLUMN {name} TEXT")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (6, CURRENT_TIMESTAMP)")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS app_users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL COLLATE NOCASE UNIQUE,
          password_hash TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'ACTIVE',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_login_at TEXT
        );
        CREATE TABLE IF NOT EXISTS app_tokens (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
          token_hash TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          last_used_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          revoked_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_app_tokens_user ON app_tokens(user_id);
        """)
        account_columns = {row["name"] for row in db.execute("PRAGMA table_info(campus_accounts)")}
        if "owner_user_id" not in account_columns:
            db.execute("ALTER TABLE campus_accounts ADD COLUMN owner_user_id INTEGER REFERENCES app_users(id) ON DELETE CASCADE")
        db.execute("CREATE INDEX IF NOT EXISTS idx_campus_accounts_owner ON campus_accounts(owner_user_id)")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (7, CURRENT_TIMESTAMP)")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS announcements (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          content TEXT NOT NULL,
          published INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS announcement_reads (
          announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
          read_at TEXT NOT NULL,
          PRIMARY KEY(announcement_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS feedback (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
          category TEXT NOT NULL,
          content TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'OPEN',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id);
        """)
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (8, CURRENT_TIMESTAMP)")
        announcement_columns = {row["name"] for row in db.execute("PRAGMA table_info(announcements)")}
        for name in ("starts_at", "ends_at"):
            if name not in announcement_columns:
                db.execute(f"ALTER TABLE announcements ADD COLUMN {name} TEXT")
        db.execute("UPDATE announcements SET starts_at=created_at WHERE starts_at IS NULL OR starts_at='' ")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (9, CURRENT_TIMESTAMP)")
        # A trigger prevents future duplicate attempts without requiring legacy
        # duplicate rows to be deleted during migration.  Some early releases
        # could record the same task more than once, so a unique index would make
        # an otherwise healthy production database fail to start.
        db.execute("DROP INDEX IF EXISTS idx_checkins_account_task_once")
        db.execute("DROP TRIGGER IF EXISTS trg_checkins_account_task_once")
        db.executescript("""
        CREATE TRIGGER IF NOT EXISTS trg_checkins_account_task_once
        BEFORE INSERT ON checkins
        WHEN NEW.account_id IS NOT NULL
          AND NEW.task_id IS NOT NULL
          AND EXISTS (
            SELECT 1
            FROM checkins prior
            LEFT JOIN campus_accounts prior_account ON prior_account.id=prior.account_id
            LEFT JOIN campus_accounts new_account ON new_account.id=NEW.account_id
            WHERE prior.task_id=NEW.task_id
              AND (
                prior.account_id=NEW.account_id
                OR (
                  new_account.campus_user_id IS NOT NULL
                  AND new_account.campus_user_id!=''
                  AND prior_account.campus_user_id=new_account.campus_user_id
                )
              )
          )
        BEGIN
          SELECT RAISE(IGNORE);
        END;
        """)
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (10, CURRENT_TIMESTAMP)")
        migration_11_applied = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=11"
        ).fetchone()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS account_task_records (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER NOT NULL REFERENCES campus_accounts(id) ON DELETE CASCADE,
          task_id TEXT NOT NULL,
          sign_wid TEXT NOT NULL DEFAULT '',
          task_name TEXT NOT NULL DEFAULT '',
          start_time TEXT,
          end_time TEXT,
          completed INTEGER NOT NULL DEFAULT 0,
          active_today INTEGER NOT NULL DEFAULT 0,
          school_status TEXT NOT NULL DEFAULT '',
          source_group TEXT NOT NULL DEFAULT '',
          record_date TEXT NOT NULL DEFAULT '',
          publisher TEXT NOT NULL DEFAULT '',
          signed_time TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          sort_at TEXT NOT NULL,
          completed_at TEXT,
          UNIQUE(account_id, task_id)
        );
        CREATE INDEX IF NOT EXISTS idx_account_task_records_recent
          ON account_task_records(account_id, sort_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS account_task_sync_state (
          account_id INTEGER PRIMARY KEY REFERENCES campus_accounts(id) ON DELETE CASCADE,
          synced_at TEXT,
          history_synced_at TEXT,
          task_count INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          updated_at TEXT NOT NULL
        );
        """)
        task_record_columns = {row["name"] for row in db.execute("PRAGMA table_info(account_task_records)")}
        for name, definition in (
            ("source_group", "TEXT NOT NULL DEFAULT ''"),
            ("record_date", "TEXT NOT NULL DEFAULT ''"),
            ("publisher", "TEXT NOT NULL DEFAULT ''"),
            ("signed_time", "TEXT"),
        ):
            if name not in task_record_columns:
                db.execute(f"ALTER TABLE account_task_records ADD COLUMN {name} {definition}")
        task_sync_columns = {row["name"] for row in db.execute("PRAGMA table_info(account_task_sync_state)")}
        if "history_synced_at" not in task_sync_columns:
            db.execute("ALTER TABLE account_task_sync_state ADD COLUMN history_synced_at TEXT")
        if not migration_11_applied:
            db.execute(
                "INSERT OR IGNORE INTO account_task_records("
                "account_id,task_id,task_name,start_time,end_time,completed,active_today,"
                "school_status,source_group,record_date,publisher,signed_time,"
                "first_seen_at,last_seen_at,sort_at,completed_at"
                ") SELECT "
                "c.account_id,c.task_id,COALESCE(c.task_name,''),c.start_time,c.end_time,"
                "CASE WHEN c.status='SUCCESS' THEN 1 ELSE 0 END,0,"
                "c.status,CASE WHEN c.status='SUCCESS' THEN 'localAutomatic' ELSE 'localAttempt' END,"
                "COALESCE(c.date,''),'',CASE WHEN c.status='SUCCESS' THEN c.submit_time ELSE NULL END,"
                "c.created_at,c.updated_at,"
                "COALESCE(NULLIF(c.end_time,''),NULLIF(c.start_time,''),c.updated_at),"
                "CASE WHEN c.status='SUCCESS' THEN COALESCE(c.submit_time,c.updated_at) ELSE NULL END "
                "FROM checkins c JOIN ("
                "SELECT account_id,task_id,COALESCE(MAX(CASE WHEN status='SUCCESS' THEN id END),MAX(id)) AS selected_id "
                "FROM checkins WHERE account_id IS NOT NULL AND task_id IS NOT NULL GROUP BY account_id,task_id"
                ") chosen ON chosen.selected_id=c.id JOIN campus_accounts a ON a.id=c.account_id"
            )
            account_ids = db.execute("SELECT DISTINCT account_id FROM account_task_records").fetchall()
            for row in account_ids:
                db.execute(
                    "DELETE FROM account_task_records WHERE account_id=? AND id NOT IN ("
                    "SELECT id FROM account_task_records WHERE account_id=? "
                    "ORDER BY sort_at DESC,id DESC LIMIT 100)",
                    (row["account_id"], row["account_id"]),
                )
            db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (11, CURRENT_TIMESTAMP)")
        db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (12, CURRENT_TIMESTAMP)")


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
