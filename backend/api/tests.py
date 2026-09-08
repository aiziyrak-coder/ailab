"""
MedLab AI — API va xavfsizlik asoslari (regressiya testlari).
"""
import json
import os

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
        self.assertEqual(data.get("product"), "DermaPATH")
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
            self.assertIn("DERMAPATH", data.get("message", "").upper())


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

    def _full_report(self, dx="teri, dermatofibroma (klassik) - benign"):
        return "\n".join([
            "#### TASHXIS",
            dx,
            "Organ/qatlam: teri, retikulyar derma | Daraja: qo'llanilmaydi | "
            "Ishonch: yuqori | Malignite qo'yish huquqi: YO'Q",
            "#### NEGA SHU TASHXIS",
            "Kollagen tuzog'i - KO'RINDI: periferiyada kollagen tutamlari o'ralgan.",
            "Grenz zonasi - KO'RINDI: epidermis ostida tor hujayrasiz yo'lak.",
            "Epidermal giperplaziya - KO'RINDI: rete ridgelar cho'zilgan.",
            "#### FAKT (o'lchangan morfologiya)",
            "Arxitektura: dermal tugun, chegara itaruvchi, simmetrik.",
            "Reaksiya patterni: neoplastik, dermal.",
            "Epidermis: akantoz, bazal pigment kuchaygan.",
            "Hujayra: fibroblast va histiotsit, qisqa to'lqinli tutamlar.",
            "Mitoz: 0/10 HPF, atipik mitoz yo'q.",
            "Stroma: periferik kollagen tuzog'i, desmoplaziya yo'q.",
            "Invaziya: yo'q - stroma buzilmagan.",
            "Chekka: baholab bo'lmaydi.",
            "#### NEGA BOSHQASI EMAS",
            "DFSP - storiform pattern va yog'ga honeycomb infiltratsiya YO'Q.",
            "Atipik fibroxantoma - pleomorf yadro va atipik mitoz YO'Q.",
            "Melanotsitar lezyon - melanotsitar uyalar YO'Q.",
            "#### TASDIQLASH",
            "IHC: FXIIIa musbat, CD34 manfiy kutiladi - DFSP ni istisno qiladi.",
            "Klinik: lezyon o'lchami va o'sish muddati so'raladi.",
            "#### BAHOLANMAGAN",
            "Chekka baholanmadi - kesma yo'nalishi ko'rinmaydi.",
        ])

    def test_skin_report_without_pattern_is_weak(self):
        """Pattern nomi va qatlam tavsifi bo'lmagan teri hisoboti qayta yozilishi kerak."""
        from lab_core import engine as eng

        lock = {"organ": "teri"}
        self.assertFalse(eng._looks_like_weak_generic(self._full_report(), "histology", lock))

        vague = "\n".join([
            "#### TASHXIS",
            "teri, dermatofibroma - benign",
            "Organ/qatlam: yumshoq to'qima | Ishonch: past | Malignite qo'yish huquqi: YO'Q",
            "#### NEGA SHU TASHXIS",
            "O'zgarishlar bor - KO'RINDI: to'qimada o'zgarish kuzatiladi.",
            "Hujayralar - KO'RINDI: hujayralar joylashgan.",
            "#### FAKT",
            "To'qima o'zgargan. Hujayralar mavjud. Umumiy ko'rinish qoniqarli.",
            "Qo'shimcha izohsiz. Tuzilma saqlangan. Chegara aniqlanmadi.",
            "#### NEGA BOSHQASI EMAS",
            "Boshqa kasalliklar - mos emas.",
            "#### TASDIQLASH",
            "IHC kerak bo'lishi mumkin.",
            "#### BAHOLANMAGAN",
            "Baholashga to'siq yo'q.",
        ])
        self.assertTrue(eng._looks_like_weak_generic(vague, "histology", lock))

    def test_verbose_report_is_rejected(self):
        from lab_core import engine as eng

        base = self._full_report()
        self.assertFalse(eng._too_verbose(base, "histology"))
        self.assertTrue(eng._too_verbose(base + "\n#### PROFILAKTIKA\nquyoshdan himoya", "histology"))
        self.assertTrue(eng._too_verbose(base + "\n#### KLINIK FIKRLASH\nSavol: nima?", "histology"))
        self.assertTrue(eng._too_verbose(base + ("\nmatn" * 3000), "histology"))

    def test_three_sections_required(self):
        """Hisobot 3 bo'limga qisqartirilgan: TASHXIS, NEGA SHU TASHXIS, FAKT."""
        from lab_core import engine as eng

        full = self._full_report()
        self.assertFalse(eng._missing_diagnosis_sections(full, "histology"))
        for drop in ("#### NEGA SHU TASHXIS", "#### FAKT"):
            cut = full.split(drop)[0]
            self.assertTrue(
                eng._missing_diagnosis_sections(cut, "histology"),
                f"{drop} yo'qligi aniqlanishi kerak",
            )

    def test_report_keeps_quantified_facts(self):
        from lab_core import engine as eng

        full = self._full_report()
        self.assertIn("0/10 HPF", full)
        self.assertFalse(eng._looks_like_technician(full))


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


