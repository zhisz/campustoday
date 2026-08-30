import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app.scheduler import _process_task, _schedule_task, _scheduled_tasks, poll
from campus.attendance import AttendanceTask


class MultiAccountSchedulerTest(unittest.TestCase):
    def test_poll_only_creates_clients_for_enabled_accounts(self):
        accounts = [
            {"id": 1, "name": "enabled", "session_cookie": "a=1", "auto_enabled": 1},
            {"id": 2, "name": "disabled", "session_cookie": "b=2", "auto_enabled": 0},
        ]
        client = MagicMock()
        client.list_today.return_value = []
        with patch.dict(os.environ, {"CPDAILY_SUBMIT_ENABLED": "false"}), \
             patch("app.scheduler.set_settings"), \
             patch("app.scheduler.enabled", return_value=True), \
             patch("app.scheduler.monitoring_window", return_value=True), \
             patch("app.scheduler._upstream_ready", return_value=True), \
             patch("app.scheduler.list_accounts", return_value=accounts), \
             patch("app.scheduler.account_device", return_value="device-profile") as device, \
             patch("app.scheduler.create_client", return_value=client) as create, \
             patch("app.scheduler.log_event"):
            poll()
        create.assert_called_once_with("a=1", "device-profile")
        device.assert_called_once_with(accounts[0])
        client.list_today.assert_called_once_with()

    def test_poll_stops_after_the_first_transport_failure(self):
        accounts = [
            {"id": 1, "name": "first", "session_cookie": "a=1", "auto_enabled": 1},
            {"id": 2, "name": "second", "session_cookie": "b=2", "auto_enabled": 1},
        ]
        client = MagicMock()
        client.list_today.side_effect = RuntimeError("Attendance API request failed")
        with patch("app.scheduler.set_settings"), \
             patch("app.scheduler.enabled", return_value=True), \
             patch("app.scheduler.monitoring_window", return_value=True), \
             patch("app.scheduler._upstream_ready", return_value=True), \
             patch("app.scheduler._defer_upstream") as defer, \
             patch("app.scheduler.list_accounts", return_value=accounts), \
             patch("app.scheduler.account_device", return_value="device-profile"), \
             patch("app.scheduler.create_client", return_value=client) as create, \
             patch("app.scheduler.log_event"):
            poll()
        self.assertEqual(create.call_count, 1)
        defer.assert_called_once_with()

    def test_successful_submit_is_saved_before_confirmation(self):
        task = AttendanceTask("task-1", "sign-1", "每日晚查寝", "", "", False, True)
        account = {"id": 7, "name": "student"}
        client = MagicMock()
        client.detail.return_value = {"signPlaceSelected": [{"address": "campus"}]}
        client.submit.return_value = {"accepted": True}
        client.list_today.side_effect = RuntimeError("Attendance API request failed")
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
             patch("app.scheduler._save_checkin") as save_checkin, \
             patch("app.scheduler.log_event"):
            result = _process_task(client, account, task)
        self.assertEqual(result, {"accepted": True})
        save_checkin.assert_called_once_with(
            account, task, "SUBMITTED_UNCONFIRMED", "Submission accepted; confirmation pending"
        )

    def test_task_is_scheduled_one_minute_after_its_opening_time(self):
        start = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(minutes=10)
        task = AttendanceTask("task-1", "sign-1", "每日晚查寝", start.isoformat(), "", False, True)
        account = {"id": 7, "name": "student"}
        _scheduled_tasks.clear()
        timer = MagicMock()
        with patch("app.scheduler.threading.Timer", return_value=timer) as factory, patch("app.scheduler.log_event"):
            _schedule_task(account, task)
        delay = factory.call_args.args[0]
        self.assertGreater(delay, 650)
        self.assertLess(delay, 670)
        self.assertTrue(timer.daemon)
        timer.start.assert_called_once_with()
        _scheduled_tasks.clear()


if __name__ == "__main__":
    unittest.main()
