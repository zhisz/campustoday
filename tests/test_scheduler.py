import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app.scheduler import _schedule_task, _scheduled_tasks, poll
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
             patch("app.scheduler.list_accounts", return_value=accounts), \
             patch("app.scheduler.create_client", return_value=client) as create, \
             patch("app.scheduler.log_event"):
            poll()
        create.assert_called_once_with("a=1")
        client.list_today.assert_called_once_with()

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
