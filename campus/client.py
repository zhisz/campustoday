import os

from .attendance import AttendanceClient
from .jxust import JxustAttendanceClient


def create_client(session_cookie=None, device_profile=None, purpose="interactive") -> AttendanceClient:
    mode = os.getenv("CPDAILY_MODE", "disabled").strip().lower()
    if mode == "jxust":
        return JxustAttendanceClient(
            session_cookie=session_cookie,
            device_profile=device_profile,
            purpose=purpose,
        )
    if mode == "disabled":
        raise RuntimeError("CPDAILY integration is disabled")
    raise RuntimeError(f"Unsupported CPDAILY_MODE={mode!r}")
