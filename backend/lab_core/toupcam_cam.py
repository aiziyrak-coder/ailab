"""Euromex/ToupTek USB mikroskop (WinUSB, VID_0547) — toupcam.dll orqali tasvir."""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading

log = logging.getLogger("medlab")

TOUPCAM_INDEX_BASE = 16
TOUPCAM_EVENT_IMAGE = 0x0004
S_OK = 0

_DLL_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vendor-drivers", "toupcam.dll"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "toupcam.dll"),
    r"C:\Program Files\ImageFocus Plus V3\toupcam.dll",
    r"C:\Program Files\ImageFocus Plus V3\x64\toupcam.dll",
    r"C:\Program Files (x86)\ImageFocus Plus V3\toupcam.dll",
    r"C:\Program Files\ImageFocus Plus\toupcam.dll",
    r"C:\Program Files\ToupTek\ToupView\toupcam.dll",
    r"C:\Program Files\ToupView\toupcam.dll",
]


class ToupcamResolution(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint), ("height", ctypes.c_uint)]


class ToupcamModelV2(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_wchar_p),
        ("flag", ctypes.c_ulonglong),
        ("maxspeed", ctypes.c_uint),
        ("preview", ctypes.c_uint),
        ("still", ctypes.c_uint),
        ("maxfanspeed", ctypes.c_uint),
        ("ioctrol", ctypes.c_uint),
        ("xpixsz", ctypes.c_float),
        ("ypixsz", ctypes.c_float),
        ("res", ToupcamResolution * 16),
    ]


class ToupcamDeviceV2(ctypes.Structure):
    _fields_ = [
        ("displayname", ctypes.c_wchar * 64),
        ("id", ctypes.c_wchar * 64),
        ("model", ctypes.POINTER(ToupcamModelV2)),
    ]


class ToupcamFrameInfoV2(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint),
        ("height", ctypes.c_uint),
        ("flag", ctypes.c_uint),
        ("seq", ctypes.c_uint),
        ("timestamp", ctypes.c_ulonglong),
    ]


class ToupcamFrameInfoV3(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint),
        ("height", ctypes.c_uint),
        ("flag", ctypes.c_uint),
        ("seq", ctypes.c_uint),
        ("timestamp", ctypes.c_ulonglong),
        ("shutterseq", ctypes.c_uint),
        ("expotime", ctypes.c_uint),
        ("expogain", ctypes.c_ushort),
        ("blacklevel", ctypes.c_ushort),
    ]


_dll = None
_dll_lock = threading.Lock()
EVENT_CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.c_uint, ctypes.c_void_p)


def _iter_dll_paths():
    seen = set()
    extra = os.environ.get("TOUPCAM_DLL", "").strip()
    paths = ([extra] if extra else []) + list(_DLL_CANDIDATES)
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vendor-drivers")
    if os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if fn.lower() == "toupcam.dll":
                    paths.append(os.path.join(dirpath, fn))
    for p in paths:
        if not p:
            continue
        ap = os.path.abspath(p)
        if ap.lower() in seen:
            continue
        seen.add(ap.lower())
        if os.path.isfile(ap):
            yield ap


def load_dll():
    global _dll
    if _dll is not None:
        return _dll
    if sys.platform != "win32":
        return None
    with _dll_lock:
        if _dll is not None:
            return _dll
        last_err = None
        for path in _iter_dll_paths():
            try:
                d = ctypes.WinDLL(path)
                if not hasattr(d, "Toupcam_EnumV2") and not hasattr(d, "Toupcam_Open"):
                    continue
                ver = "?"
                try:
                    d.Toupcam_Version.restype = ctypes.c_wchar_p
                    ver = d.Toupcam_Version() or "?"
                except Exception:
                    pass
                log.info("toupcam.dll yuklandi: %s (%s)", path, ver)
                _dll = d
                return _dll
            except OSError as e:
                last_err = e
                continue
        if last_err:
            log.warning("toupcam.dll yuklanmadi: %s", last_err)
        return None


def dll_available():
    return load_dll() is not None


