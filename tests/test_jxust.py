import io
import json
import os
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch

from campus.attendance import AttendanceTask
from campus.device import DeviceProfile
from campus.jxust import JxustAttendanceClient, ProtocolError, _reset_upstream_gate_for_tests


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
                "CPDAILY_MIN_REQUEST_INTERVAL_SECONDS": "0",
                "CPDAILY_UPSTREAM_BACKOFF_SECONDS": "10",
            },
            clear=False,
        )
        self.env.start()
        _reset_upstream_gate_for_tests()

    def tearDown(self):
        _reset_upstream_gate_for_tests()
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

    def test_identity_uses_portal_endpoint_and_returns_verified_name(self):
        envelope = {
            "code": "0",
            "message": "SUCCESS",
            "datas": {"hasLogin": True, "userName": "李尚智", "userId": "1247598866"},
        }
        with patch("urllib.request.OpenerDirector.open", return_value=FakeResponse(envelope)) as opened:
            identity = JxustAttendanceClient().identity()
        request = opened.call_args.args[0]
        self.assertTrue(request.full_url.endswith(JxustAttendanceClient.IDENTITY_PATH))
        self.assertEqual(request.data, b"")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded; charset=UTF-8")
        self.assertEqual(identity, {"user_name": "李尚智", "user_id": "1247598866"})

    def test_identity_rejects_anonymous_portal_response(self):
        envelope = {"code": "0", "message": "SUCCESS", "datas": {"hasLogin": False}}
        with patch("urllib.request.OpenerDirector.open", return_value=FakeResponse(envelope)):
            with self.assertRaisesRegex(ProtocolError, "not logged in"):
                JxustAttendanceClient().identity()

    def test_detail_sends_only_verified_identifiers(self):
        task = AttendanceTask("i1", "s1", "晚查寝", "", "", False, True)
        envelope = {"code": "0", "message": "SUCCESS", "datas": {"signMode": 0}}
        with patch("urllib.request.OpenerDirector.open", return_value=FakeResponse(envelope)) as opened:
            result = JxustAttendanceClient().detail(task)
        request = opened.call_args.args[0]
        self.assertEqual(json.loads(request.data), {"signInstanceWid": "i1", "signWid": "s1"})
        self.assertEqual(result["signMode"], 0)

    def test_month_history_uses_verified_endpoint(self):
        envelope = {"code": "0", "message": "SUCCESS", "datas": {"rows": [], "serverDate": "2026-08-29"}}
        with patch("urllib.request.OpenerDirector.open", return_value=FakeResponse(envelope)) as opened:
            result = JxustAttendanceClient().month_history("2026-08")
        request = opened.call_args.args[0]
        self.assertTrue(request.full_url.endswith(JxustAttendanceClient.HISTORY_PATH))
        self.assertEqual(json.loads(request.data), {"statisticYearMonth": "2026-08"})
        self.assertEqual(result["serverDate"], "2026-08-29")

    def test_month_history_rejects_invalid_month(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM"):
            JxustAttendanceClient().month_history("2026/08")

    def test_submit_is_off_by_default(self):
        task = AttendanceTask("i1", "s1", "晚查寝", "", "", False, True)
        with self.assertRaisesRegex(RuntimeError, "submission is disabled"):
            JxustAttendanceClient().submit(task, {"verified": True})

    def test_submit_uses_the_selected_accounts_device_profile(self):
        task = AttendanceTask("i1", "s1", "晚查寝", "", "", False, True)
        device = DeviceProfile("account-device", "9.9.6", "Account Model", "Android", "17")
        envelope = {"code": "0", "message": "SUCCESS", "datas": {}}
        with patch.dict(os.environ, {"CPDAILY_SUBMIT_ENABLED": "true"}), \
             patch("urllib.request.OpenerDirector.open", return_value=FakeResponse(envelope)) as opened:
            JxustAttendanceClient(device_profile=device).submit(
                task,
                {"verified": True, "latitude": 28.1, "longitude": 115.8, "address": "campus"},
            )
        payload = json.loads(opened.call_args.args[0].data)
        self.assertEqual(payload["deviceId"], "account-device")
        self.assertEqual(payload["model"], "Account Model")
        self.assertEqual(payload["systemName"], "Android")
        self.assertEqual(payload["systemVersion"], "17")

    def test_cookie_is_required(self):
        with patch.dict(os.environ, {"CPDAILY_SESSION_COOKIE": ""}):
            with self.assertRaisesRegex(RuntimeError, "SESSION_COOKIE"):
                JxustAttendanceClient()

    def test_background_requests_are_strictly_serialized_and_spaced(self):
        envelope = {
            "code": "0", "datas": {
                "unSignedTasks": [], "codeRcvdTasks": [], "signedTasks": [],
                "leaveTasks": [], "registerLeaveTasks": [],
            },
        }
        starts = []
        active = 0
        max_active = 0
        state_lock = threading.Lock()

        def opened(*_args, **_kwargs):
            nonlocal active, max_active
            with state_lock:
                starts.append(time.monotonic())
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with state_lock:
                active -= 1
            return FakeResponse(envelope)

        errors = []
        def request():
            try:
                JxustAttendanceClient(purpose="background").list_today()
            except Exception as exc:
                errors.append(exc)

        with patch.dict(os.environ, {"CPDAILY_MIN_REQUEST_INTERVAL_SECONDS": "0.05"}), \
             patch("urllib.request.OpenerDirector.open", side_effect=opened):
            threads = [threading.Thread(target=request) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=1)
        self.assertEqual(errors, [])
        self.assertEqual(max_active, 1)
        self.assertGreaterEqual(starts[1] - starts[0], 0.065)

    def test_interactive_request_fails_fast_while_gate_is_busy(self):
        envelope = {
            "code": "0", "datas": {
                "unSignedTasks": [], "codeRcvdTasks": [], "signedTasks": [],
                "leaveTasks": [], "registerLeaveTasks": [],
            },
        }
        entered = threading.Event()
        release = threading.Event()

        def opened(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=1)
            return FakeResponse(envelope)

        with patch("urllib.request.OpenerDirector.open", side_effect=opened):
            worker = threading.Thread(
                target=lambda: JxustAttendanceClient(purpose="background").list_today()
            )
            worker.start()
            self.assertTrue(entered.wait(timeout=1))
            with self.assertRaisesRegex(ProtocolError, "限流"):
                JxustAttendanceClient(purpose="interactive").list_today()
            release.set()
            worker.join(timeout=1)
        self.assertFalse(worker.is_alive())

    def test_transport_failure_opens_circuit_without_a_second_network_call(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=urllib.error.URLError("down")) as opened:
            with self.assertRaisesRegex(ProtocolError, "request failed"):
                JxustAttendanceClient(purpose="background").list_today()
            with self.assertRaisesRegex(ProtocolError, "熔断"):
                JxustAttendanceClient(purpose="background").list_today()
        self.assertEqual(opened.call_count, 1)


if __name__ == "__main__":
    unittest.main()
