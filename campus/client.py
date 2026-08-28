import os

from .attendance import AttendanceClient
from .jxust import JxustAttendanceClient


def create_client(session_cookie=None) -> AttendanceClient:
    mode = os.getenv("CPDAILY_MODE", "disabled").strip().lower()
    if mode == "jxust":
        return JxustAttendanceClient(session_cookie=session_cookie)
    if mode == "disabled":
        raise RuntimeError("CPDAILY integration is disabled")
    raise RuntimeError(f"Unsupported CPDAILY_MODE={mode!r}")
