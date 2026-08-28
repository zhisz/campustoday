import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.update(APP_SECRET="test-secret", ADMIN_USERNAME="admin", ADMIN_PASSWORD="a-secure-test-password", DATABASE_PATH=f"{self.tmp.name}/test.db", CPDAILY_MODE="disabled", AUTO_ENABLED="false", LOCATION_MODE="trusted_device", LOCATION_PROOF_TOKEN="proof-secret", LOCATION_MAX_AGE_SECONDS="300", LOCATION_MAX_ACCURACY_METERS="100")
        from app import create_app
        self.app = create_app(); self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = self.app.test_client()

    def tearDown(self): self.tmp.cleanup()

    def csrf(self):
        self.client.get("/login")
        with self.client.session_transaction() as session: return session["csrf"]

    def login(self): return self.client.post("/login", data={"csrf": self.csrf(), "username": "admin", "password": "a-secure-test-password"})

    def test_health(self): self.assertEqual(self.client.get("/health").json, {"status": "ok"})
    def test_login_and_status(self):
        self.assertEqual(self.login().status_code, 302)
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200); self.assertEqual(response.json["database"], "ok")
    def test_dashboard_contains_operational_sections_when_school_api_is_offline(self):
        self.login()
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for label in ("今日及即将开始", "最近设备位置", "学校签到记录", "当前签到账号", "自动签到成功"):
            self.assertIn(label, page)
    def test_bad_login(self):
        response = self.client.post("/login", data={"csrf": self.csrf(), "username": "admin", "password": "wrong"})
        self.assertEqual(response.status_code, 200)

    def test_location_proof_accepts_fresh_position_and_rejects_replay(self):
        payload = {
            "proof_id": str(uuid.uuid4()),
            "latitude": 28.1,
            "longitude": 115.8,
            "accuracy": 20,
            "coordinate_system": "wgs84",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        headers = {"Authorization": "Bearer proof-secret"}
        response = self.client.post("/api/location/proof", json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["accepted"])
        replay = self.client.post("/api/location/proof", json=payload, headers=headers)
        self.assertEqual(replay.status_code, 409)

    def test_location_proof_rejects_bad_token_and_low_accuracy(self):
        payload = {
            "proof_id": str(uuid.uuid4()),
            "latitude": 28.1,
            "longitude": 115.8,
            "accuracy": 150,
            "coordinate_system": "wgs84",
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.assertEqual(self.client.post("/api/location/proof", json=payload).status_code, 401)
        response = self.client.post("/api/location/proof", json=payload, headers={"Authorization": "Bearer proof-secret"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json["reason"], "LOCATION_ACCURACY_TOO_LOW")


if __name__ == "__main__": unittest.main()
