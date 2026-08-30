import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.db import connect, migrate


class DatabaseMigrationTest(unittest.TestCase):
    def test_duplicate_legacy_checkins_do_not_block_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.db")
            db = sqlite3.connect(path)
            db.executescript("""
            CREATE TABLE checkins (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              date TEXT NOT NULL, task_id TEXT, task_name TEXT,
              start_time TEXT, end_time TEXT, submit_time TEXT,
              status TEXT NOT NULL, response_code TEXT, response_message TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              account_id INTEGER, account_name TEXT
            );
            INSERT INTO checkins(date,task_id,status,created_at,updated_at,account_id)
              VALUES('2026-08-29','same-task','SUCCESS','now','now',1);
            INSERT INTO checkins(date,task_id,status,created_at,updated_at,account_id)
              VALUES('2026-08-29','same-task','SUCCESS','now','now',1);
            """)
            db.commit()
            db.close()

            with patch.dict(os.environ, {"DATABASE_PATH": path}, clear=False):
                migrate()
                with connect() as migrated:
                    self.assertEqual(
                        migrated.execute(
                            "SELECT COUNT(*) FROM checkins WHERE account_id=1 AND task_id='same-task'"
                        ).fetchone()[0],
                        2,
                    )
                    cursor = migrated.execute(
                        "INSERT OR IGNORE INTO checkins(date,task_id,status,created_at,updated_at,account_id) "
                        "VALUES('2026-08-30','same-task','ATTEMPT_STARTED','now','now',1)"
                    )
                    self.assertEqual(cursor.rowcount, 0)


if __name__ == "__main__":
    unittest.main()
