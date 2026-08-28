import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

from .attendance import AttendanceClient, AttendanceTask
from .device import configured_device


class ProtocolError(RuntimeError):
    pass


class JxustAttendanceClient(AttendanceClient):
    """Verified 2026 JXUST attendance API adapter.

    Authentication is a user-provided current session cookie. The adapter never
    logs it and refuses redirects so credentials cannot be forwarded elsewhere.
    """

    LIST_PATH = "/wec-counselor-attendance-apps/student/attendance/getStuAttendacesInOneDay"
    DETAIL_PATH = "/wec-counselor-attendance-apps/student/attendance/detailSignInstance"
    HISTORY_PATH = "/wec-counselor-attendance-apps/student/attendance/getStuSignInfosByWeekMonth"
    SUBMIT_PATH = "/wec-counselor-attendance-apps/student/attendance/submitSign"

    def __init__(self, session_cookie: Optional[str] = None):
        self.base_url = os.getenv("CPDAILY_BASE_URL", "https://fdm.jxust.edu.cn").rstrip("/")
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path:
            raise RuntimeError("CPDAILY_BASE_URL must be an HTTPS origin")
        self.allowed_host = parsed.hostname
        self.cookie = (session_cookie if session_cookie is not None else os.getenv("CPDAILY_SESSION_COOKIE", "")).strip()
        if not self.cookie:
            raise RuntimeError("CPDAILY_SESSION_COOKIE is not configured")
        if "\n" in self.cookie or "\r" in self.cookie:
            raise RuntimeError("CPDAILY_SESSION_COOKIE contains invalid characters")
        self.timeout = max(3, min(int(os.getenv("CPDAILY_TIMEOUT_SECONDS", "15")), 60))

    def list_today(self) -> list[AttendanceTask]:
        datas = self._post(self.LIST_PATH)
        if not isinstance(datas, dict):
            raise ProtocolError("Task list response has an unexpected shape")
        tasks = []
        groups = (
            ("unSignedTasks", False),
            ("codeRcvdTasks", False),
            ("signedTasks", True),
            ("leaveTasks", True),
            ("registerLeaveTasks", True),
        )
        for group, completed in groups:
            items = datas.get(group) or []
            if not isinstance(items, list):
                raise ProtocolError(f"Task list field {group} has an unexpected shape")
            for item in items:
                if not isinstance(item, dict):
                    continue
                instance = str(item.get("signInstanceWid") or "").strip()
                sign_wid = str(item.get("signWid") or "").strip()
                if not instance or not sign_wid:
                    continue
                tasks.append(
                    AttendanceTask(
                        task_id=instance,
                        sign_wid=sign_wid,
                        name=str(item.get("taskName") or ""),
                        start_time=str(item.get("singleTaskBeginTime") or ""),
                        end_time=str(item.get("singleTaskEndTime") or ""),
                        completed=completed,
                        requires_location=True,
                        status=str(item.get("signStatus") or group),
                    )
                )
        return tasks

    def detail(self, task: AttendanceTask) -> dict:
        result = self._post(
            self.DETAIL_PATH,
            {"signInstanceWid": task.task_id, "signWid": task.sign_wid},
        )
        if not isinstance(result, dict):
            raise ProtocolError("Task detail response has an unexpected shape")
        return result

    def month_history(self, year_month: Optional[str] = None) -> dict:
        year_month = year_month or datetime.now().strftime("%Y-%m")
        try:
            datetime.strptime(year_month, "%Y-%m")
        except ValueError:
            raise ValueError("year_month must use YYYY-MM format") from None
        result = self._post(self.HISTORY_PATH, {"statisticYearMonth": year_month})
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            raise ProtocolError("Attendance history response has an unexpected shape")
        return result

    def submit(self, task: AttendanceTask, location: dict) -> dict:
        if os.getenv("CPDAILY_SUBMIT_ENABLED", "false").strip().lower() != "true":
            raise RuntimeError("CPDAILY submission is disabled")
        if location.get("verified") is not True:
            raise RuntimeError("A verified fresh device location is required")
        device = configured_device()
        required = ("latitude", "longitude", "address")
        if any(location.get(key) in (None, "") for key in required):
            raise RuntimeError("Verified location is incomplete")
        payload = {
            "longitude": str(location["longitude"]),
            "latitude": str(location["latitude"]),
            "isMalposition": int(bool(location.get("is_malposition", False))),
            "abnormalReason": str(location.get("abnormal_reason") or ""),
            "signPhotoUrl": str(location.get("photo_url") or ""),
            "position": str(location["address"]),
            "ticket": str(location.get("ticket") or ""),
            "uaIsCpadaily": False,
            "signInstanceWid": task.task_id,
            "deviceId": device.device_id,
            "systemName": device.system_name,
            "systemVersion": device.system_version,
            "model": device.model,
        }
        if location.get("qr_uuid"):
            payload["qrUuid"] = str(location["qr_uuid"])
        result = self._post(self.SUBMIT_PATH, payload)
        return result if isinstance(result, dict) else {"result": result}

    def _post(self, path: str, payload=None):
        target = self.base_url + path
        if urllib.parse.urlsplit(target).hostname != self.allowed_host:
            raise RuntimeError("Refusing to send credentials to an unexpected host")
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        request = urllib.request.Request(
            target,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json;charset=UTF-8",
                "Cookie": self.cookie,
                "Origin": self.base_url,
                "Referer": self.base_url + "/",
                "User-Agent": os.getenv(
                    "CPDAILY_USER_AGENT",
                    "Mozilla/5.0 (Linux; Android) AppleWebKit/537.36 Mobile Safari/537.36",
                ),
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_000)
        except urllib.error.HTTPError as exc:
            raise ProtocolError(f"Attendance API returned HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise ProtocolError("Attendance API request failed") from exc
        try:
            envelope = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProtocolError("Attendance API returned invalid JSON") from None
        if not isinstance(envelope, dict) or str(envelope.get("code")) != "0":
            raise ProtocolError("Attendance API returned a business error")
        return envelope.get("datas")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
