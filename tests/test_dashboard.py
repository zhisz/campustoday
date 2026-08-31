import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.dashboard import _flatten_history, build_dashboard
from app.db import connect, migrate, now_iso
from app.task_store import upsert_account_tasks
from campus.attendance import AttendanceTask


LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class DashboardHistoryFormattingTest(unittest.TestCase):
    def test_school_history_marks_only_matching_local_success_as_automatic(self):
        rows = [{
            "dayInMonth": 29,
            "signedTasks": [
                {"signInstanceWid": "auto-task", "taskName": "晚查寝"},
                {"signInstanceWid": "manual-task", "taskName": "晚查寝"},
            ],
        }]
        records, count = _flatten_history(rows, "2026-08", {"auto-task"})

        self.assertEqual(count, 2)
        self.assertTrue(records[0]["automatic"])
        self.assertFalse(records[1]["automatic"])

    def test_full_date_from_school_is_not_prefixed_twice(self):
        records, _ = _flatten_history(
            [{"dayInMonth": "2026-08-28", "signedTasks": [{"signInstanceWid": "x"}]}],
            "2026-08",
            set(),
        )

        self.assertEqual(records[0]["date"], "2026-08-28")


class DatabaseBackedDashboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "DATABASE_PATH": os.path.join(self.tmp.name, "dashboard.db"),
                "TZ": "Asia/Shanghai",
                "CPDAILY_BASE_URL": "https://fdm.jxust.edu.cn",
            },
            clear=False,
        )
        self.env.start()
        migrate()
        self.first_account = self._insert_account("一号账号", "student-1")
        self.second_account = self._insert_account("二号账号", "student-2")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _insert_account(self, name, campus_user_id):
        at = now_iso()
        with connect() as db:
            return db.execute(
                "INSERT INTO campus_accounts("
                "name,session_cookie,auto_enabled,session_status,real_name,campus_user_id,"
                "identity_verified_at,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (name, f"MOD_AUTH_CAS={campus_user_id}", 1, "VALID", name,
                 campus_user_id, at, at, at),
            ).lastrowid

    @staticmethod
    def _school_time(value):
        return value.astimezone(LOCAL_TZ).isoformat(timespec="seconds")

    def test_build_dashboard_reads_persisted_tasks_without_school_access(self):
        now = datetime.now(LOCAL_TZ)
        tasks = [
            AttendanceTask(
                "pending-task",
                "pending-sign",
                "待签任务",
                self._school_time(now + timedelta(minutes=10)),
                self._school_time(now + timedelta(minutes=40)),
                False,
                True,
                "unSignedTasks",
            ),
            AttendanceTask(
                "signed-task",
                "signed-sign",
                "已签任务",
                self._school_time(now - timedelta(minutes=2)),
                self._school_time(now),
                True,
                True,
                "signedTasks",
            ),
            AttendanceTask(
                "missed-task",
                "missed-sign",
                "漏签任务",
                self._school_time(now - timedelta(minutes=4)),
                self._school_time(now - timedelta(minutes=3)),
                False,
                True,
                "unSignedTasks",
            ),
        ]
        upsert_account_tasks(self.first_account, tasks)
        upsert_account_tasks(
            self.second_account,
            [AttendanceTask(
                "other-account-task",
                "other-sign",
                "其他账号任务",
                self._school_time(now - timedelta(hours=1)),
                self._school_time(now),
                True,
                True,
                "signedTasks",
            )],
        )
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with connect() as db:
            db.execute(
                "INSERT INTO checkins("
                "date,task_id,task_name,start_time,end_time,submit_time,status,response_message,"
                "created_at,updated_at,account_id,account_name"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (now.strftime("%Y-%m-%d"), "signed-task", "已签任务", tasks[1].start_time,
                 tasks[1].end_time, at, "SUCCESS", "confirmed", at, at,
                 self.first_account, "一号账号"),
            )

        with patch(
            "app.dashboard.create_client",
            side_effect=AssertionError("dashboard must not create a school client"),
            create=True,
        ) as create_client, patch(
            "urllib.request.OpenerDirector.open",
            side_effect=AssertionError("dashboard must not access the school network"),
        ) as open_request:
            dashboard = build_dashboard(self.first_account)

        create_client.assert_not_called()
        open_request.assert_not_called()
        self.assertEqual(dashboard["selected_account"]["id"], self.first_account)
        self.assertEqual([item["name"] for item in dashboard["upcoming"]], ["待签任务"])

        history_by_name = {item["name"]: item for item in dashboard["school_history"]}
        self.assertNotIn("待签任务", history_by_name)
        self.assertNotIn("其他账号任务", history_by_name)
        self.assertEqual(history_by_name["已签任务"]["status"], "已签到")
        self.assertTrue(history_by_name["已签任务"]["automatic"])
        self.assertEqual(history_by_name["漏签任务"]["status"], "未签到")
        self.assertFalse(history_by_name["漏签任务"]["automatic"])
        self.assertEqual(dashboard["school_signed_count"], 1)
        self.assertIsNone(dashboard["school_error"])
        self.assertNotIn(dashboard["cloud_updated_at"], (None, "尚未同步"))

    def test_empty_cloud_snapshot_is_stable_and_does_not_access_school(self):
        with patch(
            "app.dashboard.create_client",
            side_effect=AssertionError("empty cloud state must not create a school client"),
            create=True,
        ) as create_client, patch(
            "urllib.request.OpenerDirector.open",
            side_effect=AssertionError("empty cloud state must not trigger an upstream fill"),
        ) as open_request:
            first = build_dashboard(self.first_account)
            second = build_dashboard(self.first_account)

        create_client.assert_not_called()
        open_request.assert_not_called()
        self.assertEqual(first["upcoming"], [])
        self.assertEqual(first["school_history"], [])
        self.assertEqual(second["upcoming"], [])
        self.assertEqual(second["school_history"], [])
        self.assertEqual(first["cloud_updated_at"], "尚未同步")
        self.assertIsNone(first["school_error"])

    def test_replacement_instance_is_one_history_row_and_one_signed_count(self):
        now = datetime.now(LOCAL_TZ)
        start = self._school_time(now - timedelta(minutes=10))
        end = self._school_time(now - timedelta(minutes=1))
        upsert_account_tasks(
            self.first_account,
            [
                AttendanceTask("signed-old", "stable-template", "每日晚查寝", start, end, True, True, "signedTasks"),
                AttendanceTask("signed-new", "stable-template", "每日晚查寝", start, end, True, True, "signedTasks"),
            ],
        )
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with connect() as db:
            db.execute(
                "INSERT INTO checkins("
                "date,task_id,task_name,start_time,end_time,submit_time,status,response_message,"
                "created_at,updated_at,account_id,account_name"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (now.strftime("%Y-%m-%d"), "signed-new", "每日晚查寝", start, end, at,
                 "SUCCESS", "confirmed", at, at, self.first_account, "一号账号"),
            )

        dashboard = build_dashboard(self.first_account)

        self.assertEqual(len(dashboard["school_history"]), 1)
        self.assertEqual(dashboard["school_history"][0]["id"], "signed-new")
        self.assertTrue(dashboard["school_history"][0]["automatic"])
        self.assertEqual(dashboard["school_signed_count"], 1)


if __name__ == "__main__":
    unittest.main()
