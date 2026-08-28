import os

from .attendance import AttendanceClient
from .jxust import JxustAttendanceClient


def create_client() -> AttendanceClient:
    mode = os.getenv("CPDAILY_MODE", "disabled").strip().lower()
    if mode == "jxust":
        return JxustAttendanceClient()
    if mode == "disabled":
        raise RuntimeError("CPDAILY integration is disabled")
    raise RuntimeError(f"Unsupported CPDAILY_MODE={mode!r}")