class StructuredDiagnosisTests(TestCase):
    """Tashxis tuzilgan yozuv; hisobot undan chiqariladi, qayta o'qilmaydi.

    Auditda topilgan xato: hisobot matn bo'lib o'tar va o'nga yaqin qadam uni
    regex bilan yamardi. Model «####» sarlavhasini tushirsa, hammasi jimgina
    o'tkazib yuborilardi. Quyidagi sinovlar aynan shuning takrorlanmasligini
    tekshiradi.
    """

    RAW = {
        "diagnosis": "Verruca vulgaris, yallig'langan turi",
        "malignant": False,
        "organ": "teri",
        "layer": "epidermis–papillyar derma",
        "grade": "qo'llanilmaydi",
        "evidence": [
            {"feature": "Koilotsitoz", "detail": "perinuklear tiniqlashgan 12+ keratinotsit"},
            {"feature": "Papillomatoz", "detail": "4 ta papillyar cho'qqi"},
            {"feature": "Giperkeratoz", "detail": "kompakt ortokeratoz ~120 mkm"},
        ],
        "differentials": [
            {"name": "Seboreik keratoz", "excluded_by": "shox kistalari yo'q"},
        ],
        "facts": ["Mitoz: 0/10 HPF", "Chekka: baholab bo'lmaydi"],
    }
    FEATURES = {
        "sample_quality": "o'rtacha",
        "epidermis": {"acanthosis": True, "hyperkeratosis": True,
                      "papillomatosis": True, "koilocytes": True, "parakeratosis": True},
        "dermis": {"chronic_inflammation": True},
        "cytology": {"atypia": False},
    }

    def _render(self, raw=None, features=None, names=None):
        from lab_core import dx_record as dxr
        from lab_core import engine as eng

        rec = dxr.from_json(raw if raw is not None else self.RAW)
        rec, text = eng._finish_record(
            rec, features if features is not None else self.FEATURES, None, names or [])
        return rec, text

    def test_every_section_is_always_present(self):
        """Sarlavhalar koddan yoziladi — model shakliga bog'liq emas."""
        for raw in (self.RAW, {"diagnosis": "Psoriasis vulgaris"}):
            _rec, text = self._render(raw, features={})
            self.assertTrue(text.startswith("#### TASHXIS"), text[:60])
            self.assertIn("#### NEGA SHU TASHXIS", text)
            self.assertIn("#### FAKT", text)
            self.assertIn("YAKUNIY XULOSA:", text)
            self.assertRegex(text, r"Ishonchlilik: \d{1,2}%")

    def test_report_carries_the_evidence_and_exclusions(self):
        _rec, text = self._render()
        self.assertIn("Koilotsitoz — KO'RINDI: perinuklear", text)
        self.assertIn("Rad etildi: Seboreik keratoz — shox kistalari yo'q", text)
        self.assertIn("Mitoz: 0/10 HPF", text)

    def test_unsupported_name_is_swapped_for_a_descriptive_one(self):
        from lab_core import dx_record as dxr

        thin = {"sample_quality": "past", "dominant_pattern": "acanthotic papillomatous"}
        rec, text = self._render({**self.RAW, "diagnosis": "Seboreik keratoz"}, features=thin)
        self.assertEqual(rec.certainty, dxr.CERTAIN_DESCRIPTIVE)
        self.assertIn("Tavsifiy morfologiya", text)
        self.assertLessEqual(rec.confidence, 40)
        self.assertTrue(rec.notes)

    def test_low_confidence_malignancy_keeps_the_safety_line(self):
        rec, text = self._render(
            {**self.RAW, "diagnosis": "Melanoma, yuzaki tarqaluvchi", "malignant": True},
            features={"sample_quality": "past"},
            names=["Melanoma", "Nevus", "Bazalioma"],
        )
        self.assertIn("TASDIQLANMAYDI", text)

    def test_confident_benign_carries_no_warning(self):
        _rec, text = self._render(names=["Verruca vulgaris"] * 3)
        self.assertNotIn("TASDIQLANMAYDI", text)
        self.assertNotIn("DIQQAT", text)
        self.assertNotIn("Ishonch: past", text)
        self.assertNotIn("Malignite", text)

    def test_malformed_json_falls_back_instead_of_inventing(self):
        from lab_core import dx_record as dxr

        for bad in ({}, {"diagnosis": ""}, {"diagnosis": "noaniq"}, None, "matn", []):
            self.assertIsNone(dxr.from_json(bad), bad)

    def test_evidence_not_in_the_observation_is_still_rendered_honestly(self):
        """Dalil bo'lmasa bo'lim bo'sh qolmaydi — nima yo'qligini aytadi."""
        _rec, text = self._render({"diagnosis": "Psoriasis vulgaris"}, features={})
        self.assertIn("aniq morfologik belgi ajratilmadi", text)


class AtlasMatchingTests(TestCase):
    """Ma'lumotnoma rasm to'g'ri kasallikdan olinsin.

    Yorliqlar rus tilida, so'rov lotinchada — ilgari ular faqat sinonim
    jadvalidagi nomlar uchun uchrashardi. Bundan ham yomoni: «keratoz»
    «porokeratoz» ichida bor deb hisoblanib, modelga BOSHQA kasallikning
    rasmi ko'rsatilardi.
    """

    def test_cross_script_names_match(self):
        from lab_core.atlas_images import _score_label, _tokens

        for uz, ru in [("Bazalioma", "Базалиома"), ("Melanoma", "Меланома"),
                       ("Sarkoidoz", "Саркоидоз"), ("Vitiligo", "Витилиго"),
                       ("Psoriaz", "Псориаз")]:
            self.assertGreaterEqual(_score_label(_tokens(uz), ru), 0.6, f"{uz}/{ru}")

    def test_a_shared_suffix_is_not_a_match(self):
        from lab_core.atlas_images import _score_label, _tokens

        # «keratoz» «porokeratoz» ning OXIRIDA — bu boshqa kasallik
        self.assertLess(_score_label(_tokens("Seboreik keratoz"), "Порокератоз"), 0.6)
        self.assertLess(_score_label(_tokens("Sklerotik lixen"), "Простой лихен Видаля"), 0.6)

    def test_chapter_numbering_is_stripped_and_junk_dropped(self):
        from lab_core.atlas_images import clean_label

        self.assertEqual(clean_label("XV. Псориаз"), "Псориаз")
        self.assertEqual(clean_label("4. Экзема"), "Экзема")
        self.assertEqual(clean_label("Базалиома"), "Базалиома")
        for junk in ("I. Норма и патология кожи", "ВНУТРЕННИЕ БОЛЕЗНИ",
                     "ираклий 13-09-2014_02-02-45", "Новая папка"):
            self.assertEqual(clean_label(junk), "", junk)


class ServiceStatusTests(TestCase):
    """Xizmat holati kalit borligidan emas, haqiqiy chaqiruvdan olinadi."""

    def test_quota_error_is_classified_and_translated(self):
        from lab_core import engine as eng

        cases = {
            "Error code: 429 - You have no credits remaining. Add credits": "kredit",
            "Rate limit reached for gpt-4o": "band",
            "Error code: 401 - Incorrect API key provided": "kalit",
            "Connection timed out": "aloqa",
            "The model `gpt-9` does not exist": "model",
        }
        for msg, kind in cases.items():
            self.assertEqual(eng._classify_api_error(Exception(msg)), kind, msg)
            uz = eng.api_error_uz(Exception(msg))
            self.assertNotIn("http", uz.lower())      # billing havolasi chiqmasin
            self.assertNotIn("credits", uz.lower())   # inglizcha xom matn chiqmasin
            self.assertGreater(len(uz), 30)

    def test_status_reports_a_recent_failure(self):
        from lab_core import engine as eng

        eng._note_api_error(Exception("Error code: 429 - no credits remaining"))
        st = eng.api_status()
        self.assertFalse(st["ready"])
        self.assertEqual(st["kind"], "kredit")
        eng._note_api_ok()
        self.assertTrue(eng.api_status()["ready"])


