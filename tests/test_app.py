import os
import tempfile
import unittest


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.update(APP_SECRET="test-secret", ADMIN_USERNAME="admin", ADMIN_PASSWORD="a-secure-test-password", DATABASE_PATH=f"{self.tmp.name}/test.db", CPDAILY_MODE="disabled", AUTO_ENABLED="false")
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
    def test_bad_login(self):
        response = self.client.post("/login", data={"csrf": self.csrf(), "username": "admin", "password": "wrong"})
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__": unittest.main()

