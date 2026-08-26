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
            data=json.dumps({"lab_type": "histology", "source": "upload"}),
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
            data=json.dumps({"lab_type": "histology", "source": "upload"}),
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
        a = AnalysisRecord.create_pending(self.user, "histology", "upload")
        b = AnalysisRecord.create_pending(self.user, "urine", "camera")
        self.assertTrue(a.public_id.startswith("ML-"))
        self.assertNotEqual(a.public_id, b.public_id)
        self.assertRegex(a.public_id, r"^ML-\d{6}-\d{4}$")

    def test_search_by_id_and_isolation(self):
        rec = AnalysisRecord.create_pending(self.user, "histology", "upload", "job1")
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
        AnalysisRecord.create_pending(self.user, "histology", "upload")
        r = self.client.get("/api/analyses", {"q": "%"})
        self.assertEqual(r.json().get("count"), 0)
        r2 = self.client.get("/api/analyses", {"q": "_"})
        self.assertEqual(r2.json().get("count"), 0)

    def test_detail_requires_canonical_id(self):
        rec = AnalysisRecord.create_pending(self.user, "histology", "upload")
        r = self.client.get("/api/analyses/ML")
        self.assertEqual(r.status_code, 404)
        ok = self.client.get(f"/api/analyses/{rec.public_id.replace('-', '')}")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["analysis"]["public_id"], rec.public_id)

    def test_owner_can_delete_analysis(self):
        rec = AnalysisRecord.create_pending(self.user, "histology", "upload")
        pid = rec.public_id
        r = self.client.delete(f"/api/analyses/{pid}", secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("success"))
        self.assertFalse(AnalysisRecord.objects.filter(public_id=pid).exists())

    def test_list_shows_patient_and_sample_id(self):
        AnalysisRecord.create_pending(
            self.user,
            "histology",
            "upload",
            patient_name="Aliyev Vali",
            sample_id="40FSH7OPHEMA0001",
        )
        r = self.client.get("/api/analyses", secure=True)
        self.assertEqual(r.status_code, 200)
        row = r.json()["results"][0]
        self.assertEqual(row["patient_name"], "Aliyev Vali")
        self.assertEqual(row["sample_id"], "40FSH7OPHEMA0001")

    def test_search_by_sample_id_and_patient_name(self):
        AnalysisRecord.create_pending(
            self.user,
            "histology",
            "upload",
            patient_name="Karimova Nilufar",
            sample_id="40FSH7OPHEMA0002",
        )
        by_id = self.client.get("/api/analyses", {"q": "40FSH7OPHEMA0002"}, secure=True)
        self.assertEqual(by_id.json().get("count"), 1)
        by_name = self.client.get("/api/analyses", {"q": "Nilufar"}, secure=True)
        self.assertEqual(by_name.json().get("count"), 1)

    def test_other_user_cannot_delete_analysis(self):
        rec = AnalysisRecord.create_pending(self.user, "histology", "upload")
        pid = rec.public_id
        self.client.logout()
        self.client.login(username="otheru", password="Pw2026!MedLabTest")
        r = self.client.delete(f"/api/analyses/{pid}", secure=True)
        self.assertEqual(r.status_code, 404)
        self.assertTrue(AnalysisRecord.objects.filter(public_id=pid).exists())

    def test_invalid_lab_type_filter_is_empty(self):
        AnalysisRecord.create_pending(self.user, "histology", "upload")
        r = self.client.get("/api/analyses", {"lab_type": "not_a_real_lab"})
        self.assertEqual(r.json().get("count"), 0)
        r2 = self.client.get("/api/analyses", {"lab_type": "histology"})
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
        rec = AnalysisRecord.create_pending(self.user, "histology", "upload", job_id="job-db-1")
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