class ConfidencePercentTests(TestCase):
    """Tashxis foiz bilan chiqadi; «Ishonch: past» va uzun ogohlantirish yo'q."""

    REPORT = "\n".join([
        "#### TASHXIS",
        "Bemor: 5 yosh, erkak; namuna №40FSH7OPHIST0005.",
        "Verruca vulgaris, yallig'langan turi — benign.",
        "Organ/qatlam: teri, epidermis–retikulyar derma | Daraja: qo'llanilmaydi | "
        "Ishonch: past | Malignite qo'yish huquqi: YO'Q.",
        "Ishchi taassurotlar: 1) verruca vulgaris; 2) squamous papilloma.",
        "#### NEGA SHU TASHXIS",
        "Koilotsitoz — KO'RINDI: perinuklear tiniqlashgan keratinotsitlar.",
        "#### FAKT (o'lchangan morfologiya)",
        "Mitoz: 0/10 HPF",
    ])

    FEATURES = {
        "sample_quality": "o'rtacha",
        "epidermis": {
            "acanthosis": True, "hyperkeratosis": True, "papillomatosis": True,
            "parakeratosis": True, "koilocytes": True, "horn_cysts": False,
        },
        "junction": {},
        "dermis": {"chronic_inflammation": True, "granulation": True},
        "cytology": {"atypia": False},
    }
    ADJ = {
        "chosen": "Verruca vulgaris",
        "confidence": "o'rta",
        "candidates": [
            {"name": "Verruca vulgaris", "fit": "mos"},
            {"name": "Squamous papilloma", "fit": "qisman"},
        ],
    }

    def _final(self, features=None, adj=None, names=None, report=None):
        from lab_core import engine as eng

        text = eng._mark_final_conclusion(eng._clean_dx_section(report or self.REPORT))
        return eng._finalize_confidence(text, features, adj, names or [])

    def test_meta_clutter_is_replaced_by_one_percentage(self):
        out = self._final(self.FEATURES, self.ADJ, ["Verruca vulgaris"] * 3)
        self.assertIn("YAKUNIY XULOSA: Verruca vulgaris", out)
        self.assertRegex(out, r"Ishonchlilik: \d{1,2}%")
        for gone in ("Ishonch: past", "Malignite", "DIQQAT", "Ishchi taassurot", "Bemor:"):
            self.assertNotIn(gone, out, f"{gone} qatori qolib ketdi")
        # Boshqa bo'limlarga tegilmaydi
        self.assertIn("Koilotsitoz", out)
        self.assertIn("Mitoz: 0/10 HPF", out)

    def test_agreeing_fields_score_higher_than_disagreeing_ones(self):
        from lab_core import engine as eng

        agree, _ = eng._confidence_percent(
            self.FEATURES, self.ADJ, ["Verruca vulgaris"] * 3
        )
        split, _ = eng._confidence_percent(
            self.FEATURES, self.ADJ, ["Verruca vulgaris", "Lichen planus", "Ekzema"]
        )
        self.assertGreater(agree, split)
        # Ko'pchilik yig'ilmagan — foiz shiftdan oshmaydi
        self.assertLessEqual(split, 45)

    def test_thin_evidence_is_capped(self):
        from lab_core import engine as eng

        pct, _ = eng._confidence_percent({"sample_quality": "past"}, None, [])
        self.assertLessEqual(pct, 40)
        self.assertGreaterEqual(pct, eng.CONFIDENCE_MIN)

    def test_never_reaches_one_hundred(self):
        from lab_core import engine as eng

        rich = {
            "sample_quality": "yaxshi",
            "epidermis": {f"f{i}": True for i in range(8)},
            "cytology": {f"c{i}": True for i in range(8)},
        }
        pct, _ = eng._confidence_percent(
            rich,
            {"chosen": "X", "confidence": "yuqori", "candidates": [{"name": "X", "fit": "mos"}]},
            ["X", "X", "X"],
        )
        self.assertLessEqual(pct, eng.CONFIDENCE_MAX)
        self.assertLess(pct, 100)

    def test_low_confidence_malignancy_keeps_one_safety_line(self):
        malignant = self.REPORT.replace(
            "Verruca vulgaris, yallig'langan turi — benign.",
            "Melanoma, yuzaki tarqaluvchi turi — malign.",
        )
        out = self._final(
            {"sample_quality": "past"}, None, ["Melanoma", "Nevus", "Bazalioma"],
            report=malignant,
        )
        self.assertIn("TASDIQLANMAYDI", out)

    def test_confident_diagnosis_carries_no_extra_warning(self):
        out = self._final(self.FEATURES, self.ADJ, ["Verruca vulgaris"] * 3)
        self.assertNotIn("TASDIQLANMAYDI", out)
        self.assertNotIn("barqaror emas", out)