def enum_devices():
    """[{id, name, model}] — ToupTek SDK ko‘radigan kameralar."""
    d = load_dll()
    if not d or not hasattr(d, "Toupcam_EnumV2"):
        return []
    arr = (ToupcamDeviceV2 * 16)()
    d.Toupcam_EnumV2.restype = ctypes.c_uint
    d.Toupcam_EnumV2.argtypes = [ctypes.POINTER(ToupcamDeviceV2)]
    n = int(d.Toupcam_EnumV2(arr) or 0)
    out = []
    for i in range(min(n, 16)):
        dev = arr[i]
        model = ""
        try:
            if dev.model:
                model = dev.model.contents.name or ""
        except Exception:
            model = ""
        name = (dev.displayname or "").strip() or model or f"ToupTek kamera {i}"
        out.append({"id": dev.id, "name": name, "model": model, "slot": i})
    return out


def _ok(hr):
    return int(ctypes.c_long(hr).value) >= 0


class ToupCamCapture:
    """cv2.VideoCapture ga o‘xshash interfeys: isOpened/read/get/release."""

    def __init__(self):
        self._h = None
        self._buf = None
        self._w = 0
        self._hgt = 0
        self._cb = None
        self._evt = threading.Event()
        self._lock = threading.Lock()
        self._use_wait = False

    def isOpened(self):
        return self._h is not None

    def get(self, prop):
        import cv2
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._w)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._hgt)
        return 0.0

    def set(self, _prop, _val):
        return False

    def _on_event(self, n_event, _ctx):
        if n_event == TOUPCAM_EVENT_IMAGE:
            self._evt.set()

    def _pick_preview(self, d, h):
        w = ctypes.c_int()
        ht = ctypes.c_int()
        nres = 1
        try:
            d.Toupcam_get_ResolutionNumber.restype = ctypes.c_int
            d.Toupcam_get_ResolutionNumber.argtypes = [ctypes.c_void_p]
            nres = max(1, int(d.Toupcam_get_ResolutionNumber(h)))
        except Exception:
            nres = 1
        best_i, best_score = 0, 10**18
        for i in range(nres):
            try:
                d.Toupcam_get_Resolution.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint,
                    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                ]
                d.Toupcam_get_Resolution(h, i, ctypes.byref(w), ctypes.byref(ht))
                ww, hh = int(w.value), int(ht.value)
            except Exception:
                continue
            # USB2 uchun 720p atrofi — tezroq kadr
            score = abs(ww * hh - 1280 * 720)
            if ww > 1920 or hh > 1200:
                score += 5_000_000
            if score < best_score:
                best_score, best_i = score, i
                self._w, self._hgt = ww, hh
        try:
            d.Toupcam_put_eSize.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            d.Toupcam_put_eSize(h, best_i)
            d.Toupcam_get_Size.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)
            ]
            d.Toupcam_get_Size(h, ctypes.byref(w), ctypes.byref(ht))
            self._w, self._hgt = int(w.value), int(ht.value)
        except Exception:
            pass
        if self._w <= 0:
            self._w, self._hgt = 1280, 720

    def open(self, cam_id=None):
        import numpy as np

        d = load_dll()
        if not d:
            return False
        d.Toupcam_Open.restype = ctypes.c_void_p
        d.Toupcam_Open.argtypes = [ctypes.c_wchar_p]
        h = d.Toupcam_Open(cam_id)
        if not h:
            return False
        self._h = h
        try:
            d.Toupcam_put_AutoExpoEnable.argtypes = [ctypes.c_void_p, ctypes.c_int]
            d.Toupcam_put_AutoExpoEnable(h, 1)
        except Exception:
            pass
        self._pick_preview(d, h)
        nbytes = int(self._w) * int(self._hgt) * 3
        self._buf = (ctypes.c_ubyte * nbytes)()
        self._np = np

        self._use_wait = hasattr(d, "Toupcam_WaitImageV3")
        if self._use_wait:
            try:
                d.Toupcam_StartPullModeWithCallback.argtypes = [
                    ctypes.c_void_p, EVENT_CALLBACK, ctypes.c_void_p
                ]
                d.Toupcam_StartPullModeWithCallback.restype = ctypes.c_long
                self._cb = EVENT_CALLBACK(lambda *_a: None)
                hr = d.Toupcam_StartPullModeWithCallback(h, self._cb, None)
                if not _ok(hr):
                    self._use_wait = False
            except Exception:
                self._use_wait = False

        if not self._use_wait:
            self._cb = EVENT_CALLBACK(self._on_event)
            d.Toupcam_StartPullModeWithCallback.argtypes = [
                ctypes.c_void_p, EVENT_CALLBACK, ctypes.c_void_p
            ]
            d.Toupcam_StartPullModeWithCallback.restype = ctypes.c_long
            hr = d.Toupcam_StartPullModeWithCallback(h, self._cb, None)
            if not _ok(hr):
                log.warning("Toupcam StartPullMode xato: hr=%s", hr)
                self.release()
                return False
        return True

    def _pull(self):
        d = _dll
        h = self._h
        info2 = ToupcamFrameInfoV2()
        pitch = int(self._w) * 3
        if self._use_wait and hasattr(d, "Toupcam_WaitImageV3"):
            info3 = ToupcamFrameInfoV3()
            d.Toupcam_WaitImageV3.restype = ctypes.c_long
            d.Toupcam_WaitImageV3.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.POINTER(ToupcamFrameInfoV3),
            ]
            hr = d.Toupcam_WaitImageV3(
                h, 800, ctypes.byref(self._buf), 0, 24, pitch, ctypes.byref(info3)
            )
            if _ok(hr):
                w = int(info3.width or self._w)
                ht = int(info3.height or self._hgt)
                return True, w, ht
            return False, 0, 0
        if hasattr(d, "Toupcam_PullImageWithRowPitchV2"):
            d.Toupcam_PullImageWithRowPitchV2.restype = ctypes.c_long
            d.Toupcam_PullImageWithRowPitchV2.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(ToupcamFrameInfoV2),
            ]
            hr = d.Toupcam_PullImageWithRowPitchV2(
                h, ctypes.byref(self._buf), 24, pitch, ctypes.byref(info2)
            )
        else:
            d.Toupcam_PullImageV2.restype = ctypes.c_long
            d.Toupcam_PullImageV2.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                ctypes.POINTER(ToupcamFrameInfoV2),
            ]
            hr = d.Toupcam_PullImageV2(h, ctypes.byref(self._buf), 24, ctypes.byref(info2))
        if not _ok(hr):
            return False, 0, 0
        w = int(info2.width or self._w)
        ht = int(info2.height or self._hgt)
        return True, w, ht

    def read(self):
        import cv2

        if not self._h:
            return False, None
        with self._lock:
            if not self._use_wait:
                if not self._evt.wait(1.0):
                    return False, None
                self._evt.clear()
            ok, w, ht = self._pull()
            if not ok:
                return False, None
            arr = self._np.frombuffer(self._buf, dtype=self._np.uint8)
            need = w * ht * 3
            if arr.size < need:
                return False, None
            frame = arr[:need].reshape((ht, w, 3)).copy()
            return True, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def release(self):
        h = self._h
        self._h = None
        if not h:
            return
        d = _dll
        try:
            if d and hasattr(d, "Toupcam_Stop"):
                d.Toupcam_Stop.argtypes = [ctypes.c_void_p]
                d.Toupcam_Stop(h)
        except Exception:
            pass
        try:
            if d and hasattr(d, "Toupcam_Close"):
                d.Toupcam_Close.argtypes = [ctypes.c_void_p]
                d.Toupcam_Close(h)
        except Exception:
            pass


def open_toupcam(slot=0):
    devs = enum_devices()
    cap = ToupCamCapture()
    cam_id = None
    if devs:
        i = min(max(int(slot), 0), len(devs) - 1)
        cam_id = devs[i]["id"]
    if not cap.open(cam_id):
        return None
    ok, frame = cap.read()
    if not ok or frame is None:
        # bir necha urinish — auto-expo isishi uchun
        for _ in range(8):
            ok, frame = cap.read()
            if ok and frame is not None:
                break
    if not ok:
        cap.release()
        return None
    return cap
