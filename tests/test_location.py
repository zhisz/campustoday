import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.location import match_task_place, normalize_for_task, verify_location
from app.scheduler import _task_window_open


class LocationRulesTest(unittest.TestCase):
    def test_fresh_location_is_accepted_without_static_geofence(self):
        with patch.dict(os.environ, {"LOCATION_MODE": "trusted_device", "LOCATION_MAX_AGE_SECONDS": "300", "LOCATION_MAX_ACCURACY_METERS": "100"}):
            accepted, reason = verify_location(28.1, 115.8, datetime.now(timezone.utc).isoformat(), 25)
        self.assertTrue(accepted)
        self.assertEqual(reason, "FRESH_DEVICE_LOCATION")

    def test_stale_location_is_rejected(self):
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        with patch.dict(os.environ, {"LOCATION_MODE": "trusted_device", "LOCATION_MAX_AGE_SECONDS": "300"}):
            accepted, reason = verify_location(28.1, 115.8, stale.isoformat(), 25)
        self.assertFalse(accepted)
        self.assertEqual(reason, "LOCATION_STALE")

    def test_task_place_match_uses_server_radius(self):
        places = [{"latitude": 28.1, "longitude": 115.8, "radius": 200, "address": "campus"}]
        self.assertEqual(match_task_place(28.1001, 115.8001, places)["address"], "campus")
        self.assertIsNone(match_task_place(29.1, 116.8, places))

    def test_task_window_must_be_open(self):
        detail = {
            "currentTime": "2026-08-29T21:00:00+08:00",
            "singleTaskBeginTime": "2026-08-29T20:00:00+08:00",
            "singleTaskEndTime": "2026-08-29T22:00:00+08:00",
        }
        self.assertTrue(_task_window_open(detail))
        detail["currentTime"] = "2026-08-29T23:00:00+08:00"
        self.assertFalse(_task_window_open(detail))

    def test_wgs84_is_normalized_and_gcj02_is_preserved(self):
        original = (28.1, 115.8)
        converted = normalize_for_task(*original, "wgs84")
        self.assertNotEqual(converted, original)
        self.assertEqual(normalize_for_task(*original, "gcj02"), original)


if __name__ == "__main__":
    unittest.main()
