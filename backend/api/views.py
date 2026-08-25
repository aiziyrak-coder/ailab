"""
MedLab AI REST API va kamera oqimi.
Flask loyihadagi marshrutlar bilan mos keladi (/api/*, /video_feed).
"""
import io
import os
import re
import threading
import time
from pathlib import Path

import cv2
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import close_old_connections, connection
from django.db.models import Q
from django.db.models.functions import Substr
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView
from PIL import Image
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from lab_core import engine as eng

from .models import AnalysisRecord
from .serializers import (
    AnalysisListSerializer,
    AnalysisRecordSerializer,
    AnalysisSearchSerializer,
    AnalyzeJsonSerializer,
    StartCameraSerializer,
)
from .template_mixins import MedlabPublicTemplateMixin
from .throttling import AnalyzeThrottle, CameraThrottle
from .version import MEDLAB_VERSION

_ID_EXACT_RE = re.compile(r"^(?:ML)?(\d{6})(\d{4})$", re.IGNORECASE)
_EMPTY_ANALYSIS = {
    "text": "",
    "lines": [],
    "timestamp": "",
    "status": "kutilmoqda",
    "loading": False,
    "lab_type": "",
    "job_id": "",
    "public_id": "",
}


def _normalize_analysis_query(q):
    return re.sub(r"[^A-Za-z0-9]", "", (q or "").strip()).upper()


def _canonical_public_id(value):
    compact = _normalize_analysis_query(value)
    m = _ID_EXACT_RE.fullmatch(compact)
    if not m:
        return None
    return f"ML-{m.group(1)}-{m.group(2)}"


def _filter_analyses(qs, q):
    raw = (q or "").strip()
    if not raw:
        return qs
    exact = _canonical_public_id(raw)
    if exact:
        return qs.filter(Q(public_id__iexact=exact) | Q(sample_id__iexact=raw))
    cleaned = re.sub(r"[%_\\]", "", raw).strip()
    if not cleaned:
        return qs.none()
    return qs.filter(
        Q(public_id__icontains=cleaned)
        | Q(sample_id__icontains=cleaned)
        | Q(patient_name__icontains=cleaned)
    )


def _patient_meta(request):
    ct = request.content_type or ""
    if "application/json" in ct and isinstance(getattr(request, "data", None), dict):
        name = request.data.get("patient_name") or ""
        sid = request.data.get("sample_id") or ""
    else:
        name = request.POST.get("patient_name") or ""
        sid = request.POST.get("sample_id") or ""
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:120]
    sid = re.sub(r"[^A-Za-z0-9]", "", str(sid or "")).upper()[:40]
    return name, sid


def _patient_context_from_request(request):
    """Bemor kartasidagi barcha maydonlar — tahlil promptiga kiradi."""
    ct = request.content_type or ""
    src = request.data if ("application/json" in ct and isinstance(getattr(request, "data", None), dict)) else request.POST

    def g(key, maxlen=120):
        return eng._truncate_field(src.get(key) if hasattr(src, "get") else "", maxlen)

    name, sid = _patient_meta(request)
    return {
        "patient_name": name,
        "sample_id": sid,
        "age": g("age", 8),
        "sex": g("sex", 16),
        "ward": g("ward", 80),
        "specimen_site": g("specimen_site", 80),
        "clinical_note": g("clinical_note", 200),
        "region": g("region", 40),
        "locality": g("locality", 80),
        "clinic": g("clinic", 8),
        "facility_type": g("facility_type", 8),
        "priority": g("priority", 16),
    }


def _record_to_analysis_payload(rec):
    pending = rec.status in ("tahlil_qilinmoqda", "video_tahlil_qilinmoqda")
    ts = ""
    if rec.updated_at:
        ts = timezone.localtime(rec.updated_at).strftime("%H:%M:%S")
    return {
        "text": rec.text or "",
        "lines": [l.strip() for l in (rec.text or "").split("\n") if l.strip()],
        "timestamp": ts,
        "status": rec.status,
        "loading": pending,
        "lab_type": rec.lab_type,
        "job_id": rec.job_id,
        "public_id": rec.public_id,
        "img_count": rec.img_count,
    }


