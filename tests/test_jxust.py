import io
import json
import os
import unittest
from unittest.mock import patch

from campus.attendance import AttendanceTask
from campus.jxust import JxustAttendanceClient


class FakeResponse:
    def __init__(self, value):
        self.value = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.value


class JxustClientTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "CPDAILY_BASE_URL": "https://fdm.jxust.edu.cn",
                "CPDAILY_SESSION_COOKIE": "session=test-only",
                "CPDAILY_SUBMIT_ENABLED": "false",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_list_today_parses_verified_shape(self):
        envelope = {
            "code": "0",
            "message": "SUCCESS",
            "datas": {
                "unSignedTasks": [{"signInstanceWid": "i1", "signWid": "s1", "taskName": "晚查寝", "singleTaskBeginTime": "start", "singleTaskEndTime": "end", "signStatus": 0}],
                "codeRcvdTasks": [], "signedTasks": [], "leaveTasks": [], "registerLeaveTasks": [],
            },
        }
        with patch("urllib.request.OpenerDirector.open", return_value=FakeResponse(envelope)):
            tasks = JxustAttendanceClient().list_today()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].task_id, "i1")
        self.assertEqual(tasks[0].sign_wid, "s1")
        self.assertFalse(tasks[0].completed)

    def test_detail_sends_only_verified_identifiers(self):
        task = AttendanceTask("i1", "s1", "晚查寝", "", "", False, True)
        envelope = {"code": "0", "message": "SUCCESS", "datas": {"signMode": 0}}
        with patch("urllib.request.OpenerDirector.open", return_value=FakeResponse(envelope)) as opened:
            result = JxustAttendanceClient().detail(task)
        request = opened.call_args.args[0]
        self.assertEqual(json.loads(request.data), {"signInstanceWid": "i1", "signWid": "s1"})
        self.assertEqual(result["signMode"], 0)

    def test_submit_is_off_by_default(self):
        task = AttendanceTask("i1", "s1", "晚查寝", "", "", False, True)
        with self.assertRaisesRegex(RuntimeError, "submission is disabled"):
            JxustAttendanceClient().submit(task, {"verified": True})

    def test_cookie_is_required(self):
        with patch.dict(os.environ, {"CPDAILY_SESSION_COOKIE": ""}):
            with self.assertRaisesRegex(RuntimeError, "SESSION_COOKIE"):
                JxustAttendanceClient()


if __name__ == "__main__":
    unittest.main()
