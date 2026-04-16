"""
MedLab AI REST API va kamera oqimi.
Flask loyihadagi marshrutlar bilan mos keladi (/api/*, /video_feed).
"""
import io
import os
import threading
import time
from pathlib import Path

import cv2
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.views.generic import TemplateView
from PIL import Image
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from lab_core import engine as eng

from .serializers import AnalyzeJsonSerializer, StartCameraSerializer
from .template_mixins import MedlabPublicTemplateMixin
from .throttling import AnalyzeThrottle, CameraThrottle
from .version import MEDLAB_VERSION


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
    return StreamingHttpResponse(
        eng.generate_mjpeg(),
        content_type="multipart/x-mixed-replace; boundary=frame",
    )


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

        eng.ensure_gemini_from_env()
        ziyrakai_ready = eng.gemini_model is not None
        overall = db_ok and snap_ok
        payload = {
            "ok": overall,
            "service": "medlab-ai",
            "version": MEDLAB_VERSION,
            "database": db_ok,
            "snapshot_dir_writable": snap_ok,
            "ziyrakai_ready": ziyrakai_ready,
            "product": eng.ZIYRAKAI_DISPLAY_NAME,
        }
        st = status.HTTP_200_OK if overall else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=st)


class ScanCamerasView(APIView):
    """GET /api/scan_cameras"""

    throttle_classes = [CameraThrottle]

    def get(self, request):
        return Response({"cameras": eng.scan_cameras()})


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
            time.sleep(0.35)

            cam = eng.open_camera(idx)
            if cam is None:
                return Response(
                    {"success": False, "message": f"Kamera {idx} ochilmadi."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            eng.camera = cam
            eng.camera_index = idx
            eng.stream_active = True
            threading.Thread(target=eng.capture_thread, daemon=True).start()

            w = int(cam.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cam.get(cv2.CAP_PROP_FRAME_HEIGHT))

        return Response(
            {
                "success": True,
                "message": f"Kamera {idx} ulandi ({w}×{h})",
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
            lab_type = vd.get("lab_type", "hematology")
            raw_prompt = vd.get("prompt")
            source = vd.get("source", "upload")
            micro_d = eng.microscope_dict_from_input(json_body=raw)
        else:
            lab_type = request.POST.get("lab_type", "hematology")
            raw_prompt = request.POST.get("prompt", None)
            source = request.POST.get("source", "upload")
            micro_d = eng.microscope_dict_from_input(form_get=request.POST.get)

        if source not in ("camera", "upload"):
            source = "upload"

        lab_type = eng._normalize_lab_type(lab_type)
        custom_prompt = (
            eng._truncate_field(raw_prompt, eng.MAX_CUSTOM_PROMPT_LEN)
            if raw_prompt
            else None
        )

        eng.ensure_gemini_from_env()
        if eng.gemini_model is None:
            return Response(
                {
                    "success": False,
                    "message": (
                        "ZiyrakAi ishlamayapti: GEMINI_API_KEY backend/.env faylida yo‘q yoki bo‘sh. "
                        "Serverda: nano /opt/ailab/backend/.env — GEMINI_API_KEY=... qo‘shing, "
                        "keyin: sudo systemctl restart ailab-gunicorn"
                    ),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        micro_pfx = eng._microscope_prompt_prefix(micro_d)

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
                with eng.analysis_lock:
                    if eng.latest_analysis.get("loading"):
                        return Response(
                            {
                                "success": False,
                                "busy": True,
                                "message": (
                                    "Boshqa tahlil hali bajarilmoqda. Natija chiqishini kuting "
                                    "yoki birozdan keyin qayta urining."
                                ),
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    eng.latest_analysis.update(
                        {
                            "loading": True,
                            "status": "tahlil_qilinmoqda",
                            "lab_type": lab_type,
                            "text": "",
                            "lines": [],
                        }
                    )
                threading.Thread(
                    target=eng.do_analyze,
                    args=([pil_img], lab_type, custom_prompt, micro_pfx),
                    daemon=True,
                ).start()
                return Response(
                    {
                        "success": True,
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
            if len(files) > eng.MAX_UPLOAD_FILES:
                return Response(
                    {
                        "success": False,
                        "message": f"Bir vaqtning o'zida maksimum {eng.MAX_UPLOAD_FILES} ta fayl yuklash mumkin.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            pil_images = []
            video_files = []
            upload_notes = []

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

            with eng.analysis_lock:
                if eng.latest_analysis.get("loading"):
                    return Response(
                        {
                            "success": False,
                            "busy": True,
                            "message": (
                                "Boshqa tahlil hali bajarilmoqda. Natija chiqishini kuting "
                                "yoki birozdan keyin qayta urining."
                            ),
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                eng.latest_analysis.update(
                    {
                        "loading": True,
                        "status": (
                            "video_tahlil_qilinmoqda"
                            if video_files
                            else "tahlil_qilinmoqda"
                        ),
                        "lab_type": lab_type,
                        "text": "",
                        "lines": [],
                    }
                )

            if video_files:
                vdata, vname = video_files[0]
                if len(video_files) > 1:
                    upload_notes.append(
                        "Bir vaqtda faqat bitta video tahlil qilinadi (birinchi tanlangan: "
                        f"{vname})."
                    )
                threading.Thread(
                    target=eng.do_analyze_video,
                    args=(
                        vdata,
                        lab_type,
                        custom_prompt,
                        pil_images,
                        micro_pfx,
                        vname,
                    ),
                    daemon=True,
                ).start()
                if pil_images:
                    msg = f"Video ({vname}) va {len(pil_images)} ta rasm tahlil qilinmoqda..."
                else:
                    msg = f"Video tahlil qilinmoqda: {vname}"
            else:
                threading.Thread(
                    target=eng.do_analyze,
                    args=(pil_images, lab_type, custom_prompt, micro_pfx),
                    daemon=True,
                ).start()
                msg = f"{len(pil_images)} ta rasm tahlil qilinmoqda..."

            out = {"success": True, "message": msg, "count": total}
            if upload_notes:
                out["warnings"] = upload_notes
            return Response(out)

        except Exception as e:
            with eng.analysis_lock:
                eng.latest_analysis.update({"loading": False})
            eng.log.exception("api/analyze: %s", e)
            if settings.DEBUG:
                err_msg = str(e)
            else:
                err_msg = "Serverda ichki xato. Administratorga murojaat qiling yoki keyinroq qayta urining."
            return Response(
                {"success": False, "message": err_msg},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AnalysisResultView(APIView):
    """GET /api/analysis_result"""

    def get(self, request):
        with eng.analysis_lock:
            return Response(eng.latest_analysis.copy())


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
        eng.ensure_gemini_from_env()
        return Response(
            {
                "stream_active": eng.stream_active,
                "has_frame": eng.latest_frame is not None,
                "ziyrakai_ready": eng.gemini_model is not None,
                "product": eng.ZIYRAKAI_DISPLAY_NAME,
                "analysis": eng.latest_analysis.copy(),
            }
        )