class DermatopathologyCanonTests(TestCase):
    """Teri holatida dermatopatologiya kanoni va xavfsizlik filtrlari."""

    def test_skin_case_detected_from_specimen_site(self):
        from lab_core import engine as eng

        self.assertTrue(eng._is_skin_case(None, {"specimen_site": "Teri, yelka"}))
        self.assertTrue(eng._is_skin_case({"organ": "teri"}, None))
        self.assertFalse(eng._is_skin_case({"organ": "buyrak"}, {"specimen_site": "teri"}))

    def test_derm_protocol_only_for_skin(self):
        from lab_core import engine as eng

        skin = eng._histology_protocol({"organ": "teri"})
        other = eng._histology_protocol({"organ": "ichak"})
        self.assertIn("DERMATOPATOLOGIYA ALGORITMI", skin)
        self.assertNotIn("DERMATOPATOLOGIYA ALGORITMI", other)

    def test_melanoma_needs_breslow_and_mitosis(self):
        from lab_core import engine as eng

        weak = "#### ANIQ TASHXIS\nYetakchi: Melanoma (70%)\nEpidermis o'zgargan."
        strong = (
            "#### ANIQ TASHXIS\nYetakchi: Melanoma (70%)\n"
            "Breslow 1.2 mm, mitoz 3/mm2, pagetoid tarqalish va assimetriya bor."
        )
        self.assertTrue(eng._histology_melanoma_overcall(weak))
        self.assertFalse(eng._histology_melanoma_overcall(strong))

    def test_nevus_lead_is_not_melanoma_overcall(self):
        from lab_core import engine as eng

        txt = "#### ANIQ TASHXIS\nYetakchi: Intradermal nevus (85%)\nMelanoma emas."
        self.assertFalse(eng._histology_melanoma_overcall(txt))

    def test_skin_report_without_pattern_is_weak(self):
        from lab_core import engine as eng

        lock = {"organ": "teri"}
        good = "\n".join([
            "#### TASHXIS",
            "teri, dermatofibroma - benign",
            "Organ/qatlam: teri, dermis | Ishonch: yuqori | Malignite qo'yish huquqi: YO'Q",
            "#### NEGA SHU TASHXIS",
            "Kollagen tuzogi - KO'RINDI: periferiyada kollagen tutamlari o'ralgan.",
            "Grenz zonasi - KO'RINDI: epidermis ostida tor bo'sh yo'lak.",
            "#### FAKT (ko'rinadigan morfologiya)",
            "Reaksiya patterni: neoplastik, dermal.",
            "Epidermis: giperplaziya.",
            "Hujayra: fibroblast va histiotsit, to'lqinli tutam.",
            "Mitoz: kam.",
            "#### NEGA BOSHQASI EMAS",
            "DFSP - yog'ga honeycomb infiltratsiya YO'Q.",
        ])
        self.assertFalse(eng._looks_like_weak_generic(good, "histology", lock))

        no_pattern = (
            good.replace("Epidermis", "To'qima")
            .replace("dermis", "soha")
            .replace("Reaksiya patterni: neoplastik, dermal.", "Umumiy ko'rinish.")
        )
        self.assertTrue(eng._looks_like_weak_generic(no_pattern, "histology", lock))

    def test_verbose_report_is_rejected(self):
        from lab_core import engine as eng

        base = "\n".join([
            "#### TASHXIS",
            "teri, dermatofibroma - benign",
            "#### NEGA SHU TASHXIS",
            "Kollagen tuzogi - KO'RINDI: periferik kollagen o'ralgan.",
            "#### FAKT (ko'rinadigan morfologiya)",
            "Epidermis: giperplaziya. Dermis: fibroblast proliferatsiyasi.",
            "Reaksiya patterni: neoplastik.",
            "#### NEGA BOSHQASI EMAS",
            "DFSP - infiltratsiya yo'q.",
        ])
        self.assertFalse(eng._too_verbose(base, "histology"))
        self.assertTrue(eng._too_verbose(base + "\n#### PROFILAKTIKA\nquyoshdan himoya", "histology"))
        self.assertTrue(eng._too_verbose(base + "\n#### KLINIK FIKRLASH\nSavol: nima?", "histology"))
        self.assertTrue(eng._too_verbose(base + ("\nmatn" * 3000), "histology"))

    def test_short_four_section_report_is_accepted(self):
        from lab_core import engine as eng

        rep = "\n".join([
            "#### TASHXIS",
            "teri, seborrheic keratosis - benign",
            "#### NEGA SHU TASHXIS",
            "Shox kistalari - KO'RINDI: epidermis ichida keratin kistalari.",
            "#### FAKT (ko'rinadigan morfologiya)",
            "Reaksiya patterni: neoplastik, epidermal lezyon.",
            "Epidermis: bazaloid hujayralar hisobiga akantoz, sirtda giperkeratoz.",
            "Shox kistalari va psevdokistalar epiteliy ichida ko'rinadi.",
            "Chegara: aniq, itaruvchi; bazal membrana butun.",
            "Yadro: bir xil, atipiya yo'q; mitoz kam va normal shaklda.",
            "Dermis: tinch, yengil perivaskulyar limfotsitar infiltrat.",
            "Invaziya: yo'q. Nekroz: yo'q. Pigment: bazal qatlamda o'rtacha.",
            "#### NEGA BOSHQASI EMAS",
            "SCC - to'liq qalinlikdagi keratinotsit atipiyasi va invaziya YO'Q.",
            "Verruca - koilotsit va gipergranuloz YO'Q.",
        ])
        self.assertFalse(eng._missing_diagnosis_sections(rep, "histology"))
        self.assertFalse(eng._too_verbose(rep, "histology"))
        self.assertFalse(eng._looks_like_weak_generic(rep, "histology", {"organ": "teri"}))


