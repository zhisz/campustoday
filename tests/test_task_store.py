import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.db import connect, migrate, now_iso
from app.task_store import (
    history_sync_due,
    list_account_tasks,
    upsert_account_history,
    upsert_account_tasks,
)
from campus.attendance import AttendanceTask


class AccountTaskStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.tmp.name, "task-store.db")
        self.environment = patch.dict(
            os.environ,
            {"DATABASE_PATH": self.database_path},
            clear=False,
        )
        self.environment.start()
        migrate()
        self.first_account = self._create_account("first")
        self.second_account = self._create_account("second")

    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()

    def _create_account(self, name):
        at = now_iso()
        with connect() as db:
            return db.execute(
                "INSERT INTO campus_accounts("
                "name,session_cookie,auto_enabled,created_at,updated_at"
                ") VALUES(?,?,?,?,?)",
                (name, f"MOD_AUTH_CAS={name}", 0, at, at),
            ).lastrowid

    @staticmethod
    def _task(
        task_id,
        *,
        sign_wid=None,
        name=None,
        start_time="2026-08-30T20:00:00+08:00",
        end_time="2026-08-30T23:00:00+08:00",
        completed=False,
        status=None,
    ):
        return AttendanceTask(
            task_id=task_id,
            sign_wid=sign_wid if sign_wid is not None else f"sign-{task_id}",
            name=name if name is not None else f"task-{task_id}",
            start_time=start_time,
            end_time=end_time,
            completed=completed,
            requires_location=True,
            status=status if status is not None else ("signedTasks" if completed else "unSignedTasks"),
        )

    def test_same_school_task_id_is_isolated_by_account(self):
        upsert_account_tasks(
            self.first_account,
            [self._task("shared", name="first account task")],
        )
        upsert_account_tasks(
            self.second_account,
            [self._task("shared", name="second account task")],
        )

        first = list_account_tasks(self.first_account)
        second = list_account_tasks(self.second_account)

        self.assertEqual([row["task_name"] for row in first], ["first account task"])
        self.assertEqual([row["task_name"] for row in second], ["second account task"])
        with connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM account_task_records WHERE task_id='shared'"
                ).fetchone()[0],
                2,
            )

    def test_repeated_upsert_updates_one_row_instead_of_duplicating_it(self):
        upsert_account_tasks(
            self.first_account,
            [self._task("repeat", sign_wid="old-sign", name="old name")],
        )
        upsert_account_tasks(
            self.first_account,
            [self._task("repeat", sign_wid="new-sign", name="new name")],
        )

        rows = list_account_tasks(self.first_account)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_name"], "new name")
        self.assertEqual(rows[0]["sign_wid"], "new-sign")

    def test_completed_task_never_rolls_back_to_pending(self):
        upsert_account_tasks(
            self.first_account,
            [self._task("transition", status="unSignedTasks")],
        )
        upsert_account_tasks(
            self.first_account,
            [self._task("transition", completed=True, status="signedTasks")],
        )
        completed = list_account_tasks(self.first_account)[0]
        self.assertEqual(completed["completed"], 1)
        self.assertEqual(completed["school_status"], "signedTasks")
        self.assertIsNotNone(completed["completed_at"])

        # A stale pending snapshot must not undo a terminal school state.
        upsert_account_tasks(
            self.first_account,
            [self._task("transition", completed=False, status="unSignedTasks")],
        )
        after_stale_snapshot = list_account_tasks(self.first_account)[0]
        self.assertEqual(after_stale_snapshot["completed"], 1)
        self.assertEqual(after_stale_snapshot["school_status"], "signedTasks")
        self.assertEqual(after_stale_snapshot["completed_at"], completed["completed_at"])

    def test_empty_snapshot_preserves_previous_active_tasks(self):
        upsert_account_tasks(
            self.first_account,
            [self._task("still-visible")],
        )

        self.assertEqual(upsert_account_tasks(self.first_account, []), 0)

        rows = list_account_tasks(self.first_account)
        self.assertEqual([row["task_id"] for row in rows], ["still-visible"])
        self.assertEqual(rows[0]["active_today"], 1)

    def test_retention_keeps_only_newest_100_for_one_account(self):
        other_task = self._task(
            "other-account-old-task",
            end_time="2020-01-01T00:00:00+00:00",
        )
        upsert_account_tasks(self.second_account, [other_task])

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tasks = [
            self._task(
                f"retained-{index:03d}",
                start_time=(base + timedelta(minutes=index)).isoformat(),
                end_time=(base + timedelta(minutes=index, seconds=30)).isoformat(),
            )
            for index in range(101)
        ]
        upsert_account_tasks(self.first_account, tasks)

        with connect() as db:
            first_ids = {
                row["task_id"]
                for row in db.execute(
                    "SELECT task_id FROM account_task_records WHERE account_id=?",
                    (self.first_account,),
                ).fetchall()
            }
            second_ids = {
                row["task_id"]
                for row in db.execute(
                    "SELECT task_id FROM account_task_records WHERE account_id=?",
                    (self.second_account,),
                ).fetchall()
            }

        self.assertEqual(len(first_ids), 100)
        self.assertNotIn("retained-000", first_ids)
        self.assertIn("retained-100", first_ids)
        self.assertEqual(second_ids, {"other-account-old-task"})

    def test_empty_update_fields_do_not_erase_known_task_metadata(self):
        original = self._task(
            "metadata",
            sign_wid="known-sign",
            name="known name",
            start_time="2026-08-30T20:15:00+08:00",
            end_time="2026-08-30T22:45:00+08:00",
            status="unSignedTasks",
        )
        upsert_account_tasks(self.first_account, [original])

        upsert_account_tasks(
            self.first_account,
            [
                self._task(
                    "metadata",
                    sign_wid="",
                    name="",
                    start_time="",
                    end_time="",
                    status="",
                )
            ],
        )

        row = list_account_tasks(self.first_account)[0]
        self.assertEqual(row["sign_wid"], "known-sign")
        self.assertEqual(row["task_name"], "known name")
        self.assertEqual(row["start_time"], "2026-08-30T20:15:00+08:00")
        self.assertEqual(row["end_time"], "2026-08-30T22:45:00+08:00")
        self.assertEqual(row["school_status"], "unSignedTasks")

    def test_month_history_persists_school_status_publisher_sign_time_and_date(self):
        history = {
            "rows": [
                {
                    "dayInMonth": "30",
                    "signedTasks": [
                        {
                            "signInstanceWid": "history-fields",
                            "signWid": "history-sign",
                            "taskName": "monthly history task",
                            "singleTaskBeginTime": "2026-08-30T20:00:00+08:00",
                            "singleTaskEndTime": "2026-08-30T22:00:00+08:00",
                            "rateSignDate": "2026-08-30T20:36:12+08:00",
                            "senderUserName": "history publisher",
                            "signStatus": "SIGNED",
                        }
                    ],
                }
            ]
        }

        self.assertEqual(
            upsert_account_history(self.first_account, history, "2026-08"),
            1,
        )

        row = list_account_tasks(self.first_account)[0]
        self.assertEqual(row["source_group"], "signedTasks")
        self.assertEqual(row["school_status"], "SIGNED")
        self.assertEqual(row["publisher"], "history publisher")
        self.assertEqual(row["signed_time"], "2026-08-30T20:36:12+08:00")
        self.assertEqual(row["record_date"], "2026-08-30")
        self.assertEqual(row["completed"], 1)

    def test_signed_history_updates_the_existing_pending_daily_task(self):
        upsert_account_tasks(
            self.first_account,
            [
                self._task(
                    "daily-then-history",
                    sign_wid="daily-sign",
                    name="pending daily task",
                    status="unSignedTasks",
                )
            ],
        )
        history = {
            "rows": [
                {
                    "dayInMonth": "2026-08-30",
                    "signedTasks": [
                        {
                            "signInstanceWid": "daily-then-history",
                            "signWid": "daily-sign",
                            "taskName": "signed daily task",
                            "rateSignDate": "2026-08-30T21:01:02+08:00",
                            "senderUserName": "teacher",
                        }
                    ],
                }
            ]
        }

        upsert_account_history(self.first_account, history, "2026-08")

        rows = list_account_tasks(self.first_account)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_id"], "daily-then-history")
        self.assertEqual(rows[0]["task_name"], "signed daily task")
        self.assertEqual(rows[0]["completed"], 1)
        self.assertEqual(rows[0]["source_group"], "signedTasks")
        self.assertEqual(rows[0]["publisher"], "teacher")
        self.assertEqual(rows[0]["signed_time"], "2026-08-30T21:01:02+08:00")

    def test_empty_month_history_preserves_existing_records(self):
        upsert_account_tasks(
            self.first_account,
            [self._task("preserved-after-empty-history")],
        )

        self.assertEqual(
            upsert_account_history(self.first_account, {"rows": []}, "2026-08"),
            0,
        )

        rows = list_account_tasks(self.first_account)
        self.assertEqual(
            [row["task_id"] for row in rows],
            ["preserved-after-empty-history"],
        )
        self.assertEqual(rows[0]["active_today"], 1)

    def test_history_sync_is_not_due_immediately_after_successful_sync(self):
        self.assertTrue(history_sync_due(self.first_account, 360))

        upsert_account_history(self.first_account, {"rows": []}, "2026-08")

        self.assertFalse(history_sync_due(self.first_account, 360))
        self.assertTrue(history_sync_due(self.second_account, 360))

    def test_rerunning_migration_11_does_not_reimport_removed_legacy_row(self):
        at = now_iso()
        with connect() as db:
            db.execute(
                "INSERT INTO checkins("
                "date,task_id,task_name,status,submit_time,created_at,updated_at,account_id,account_name"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "2026-08-30",
                    "legacy-checkin",
                    "legacy task",
                    "SUCCESS",
                    at,
                    at,
                    at,
                    self.first_account,
                    "first",
                ),
            )
            # Recreate the pre-v11 marker state so this migrate call performs
            # the one-time legacy import.
            db.execute("DELETE FROM schema_migrations WHERE version=11")

        migrate()
        with connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM account_task_records "
                    "WHERE account_id=? AND task_id='legacy-checkin'",
                    (self.first_account,),
                ).fetchone()[0],
                1,
            )
            db.execute(
                "DELETE FROM account_task_records "
                "WHERE account_id=? AND task_id='legacy-checkin'",
                (self.first_account,),
            )

        migrate()
        with connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM account_task_records "
                    "WHERE account_id=? AND task_id='legacy-checkin'",
                    (self.first_account,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version=11"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
