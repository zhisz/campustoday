import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, call, patch
from zoneinfo import ZoneInfo

from app.scheduler import (
    _eligible_accounts,
    _off_window_sync_due,
    _process_task,
    _schedule_task,
    _scheduled_tasks,
    _sync_accounts,
    poll,
)
from campus.attendance import AttendanceTask
from campus.jxust import UpstreamUnavailable


class MultiAccountSchedulerTest(unittest.TestCase):
    def test_off_window_sync_reads_due_task_lists_only(self):
        accounts = [
            {"id": 1, "name": "due", "session_cookie": "a=1", "auto_enabled": 1, "session_status": "VALID", "campus_user_id": "student-1"},
            {"id": 2, "name": "fresh", "session_cookie": "b=2", "auto_enabled": 1, "session_status": "VALID", "campus_user_id": "student-2"},
        ]
        client = MagicMock()
        client.list_today.return_value = []
        with patch("app.scheduler.set_settings"), \
             patch("app.scheduler.enabled", return_value=True), \
             patch("app.scheduler.monitoring_window", return_value=False), \
             patch("app.scheduler._upstream_ready", return_value=True), \
             patch("app.scheduler.list_accounts", return_value=accounts), \
             patch("app.scheduler._off_window_sync_due", side_effect=[True, False]), \
             patch("app.scheduler.create_client", return_value=client) as create, \
             patch("app.scheduler.upsert_account_tasks") as upsert_tasks, \
             patch("app.scheduler.upsert_account_history") as upsert_history, \
             patch("app.scheduler.history_sync_due") as history_due, \
             patch("app.scheduler._schedule_task") as schedule_task, \
             patch("app.scheduler.record_task_sync_error"), \
             patch("app.scheduler._mark_account_poll_success") as mark_success, \
             patch("app.scheduler._mark_account_poll_failure"), \
             patch("app.scheduler.log_event") as log_event:
            poll()
        create.assert_called_once_with("a=1", purpose="scheduler")
        client.list_today.assert_called_once_with()
        upsert_tasks.assert_called_once_with(1, [])
        mark_success.assert_called_once_with(1)
        upsert_history.assert_not_called()
        history_due.assert_not_called()
        schedule_task.assert_not_called()
        self.assertEqual(log_event.call_args.args[0], "OFF_WINDOW_SYNC_OK")

    def test_off_window_sync_due_is_hourly_per_account(self):
        now = datetime.now(ZoneInfo("UTC"))
        common = (
            patch("app.scheduler._off_window_scan_started", return_value=True),
            patch("app.scheduler._has_cached_today_attendance_task", return_value=False),
        )
        with common[0], common[1], patch("app.scheduler.latest_task_sync", return_value=(now - timedelta(minutes=59)).isoformat()):
            self.assertFalse(_off_window_sync_due(1))
        with patch("app.scheduler._off_window_scan_started", return_value=True), \
             patch("app.scheduler._has_cached_today_attendance_task", return_value=False), \
             patch("app.scheduler.latest_task_sync", return_value=(now - timedelta(minutes=61)).isoformat()):
            self.assertTrue(_off_window_sync_due(1))
        with patch("app.scheduler._off_window_scan_started", return_value=True), \
             patch("app.scheduler._has_cached_today_attendance_task", return_value=False), \
             patch("app.scheduler.latest_task_sync", return_value=None):
            self.assertTrue(_off_window_sync_due(1))

    def test_off_window_sync_waits_until_one_am(self):
        with patch("app.scheduler._off_window_scan_started", return_value=False), \
             patch("app.scheduler._has_cached_today_attendance_task") as has_task, \
             patch("app.scheduler.latest_task_sync") as latest:
            self.assertFalse(_off_window_sync_due(1))
        has_task.assert_not_called()
        latest.assert_not_called()

    def test_off_window_sync_stops_after_today_attendance_is_cached(self):
        with patch("app.scheduler._off_window_scan_started", return_value=True), \
             patch("app.scheduler._has_cached_today_attendance_task", return_value=True), \
             patch("app.scheduler.latest_task_sync") as latest:
            self.assertFalse(_off_window_sync_due(1))
        latest.assert_not_called()

    def test_duplicate_school_identity_is_excluded_before_any_request(self):
        accounts = [
            {"id": 1, "auto_enabled": 1, "session_status": "VALID", "campus_user_id": "same"},
            {"id": 2, "auto_enabled": 0, "session_status": "VALID", "campus_user_id": "same"},
        ]
        with patch("app.scheduler.list_accounts", return_value=accounts):
            self.assertEqual(_eligible_accounts(), [])

    def test_sync_account_selection_includes_disabled_unique_accounts(self):
        accounts = [
            {"id": 1, "auto_enabled": 1, "session_status": "VALID", "campus_user_id": "same"},
            {"id": 2, "auto_enabled": 0, "session_status": "VALID", "campus_user_id": "same"},
            {"id": 3, "auto_enabled": 1, "session_status": "INVALID", "campus_user_id": "invalid"},
            {"id": 4, "auto_enabled": 0, "session_status": "VALID", "campus_user_id": "unique"},
        ]
        with patch("app.scheduler.list_accounts", return_value=accounts):
            self.assertEqual([account["id"] for account in _sync_accounts()], [4])

    def test_poll_syncs_valid_accounts_even_when_automation_is_disabled(self):
        accounts = [
            {"id": 1, "name": "enabled", "session_cookie": "a=1", "auto_enabled": 1, "session_status": "VALID", "campus_user_id": "student-1"},
            {"id": 2, "name": "disabled", "session_cookie": "b=2", "auto_enabled": 0, "session_status": "VALID", "campus_user_id": "student-2"},
        ]
        clients = [MagicMock(), MagicMock()]
        for client in clients:
            client.list_today.return_value = []
        with patch.dict(os.environ, {"CPDAILY_SUBMIT_ENABLED": "false"}), \
             patch("app.scheduler.set_settings"), \
             patch("app.scheduler.enabled", return_value=True), \
             patch("app.scheduler.monitoring_window", return_value=True), \
             patch("app.scheduler._upstream_ready", return_value=True), \
             patch("app.scheduler.list_accounts", return_value=accounts), \
             patch("app.scheduler.account_device") as device, \
             patch("app.scheduler.create_client", side_effect=clients) as create, \
             patch("app.scheduler.upsert_account_tasks") as upsert_tasks, \
             patch("app.scheduler.upsert_account_history"), \
             patch("app.scheduler.history_sync_due", return_value=False) as history_due, \
             patch("app.scheduler.record_task_sync_error"), \
             patch("app.scheduler._mark_account_poll_success") as mark_success, \
             patch("app.scheduler._mark_account_poll_failure"), \
             patch("app.scheduler._history_sync_interval_minutes", return_value=360), \
             patch("app.scheduler.log_event"):
            poll()
        self.assertEqual(create.call_args_list, [
            call("a=1", purpose="scheduler"),
            call("b=2", purpose="scheduler"),
        ])
        device.assert_not_called()
        for client in clients:
            client.list_today.assert_called_once_with()
            client.month_history.assert_not_called()
        self.assertEqual(upsert_tasks.call_args_list, [call(1, []), call(2, [])])
        self.assertEqual(history_due.call_args_list, [call(1, 360), call(2, 360)])
        self.assertEqual(mark_success.call_args_list, [call(1), call(2)])

    def test_poll_syncs_month_history_for_at_most_one_due_account(self):
        accounts = [
            {"id": 1, "name": "first", "session_cookie": "a=1", "auto_enabled": 0, "session_status": "VALID", "campus_user_id": "student-1"},
            {"id": 2, "name": "second", "session_cookie": "b=2", "auto_enabled": 0, "session_status": "VALID", "campus_user_id": "student-2"},
            {"id": 3, "name": "third", "session_cookie": "c=3", "auto_enabled": 0, "session_status": "VALID", "campus_user_id": "student-3"},
        ]
        clients = [MagicMock(), MagicMock(), MagicMock()]
        for client in clients:
            client.list_today.return_value = []
        history = {"rows": [{"dayInMonth": "30", "signedTasks": []}]}
        clients[1].month_history.return_value = history
        with patch.dict(os.environ, {"CPDAILY_SUBMIT_ENABLED": "false"}), \
             patch("app.scheduler.set_settings"), \
             patch("app.scheduler.enabled", return_value=True), \
             patch("app.scheduler.monitoring_window", return_value=True), \
             patch("app.scheduler._upstream_ready", return_value=True), \
             patch("app.scheduler.list_accounts", return_value=accounts), \
             patch("app.scheduler.account_device") as device, \
             patch("app.scheduler.create_client", side_effect=clients), \
             patch("app.scheduler.upsert_account_tasks") as upsert_tasks, \
             patch("app.scheduler.upsert_account_history", return_value=0) as upsert_history, \
             patch("app.scheduler.history_sync_due", side_effect=[False, True]) as history_due, \
             patch("app.scheduler.record_task_sync_error"), \
             patch("app.scheduler._mark_account_poll_success") as mark_success, \
             patch("app.scheduler._mark_account_poll_failure"), \
             patch("app.scheduler._history_sync_interval_minutes", return_value=360), \
             patch("app.scheduler.log_event"):
            poll()
        device.assert_not_called()
        self.assertEqual(upsert_tasks.call_args_list, [call(1, []), call(2, []), call(3, [])])
        self.assertEqual(mark_success.call_args_list, [call(1), call(2), call(3)])
        self.assertEqual(history_due.call_args_list, [call(1, 360), call(2, 360)])
        clients[0].month_history.assert_not_called()
        clients[1].month_history.assert_called_once()
        history_month = clients[1].month_history.call_args.args[0]
        self.assertRegex(history_month, r"^\d{4}-\d{2}$")
        clients[2].month_history.assert_not_called()
        upsert_history.assert_called_once_with(2, history, history_month)

    def test_poll_stops_after_the_first_transport_failure(self):
        accounts = [
            {"id": 1, "name": "first", "session_cookie": "a=1", "auto_enabled": 1, "session_status": "VALID", "campus_user_id": "student-1"},
            {"id": 2, "name": "second", "session_cookie": "b=2", "auto_enabled": 1, "session_status": "VALID", "campus_user_id": "student-2"},
        ]
        client = MagicMock()
        client.list_today.side_effect = RuntimeError("Attendance API request failed")
        error = client.list_today.side_effect
        with patch("app.scheduler.set_settings"), \
             patch("app.scheduler.enabled", return_value=True), \
             patch("app.scheduler.monitoring_window", return_value=True), \
             patch("app.scheduler._upstream_ready", return_value=True), \
             patch("app.scheduler._defer_upstream") as defer, \
             patch("app.scheduler.list_accounts", return_value=accounts), \
             patch("app.scheduler.account_device") as device, \
             patch("app.scheduler.create_client", return_value=client) as create, \
             patch("app.scheduler.upsert_account_tasks") as upsert_tasks, \
             patch("app.scheduler.upsert_account_history") as upsert_history, \
             patch("app.scheduler.history_sync_due") as history_due, \
             patch("app.scheduler.record_task_sync_error") as record_error, \
             patch("app.scheduler._mark_account_poll_success") as mark_success, \
             patch("app.scheduler._mark_account_poll_failure") as mark_failure, \
             patch("app.scheduler.log_event"):
            poll()
        self.assertEqual(create.call_count, 1)
        device.assert_not_called()
        upsert_tasks.assert_not_called()
        upsert_history.assert_not_called()
        history_due.assert_not_called()
        mark_success.assert_not_called()
        record_error.assert_called_once_with(1, error)
        mark_failure.assert_called_once_with(1, error)
        defer.assert_called_once_with()

    def test_successful_submit_is_marked_pending_without_immediate_confirmation(self):
        task = AttendanceTask("task-1", "sign-1", "每日晚查寝", "", "", False, True)
        account = {"id": 7, "name": "student"}
        client = MagicMock()
        client.detail.return_value = {"signPlaceSelected": [{"address": "campus"}]}
        client.submit.return_value = {"accepted": True}
        location = {
            "latitude": 28.1, "longitude": 115.8, "accuracy": 10,
            "observed_at": "2026-08-30T14:00:00+00:00", "address": "campus",
            "coordinate_system": "GCJ02",
        }
        with patch("app.scheduler._task_window_open", return_value=True), \
             patch("app.scheduler._latest_location", return_value=location), \
             patch("app.scheduler.verify_location", return_value=(True, "ok")), \
             patch("app.scheduler.normalize_for_task", return_value=(28.1, 115.8)), \
             patch("app.scheduler.match_task_place", return_value={"address": "campus"}), \
             patch("app.scheduler._save_task"), \
             patch("app.scheduler._save_checkin", return_value=True) as save_checkin, \
             patch("app.scheduler._mark_checkin_submitted") as mark_submitted, \
             patch("app.scheduler.log_event"):
            result = _process_task(client, account, task)
        self.assertEqual(result, {"accepted": True})
        save_checkin.assert_called_once_with(
            account, task, "ATTEMPT_STARTED", "Submission attempt started"
        )
        mark_submitted.assert_called_once_with(account["id"], task.task_id)
        client.list_today.assert_not_called()

    def test_gate_rejection_removes_provisional_attempt_for_safe_retry(self):
        task = AttendanceTask("task-1", "sign-1", "每日晚查寝", "", "", False, True)
        account = {"id": 7, "name": "student"}
        client = MagicMock()
        client.detail.return_value = {"signPlaceSelected": [{"address": "campus"}]}
        client.submit.side_effect = UpstreamUnavailable("学校接口暂时熔断")
        location = {
            "latitude": 28.1, "longitude": 115.8, "accuracy": 10,
            "observed_at": "2026-08-30T14:00:00+00:00", "address": "campus",
            "coordinate_system": "GCJ02",
        }
        with patch("app.scheduler._task_window_open", return_value=True), \
             patch("app.scheduler._latest_location", return_value=location), \
             patch("app.scheduler.verify_location", return_value=(True, "ok")), \
             patch("app.scheduler.normalize_for_task", return_value=(28.1, 115.8)), \
             patch("app.scheduler.match_task_place", return_value={"address": "campus"}), \
             patch("app.scheduler._save_task"), \
             patch("app.scheduler._save_checkin", return_value=True), \
             patch("app.scheduler._delete_unsent_checkin_attempt") as delete_attempt:
            with self.assertRaises(UpstreamUnavailable):
                _process_task(client, account, task)
        delete_attempt.assert_called_once_with(account["id"], task.task_id)

    def test_ambiguous_submit_failure_is_locked_for_confirmation_only(self):
        task = AttendanceTask("task-1", "sign-1", "每日晚查寝", "", "", False, True)
        account = {"id": 7, "name": "student"}
        client = MagicMock()
        client.detail.return_value = {"signPlaceSelected": [{"address": "campus"}]}
        client.submit.side_effect = RuntimeError("Attendance API request failed")
        location = {
            "latitude": 28.1, "longitude": 115.8, "accuracy": 10,
            "observed_at": "2026-08-30T14:00:00+00:00", "address": "campus",
            "coordinate_system": "GCJ02",
        }
        with patch("app.scheduler._task_window_open", return_value=True), \
             patch("app.scheduler._latest_location", return_value=location), \
             patch("app.scheduler.verify_location", return_value=(True, "ok")), \
             patch("app.scheduler.normalize_for_task", return_value=(28.1, 115.8)), \
             patch("app.scheduler.match_task_place", return_value={"address": "campus"}), \
             patch("app.scheduler._save_task"), \
             patch("app.scheduler._save_checkin", return_value=True), \
             patch("app.scheduler._mark_checkin_unknown") as mark_unknown:
            with self.assertRaisesRegex(RuntimeError, "request failed"):
                _process_task(client, account, task)
        mark_unknown.assert_called_once_with(account["id"], task.task_id, client.submit.side_effect)

    def test_task_is_scheduled_fifteen_seconds_after_its_opening_time(self):
        start = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(minutes=10)
        task = AttendanceTask("task-1", "sign-1", "每日晚查寝", start.isoformat(), "", False, True)
        account = {"id": 7, "name": "student"}
        _scheduled_tasks.clear()
        timer = MagicMock()
        with patch("app.scheduler.threading.Timer", return_value=timer) as factory, patch("app.scheduler.log_event"):
            _schedule_task(account, task)
        delay = factory.call_args.args[0]
        self.assertGreater(delay, 605)
        self.assertLess(delay, 625)
        self.assertTrue(timer.daemon)
        timer.start.assert_called_once_with()
        _scheduled_tasks.clear()


if __name__ == "__main__":
    unittest.main()
