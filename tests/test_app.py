import os
import json
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(f"{self.tmp.name}/apks")
        os.environ.update(APP_SECRET="test-secret", ADMIN_USERNAME="admin", ADMIN_PASSWORD="a-secure-test-password", DATABASE_PATH=f"{self.tmp.name}/test.db", APKS_PATH=f"{self.tmp.name}/apks", CPDAILY_MODE="disabled", CPDAILY_SESSION_COOKIE="", AUTO_ENABLED="false", LOCATION_MODE="trusted_device", LOCATION_PROOF_TOKEN="proof-secret", LOCATION_MAX_AGE_SECONDS="300", LOCATION_MAX_ACCURACY_METERS="100", CPDAILY_DEVICE_ID="device-default", CPDAILY_APP_VERSION="9.9.6", CPDAILY_DEVICE_MODEL="Default Model", CPDAILY_SYSTEM_NAME="Android", CPDAILY_SYSTEM_VERSION="16")
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
        for label in ("今日及即将开始", "最近设备位置", "签到记录", "+添加签到账号", "自动签到成功"):
            self.assertIn(label, page)
    def test_bad_login(self):
        response = self.client.post("/login", data={"csrf": self.csrf(), "username": "admin", "password": "wrong"})
        self.assertEqual(response.status_code, 200)

    def test_campus_account_can_be_added_updated_and_deleted_without_echoing_cookie(self):
        self.login()
        with self.client.session_transaction() as current_session:
            token = current_session["csrf"]
        cookie_value = "private-test-cookie"
        cookie = f"MOD_AUTH_CAS={cookie_value}"
        campus_client = MagicMock()
        campus_client.identity.return_value = {"user_name": "李尚智", "user_id": "1247598866"}
        with patch("app.campus_accounts.create_client", return_value=campus_client):
            response = self.client.post("/accounts", data={"csrf": token, "name": "测试账号", "session_cookie": cookie_value, "auto_enabled": "true"})
        self.assertEqual(response.status_code, 302)
        page = self.client.get("/accounts").get_data(as_text=True)
        self.assertIn("李尚智", page)
        self.assertIn("已通过学校门户验证，不可修改", page)
        self.assertIn("readonly", page)
        self.assertNotIn(cookie, page)
        from app.db import connect
        with connect() as db:
            account = db.execute("SELECT id,name,real_name,campus_user_id,auto_enabled FROM campus_accounts").fetchone()
        self.assertEqual((account["name"], account["real_name"], account["campus_user_id"]), ("李尚智", "李尚智", "1247598866"))
        cooldown = self.client.post(f"/accounts/{account['id']}/check", data={"csrf": token}, follow_redirects=True)
        self.assertIn("60 秒内的最近结果", cooldown.get_data(as_text=True))
        self.client.post(f"/accounts/{account['id']}/update", data={"csrf": token, "name": "新名称", "session_cookie": "", "auto_enabled": "false", "device_id": "device-new", "device_model": "New Model", "system_name": "Android", "system_version": "17"})
        with connect() as db:
            updated = db.execute("SELECT name,auto_enabled,session_cookie,device_id,device_model,system_version FROM campus_accounts WHERE id=?", (account["id"],)).fetchone()
        self.assertEqual((updated["name"], updated["auto_enabled"], updated["session_cookie"]), ("李尚智", 0, cookie))
        self.assertEqual((updated["device_id"], updated["device_model"], updated["system_version"]), ("device-new", "New Model", "17"))
        toggle = self.client.post(f"/accounts/{account['id']}/toggle", data={"csrf": token, "enabled": "true"})
        self.assertEqual(toggle.status_code, 302)
        with connect() as db:
            self.assertEqual(db.execute("SELECT auto_enabled FROM campus_accounts WHERE id=?", (account["id"],)).fetchone()[0], 1)
        dashboard = self.client.get(f"/dashboard?account={account['id']}").get_data(as_text=True)
        self.assertIn("刷新并检测会话", dashboard)
        self.assertIn("关闭自动签到", dashboard)
        self.assertIn("签到方式", dashboard)
        self.client.post(f"/accounts/{account['id']}/delete", data={"csrf": token})
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campus_accounts").fetchone()[0], 0)

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

    def test_mobile_users_only_see_their_own_accounts(self):
        def register(username):
            response = self.client.post("/api/v1/auth/register", json={"username": username, "password": "strong-pass-123"})
            self.assertEqual(response.status_code, 201)
            return {"Authorization": f"Bearer {response.json['token']}"}

        first, second = register("first_user"), register("second_user")
        campus_client = MagicMock()
        campus_client.identity.return_value = {"user_name": "李尚智", "user_id": "student-1"}
        payload = {
            "session_cookie": "private-campus-cookie",
            "device": {"device_id": "android-id", "app_version": "1.0.0", "model": "Pixel", "system_name": "Android", "system_version": "16"},
        }
        with patch("app.campus_accounts.create_client", return_value=campus_client):
            created = self.client.post("/api/v1/accounts", json=payload, headers=first)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["account"]["name"], "李尚智")
        self.assertFalse(created.json["account"]["auto_enabled"])
        account_id = created.json["account"]["id"]
        self.assertEqual(len(self.client.get("/api/v1/accounts", headers=first).json["accounts"]), 1)
        self.assertEqual(self.client.get("/api/v1/accounts", headers=second).json["accounts"], [])
        self.assertEqual(self.client.get(f"/api/v1/accounts/{account_id}", headers=second).status_code, 404)
        toggled = self.client.patch(f"/api/v1/accounts/{account_id}", json={"auto_enabled": True}, headers=first)
        self.assertTrue(toggled.json["account"]["auto_enabled"])

    def test_mobile_release_metadata_and_download(self):
        apk = f"{self.tmp.name}/apks/campustoday-1.0.0.apk"
        with open(apk, "wb") as handle:
            handle.write(b"test-apk")
        releases = [{"version_code": 1, "version_name": "1.0.0", "filename": "campustoday-1.0.0.apk", "sha256": "abc", "size_label": "8 B", "release_notes": "first", "mandatory": False}]
        with open(f"{self.tmp.name}/apks/releases.json", "w", encoding="utf-8") as handle:
            json.dump(releases, handle)
        version = self.client.get("/api/v1/app/version")
        self.assertEqual(version.status_code, 200)
        self.assertTrue(version.json["download_url"].endswith("/download/campustoday-1.0.0.apk"))
        proxied = self.client.get("/api/v1/app/version", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "campustoday.example"})
        self.assertEqual(proxied.json["download_url"], "https://campustoday.example/download/campustoday-1.0.0.apk")
        self.assertEqual(self.client.get("/download/campustoday-1.0.0.apk").data, b"test-apk")
        self.assertIn("下载最新版 APK", self.client.get("/app").get_data(as_text=True))
        self.assertIn("https://github.com/zhisz/campustoday", self.client.get("/app").get_data(as_text=True))

    def test_announcements_and_nonanonymous_feedback(self):
        registered = self.client.post("/api/v1/auth/register", json={"username": "feedback_user", "password": "strong-pass-123"})
        headers = {"Authorization": f"Bearer {registered.json['token']}"}
        self.login()
        with self.client.session_transaction() as current_session:
            csrf = current_session["csrf"]
        published = self.client.post("/announcements", data={"csrf": csrf, "title": "维护通知", "content": "今晚服务更新"})
        self.assertEqual(published.status_code, 302)
        notices = self.client.get("/api/v1/announcements", headers=headers).json["announcements"]
        self.assertEqual(notices[0]["title"], "维护通知")
        self.assertFalse(notices[0]["is_read"])
        self.assertEqual(self.client.post(f"/api/v1/announcements/{notices[0]['id']}/read", headers=headers).status_code, 200)
        self.assertTrue(self.client.get("/api/v1/announcements", headers=headers).json["announcements"][0]["is_read"])
        response = self.client.post("/api/v1/feedback", json={"category": "界面建议", "content": "希望按钮更明显"}, headers=headers)
        self.assertEqual(response.status_code, 201)
        page = self.client.get("/feedback").get_data(as_text=True)
        self.assertIn("feedback_user", page)
        self.assertIn("希望按钮更明显", page)

    def test_mobile_user_claims_matching_legacy_account_without_duplicate(self):
        from app.campus_accounts import check_session, create_account
        campus_client = MagicMock()
        campus_client.identity.return_value = {"user_name": "李尚智", "user_id": "same-student"}
        with patch("app.campus_accounts.create_client", return_value=campus_client):
            legacy_id = create_account("旧账号", "legacy-cookie", True)
            check_session(legacy_id)
        registered = self.client.post("/api/v1/auth/register", json={"username": "claim_user", "password": "strong-pass-123"})
        headers = {"Authorization": f"Bearer {registered.json['token']}"}
        payload = {"session_cookie": "new-cookie", "device": {"device_id": "phone-id", "app_version": "1.0.0", "model": "V2408A", "system_name": "Android", "system_version": "15"}}
        with patch("app.campus_accounts.create_client", return_value=campus_client):
            created = self.client.post("/api/v1/accounts", json=payload, headers=headers)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["account"]["id"], legacy_id)
        self.assertFalse(created.json["account"]["auto_enabled"])
        from app.db import connect
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campus_accounts WHERE campus_user_id='same-student'").fetchone()[0], 1)
            claimed = db.execute("SELECT owner_user_id,session_cookie,device_id FROM campus_accounts WHERE id=?", (legacy_id,)).fetchone()
        self.assertIsNotNone(claimed["owner_user_id"])
        self.assertEqual((claimed["session_cookie"], claimed["device_id"]), ("MOD_AUTH_CAS=new-cookie", "phone-id"))


if __name__ == "__main__": unittest.main()