class EvidenceCeilingTests(TestCase):
    """Dalil kam bo'lsa ishonchli aniq tashxis chiqmasligi kerak."""

    REPORT = "\n".join([
        "#### TASHXIS",
        "Seboreik keratoz - akantotik variant - benign",
        "Organ/qatlam: Teri, epidermis | Daraja: qo'llanilmaydi | Ishonch: yuqori | "
        "Malignite qo'yish huquqi: YO'Q",
        "#### NEGA SHU TASHXIS",
        "Akantoz - KO'RINDI: epidermis qalinlashgan.",
        "#### FAKT (o'lchangan morfologiya)",
        "Mitoz: 0/10 HPF.",
        "#### NEGA BOSHQASI EMAS",
        "SCC - atipiya yo'q.",
        "#### TASDIQLASH",
        "IHC shart emas.",
        "#### BAHOLANMAGAN",
        "Chekka baholanmadi.",
    ])

    THIN = {
        "sample_quality": "o'rtacha",
        "dominant_pattern": "acanthosis with lymphoid infiltrate",
        "epidermis": {"acanthosis": True, "hyperkeratosis": True, "horn_cysts": False},
        "junction": {}, "dermis": {"dense_lymphoid_infiltrate": True}, "cytology": {},
    }

    RICH = {
        "sample_quality": "yaxshi",
        "dominant_pattern": "exophytic basaloid proliferation",
        "epidermis": {
            "acanthosis": True, "hyperkeratosis": True, "horn_cysts": True,
            "basaloid_proliferation": True, "papillomatosis": True,
            "parakeratosis": True, "basal_pigment": True,
        },
        "junction": {}, "dermis": {"dense_lymphoid_infiltrate": True, "solar_elastosis": True},
        "cytology": {},
    }

    def test_thin_evidence_marks_the_name_provisional(self):
        """Dalil kam bo'lsa nom saqlanadi, lekin «taxminiy» deb belgilanadi.

        Ilgari nom butunlay «Aniq tashxis uchun yetarli emas» bilan
        almashtirilardi — shifokorga bu hech narsa bermasdi.
        """
        from lab_core import engine as eng

        n, level = eng._evidence_level(self.THIN)
        self.assertEqual(level, "past")
        out = eng._apply_evidence_rules(self.REPORT, self.THIN)
        self.assertIn("Seboreik keratoz", out)
        self.assertIn("taxminiy", out.lower())
        self.assertIn("Ishonch: past", out)
        self.assertNotIn("Ishonch: yuqori", out)
        # Qolgan bo'limlar joyida qoladi
        for head in ("#### NEGA SHU TASHXIS", "#### FAKT"):
            self.assertIn(head, out)

    def test_rich_evidence_keeps_the_diagnosis(self):
        from lab_core import engine as eng

        n, level = eng._evidence_level(self.RICH)
        self.assertGreaterEqual(n, 8)
        self.assertEqual(level, "yuqori")
        out = eng._apply_evidence_rules(self.REPORT, self.RICH)
        self.assertIn("Seboreik keratoz", out)
        self.assertIn("Ishonch: yuqori", out)

    def test_medium_evidence_caps_confidence(self):
        from lab_core import engine as eng

        mid = dict(self.RICH)
        mid["sample_quality"] = "o'rtacha"
        out = eng._apply_evidence_rules(self.REPORT, mid)
        self.assertIn("Seboreik keratoz", out)
        self.assertIn("Ishonch: o'rta", out)

    def test_contradicted_name_is_replaced_by_a_descriptive_one(self):
        """Nom ko'rikka zid bo'lsa — tavsifiy morfologiyaga almashtiriladi."""
        from lab_core import engine as eng

        out = eng._mark_provisional(self.REPORT, self.THIN, "sinov sababi", rename=True)
        self.assertNotIn("Seboreik keratoz", out.split("#### NEGA SHU TASHXIS")[0])
        self.assertIn("sabab: sinov sababi", out.lower())
        self.assertIn("taxminiy", out.lower())

    def test_image_spread_covers_the_whole_set(self):
        from lab_core import engine as eng

        parts = [{"image_url": {"url": str(i)}} for i in range(26)]
        picked = eng._spread_pick(parts, 8)
        self.assertEqual(len(picked), 8)
        self.assertEqual(picked[0]["image_url"]["url"], "0")
        self.assertEqual(picked[-1]["image_url"]["url"], "25")
        self.assertEqual(eng._spread_pick(parts[:5], 8), parts[:5])


class CaseArchiveTests(TestCase):
    """Keys arxivi — xato tashxislarni keyin ko'rib chiqish uchun.

    Bemor materiali bo'lgani uchun arxiv ixtiyoriy va bemorni shaxsan
    aniqlaydigan maydonlar unga tushmaydi.
    """

    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp()
        self._old = dict(os.environ)
        os.environ["CASE_ARCHIVE_DIR"] = self.dir

    def tearDown(self):
        import shutil

        os.environ.clear()
        os.environ.update(self._old)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _img(self):
        from PIL import Image

        return Image.new("RGB", (32, 32), (200, 150, 190))

    def test_disabled_by_default_writes_nothing(self):
        from lab_core import case_archive

        os.environ.pop("CASE_ARCHIVE", None)
        self.assertFalse(case_archive.enabled())
        self.assertEqual(case_archive.save([self._img()], "hisobot"), "")
        self.assertEqual(os.listdir(self.dir), [])

    def test_enabled_stores_images_report_and_observation(self):
        from lab_core import case_archive

        os.environ["CASE_ARCHIVE"] = "1"
        root = case_archive.save(
            [self._img()], "#### TASHXIS\nYAKUNIY XULOSA: Psoriaz\n",
            {"sample_quality": "yaxshi"}, {"name": "Psoriaz", "confidence": 71},
            {"sample_id": "40FSH7OPHIST0005", "patient_name": "Aliyev A.",
             "ward": "3-palata", "specimen_site": "tirsak"},
        )
        self.assertTrue(root and os.path.isdir(root))
        names = sorted(os.listdir(root))
        self.assertIn("kesma_01.jpg", names)
        self.assertIn("hisobot.md", names)
        self.assertIn("korik.json", names)

        meta = json.loads(open(os.path.join(root, "keys.json"), encoding="utf-8").read())
        self.assertEqual(meta["tashxis"]["confidence"], 71)
        self.assertEqual(meta["kontekst"]["specimen_site"], "tirsak")
        # Bemorni shaxsan aniqlaydigan maydonlar saqlanmaydi
        for gone in ("patient_name", "ward"):
            self.assertNotIn(gone, meta["kontekst"], gone)
        self.assertNotIn("Aliyev", json.dumps(meta, ensure_ascii=False))

    def test_expired_months_are_pruned(self):
        from lab_core import case_archive

        os.environ["CASE_ARCHIVE"] = "1"
        os.environ["CASE_ARCHIVE_DAYS"] = "30"
        old = os.path.join(self.dir, "2019-01", "eski")
        os.makedirs(old)
        case_archive.save([self._img()], "hisobot")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "2019-01")))


