"""
ImageFocusAlpha yoki boshqa mikroskop dasturidan screen capture orqali
tasvirni olish va ZiyrakAi tahlili (texnik API orqali).

Bu yondashuv istalgan mikroskop dasturi bilan ishlaydi.
"""
import cv2
import numpy as np
import threading
import time
import subprocess
import sys
import os

try:
    import mss
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "mss"], check=True)
    import mss

try:
    import win32gui
    import win32con
    HAS_WIN32 = True
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "pywin32"], check=True)
    try:
        import win32gui, win32con
        HAS_WIN32 = True
    except:
        HAS_WIN32 = False


def find_microscope_window():
    """ImageFocusAlpha yoki boshqa mikroskop oynasini topish"""
    if not HAS_WIN32:
        return None, None

    target_titles = [
        'imagefocus', 'image focus', 'euromex', 'cmex',
        'microscop', 'mikroskop', 'camera preview', 'usb camera'
    ]

    found_hwnd = None
    found_title = None

    def callback(hwnd, _):
        nonlocal found_hwnd, found_title
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            for t in target_titles:
                if t in title:
                    found_hwnd = hwnd
                    found_title = win32gui.GetWindowText(hwnd)
                    return False
        return True

    win32gui.EnumWindows(callback, None)
    return found_hwnd, found_title


def capture_window(hwnd):
    """Berilgan oynadan screen capture"""
    if not HAS_WIN32 or not hwnd:
        return None
    try:
        rect = win32gui.GetWindowRect(hwnd)
        x, y, x2, y2 = rect
        w, h = x2 - x, y2 - y
        if w < 10 or h < 10:
            return None
        with mss.mss() as sct:
            monitor = {"top": y, "left": x, "width": w, "height": h}
            img = sct.grab(monitor)
            frame = np.array(img)
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    except:
        return None


def capture_fullscreen():
    """To'liq ekrandan capture"""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)


if __name__ == "__main__":
    print("Mikroskop oynasi qidirilmoqda...")
    hwnd, title = find_microscope_window()
    if hwnd:
        print(f"Topildi: '{title}'")
        frame = capture_window(hwnd)
        if frame is not None:
            cv2.imwrite("window_capture.jpg", frame)
            print(f"Oyna surati saqlandi: window_capture.jpg ({frame.shape})")
    else:
        print("Mikroskop oynasi topilmadi.")
        print("To'liq ekran surati olinmoqda...")
        frame = capture_fullscreen()
        cv2.imwrite("screen_capture.jpg", frame)
        print(f"Ekran surati saqlandi: screen_capture.jpg ({frame.shape})")
