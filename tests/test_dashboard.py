import unittest

from app.dashboard import _flatten_history


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


if __name__ == "__main__":
    unittest.main()