class CriteriaTableTests(TestCase):
    """Professor mezon jadvali — deterministik hakamlik va qo'riqchi."""

    @staticmethod
    def _feats(**groups):
        f = {"sample_quality": "yaxshi", "cytology": {}, "inflammation": {}}
        for g, keys in groups.items():
            if isinstance(keys, dict):
                f.setdefault(g, {}).update(keys)
            else:
                f[g] = {k: True for k in keys}
        return f

    def test_classic_pictures_rank_their_diagnosis_first(self):
        from lab_core import dx_criteria as dxc

        cases = {
            "Psoriasis": self._feats(epidermis=["regular_elongated_rete", "parakeratosis",
                                                "munro_microabscess", "suprapapillary_thinning"]),
            "Lichen planus": self._feats(junction=["band_like_infiltrate", "vacuolar_change",
                                                   "civatte_bodies"], epidermis=["sawtooth_rete"]),
            "Bazal hujayrali": self._feats(epidermis=["basaloid_proliferation"],
                                           junction=["peripheral_palisading", "clefting_retraction"]),
            "Melanoma": self._feats(junction=["pagetoid_spread", "asymmetric_melanocytic_growth",
                                              "single_melanocyte_proliferation"],
                                    cytology={"pleomorphism": "kuchli", "deep_mitoses": True}),
            "Verruca": self._feats(epidermis=["koilocytes", "papillomatosis", "hyperkeratosis"]),
            "Sarkoidoz": self._feats(dermis=["naked_granulomas", "granuloma"]),
            "Leykotsitoklastik": self._feats(dermis=["vasculitis", "leukocytoclasia",
                                                     "fibrinoid_vessel_necrosis"]),
            "Pemphigus vulgaris": self._feats(epidermis=["acantholysis", "intraepidermal_vesicle"]),
        }
        for expect, f in cases.items():
            top = dxc.rank_candidates(f, 1)
            self.assertTrue(top, expect)
            self.assertIn(expect.lower()[:6], top[0]["name"].lower(), f"{expect} -> {top[0]['name']}")

    def test_no_features_means_no_candidates(self):
        from lab_core import dx_criteria as dxc

        self.assertEqual(dxc.rank_candidates(self._feats()), [])

    def test_excluding_feature_blocks_the_name(self):
        from lab_core import dx_criteria as dxc

        bcc = self._feats(epidermis=["basaloid_proliferation"],
                          junction=["peripheral_palisading", "clefting_retraction"])
        ev = dxc.check_name("Seboreik keratoz", bcc)
        self.assertIn("peripheral_palisading", ev["excluding_present"])
        ev = dxc.check_name("Melanoma", self._feats(epidermis=["koilocytes", "papillomatosis"]))
        self.assertEqual(ev["essential_hits"], 0)

    def test_names_are_recognised_across_languages(self):
        from lab_core import dx_criteria as dxc

        for name in ("Псориаз обыкновенный", "Psoriasis vulgaris", "psoriaz, klassik"):
            self.assertEqual(dxc.find_entity(name)["name"], "Psoriasis vulgaris", name)
        for name in ("Базалиома нодулярная", "bazal hujayrali karsinoma", "BCC"):
            self.assertIn("Bazal", dxc.find_entity(name)["name"], name)
        self.assertIsNone(dxc.find_entity("umuman notanish nom"))

    def test_guard_downgrades_an_unsupported_name_via_criteria(self):
        from lab_core import dx_record as dxr
        from lab_core import engine as eng

        # Ko'rikda BCC belgilari, model esa «seboreik keratoz» dedi
        f = self._feats(epidermis=["basaloid_proliferation"],
                        junction=["peripheral_palisading", "clefting_retraction"])
        rec = dxr.from_json({"diagnosis": "Seboreik keratoz", "evidence": [
            {"feature": "bazaloid", "detail": "x"}, {"feature": "palisad", "detail": "y"}]})
        rec, text = eng._finish_record(rec, f, None, [])
        self.assertEqual(rec.certainty, dxr.CERTAIN_PROVISIONAL)
        self.assertLessEqual(rec.confidence, 50)
        self.assertTrue(any("rad etuvchi" in n for n in rec.notes), rec.notes)
        self.assertIn("taxminiy", text)


class StructuredPipelineMockTests(TestCase):
    """Qaror bosqichi — soxta model bilan uchidan-uchigacha."""

    FEATURES = {
        "sample_quality": "yaxshi",
        "dominant_pattern": "psoriasiform",
        "epidermis": {"regular_elongated_rete": True, "parakeratosis": True,
                      "munro_microabscess": True, "acanthosis": True},
        "dermis": {"dilated_tortuous_capillaries": True},
        "cytology": {}, "inflammation": {"type": "neytrofil"},
    }

    def _with_model(self, reply):
        from lab_core import engine as eng

        calls = []

        def fake(messages, kwargs, model=None, label=""):
            calls.append(messages)
            return reply

        eng._meter_reset()          # har sinov — yangi keys byudjeti
        old = eng._chat_complete
        eng._chat_complete = fake
        # Rasm bilan — rad etilganda «matn qisqartirilgan, rasmsiz» urinish bo'lsin
        img = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/", "detail": "high"}}]
        try:
            rec = eng._decide_diagnosis(self.FEATURES, None, "", {"temperature": 0.0},
                                        image_parts=img)
        finally:
            eng._chat_complete = old
        return rec, calls

    def test_decision_json_becomes_a_record_and_a_report(self):
        from lab_core import engine as eng

        reply = json.dumps({
            "diagnosis": "Psoriasis vulgaris", "malignant": False, "organ": "teri",
            "layer": "epidermis", "grade": "qo'llanilmaydi",
            "evidence": [{"feature": "Munro mikroabsessi", "detail": "parakeratozda 3 ta to'plam"}],
            "differentials": [{"name": "Ekzema", "excluded_by": "spongioz yo'q"}],
            "facts": ["Mitoz: 0/10 HPF"],
        })
        rec, calls = self._with_model(reply)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.name, "Psoriasis vulgaris")
        # Qaror so'rovida mezon jadvali va belgilar bloki bor
        user = json.dumps(calls[0][1]["content"], ensure_ascii=False)
        self.assertIn("MEZON JADVALI", user)
        self.assertIn("Psoriasis vulgaris", user)
        rec, text = eng._finish_record(rec, self.FEATURES, None, ["Psoriasis"] * 3)
        self.assertTrue(text.startswith("#### TASHXIS"))
        self.assertIn("YAKUNIY XULOSA: Psoriasis vulgaris", text)
        self.assertGreaterEqual(rec.confidence, 60)

    def test_garbage_reply_yields_none_so_the_old_path_runs(self):
        rec, _ = self._with_model("Kechirasiz, bu rasmda hech narsa ko'rmadim.")
        self.assertIsNone(rec)

    def test_refusal_is_retried_then_gives_up_cleanly(self):
        rec, calls = self._with_model("I'm sorry, I can't assist with that.")
        self.assertIsNone(rec)
        self.assertGreaterEqual(len(calls), 2)   # yengilroq urinish bo'ldi


