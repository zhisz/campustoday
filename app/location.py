import math
import os
from datetime import datetime, timezone


def verify_location(latitude: float, longitude: float, observed_at: str):
    mode = os.getenv("LOCATION_MODE", "trusted_device")
    if mode == "disabled":
        return False, "LOCATION_DISABLED"
    if mode not in {"trusted_device", "manual_verified"}:
        return False, "UNKNOWN_LOCATION_MODE"
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return False, "TIMESTAMP_MUST_HAVE_TIMEZONE"
        age = abs((datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds())
        if age > int(os.getenv("LOCATION_MAX_AGE_SECONDS", "300")):
            return False, "LOCATION_STALE"
        center_lat = float(os.environ["GEOFENCE_LAT"])
        center_lon = float(os.environ["GEOFENCE_LON"])
        radius = float(os.getenv("GEOFENCE_RADIUS_METERS", "1000"))
    except (KeyError, ValueError):
        return False, "GEOFENCE_NOT_CONFIGURED"
    distance = haversine(latitude, longitude, center_lat, center_lon)
    return (True, "VERIFIED_ON_CAMPUS") if distance <= radius else (False, "OUTSIDE_GEOFENCE")


def haversine(lat1, lon1, lat2, lon2):
    earth = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * earth * math.atan2(math.sqrt(a), math.sqrt(1 - a))

