#!/usr/bin/env python3
"""Local USB microscope bridge for https://lab.fermi.uz (ToupTek / DirectShow)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(str(BACKEND))

from lab_core import engine as eng  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("MEDLAB_CAM_PORT", "8012"))
ORIGINS = {
    "https://lab.fermi.uz",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
}


def _cors(handler: BaseHTTPRequestHandler) -> None:
    origin = (handler.headers.get("Origin") or "").rstrip("/")
    if origin in ORIGINS or origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost"):
        handler.send_header("Access-Control-Allow-Origin", origin)
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Private-Network", "true")
    handler.send_header("Vary", "Origin")
    handler.send_header("Cache-Control", "no-store")


def _json(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    _cors(handler)
    handler.end_headers()
    handler.wfile.write(raw)


def _start_cam(idx: int) -> dict:
    with eng.camera_op_lock:
        if eng.camera:
            eng.camera.release()
            eng.camera = None
        eng.stream_active = False
        with eng.frame_lock:
            eng.latest_frame = None
            eng.preview_jpeg = None
        time.sleep(0.25)
        cam = eng.open_camera(idx)
        if cam is None:
            return {"success": False, "message": f"Kamera {idx} ochilmadi."}
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
        if not got:
            eng.stream_active = False
            time.sleep(0.2)
            if eng.camera:
                eng.camera.release()
                eng.camera = None
            return {"success": False, "message": "Kamera ochildi, lekin tasvir kelmadi."}
        try:
            import cv2
            w = int(cam.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cam.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        except Exception:
            w = h = 0
        return {"success": True, "message": f"Ulandi ({w}×{h})" if w and h else "Ulandi"}


def _stop_cam() -> None:
    with eng.camera_op_lock:
        eng.stream_active = False
        time.sleep(0.2)
        if eng.camera:
            eng.camera.release()
            eng.camera = None
        with eng.frame_lock:
            eng.latest_frame = None
            eng.preview_jpeg = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[cam-agent] " + (fmt % args) + "\n")

    def do_OPTIONS(self):
        self.send_response(204)
        _cors(self)
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/api/cam-health", "/health"):
            _json(self, 200, {"ok": True, "service": "medlab-cam", "port": PORT})
            return
        if path == "/api/scan_cameras":
            data = eng.scan_cameras()
            if not isinstance(data, dict):
                data = {"cameras": data}
            data["agent"] = True
            _json(self, 200, data)
            return
        if path in ("/api/frame.jpg", "/api/frame"):
            with eng.frame_lock:
                jpeg = eng.preview_jpeg
            if not jpeg:
                self.send_response(503)
                _cors(self)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            _cors(self)
            self.end_headers()
            self.wfile.write(jpeg)
            return
        _json(self, 404, {"ok": False, "message": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = {}
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = {}
        if path == "/api/start_camera":
            try:
                idx = int(body.get("index"))
            except Exception:
                _json(self, 400, {"success": False, "message": "Noto‘g‘ri kamera indeksi"})
                return
            _json(self, 200, _start_cam(idx))
            return
        if path == "/api/stop_camera":
            _stop_cam()
            _json(self, 200, {"success": True, "message": "Kamera to'xtatildi"})
            return
        _json(self, 404, {"ok": False, "message": "not found"})


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    print(f"MedLab cam agent {HOST}:{PORT}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop_cam()
        httpd.server_close()


if __name__ == "__main__":
    main()