class TokenBudgetTests(TestCase):
    """Sarf hisobi va keys chegarasi — hisob minusga tushmasin."""

    def test_message_stats_count_images_and_text(self):
        from lab_core import engine as eng

        msgs = [{"role": "system", "content": "abcd" * 10},
                {"role": "user", "content": [{"type": "text", "text": "xy" * 5},
                                             {"type": "image_url", "image_url": {"url": "u"}},
                                             {"type": "image_url", "image_url": {"url": "v"}}]}]
        self.assertEqual(eng._message_stats(msgs), (2, 50))

    def test_case_budget_stops_runaway_calls(self):
        from lab_core import engine as eng

        eng._meter_reset()
        for i in range(eng._max_calls_per_case()):
            eng._meter_add("sinov", None, 1, 400)
        with self.assertRaises(eng.CaseBudgetExceeded):
            eng._budget_check("ortiqcha")
        s = eng._meter_summary()
        self.assertEqual(s["calls"], eng._max_calls_per_case())
        self.assertGreater(s["total_tokens"], 0)
        eng._meter_reset()
        eng._budget_check("yangi keys")     # reset dan keyin yana ruxsat

    def test_open_case_meter_survives_the_pipeline_entry(self):
        """do_analyze hisobni ochadi; _openai_generate uni nolga tushirmaydi —
        klinik surat chaqiruvi ham keys chegarasiga kiradi."""
        from lab_core import engine as eng

        eng._meter_begin()
        eng._meter_add("klinik surat", None, 1, 200)
        eng._meter_ensure()                      # ochiq — tegmaydi
        self.assertEqual(eng._meter_summary()["calls"], 1)
        eng._meter_end()
        eng._meter_ensure()                      # yopiq — yangi keys
        self.assertEqual(eng._meter_summary()["calls"], 0)

    def test_default_ceiling_is_a_safety_net_not_a_budget(self):
        from lab_core import engine as eng

        for k in ("OPENAI_MAX_CALLS_PER_CASE", "OPENAI_MAX_TOKENS_PER_CASE"):
            os.environ.pop(k, None)
        self.assertLessEqual(eng._max_calls_per_case(), 5)
        self.assertLessEqual(eng._max_tokens_per_case(), 40000)


class EconomyPipelineMockTests(TestCase):
    """Tejamkor quvur: keysga ≤3 chaqiruv, hisobot to'liq, sarf yozib boriladi."""

    def _fake(self, calls):
        from lab_core import dx_criteria as dxc

        obs = json.loads(dxc.observe_schema_json())
        obs.update({"not_tissue": False, "organ": "teri", "sample_quality": "yaxshi",
                    "dominant_pattern": "psoriasiform", "not_assessable_uz": [],
                    "observations_uz": ["rete cho'zilgan"]})
        for k in ("regular_elongated_rete", "parakeratosis", "munro_microabscess",
                  "suprapapillary_thinning"):
            obs["epidermis"][k] = True
        decision = json.dumps({"diagnosis": "Psoriasis vulgaris", "organ": "teri",
                               "layer": "epidermis", "evidence": [
                                   {"feature": "Munro mikroabsessi", "detail": "3 ta"},
                                   {"feature": "Parakeratoz", "detail": "tutash"}],
                               "facts": ["Mitoz: 0/10 HPF"]})

        gestalt = json.dumps({"gestalt": "psoriasiform plaque", "diagnosis": "Psoriasis vulgaris",
                              "confidence": "high", "decisive_features": ["Munro", "regular rete"],
                              "alternatives": [{"name": "Ekzema", "why_less_likely": "no spongiosis"}],
                              "clinicopathologic_fit": "fits"})

        def fake(messages, kwargs, model=None, label=""):
            calls.append(label)
            sysm = messages[0].get("content") or ""
            if "histopathology image reader" in sysm:
                return json.dumps(obs)
            if "senior dermatopathologist signing out" in sysm:
                return gestalt
            return decision
        return fake

    def test_two_calls_full_report_and_token_trace(self):
        from PIL import Image
        from lab_core import engine as eng

        calls = []
        old_chat, old_client = eng._chat_complete, eng.openai_client
        env_old = {k: os.environ.get(k) for k in ("HISTOLOGY_ECONOMY", "HISTOLOGY_KB", "HISTOLOGY_ATLAS")}
        os.environ.update({"HISTOLOGY_ECONOMY": "1", "HISTOLOGY_KB": "0", "HISTOLOGY_ATLAS": "0"})
        eng._chat_complete, eng.openai_client = self._fake(calls), object()
        try:
            imgs = [Image.new("RGB", (900, 700), (170, 110, 190)) for _ in range(4)]
            trace = {}
            out = eng._openai_generate(["prompt"] + imgs, "histology",
                                       {"specimen_site": "tirsak terisi"}, trace=trace)
        finally:
            eng._chat_complete, eng.openai_client = old_chat, old_client
            for k, v in env_old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        # umumiy ko'rinish → ko'rik → qaror → tekshiruv: 4 chaqiruv, ortiq emas
        self.assertEqual(calls, ["umumiy ko'rinish", "ko'rik", "qaror", "tekshiruv"], calls)
        self.assertGreaterEqual(trace["record"]["confidence"], 70)   # gestalt mos → bonus
        self.assertEqual(trace["gestalt"]["diagnosis"], "Psoriasis vulgaris")
        self.assertTrue(out.startswith("#### TASHXIS"))
        self.assertIn("YAKUNIY XULOSA: Psoriasis vulgaris", out)
        self.assertRegex(out, r"Ishonchlilik: \d{1,2}%")
        self.assertIn("tokens", trace)
        self.assertEqual(trace["record"]["name"], "Psoriasis vulgaris")

    def test_clinical_photo_reaches_the_decision_and_the_report(self):
        """Klinik surat tavsifi qaror so'roviga kiradi va hisobotda ko'rinadi —
        ilgari u faqat eski matn yo'lidagi full_prompt ichida qolib ketardi."""
        from PIL import Image
        from lab_core import engine as eng

        calls, seen = [], {}

        base = self._fake(calls)

        def fake(messages, kwargs, model=None, label=""):
            if label == "qaror":
                seen["user"] = json.dumps(messages[-1]["content"], ensure_ascii=False)
                seen["images"] = eng._message_stats(messages)[0]
            return base(messages, kwargs, model, label)

        old_chat, old_client = eng._chat_complete, eng.openai_client
        env_old = {k: os.environ.get(k) for k in ("HISTOLOGY_ECONOMY", "HISTOLOGY_KB", "HISTOLOGY_ATLAS")}
        os.environ.update({"HISTOLOGY_ECONOMY": "1", "HISTOLOGY_KB": "0", "HISTOLOGY_ATLAS": "0"})
        eng._chat_complete, eng.openai_client = fake, object()
        try:
            imgs = [Image.new("RGB", (900, 700), (170, 110, 190)) for _ in range(2)]
            clin = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/", "detail": "low"}}]
            block = ("### KLINIK KO'RINISH (bemor tanasidagi rasm — kesma EMAS)\n"
                     "Tirsakda kumushsimon tangachali qizil blyashkalar, chegarasi aniq.\n"
                     "Bu klinik kontekst: hisobga ol.\n")
            out = eng._openai_generate(["prompt"] + imgs, "histology", {"specimen_site": "tirsak"},
                                       clinical_block=block, clinical_parts=clin)
        finally:
            eng._chat_complete, eng.openai_client = old_chat, old_client
            for k, v in env_old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertIn("kumushsimon tangachali", seen["user"])
        self.assertGreaterEqual(seen["images"], 2)        # kesma + klinik surat
        self.assertIn("Klinik ko'rinish (tana surati): Tirsakda", out)

    def test_criteria_fallback_when_the_model_gives_nothing(self):
        from lab_core import engine as eng
        from lab_core import dx_record as dxr

        f = {"sample_quality": "yaxshi", "epidermis": {"koilocytes": True, "papillomatosis": True,
                                                       "hyperkeratosis": True}}
        rec = eng._record_from_criteria(f)
        self.assertIsNotNone(rec)
        self.assertIn("Verruca", rec.name)
        self.assertEqual(rec.certainty, dxr.CERTAIN_PROVISIONAL)
        self.assertTrue(rec.evidence)


