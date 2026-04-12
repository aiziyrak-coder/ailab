"""
MedLab AI — API va xavfsizlik asoslari (regressiya testlari).
"""
import json

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings


class HealthEndpointTests(TestCase):
    """Monitoring — autentifikatsiyasiz."""

    def test_health_get_ok(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("service"), "medlab-ai")
        self.assertIn("version", data)
        self.assertIn("ziyrakai_ready", data)
        self.assertEqual(data.get("product"), "ZiyrakAi")
        self.assertTrue(data.get("database"))
        self.assertTrue(data.get("snapshot_dir_writable"))


class AuthRequiredTests(TestCase):
    """Himoyalangan marshrutlar."""

    def test_analyze_requires_login(self):
        r = self.client.post(
            "/api/analyze",
            data=json.dumps({"lab_type": "hematology", "source": "upload"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_analysis_result_requires_login(self):
        r = self.client.get("/api/analysis_result")
        self.assertEqual(r.status_code, 403)

    def test_auth_me_requires_login(self):
        r = self.client.get("/api/auth/me")
        self.assertEqual(r.status_code, 403)

    def test_auth_check_anonymous(self):
        r = self.client.get("/api/auth/check")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data.get("authenticated"))


class LoginApiTests(TestCase):
    """CSRF bilan login API."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        User.objects.create_user(username="audittest", password="TestPass2026!xx")

    def _csrf_post(self, path, payload):
        self.client.get("/login")
        token = self.client.cookies.get("csrftoken")
        self.assertIsNotNone(token, "CSRF cookie kerak")
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token.value,
        )

    def test_login_wrong_password(self):
        r = self._csrf_post(
            "/api/auth/login",
            {"username": "audittest", "password": "notthepassword"},
        )
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertFalse(data.get("success"))

    def test_login_success(self):
        r = self._csrf_post(
            "/api/auth/login",
            {"username": "audittest", "password": "TestPass2026!xx"},
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("user", {}).get("username"), "audittest")


class AnalyzeEdgeCaseTests(TestCase):
    """Tahlil so‘rovi — fayl / ZiyrakAi holatlari."""

    @override_settings(DEBUG=False)
    def test_analyze_json_without_files_not_500(self):
        """Faylsiz JSON: 400 yoki ZiyrakAi yo‘q bo‘lsa 503; tasodifiy 500 bo‘lmasligi kerak."""
        user = User.objects.create_user(username="puser", password="Pw2026!MedLabTest")
        client = Client(enforce_csrf_checks=True)
        client.get("/login")
        tok = client.cookies["csrftoken"].value
        client.post(
            "/api/auth/login",
            data=json.dumps({"username": "puser", "password": "Pw2026!MedLabTest"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=tok,
        )
        # Bo‘sh analyze — 400 (fayl yo‘q), 503 (ZiyrakAi yo‘q) yoki boshqa; 500 bo‘lmasligi kerak oddiy holatda
        r = client.post(
            "/api/analyze",
            data=json.dumps({"lab_type": "hematology", "source": "upload"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertIn(r.status_code, (400, 503))
        if r.status_code == 503:
            data = r.json()
            self.assertIn("ZIYRAKAI", data.get("message", "").upper())


class ApiHostUiRedirectTests(TestCase):
    """API domenida HTML sahifalar UI ga yo'naltiriladi."""

    _hosts = {"ALLOWED_HOSTS": ["ailabapi.example.com", "testserver", "localhost", "127.0.0.1"]}

    @override_settings(
        MEDLAB_PUBLIC_API_BASE="https://ailabapi.example.com",
        MEDLAB_PUBLIC_UI_BASE="https://ailab.example.com",
        MEDLAB_API_HOSTNAME="ailabapi.example.com",
        **_hosts,
    )
    def test_login_on_api_host_redirects(self):
        r = self.client.get("/login?next=/", HTTP_HOST="ailabapi.example.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "https://ailab.example.com/login?next=/")

    @override_settings(
        MEDLAB_PUBLIC_API_BASE="https://ailabapi.example.com",
        MEDLAB_PUBLIC_UI_BASE="https://ailab.example.com",
        MEDLAB_API_HOSTNAME="ailabapi.example.com",
        **_hosts,
    )
    def test_api_paths_not_redirected(self):
        r = self.client.get("/api/health", HTTP_HOST="ailabapi.example.com")
        self.assertEqual(r.status_code, 200)