def _analysis_snapshot_for(request):
    with eng.analysis_lock:
        snap = eng.latest_analysis.copy()
    owner = snap.get("user_id")
    if owner is not None and owner != request.user.id:
        return dict(_EMPTY_ANALYSIS)
    snap.pop("user_id", None)
    return snap


def _busy_response(request):
    with eng.analysis_lock:
        snap = eng.latest_analysis.copy()
    payload = {
        "success": False,
        "busy": True,
        "job_id": "",
        "public_id": "",
        "message": (
            "Boshqa tahlil hali bajarilmoqda. Natija chiqishini kuting "
            "yoki birozdan keyin qayta urining."
        ),
    }
    if snap.get("user_id") == request.user.id:
        payload["job_id"] = snap.get("job_id") or ""
        payload["public_id"] = snap.get("public_id") or ""
    return Response(payload, status=status.HTTP_409_CONFLICT)


def _attach_analysis_record(request, lab_type, source, job_id, img_count=0, status="tahlil_qilinmoqda"):
    rec = None
    ctx = _patient_context_from_request(request)
    try:
        rec = AnalysisRecord.create_pending(
            user=request.user,
            lab_type=lab_type,
            source=source,
            job_id=job_id or "",
            img_count=img_count,
            status=status,
            patient_name=ctx.get("patient_name") or "",
            sample_id=ctx.get("sample_id") or "",
            age=ctx.get("age") or "",
            sex=ctx.get("sex") or "",
            ward=ctx.get("ward") or "",
            specimen_site=ctx.get("specimen_site") or "",
            clinical_note=ctx.get("clinical_note") or "",
            region=ctx.get("region") or "",
            locality=ctx.get("locality") or "",
            clinic=ctx.get("clinic") or "",
            facility_type=ctx.get("facility_type") or "",
        )
        with eng.analysis_lock:
            if eng.latest_analysis.get("job_id") == job_id:
                eng.latest_analysis["public_id"] = rec.public_id
    except Exception:
        eng.log.exception("Tahlil ID yaratilmadi")
    return rec


def _persist_analysis_record(record_pk, job_id=""):
    close_old_connections()
    if not record_pk:
        return
    try:
        rec = AnalysisRecord.objects.filter(pk=record_pk).first()
        if not rec:
            return
        snap = eng.take_completed_job(job_id) if job_id else None
        if snap is None:
            with eng.analysis_lock:
                snap = eng.latest_analysis.copy()
            if job_id and snap.get("job_id") and snap.get("job_id") != job_id:
                return
        rec.text = snap.get("text") or ""
        rec.status = snap.get("status") or rec.status
        try:
            rec.img_count = max(0, min(int(snap.get("img_count") or rec.img_count or 0), 32767))
        except (TypeError, ValueError):
            pass
        rec.save(update_fields=["text", "status", "img_count", "updated_at"])
    except Exception:
        eng.log.exception("Tahlil tarixga yozilmadi")
    finally:
        close_old_connections()


def _spawn_analyze(target, args, record_pk, job_id=""):
    def runner():
        close_old_connections()
        try:
            target(*args)
        finally:
            _persist_analysis_record(record_pk, job_id=job_id)
            close_old_connections()

    threading.Thread(target=runner, daemon=True).start()


def _abort_started_job(job_id, rec, message):
    if job_id:
        with eng.analysis_lock:
            if eng.latest_analysis.get("job_id") == job_id:
                eng._publish_analysis({
                    "loading": False,
                    "status": "xato",
                    "text": message,
                    "lines": [message],
                    "timestamp": time.strftime("%H:%M:%S"),
                })
    if rec is not None:
        try:
            rec.status = "xato"
            rec.text = message
            rec.save(update_fields=["status", "text", "updated_at"])
        except Exception:
            eng.log.exception("Tahlil xato holatiga yozilmadi")


