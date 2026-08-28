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
    raise RuntimeError("No trusted device has been enrolled")

