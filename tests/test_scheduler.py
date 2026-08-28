import os
import unittest
from unittest.mock import MagicMock, patch

from app.scheduler import poll


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


if __name__ == "__main__":
    unittest.main()
