import os

from .attendance import AttendanceClient


def create_client() -> AttendanceClient:
    mode = os.getenv("CPDAILY_MODE", "disabled").strip().lower()
    if mode != "disabled":
        raise RuntimeError(f"Unsupported CPDAILY_MODE={mode!r}; current protocol is not verified")
    raise RuntimeError("CPDAILY integration is disabled")

