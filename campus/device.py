import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceProfile:
    device_id: str
    app_version: str
    model: str
    system_name: str
    system_version: str


def configured_device() -> DeviceProfile:
    """Device identity must come from a trusted enrolled device, never a random spoof."""
    values = {
        "device_id": os.getenv("CPDAILY_DEVICE_ID", "").strip(),
        "app_version": os.getenv("CPDAILY_APP_VERSION", "").strip(),
        "model": os.getenv("CPDAILY_DEVICE_MODEL", "").strip(),
        "system_name": os.getenv("CPDAILY_SYSTEM_NAME", "").strip(),
        "system_version": os.getenv("CPDAILY_SYSTEM_VERSION", "").strip(),
    }
    if not all(values.values()):
        raise RuntimeError("No complete trusted device profile has been enrolled")
    if any("\n" in value or "\r" in value for value in values.values()):
        raise RuntimeError("Trusted device profile contains invalid characters")
    return DeviceProfile(**values)
