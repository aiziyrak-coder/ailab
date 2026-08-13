"""
MedLab AI — API va xavfsizlik asoslari (regressiya testlari).
"""
import json

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from api.models import AnalysisRecord


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
        self.assertEqual(data.get("product"), "MedLab")
        self.assertTrue(data.get("database"))
        self.assertTrue(data.get("snapshot_dir_writable"))
        self.assertIn("env", data)
        self.assertIn("Content-Security-Policy", r)

    def test_health_alias_unauthenticated(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))


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

    def test_analyses_requires_login(self):
        r = self.client.get("/api/analyses")
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
            self.assertIn("MEDLAB", data.get("message", "").upper())


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


class AnalysisHistoryTests(TestCase):
    """Tahlillar tarixi — unikal ID va qidiruv."""

    def setUp(self):
        self.user = User.objects.create_user(username="histuser", password="Pw2026!MedLabTest")
        self.other = User.objects.create_user(username="otheru", password="Pw2026!MedLabTest")
        self.client.login(username="histuser", password="Pw2026!MedLabTest")

    def tearDown(self):
        from lab_core import engine as eng

        with eng.analysis_lock:
            eng.latest_analysis.update(
                {
                    "text": "",
                    "lines": [],
                    "timestamp": "",
                    "status": "kutilmoqda",
                    "loading": False,
                    "lab_type": "",
                    "job_id": "",
                    "public_id": "",
                    "user_id": None,
                    "img_count": 0,
                }
            )
            eng._completed_jobs.clear()

    def test_ids_increment_and_format(self):
        a = AnalysisRecord.create_pending(self.user, "hematology", "upload")
        b = AnalysisRecord.create_pending(self.user, "urine", "camera")
        self.assertTrue(a.public_id.startswith("ML-"))
        self.assertNotEqual(a.public_id, b.public_id)
        self.assertRegex(a.public_id, r"^ML-\d{6}-\d{4}$")

    def test_search_by_id_and_isolation(self):
        rec = AnalysisRecord.create_pending(self.user, "hematology", "upload", "job1")
        rec.text = "| WBC | 12 |"
        rec.status = "tayyor"
        rec.save()

        r = self.client.get("/api/analyses", {"q": rec.public_id})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("count"), 1)
        self.assertEqual(data["results"][0]["public_id"], rec.public_id)
        self.assertNotIn("text", data["results"][0])
        self.assertIn("preview", data["results"][0])

        compact = rec.public_id.replace("-", "")
        r2 = self.client.get("/api/analyses", {"q": compact.lower()})
        self.assertEqual(r2.json().get("count"), 1)

        d = self.client.get(f"/api/analyses/{rec.public_id}")
        self.assertEqual(d.status_code, 200)
        self.assertIn("WBC", d.json()["analysis"]["text"])

        self.client.logout()
        self.client.login(username="otheru", password="Pw2026!MedLabTest")
        hidden = self.client.get(f"/api/analyses/{rec.public_id}")
        self.assertEqual(hidden.status_code, 404)
        empty = self.client.get("/api/analyses")
        self.assertEqual(empty.json().get("count"), 0)

    def test_like_wildcards_do_not_match_all(self):
        AnalysisRecord.create_pending(self.user, "hematology", "upload")
        r = self.client.get("/api/analyses", {"q": "%"})
        self.assertEqual(r.json().get("count"), 0)
        r2 = self.client.get("/api/analyses", {"q": "_"})
        self.assertEqual(r2.json().get("count"), 0)

    def test_detail_requires_canonical_id(self):
        rec = AnalysisRecord.create_pending(self.user, "hematology", "upload")
        r = self.client.get("/api/analyses/ML")
        self.assertEqual(r.status_code, 404)
        ok = self.client.get(f"/api/analyses/{rec.public_id.replace('-', '')}")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["analysis"]["public_id"], rec.public_id)

    def test_owner_can_delete_analysis(self):
        rec = AnalysisRecord.create_pending(self.user, "hematology", "upload")
        pid = rec.public_id
        r = self.client.delete(f"/api/analyses/{pid}", secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("success"))
        self.assertFalse(AnalysisRecord.objects.filter(public_id=pid).exists())

    def test_other_user_cannot_delete_analysis(self):
        rec = AnalysisRecord.create_pending(self.user, "hematology", "upload")
        pid = rec.public_id
        self.client.logout()
        self.client.login(username="otheru", password="Pw2026!MedLabTest")
        r = self.client.delete(f"/api/analyses/{pid}", secure=True)
        self.assertEqual(r.status_code, 404)
        self.assertTrue(AnalysisRecord.objects.filter(public_id=pid).exists())

    def test_invalid_lab_type_filter_is_empty(self):
        AnalysisRecord.create_pending(self.user, "hematology", "upload")
        r = self.client.get("/api/analyses", {"lab_type": "not_a_real_lab"})
        self.assertEqual(r.json().get("count"), 0)
        r2 = self.client.get("/api/analyses", {"lab_type": "hematology"})
        self.assertEqual(r2.json().get("count"), 1)

    def test_analysis_result_scoped_to_owner(self):
        from lab_core import engine as eng

        with eng.analysis_lock:
            eng.latest_analysis.update(
                {
                    "user_id": self.other.id,
                    "text": "SECRET_OTHER_USER",
                    "status": "tayyor",
                    "loading": False,
                    "job_id": "job-other",
                    "public_id": "ML-260813-9999",
                }
            )
        r = self.client.get("/api/analysis_result")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("SECRET_OTHER_USER", r.json().get("text") or "")
        self.assertEqual(r.json().get("status"), "kutilmoqda")

    def test_completed_job_survives_newer_job(self):
        from lab_core import engine as eng

        eng._publish_analysis(
            {
                "job_id": "job-old",
                "text": "FIRST_REPORT",
                "status": "tayyor",
                "loading": False,
                "user_id": self.user.id,
            }
        )
        started = eng.begin_analysis_job("urine", user_id=self.user.id)
        self.assertTrue(started)
        snap = eng.take_completed_job("job-old")
        self.assertIsNotNone(snap)
        self.assertEqual(snap.get("text"), "FIRST_REPORT")

    def test_analysis_result_reads_db_by_job_id(self):
        rec = AnalysisRecord.create_pending(self.user, "hematology", "upload", job_id="job-db-1")
        rec.text = "FROM_DB"
        rec.status = "tayyor"
        rec.save()
        from lab_core import engine as eng

        with eng.analysis_lock:
            eng.latest_analysis.update(
                {
                    "user_id": None,
                    "text": "",
                    "status": "kutilmoqda",
                    "loading": False,
                    "job_id": "",
                    "public_id": "",
                }
            )
        r = self.client.get("/api/analysis_result", {"job_id": "job-db-1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("text"), "FROM_DB")
        self.assertEqual(r.json().get("public_id"), rec.public_id)


class BackupCommandTests(TestCase):
    def test_backup_db_writes_sqlite_copy(self):
        import tempfile
        from pathlib import Path

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as d:
            call_command("backup_db", dir=d)
            files = list(Path(d).glob("db_*.sqlite3"))
            self.assertTrue(files)
            self.assertGreater(files[0].stat().st_size, 0)