class DecisiveFeatureVerificationTests(TestCase):
    """Hal qiluvchi belgilar qayta tekshiriladi — bitta ko'rikning shovqini
    to'g'ridan-to'g'ri tashxisga o'tmasin (psoriaz → «Hailey–Hailey 87%» holati)."""

    def _psoriasis_misread(self):
        # Ko'rik Kogoj pustulasini akantoliz deb o'qidi
        return {"sample_quality": "yaxshi", "dominant_pattern": "pustular-acantholytic",
                "epidermis": {"acantholysis": True, "regular_elongated_rete": True,
                              "parakeratosis": True, "neutrophils_in_corneum": True,
                              "spongiform_pustule": True, "intraepidermal_vesicle": True},
                "dermis": {"dilated_tortuous_capillaries": True}, "cytology": {}}

    def test_a_single_feature_winner_does_not_get_full_confidence(self):
        from lab_core import engine as eng

        f = {"sample_quality": "yaxshi", "epidermis": {"acantholysis": True}, "cytology": {}}
        f["_chosen_name"] = "Hailey-Hailey kasalligi"
        pct, why = eng._confidence_percent(f, None, [])
        self.assertLess(pct, 55, why)

    def test_verification_flip_rewrites_the_name(self):
        from lab_core import engine as eng
        from lab_core import dx_record as dxr

        f = self._psoriasis_misread()
        rec = dxr.from_json({"diagnosis": "Hailey-Hailey kasalligi", "evidence": [
            {"feature": "akantoliz", "detail": "x"}, {"feature": "pufakcha", "detail": "y"}]})
        keys = eng._decisive_features(rec, f)
        self.assertIn("acantholysis", keys)

        def fake(messages, kwargs, model=None, label=""):
            return json.dumps({k: ("yo'q" if k == "acantholysis" else "bor") for k in keys})

        eng._meter_reset()
        old = eng._chat_complete
        eng._chat_complete = fake
        try:
            img = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/"}}]
            changes = eng._verify_decisive_features(rec, f, img, {"temperature": 0.0})
        finally:
            eng._chat_complete = old
        self.assertTrue(changes)
        self.assertFalse(eng._feature_true(f, "acantholysis"))
        rec, text = eng._finish_record(rec, f, None, [], changes)
        self.assertIn("psoriaz", rec.name.lower())
        self.assertLessEqual(rec.confidence, 60)
        self.assertIn("YAKUNIY XULOSA:", text)

    def test_acantholytic_names_are_excluded_by_psoriasis_findings(self):
        from lab_core import dx_criteria as dxc

        f = self._psoriasis_misread()
        top = dxc.rank_candidates(f, 3)
        self.assertTrue(top)
        self.assertNotIn("hailey", top[0]["name"].lower(), [r["name"] for r in top])


