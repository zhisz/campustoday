import math
import os
from datetime import datetime, timezone


def verify_location(latitude: float, longitude: float, observed_at: str, accuracy: float = 0):
    mode = os.getenv("LOCATION_MODE", "trusted_device")
    if mode == "disabled":
        return False, "LOCATION_DISABLED"
    if mode != "trusted_device":
        return False, "UNKNOWN_LOCATION_MODE"
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return False, "TIMESTAMP_MUST_HAVE_TIMEZONE"
        age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        if age < -30:
            return False, "LOCATION_FROM_FUTURE"
        if age > int(os.getenv("LOCATION_MAX_AGE_SECONDS", "300")):
            return False, "LOCATION_STALE"
        if accuracy < 0 or accuracy > float(os.getenv("LOCATION_MAX_ACCURACY_METERS", "100")):
            return False, "LOCATION_ACCURACY_TOO_LOW"
    except ValueError:
        return False, "INVALID_LOCATION_PROOF"
    return True, "FRESH_DEVICE_LOCATION"


def match_task_place(latitude: float, longitude: float, places):
    """Return the matching institution-provided task place, never a fixed fallback."""
    if not isinstance(places, list):
        return None
    matches = []
    for place in places:
        if not isinstance(place, dict):
            continue
        try:
            place_lat = float(place["latitude"])
            place_lon = float(place["longitude"])
            radius = float(place["radius"])
            if radius <= 0:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        distance = haversine(latitude, longitude, place_lat, place_lon)
        if distance <= radius:
            matches.append((distance, place))
    return min(matches, key=lambda item: item[0])[1] if matches else None


def normalize_for_task(latitude: float, longitude: float, coordinate_system: str):
    if coordinate_system == "gcj02":
        return latitude, longitude
    if coordinate_system == "wgs84":
        return wgs84_to_gcj02(latitude, longitude)
    raise ValueError("Unsupported coordinate system")


def wgs84_to_gcj02(latitude: float, longitude: float):
    if not (0.8293 <= latitude <= 55.8271 and 72.004 <= longitude <= 137.8347):
        return latitude, longitude
    a, ee = 6378245.0, 0.006693421622965943
    dlat = _transform_lat(longitude - 105.0, latitude - 35.0)
    dlon = _transform_lon(longitude - 105.0, latitude - 35.0)
    radlat = latitude / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (a / sqrt_magic * math.cos(radlat) * math.pi)
    return latitude + dlat, longitude + dlon


def _transform_lat(x, y):
    value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    return value + (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0


def _transform_lon(x, y):
    value = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    return value + (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0


def haversine(lat1, lon1, lat2, lon2):
    earth = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * earth * math.atan2(math.sqrt(a), math.sqrt(1 - a))