class KnowledgeBaseSourceTests(TestCase):
    """Kitob manbalarini fayl nomidan aniqlash va sozlamalar."""

    def test_detect_source_for_all_books(self):
        from lab_core.histology_kb import detect_source

        cases = {
            "Weedon's_Skin_Pathology_3rd_ed.pdf": "weedon",
            "Weedon's_Skin_Pathology_Essentials_R_Johnston.pdf": "weedon_estimate",
            "Dermatopathology__Diagnosis_by_First_Impression.pdf": "first_impression",
            "Dermatopathology_Vademecum_Ramon_L_Sanchez.pdf": "vademecum",
            "Dermatopathology The Basics.pdf": "derm_basics",
            "Color_Atlas_of_Dermatopathology.pdf": "color_atlas",
            "Pathology of Vascular Skin Lesions.pdf": "vascular_skin",
            "Genetics of Melanoma.pdf": "melanoma_genetics",
            "Атлас_диагностических_биопсий_кожи.pdf": "atlas_biopsy_ru",
            "ДЕРМАТООНКОПАТОЛОГИЯ.pdf": "dermatoonko_ru",
            "Дерматология Цветкова 2003.pdf": "tsvetkova_ru",
            "1.Junqueira's_Basic_Histology.pdf": "junqueira",
        }
        cases["Weedon's_Skin_Pathology_Essentials_R_Johnston.pdf"] = "weedon_essentials"
        for name, expected in cases.items():
            self.assertEqual(detect_source(name), expected, name)

    def test_skin_sources_are_boosted_for_skin_organ(self):
        from lab_core.histology_kb import _source_bonus

        self.assertGreater(_source_bonus("weedon", "teri"), _source_bonus("mboc", "teri"))
        self.assertGreater(_source_bonus("junqueira", "ichak"), _source_bonus("weedon", "ichak"))

    def test_prompt_block_empty_without_hits(self):
        from lab_core.histology_kb import format_prompt_block

        self.assertEqual(format_prompt_block([]), "")


class ObservationGateTests(TestCase):
    """Tashxis tasvirdagi belgilarga bog'langanini tekshirish."""

    FEATURES = {
        "sample_quality": "yaxshi",
        "dominant_pattern": "exophytic papillomatous epidermal proliferation",
        "invasion": "yo'q",
        "layers_present": {"epidermis": True, "dermis": True},
        "epidermis": {
            "acanthosis": True, "hyperkeratosis": True, "papillomatosis": True,
            "koilocytes": True, "horn_cysts": False, "basaloid_proliferation": False,
            "parakeratosis": False, "spongiosis": False, "full_thickness_atypia": False,
        },
        "junction": {
            "peripheral_palisading": False, "pagetoid_spread": False,
            "melanocyte_nests": False, "single_melanocyte_proliferation": False,
            "clefting_retraction": False, "band_like_infiltrate": False,
            "interface_damage": False,
        },
        "dermis": {
            "spindle_cells": False, "storiform_pattern": False, "collagen_trapping": False,
            "granuloma": False, "vasculitis": False, "vascular_proliferation": False,
            "tumour_nodule": False, "solar_elastosis": False,
        },
        "cytology": {"keratin_pearls": False, "pleomorphism": "yo'q", "mitoses_10hpf": "0"},
        "observations_uz": ["Epidermis papillomatoz va akantoz.", "Koilotsitlar bor."],
    }

    def test_unsupported_diagnosis_is_flagged(self):
        from lab_core import engine as eng

        for name in ("Seboreik keratoz", "Melanoma", "Dermatofibroma"):
            txt = "#### TASHXIS\n" + name + " - benign\n"
            self.assertTrue(
                eng._report_contradicts_features(txt, self.FEATURES),
                f"{name} bloklanishi kerak edi",
            )

    def test_supported_diagnosis_passes(self):
        from lab_core import engine as eng

        txt = "#### TASHXIS\nVerruca vulgaris - benign\n"
        self.assertFalse(eng._report_contradicts_features(txt, self.FEATURES))

    def test_insufficient_answer_passes(self):
        from lab_core import engine as eng

        txt = "#### TASHXIS\nAniq tashxis uchun yetarli emas\n"
        self.assertFalse(eng._report_contradicts_features(txt, self.FEATURES))

    def test_features_block_lists_seen_and_unseen(self):
        from lab_core import engine as eng

        blk = eng._features_prompt_block(self.FEATURES)
        self.assertIn("KO'RINGAN", blk)
        self.assertIn("koilotsitlar", blk)
        self.assertIn("shox kistalari", blk)  # ko'rinmaganlar ro'yxatida
        self.assertIn("papillomatous", blk)

    def test_kb_query_follows_the_image(self):
        from lab_core import engine as eng

        q1 = eng._features_query_text(self.FEATURES)
        other = {
            "dominant_pattern": "dermal spindle cell nodule",
            "invasion": "yo'q",
            "epidermis": {"acanthosis": True},
            "dermis": {"spindle_cells": True, "storiform_pattern": True, "collagen_trapping": True},
            "junction": {}, "cytology": {},
        }
        q2 = eng._features_query_text(other)
        self.assertIn("koilocytes", q1)
        self.assertIn("storiform", q2)
        self.assertNotEqual(q1, q2)

    def test_observation_parses_json_in_code_fence(self):
        from lab_core import engine as eng

        raw = '```json\n{"invasion": "yo\'q", "dominant_pattern": "x"}\n```'
        parsed = eng._parse_observation(raw)
        self.assertEqual(parsed.get("dominant_pattern"), "x")