def favicon_view(_request):
    try:
        base = Path(settings.FRONTEND_DIR).resolve()
        p = (base / "static" / "logo.png").resolve()
        p.relative_to(base)
        if not p.is_file():
            return HttpResponse(status=204)
    except (OSError, ValueError, RuntimeError):
        return HttpResponse(status=204)
    return FileResponse(p.open("rb"), content_type="image/png")


def video_feed_view(request):
    if not request.user.is_authenticated:
        return HttpResponse("Kirish kerak", status=401, content_type="text/plain; charset=utf-8")
    resp = StreamingHttpResponse(
        eng.generate_mjpeg(),
        content_type="multipart/x-mixed-replace; boundary=frame",
    )
    resp["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp["Pragma"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


@method_decorator(ensure_csrf_cookie, name="dispatch")
class IndexView(MedlabPublicTemplateMixin, LoginRequiredMixin, TemplateView):
    login_url = "/login"
    template_name = "index.html"


class HealthView(APIView):
    """GET /api/health — monitoring."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        db_ok = False
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False

        snap_ok = False
        try:
            p = Path(eng.SNAPSHOT_DIR)
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".health_write_probe"
            probe.write_text("1", encoding="utf-8")
            probe.unlink(missing_ok=True)
            snap_ok = True
        except OSError:
            snap_ok = False

        eng.ensure_openai_from_env()
        ziyrakai_ready = eng.openai_client is not None

        try:
            from lab_core.histology_kb import index_stats

            kb = index_stats()
        except Exception:
            kb = {"ready": False, "chunks": 0, "sources": {}}

        overall = db_ok and snap_ok
        payload = {
            "ok": overall,
            "service": "medlab-ai",
            "version": MEDLAB_VERSION,
            "env": getattr(settings, "DJANGO_ENV", ""),
            "database": db_ok,
            "snapshot_dir_writable": snap_ok,
            "ziyrakai_ready": ziyrakai_ready,
            "product": eng.ZIYRAKAI_DISPLAY_NAME,
            "knowledge_base": {
                "ready": bool(kb.get("ready")),
                "chunks": kb.get("chunks") or 0,
                "skin_chunks": kb.get("skin_chunks") or 0,
                "books": len(kb.get("sources") or {}),
            },
        }
        st = status.HTTP_200_OK if overall else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=st)


class ScanCamerasView(APIView):
    """GET /api/scan_cameras"""

    throttle_classes = [CameraThrottle]

    def get(self, request):
        data = eng.scan_cameras()
        if isinstance(data, dict) and "cameras" in data:
            return Response(data)
        return Response({"cameras": data})


class StartCameraView(APIView):
    """POST /api/start_camera — JSON: {\"index\": 0}"""

    throttle_classes = [CameraThrottle]

    def post(self, request):
        ser = StartCameraSerializer(data=request.data)
        if not ser.is_valid():
            return Response(
                {"success": False, "message": "Noto‘g‘ri kamera indeksi", "errors": ser.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        idx = ser.validated_data["index"]

        with eng.camera_op_lock:
            if eng.camera:
                eng.camera.release()
                eng.camera = None
            eng.stream_active = False
            with eng.frame_lock:
                eng.latest_frame = None
                eng.preview_jpeg = None
            time.sleep(0.35)

            cam = eng.open_camera(idx)
            if cam is None:
                usb = eng._probe_windows_microscope_usb()
                extra = ""
                if usb.get("found"):
                    extra = (
                        " Mikroskop USB da bor. Ro‘yxatdan ToupcamMicro ni tanlab qayta Yoqish ni bosing."
                    )
                return Response(
                    {"success": False, "message": f"Kamera {idx} ochilmadi." + extra},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            eng.camera = cam
            eng.camera_index = idx
            eng.stream_active = True
            threading.Thread(target=eng.capture_thread, daemon=True).start()

            deadline = time.time() + 6.0
            got = False
            while time.time() < deadline:
                with eng.frame_lock:
                    got = eng.latest_frame is not None
                if got:
                    break
                time.sleep(0.05)

            w = int(cam.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cam.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

            if not got:
                eng.stream_active = False
                time.sleep(0.2)
                if eng.camera:
                    eng.camera.release()
                    eng.camera = None
                return Response(
                    {
                        "success": False,
                        "message": (
                            "Kamera ochildi, lekin tasvir kelmadi. "
                            "Boshqa USB portga ulab qayta Yoqish ni bosing."
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response(
            {
                "success": True,
                "message": f"Ulandi ({w}×{h})" if w and h else "Ulandi",
            }
        )


class StopCameraView(APIView):
    """POST /api/stop_camera"""

    throttle_classes = [CameraThrottle]

    def post(self, request):
        with eng.camera_op_lock:
            eng.stream_active = False
            time.sleep(0.25)
            if eng.camera:
                eng.camera.release()
                eng.camera = None
            with eng.frame_lock:
                eng.latest_frame = None
                eng.preview_jpeg = None
        return Response({"success": True, "message": "Kamera to'xtatildi"})


class AnalyzeView(APIView):
    """
    POST /api/analyze
    - JSON: lab_type, prompt, source, microscope
    - multipart: lab_type, source, files[], file, micro_*
    """

    throttle_classes = [AnalyzeThrottle]

    def post(self, request):
        ct = request.content_type or ""
        if "application/json" in ct:
            raw = request.data if isinstance(request.data, dict) else {}
            ser = AnalyzeJsonSerializer(data=raw)
            if not ser.is_valid():
                return Response(
                    {"success": False, "message": "Noto‘g‘ri JSON", "errors": ser.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            vd = ser.validated_data
            lab_type = vd.get("lab_type", "histology")
            raw_prompt = vd.get("prompt")
            source = vd.get("source", "upload")
            micro_d = eng.microscope_dict_from_input(json_body=raw)
        else:
            lab_type = request.POST.get("lab_type", "histology")
            raw_prompt = request.POST.get("prompt", None)
            source = request.POST.get("source", "upload")
            micro_d = eng.microscope_dict_from_input(form_get=request.POST.get)

        if source not in ("camera", "upload", "phone"):
            source = "upload"

        lab_type = eng._normalize_lab_type(lab_type)
        custom_prompt = (
            eng._truncate_field(raw_prompt, eng.MAX_CUSTOM_PROMPT_LEN)
            if raw_prompt
            else None
        )

        eng.ensure_openai_from_env()
        if eng.openai_client is None:
            return Response(
                {
                    "success": False,
                    "message": (
                        "MedLab tahlil ishlamayapti: OPENAI_API_KEY backend/.env faylida yo‘q yoki bo‘sh. "
                        "Kalitni qo‘shib serverni qayta ishga tushiring."
                    ),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        micro_pfx = eng._microscope_prompt_prefix(micro_d)
        patient_ctx = _patient_context_from_request(request)
        job_id = ""
        rec = None
        spawned = False

        try:
            if source == "camera":
                with eng.frame_lock:
                    frame = (
                        eng.latest_frame.copy()
                        if eng.latest_frame is not None
                        else None
                    )
                if frame is None:
                    return Response(
                        {"success": False, "message": "Kamera yoqilmagan"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                bgr = eng._ensure_bgr_frame(frame)
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
                job_id = eng.begin_analysis_job(
                    lab_type, "tahlil_qilinmoqda", user_id=request.user.id
                )
                if not job_id:
                    return _busy_response(request)
                rec = _attach_analysis_record(
                    request, lab_type, source, job_id, img_count=1, status="tahlil_qilinmoqda"
                )
                eng.log.info(
                    "analyze_start user=%s lab=%s source=%s job=%s id=%s",
                    getattr(request.user, "username", ""),
                    lab_type,
                    source,
                    job_id,
                    rec.public_id if rec else "",
                )
                _spawn_analyze(
                    eng.do_analyze,
                    ([pil_img], lab_type, custom_prompt, micro_pfx, patient_ctx),
                    rec.pk if rec else None,
                    job_id=job_id,
                )
                spawned = True
                return Response(
                    {
                        "success": True,
                        "job_id": job_id,
                        "public_id": rec.public_id if rec else "",
                        "message": "Kamera kadri tahlil qilinmoqda...",
                    }
                )

            files = request.FILES.getlist("files[]")
            if not files or all(not getattr(f, "name", "") for f in files):
                files = request.FILES.getlist("file")
            if not files:
                return Response(
                    {"success": False, "message": "Fayl yuklanmagan"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            files = [f for f in files if f and getattr(f, "name", "")]
            extra_files = 0
            if len(files) > eng.MAX_UPLOAD_FILES:
                extra_files = len(files) - eng.MAX_UPLOAD_FILES
                files = files[: eng.MAX_UPLOAD_FILES]
                upload_notes_pre = [
                    f"Birinchi {eng.MAX_UPLOAD_FILES} ta fayl olindi "
                    f"({extra_files} tasi cheklov tufayli tashlandi)."
                ]
            else:
                upload_notes_pre = []

            pil_images = []
            video_files = []
            upload_notes = list(upload_notes_pre)

            for f in files:
                fname = (f.name or "fayl").strip()
                ext = os.path.splitext(fname.lower())[1]
                fdata = f.read()
                if not fdata:
                    upload_notes.append(f"{fname}: bo'sh fayl")
                    continue
                if ext in eng.VIDEO_EXT:
                    if len(fdata) > eng.MAX_VIDEO_BYTES:
                        upload_notes.append(
                            f"{fname}: video {eng.MAX_VIDEO_BYTES // (1024 * 1024)} MB dan oshmasligi kerak"
                        )
                        continue
                    video_files.append((fdata, fname))
                elif ext in eng.IMAGE_EXT:
                    try:
                        img = Image.open(io.BytesIO(fdata))
                        img.load()
                        pil_images.append(img.convert("RGB"))
                    except Image.DecompressionBombError:
                        upload_notes.append(f"{fname}: rasm hajmi cheklovdan oshgan")
                    except Exception as e:
                        eng.log.warning("Rasm o'qilmadi %s: %s", fname, e)
                        upload_notes.append(f"{fname}: rasm sifatida ochilmadi")
                else:
                    upload_notes.append(
                        f"{fname}: format qo'llab-quvvatlanmaydi ({ext or '—'})"
                    )

            total = len(pil_images) + len(video_files)
            if total == 0:
                msg = "Yaroqli fayl topilmadi"
                if upload_notes:
                    msg += " — " + "; ".join(upload_notes[:6])
                    if len(upload_notes) > 6:
                        msg += f" (+{len(upload_notes) - 6})"
                return Response(
                    {"success": False, "message": msg},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            job_status = (
                "video_tahlil_qilinmoqda" if video_files else "tahlil_qilinmoqda"
            )
            job_id = eng.begin_analysis_job(lab_type, job_status, user_id=request.user.id)
            if not job_id:
                return _busy_response(request)

            rec = _attach_analysis_record(
                request, lab_type, source, job_id, img_count=total, status=job_status
            )
            eng.log.info(
                "analyze_start user=%s lab=%s source=%s job=%s id=%s",
                getattr(request.user, "username", ""),
                lab_type,
                source,
                job_id,
                rec.public_id if rec else "",
            )

            if video_files:
                vdata, vname = video_files[0]
                if len(video_files) > 1:
                    upload_notes.append(
                        "Bir vaqtda faqat bitta video tahlil qilinadi (birinchi tanlangan: "
                        f"{vname})."
                    )
                _spawn_analyze(
                    eng.do_analyze_video,
                    (
                        vdata,
                        lab_type,
                        custom_prompt,
                        pil_images,
                        micro_pfx,
                        vname,
                        patient_ctx,
                    ),
                    rec.pk if rec else None,
                    job_id=job_id,
                )
                spawned = True
                if pil_images:
                    msg = f"Video ({vname}) va {len(pil_images)} ta rasm tahlil qilinmoqda..."
                else:
                    msg = f"Video tahlil qilinmoqda: {vname}"
            else:
                _spawn_analyze(
                    eng.do_analyze,
                    (pil_images, lab_type, custom_prompt, micro_pfx, patient_ctx),
                    rec.pk if rec else None,
                    job_id=job_id,
                )
                spawned = True
                msg = f"{len(pil_images)} ta rasm tahlil qilinmoqda..."

            out = {
                "success": True,
                "message": msg,
                "count": total,
                "job_id": job_id,
                "public_id": rec.public_id if rec else "",
            }
            if upload_notes:
                out["warnings"] = upload_notes
            return Response(out)

        except Exception as e:
            eng.log.exception("api/analyze: %s", e)
            if settings.DEBUG:
                err_msg = str(e)
            else:
                err_msg = "Serverda ichki xato. Administratorga murojaat qiling yoki keyinroq qayta urining."
            if not spawned:
                _abort_started_job(job_id, rec, err_msg)
            return Response(
                {"success": False, "message": err_msg},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AnalysisResultView(APIView):
    """GET /api/analysis_result — xotira, yo'q bo'lsa DB (ko'p worker)."""

    def get(self, request):
        job_id = (request.query_params.get("job_id") or "").strip()
        snap = _analysis_snapshot_for(request)
        mem_job = snap.get("job_id") or ""
        if snap.get("loading") or snap.get("status") in ("tayyor", "xato"):
            if not job_id or not mem_job or mem_job == job_id:
                return Response(snap)
        qs = AnalysisRecord.objects.filter(user=request.user)
        rec = qs.filter(job_id=job_id).first() if job_id else None
        if rec is None and not job_id:
            rec = qs.first()
        if rec is None:
            return Response(dict(_EMPTY_ANALYSIS))
        return Response(_record_to_analysis_payload(rec))


class AnalysisListView(APIView):
    """GET /api/analyses?q=&lab_type=&page="""

    def get(self, request):
        ser = AnalysisSearchSerializer(data=request.query_params)
        if not ser.is_valid():
            return Response(
                {"success": False, "message": "Noto‘g‘ri qidiruv", "errors": ser.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        q = ser.validated_data.get("q") or ""
        lab_type = (ser.validated_data.get("lab_type") or "").strip()
        try:
            page = max(1, int(request.query_params.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        page_size = 25

        qs = AnalysisRecord.objects.filter(user=request.user)
        lab_key = lab_type.strip().lower()
        if lab_key:
            if lab_key in eng.ALLOWED_LAB_TYPES:
                qs = qs.filter(lab_type=lab_key)
            else:
                qs = qs.none()
        qs = _filter_analyses(qs, q)
        total = qs.count()
        start = (page - 1) * page_size
        items = list(
            qs.annotate(_preview_src=Substr("text", 1, 180)).defer("text")[start : start + page_size]
        )
        exact = None
        canon = _canonical_public_id(q)
        if canon and items:
            exact = items[0].public_id
        return Response(
            {
                "success": True,
                "count": total,
                "page": page,
                "page_size": page_size,
                "has_more": start + len(items) < total,
                "exact_id": exact,
                "results": AnalysisListSerializer(items, many=True).data,
            }
        )


def _user_analysis_or_none(request, public_id):
    lookup = _canonical_public_id(public_id) or (public_id or "").strip()
    if not lookup:
        return None
    return AnalysisRecord.objects.filter(user=request.user, public_id__iexact=lookup).first()


class AnalysisDetailView(APIView):
    """GET / DELETE /api/analyses/<public_id>"""

    def get(self, request, public_id):
        rec = _user_analysis_or_none(request, public_id)
        if rec is None:
            return Response(
                {"success": False, "message": f"Tahlil topilmadi: {public_id}"},
                status=status.HTTP_404_NOT_FOUND,
            )
        data = AnalysisRecordSerializer(rec).data
        return Response({"success": True, "analysis": data})

    def delete(self, request, public_id):
        rec = _user_analysis_or_none(request, public_id)
        if rec is None:
            return Response(
                {"success": False, "message": f"Tahlil topilmadi: {public_id}"},
                status=status.HTTP_404_NOT_FOUND,
            )
        pid = rec.public_id
        rec.delete()
        return Response({"success": True, "message": "Tahlil o‘chirildi", "public_id": pid})


class PatientLookupView(APIView):
    """GET /api/patients/lookup?q= — oxirgi saqlangan bemor kartasi (avto-to‘ldirish)."""

    def get(self, request):
        q = re.sub(r"\s+", " ", (request.query_params.get("q") or "")).strip()
        if len(q) < 2:
            return Response({"success": True, "found": False, "patient": None})
        rec = (
            AnalysisRecord.objects.filter(user=request.user, patient_name__icontains=q)
            .exclude(patient_name="")
            .order_by("-created_at")
            .first()
        )
        if rec is None:
            # Aniqroq: boshidagi moslik
            rec = (
                AnalysisRecord.objects.filter(user=request.user, patient_name__istartswith=q[:40])
                .exclude(patient_name="")
                .order_by("-created_at")
                .first()
            )
        if rec is None:
            return Response({"success": True, "found": False, "patient": None})
        return Response(
            {
                "success": True,
                "found": True,
                "patient": {
                    "patient_name": rec.patient_name or "",
                    "age": rec.age or "",
                    "sex": rec.sex or "",
                    "ward": rec.ward or "",
                    "specimen_site": rec.specimen_site or "",
                    "clinical_note": rec.clinical_note or "",
                    "region": rec.region or "",
                    "locality": rec.locality or "",
                    "clinic": rec.clinic or "",
                    "facility_type": rec.facility_type or "",
                    "lab_type": rec.lab_type or "",
                    "last_public_id": rec.public_id,
                },
            }
        )


class CaptureView(APIView):
    """POST /api/capture"""

    throttle_classes = [CameraThrottle]

    def post(self, request):
        with eng.frame_lock:
            frame = (
                eng.latest_frame.copy()
                if eng.latest_frame is not None
                else None
            )
        if frame is None:
            return Response(
                {"success": False, "message": "Tasvir yo'q"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            os.makedirs(eng.SNAPSHOT_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            fn = os.path.join(eng.SNAPSHOT_DIR, f"snapshot_{ts}.jpg")
            ok = cv2.imwrite(fn, frame)
            if not ok:
                return Response(
                    {
                        "success": False,
                        "message": "Fayl yozilmadi (disk yoki format)",
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            snap_root = Path(eng.SNAPSHOT_DIR).resolve()
            fn_path = Path(fn).resolve()
            try:
                rel = fn_path.relative_to(snap_root).as_posix()
            except ValueError:
                rel = Path(fn).name
            return Response(
                {"success": True, "message": f"Saqlandi: snapshots/{rel}"}
            )
        except OSError as e:
            eng.log.warning("Snapshot: %s", e)
            return Response(
                {
                    "success": False,
                    "message": "Snapshot papkasiga yozib bo'lmadi",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class StatusView(APIView):
    """GET /api/status"""

    def get(self, request):
        eng.ensure_openai_from_env()
        return Response(
            {
                "stream_active": eng.stream_active,
                "has_frame": eng.latest_frame is not None,
                "ziyrakai_ready": eng.openai_client is not None,
                "product": eng.ZIYRAKAI_DISPLAY_NAME,
                "analysis": _analysis_snapshot_for(request),
            }
        )
