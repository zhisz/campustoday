import unittest
import threading
from unittest.mock import MagicMock, patch

from app.dashboard import (
    _flatten_history,
    _school_data,
    _wait_for_school_refresh,
    invalidate_school_cache,
)


class DashboardHistoryTest(unittest.TestCase):
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

    def test_school_dashboard_requests_are_cached_per_account(self):
        account = {"id": 91, "session_cookie": "MOD_AUTH_CAS=test"}
        client = MagicMock()
        client.list_today.return_value = []
        client.month_history.return_value = {"rows": []}
        invalidate_school_cache(account["id"])
        with patch("app.dashboard.create_client", return_value=client) as create:
            with self.assertRaisesRegex(RuntimeError, "后台刷新"):
                _school_data(account, "2026-08")
            _wait_for_school_refresh(account["id"])
            _school_data(account, "2026-08")
        create.assert_called_once_with(account["session_cookie"])
        client.list_today.assert_called_once_with()
        client.month_history.assert_called_once_with("2026-08")

    def test_invalidating_a_queued_refresh_does_not_deadlock(self):
        gate = threading.Event()
        started = threading.Event()

        def blocked_fetch(account, year_month):
            if account["id"] == 201:
                started.set()
                gate.wait(timeout=2)
            return {"tasks": [], "history": {"rows": []}}

        accounts = [
            {"id": account_id, "session_cookie": f"MOD_AUTH_CAS={account_id}"}
            for account_id in (201, 202)
        ]
        for account in accounts:
            invalidate_school_cache(account["id"])
        with patch("app.dashboard._fetch_school_data", side_effect=blocked_fetch):
            with self.assertRaisesRegex(RuntimeError, "后台刷新"):
                _school_data(accounts[0], "2026-08")
            self.assertTrue(started.wait(timeout=1))
            with self.assertRaisesRegex(RuntimeError, "后台刷新"):
                _school_data(accounts[1], "2026-08")

            invalidator = threading.Thread(target=invalidate_school_cache, args=(202,))
            invalidator.start()
            invalidator.join(timeout=1)
            gate.set()
            self.assertFalse(invalidator.is_alive(), "queued refresh cancellation deadlocked")
            _wait_for_school_refresh(201)


if __name__ == "__main__":
    unittest.main()