class ClinicalReasoningTests(TestCase):
    """Klinik mantiq: yakka tugun ≠ tarqoq dermatoz; yo'llanma va tana surati
    tekshiruvga yo'nalish beradi (3-keys: piogen granuloma → «Darier» xatosi)."""

    MISREAD = {  # serverdagi xato keysning aynan ko'rik natijasi
        "sample_quality": "o'rtacha", "cytology": {},
        "epidermis": {k: True for k in ["acanthosis", "hyperkeratosis", "parakeratosis",
                                        "papillomatosis", "acantholysis", "dyskeratosis",
                                        "intraepidermal_vesicle", "keratin_filled_crater"]},
        "dermis": {k: True for k in ["dense_lymphoid_infiltrate", "eosinophils", "neutrophils",
                                     "superficial_perivascular_infiltrate", "papillary_dermal_edema"]},
    }
    CLIN = "Bo'yinda yakka, qizil, oyoqchali yaltiroq tugun ~4 mm."
    REF = "Ангиома? Эксцизионная биопсия с шеи"

    def test_cyrillic_referral_names_are_recognised(self):
        from lab_core import dx_criteria as dxc

        names = dxc.referral_entities(self.REF)
        self.assertTrue(any("Gemangioma" in n or "Piogen" in n for n in names), names)
        self.assertEqual(dxc.find_entity("Псориаз обыкновенный")["name"], "Psoriasis vulgaris")

    def test_solitary_nodule_demotes_eruptive_dermatoses(self):
        from lab_core import dx_criteria as dxc

        self.assertEqual(dxc.clinical_hint(self.CLIN), "solitary")
        plain = dxc.rank_candidates(self.MISREAD, 1)[0]["name"]
        self.assertIn("Darier", plain)
        top = dxc.rank_candidates(self.MISREAD, 3, self.CLIN, self.REF)
        self.assertNotIn("Darier", top[0]["name"], [r["name"] for r in top])

    def test_clinical_cues_point_the_verification_at_vascular_features(self):
        from lab_core import dx_criteria as dxc
        from lab_core import dx_record as dxr
        from lab_core import engine as eng

        self.assertIn("Piogen granuloma", dxc.clinical_entities(self.CLIN))
        rec = dxr.from_json({"diagnosis": "Darier kasalligi", "evidence": [
            {"feature": "akantoliz", "detail": "x"}, {"feature": "diskeratoz", "detail": "y"}]})
        keys = eng._decisive_features(rec, dict(self.MISREAD), self.REF + " " + self.CLIN)
        self.assertIn("lobular_capillary_proliferation", keys)
        self.assertIn("acantholysis", keys)

    def test_discordant_diagnosis_is_flagged_and_capped(self):
        """Yakka tugun + «Hailey–Hailey» → nomuvofiqlik yoziladi, ishonch ≤45%."""
        from lab_core import dx_record as dxr
        from lab_core import engine as eng

        rec = dxr.from_json({"diagnosis": "Hailey-Hailey kasalligi", "organ": "teri", "evidence": [
            {"feature": "akantoliz", "detail": "x"}, {"feature": "akantoz", "detail": "y"}]})
        f = json.loads(json.dumps(self.MISREAD))
        rec, text = eng._finish_record(rec, f, None, [], [], clinical_text=self.CLIN,
                                       referral_text=self.REF)
        self.assertIn("NOMUVOFIQLIK", text)
        self.assertIn("Piogen granuloma", text)          # kutilgan nozologiya aytiladi
        self.assertLessEqual(rec.confidence, 45)
        self.assertIn("taxminiy", text)

    def test_verified_hypothesis_replaces_an_unlisted_name(self):
        """Nom jadvalda yo'q (SCAP), tekshiruv «tomir proliferatsiyasi: bor» dedi va
        yo'llanma «angioma» — nom gipotezaga o'tadi, zid differensial yo'qoladi."""
        from lab_core import dx_record as dxr
        from lab_core import engine as eng

        rec = dxr.from_json({"diagnosis": "Siringotsistadenoma papilliferum", "organ": "teri",
                             "evidence": [{"feature": "papillyar", "detail": "x"},
                                          {"feature": "bezli", "detail": "y"}],
                             "differentials": [{"name": "Gemangioma",
                                                "excluded_by": "tomir proliferatsiyasi ko'rinmadi"}]})
        f = {"sample_quality": "o'rtacha", "cytology": {},
             "epidermis": {"acanthosis": True}, "dermis": {"vascular_proliferation": True}}
        changes = ["tomir proliferatsiyasi: bor"]
        rec, text = eng._finish_record(rec, f, None, [], changes, clinical_text=self.CLIN,
                                       referral_text=self.REF)
        self.assertIn("Gemangioma", rec.name)
        self.assertNotIn("tomir proliferatsiyasi ko'rinmadi", text)
        self.assertIn("taxminiy", text)
        self.assertLessEqual(rec.confidence, 60)

    def test_immaterial_verification_changes_do_not_cap_a_correct_answer(self):
        """PG to'g'ri, gestalt mos; tekshiruv «bazal vakuolizatsiya: bor» dedi —
        PG uchun ahamiyatsiz, shift tushmasin."""
        from lab_core import dx_record as dxr
        from lab_core import engine as eng

        f = {"sample_quality": "o'rtacha", "cytology": {},
             "epidermis": {"acanthosis": True, "ulceration": True, "polypoid_exophytic": True,
                           "epidermal_collarette": True},
             "dermis": {"lobular_capillary_proliferation": True, "vascular_proliferation": True,
                        "neutrophils": True, "papillary_dermal_edema": True}}
        rec = dxr.from_json({"diagnosis": "Lobulyar kapillyar gemangioma (piogen granuloma)",
                             "organ": "teri", "evidence": [
                                 {"feature": "Polipoid arxitektura", "detail": "x"},
                                 {"feature": "Tomir proliferatsiyasi", "detail": "y"},
                                 {"feature": "Yara", "detail": "z"}]})
        gestalt = {"diagnosis": "Lobular capillary hemangioma (pyogenic granuloma)",
                   "confidence": "moderate"}
        rec, text = eng._finish_record(
            rec, f, None, [], ["bazal qavat vakuolizatsiyasi: bor"],
            clinical_text=self.CLIN, referral_text=self.REF, gestalt=gestalt)
        self.assertEqual(rec.gestalt_agreement, "mos")
        self.assertGreaterEqual(rec.confidence, 70, rec.confidence_why)
        self.assertNotIn("taxminiy", text)

    def test_after_verification_the_vascular_lesion_wins(self):
        from lab_core import dx_criteria as dxc

        f = json.loads(json.dumps(self.MISREAD))
        f["epidermis"].update({"acantholysis": False, "dyskeratosis": False,
                               "intraepidermal_vesicle": False})
        f["dermis"].update({"lobular_capillary_proliferation": True,
                            "vascular_proliferation": True, "extravasated_erythrocytes": True})
        top = dxc.rank_candidates(f, 1, self.CLIN, self.REF)[0]
        self.assertIn("Piogen", top["name"])


class LexicalFallbackTests(TestCase):
    """Embedding tushganda kitoblar so'zma-so'z qidiruv bilan yetib boradi."""

    CHUNKS = [
        {"source": "weedon", "page": 1, "text": "Psoriasis shows regular acanthosis, parakeratosis "
                                                  "and Munro microabscesses with suprapapillary thinning."},
        {"source": "weedon", "page": 2, "text": "Basal cell carcinoma: basaloid islands, peripheral "
                                                  "palisading and retraction clefts."},
        {"source": "derm_guide_ru", "page": 3, "text": "Псориаз: паракератоз, микроабсцессы Мунро, "
                                                        "равномерный акантоз, истончение над сосочками."},
        {"source": "derm_guide_ru", "page": 4, "text": "Базалиома: палисад по периферии, ретракция."},
    ] * 2

    def test_tfidf_ranks_the_right_chunks(self):
        from lab_core import histology_kb as kb

        vec, mat = kb._build_lexical(self.CHUNKS)
        self.assertIsNotNone(vec)
        sc = kb._lexical_scores(vec, mat, ["psoriasis Munro microabscess parakeratosis",
                                           "псориаз микроабсцессы Мунро"])
        top = sorted(range(len(self.CHUNKS)), key=lambda i: -sc[i])[:2]
        for i in top:
            t = self.CHUNKS[i]["text"].lower()
            self.assertTrue("psoriasis" in t or "псориаз" in t, t)
