#!/usr/bin/env python3
"""
PyshareX - Cross-platform screen capture and recording tool
Inspired by ShareX, built with Python and PySide6
"""

import sys
import os


def _fast_ipc_client():
    """`pysharex.py --action <nazwa>` (skróty systemowe): wyślij akcję do
    działającej instancji przez gniazdo unix ZANIM załadują się Qt i reszta
    modułów - skrót reaguje natychmiast i nie wymaga drugiego QApplication.
    Log: ~/.cache/pysharex/hotkey.log"""
    if os.name == "nt":
        return
    action = None
    for i, a in enumerate(sys.argv):
        if a == "--action" and i + 1 < len(sys.argv):
            action = sys.argv[i + 1]
        elif a.startswith("--action="):
            action = a.split("=", 1)[1]
    if not action:
        return
    import socket, time as _t
    cdir = os.path.expanduser("~/.cache/pysharex")

    def log(msg):
        try:
            os.makedirs(cdir, exist_ok=True)
            with open(os.path.join(cdir, "hotkey.log"), "a", encoding="utf-8") as f:
                f.write(f"{_t.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass

    name = "pysharex-" + (os.environ.get("USER") or os.environ.get("USERNAME") or "user")
    cands = []
    try:
        with open(os.path.join(cdir, "ipc-path"), encoding="utf-8") as f:
            cands.append(f.read().strip())
    except Exception:
        pass
    for d in (os.environ.get("TMPDIR"), "/tmp", os.environ.get("XDG_RUNTIME_DIR")):
        if d:
            cands.append(os.path.join(d, name))
    for path in dict.fromkeys(c for c in cands if c):
        try:
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.settimeout(1.5)
            c.connect(path)
            c.sendall((action + "\n").encode("utf-8"))
            c.close()
            log(f"--action {action}: wysłano do instancji ({path})")
            sys.exit(0)
        except SystemExit:
            raise
        except Exception as e:
            log(f"--action {action}: {path}: {e}")
    log(f"--action {action}: brak działającej instancji - uruchamiam program")


_fast_ipc_client()
import json
import time
import threading
import subprocess
import tempfile
import platform
import math
import struct
import wave as wv
import io
from pathlib import Path
from datetime import datetime

import urllib.request # Potrzebne do otwierania z sieci
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QSystemTrayIcon, QMenu, QFileDialog, QDialog, QLineEdit,
    QComboBox, QCheckBox, QGroupBox, QScrollArea, QFrame,
    QMessageBox, QListWidget, QListWidgetItem,
    QDialogButtonBox, QSpinBox, QTabWidget, QRadioButton, QButtonGroup,
    QTextEdit, QSizePolicy, QStackedWidget, QColorDialog, QInputDialog,
    QGraphicsScene, QGraphicsView, QGraphicsItem, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsTextItem,
    QWidgetAction, QTabBar
)
from PySide6.QtCore import (
    QPointF, Qt, QThread, Signal, QTimer, QSize, QRect, QPoint,
    QStandardPaths, QElapsedTimer, QLineF, QRectF, QSizeF, QObject, QEvent
)
from PySide6.QtGui import (
    QIcon, QKeySequence, QAction, QMouseEvent, QPixmap, QPainter, QColor,
    QFont, QPen, QBrush, QCursor, QPainterPath, QImage, QPainterPathStroker,
    QFontMetricsF
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QShortcut

try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    from pynput import keyboard as _pynput_kb
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import easyocr as _easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False

# Must be set before paddleocr/paddlepaddle is imported — disables OneDNN/MKL-DNN
# which causes ConvertPirAttribute2RuntimeAttribute crash on Windows CPU
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"

try:
    from paddleocr import PaddleOCR as _PaddleOCR
    PADDLEOCR_AVAILABLE = True
except ImportError:
    PADDLEOCR_AVAILABLE = False

try:
    import qrcode as _qrcode
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False

_easyocr_reader   = None   # lazy singleton — first use initialises it
_easyocr_reader_lang = None  # canonical lang code the cached reader was built for
_paddleocr_reader = None   # lazy singleton — first use initialises it
_paddleocr_reader_lang = None  # canonical lang code the cached reader was built for


# ── OCR language support ────────────────────────────────────────────────
# One canonical (ISO 639-1-ish) code per language, used in Settings and in
# config storage. Each OCR backend has its own naming scheme, so we keep a
# separate mapping per engine below rather than assuming they line up.
OCR_LANGUAGES = [
    ("English",    "en"),
    ("Polski",     "pl"),
    ("Deutsch",    "de"),
    ("Français",   "fr"),
    ("Español",    "es"),
    ("Italiano",   "it"),
    ("Português",  "pt"),
    ("Русский",    "ru"),
    ("Українська", "uk"),
    ("Čeština",    "cs"),
]
OCR_LANGUAGE_CODES = {code for _, code in OCR_LANGUAGES}
OCR_LANGUAGE_NAMES = {code: name for name, code in OCR_LANGUAGES}


def _make_ocr_lang_grid(checked_codes, columns: int = 3):
    """Build a QGridLayout of checkboxes covering OCR_LANGUAGES, with the
    codes in `checked_codes` pre-checked. Returns (grid, {code: checkbox})
    so callers can read back the selection later. Shared between the
    Settings dialog and the OCR/QR Toolbox so both look/behave the same."""
    grid = QGridLayout()
    checked = set(checked_codes or [])
    boxes = {}
    for i, (name, code) in enumerate(OCR_LANGUAGES):
        cb = QCheckBox(name)
        cb.setChecked(code in checked)
        boxes[code] = cb
        grid.addWidget(cb, i // columns, i % columns)
    return grid, boxes

# PaddleOCR's classic (v2/v3 python package) `lang=` parameter. Mostly
# matches the canonical code, except German which uses its English name.
_PADDLE_LANG_MAP = {
    "en": "en", "pl": "pl", "de": "german", "fr": "fr", "es": "es",
    "it": "it", "pt": "pt", "ru": "ru", "uk": "uk", "cs": "cs",
}

# Tesseract uses 3-letter ISO 639-2/T codes and needs the matching
# tessdata language pack installed (e.g. `tesseract-ocr-pol` on Debian/Ubuntu).
_TESSERACT_LANG_MAP = {
    "en": "eng", "pl": "pol", "de": "deu", "fr": "fra", "es": "spa",
    "it": "ita", "pt": "por", "ru": "rus", "uk": "ukr", "cs": "ces",
}

# EasyOCR uses ISO 639-1 codes directly, same as our canonical codes.
_EASYOCR_LANG_MAP = {code: code for _, code in OCR_LANGUAGES}


def _paddle_lang(code: str) -> str:
    return _PADDLE_LANG_MAP.get(code, code)


def _tesseract_lang(code: str) -> str:
    return _TESSERACT_LANG_MAP.get(code, code)


def _easyocr_langs(codes) -> list:
    """Map a list of canonical language codes to EasyOCR's codes, de-duped
    and order-preserved. EasyOCR requires all languages in one Reader to
    share a compatible character set (e.g. Latin-script languages can mix,
    but Cyrillic can't mix with most Latin-only ones) — an incompatible
    combination raises inside easyocr.Reader(), which callers should catch."""
    if isinstance(codes, str):
        codes = [codes]
    out = []
    for c in codes:
        mapped = _EASYOCR_LANG_MAP.get(c, c)
        if mapped not in out:
            out.append(mapped)
    return out or ["en"]


def _get_easyocr_reader(lang_codes="pl"):
    global _easyocr_reader, _easyocr_reader_lang
    langs = _easyocr_langs(lang_codes)
    key = tuple(langs)
    if EASYOCR_AVAILABLE and (_easyocr_reader is None or _easyocr_reader_lang != key):
        try:
            _easyocr_reader = _easyocr.Reader(langs, gpu=False, verbose=False)
            _easyocr_reader_lang = key
        except Exception as e:
            print(f"EasyOCR init error: {e}")
            _easyocr_reader = None
            _easyocr_reader_lang = None
    return _easyocr_reader


# Stores both the reader instance and which API generation it uses
_paddleocr_api_version = None   # "v3" | "v2" | None
_paddleocr_init_error  = None   # last init error message, shown to user

def _get_paddleocr_reader(lang_code: str = "pl"):
    global _paddleocr_reader, _paddleocr_reader_lang, _paddleocr_api_version, _paddleocr_init_error
    if PADDLEOCR_AVAILABLE and (_paddleocr_reader is None or _paddleocr_reader_lang != lang_code):
        # Disable OneDNN/MKL-DNN — causes ConvertPirAttribute crash on Windows
        os.environ.setdefault("FLAGS_use_mkldnn", "0")
        os.environ.setdefault("PADDLE_DISABLE_MKL", "1")
        os.environ.setdefault("FLAGS_onednn_cpu_enable", "0")
        # PaddleOCR 3.x removed use_angle_cls; try without it first.
        # PaddleOCR 2.x requires use_angle_cls=True for best results.
        # Build kwargs progressively — drop params that cause TypeError/unknown-arg errors
        def _try_init_paddle(kwargs: dict):
            """Try to init PaddleOCR, stripping one unknown kwarg at a time."""
            import copy
            kw = copy.copy(kwargs)
            removable = ["show_log", "use_angle_cls", "use_textline_orientation"]
            tried = set()
            while True:
                try:
                    return _PaddleOCR(**kw)
                except Exception as e:
                    msg = str(e)
                    removed = False
                    for param in removable:
                        if param in kw and param not in tried and (
                            "Unknown argument" in msg or param in msg
                        ):
                            del kw[param]
                            tried.add(param)
                            removed = True
                            break
                    if not removed:
                        raise  # nothing left to strip — real error

        pl = _paddle_lang(lang_code)
        last_err = None
        for ver, kwargs in [
            ("v3", {"lang": pl, "show_log": False}),
            ("v2", {"use_angle_cls": True, "lang": pl, "show_log": False}),
        ]:
            try:
                inst = _try_init_paddle(kwargs)
                _paddleocr_reader = inst
                _paddleocr_reader_lang = lang_code
                _paddleocr_api_version = ver
                break
            except Exception as e:
                last_err = e
                continue
        if _paddleocr_reader is None:
            _paddleocr_init_error = str(last_err)
            print(f"PaddleOCR init error: {last_err}")
            if IS_LINUX:
                print("[PyshareX] PaddleOCR may crash on Linux VMs or CPUs without AVX. "
                      "Switch to EasyOCR in Settings → OCR engine.")
    return _paddleocr_reader

def _paddle_extract(result_obj, api_version):
    """Extract (lines, avg_confidence) from a PaddleOCR result, for either
    the v3 (.predict) or v2 (.ocr) API result shapes. Used to score results
    from different language models against each other."""
    lines, confs = [], []
    if api_version == "v3":
        for item in (result_obj or []):
            texts, scores = None, None
            try:
                texts = item["rec_texts"]
                scores = item.get("rec_scores")
            except (KeyError, TypeError, AttributeError):
                texts = getattr(item, "rec_texts", None)
                scores = getattr(item, "rec_scores", None)
            if isinstance(texts, (list, tuple)):
                for i, t in enumerate(texts):
                    if not t:
                        continue
                    lines.append(str(t))
                    if scores and i < len(scores):
                        try: confs.append(float(scores[i]))
                        except Exception: pass
            elif isinstance(texts, str) and texts:
                lines.append(texts)
            else:
                try:
                    for line in item:
                        if line and len(line) >= 2:
                            text_info = line[1]
                            if isinstance(text_info, (list, tuple)) and text_info:
                                lines.append(str(text_info[0]))
                                if len(text_info) > 1:
                                    try: confs.append(float(text_info[1]))
                                    except Exception: pass
                            elif isinstance(text_info, str):
                                lines.append(text_info)
                except Exception:
                    pass
    else:
        for block in (result_obj or []):
            if block is None:
                continue
            for line in block:
                if line and len(line) >= 2:
                    text_info = line[1]
                    if isinstance(text_info, (list, tuple)) and text_info:
                        lines.append(str(text_info[0]))
                        if len(text_info) > 1:
                            try: confs.append(float(text_info[1]))
                            except Exception: pass
                    elif isinstance(text_info, str):
                        lines.append(text_info)
    avg_conf = (sum(confs) / len(confs)) if confs else (0.3 if lines else 0.0)
    return lines, avg_conf


IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"

def _get_visible_window_bounds(hwnd):
    """Zwraca (left, top, right, bottom) faktycznie widocznych granic okna (Windows).

    win32gui.GetWindowRect zwraca prostokąt razem z niewidzialną ramką/cieniem,
    które DWM dorysowuje wokół okna na Windows 10/11 (zwykle kilka-kilkanaście
    px marginesu z każdej strony) - przez co zrzut ekranu ma dodatkowy pusty
    margines i wygląda na nieprecyzyjnie przycięty. DwmGetWindowAttribute z
    DWMWA_EXTENDED_FRAME_BOUNDS zwraca realne, widoczne granice okna, bez
    tego marginesu.
    """
    import win32gui
    try:
        import ctypes
        from ctypes import wintypes
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        rect = wintypes.RECT()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return win32gui.GetWindowRect(hwnd)


def _get_child_window_bounds(hwnd, parent_bounds, min_size=24):
    """Widoczne okna potomne (HWND) `hwnd` jako fizyczne (l, t, r, b).

    To właśnie pozwala wykryć "fragment okna" - np. obszar strony w Chrome
    (Chrome_RenderWidgetHostHWND), panel podglądu, listę plików w Eksploratorze,
    pole edycji itp. - tak jak robi to ShareX. Prostokąty są przycięte do
    widocznych granic rodzica (dziecko potrafi wystawać poza okno), a te
    identyczne z rodzicem lub mniejsze niż `min_size` px są pomijane.
    """
    import win32gui
    pl, pt, pr, pb = parent_bounds
    seen, out = set(), []

    def _cb(ch, _):
        try:
            if not win32gui.IsWindowVisible(ch):
                return True
            l, t, r, b = win32gui.GetWindowRect(ch)
            l, t = max(l, pl), max(t, pt)
            r, b = min(r, pr), min(b, pb)
            if r - l < min_size or b - t < min_size:
                return True
            key = (l, t, r, b)
            if key == tuple(parent_bounds) or key in seen:
                return True
            seen.add(key)
            out.append(key)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        pass
    return out


def _is_window_cloaked(hwnd) -> bool:
    """
    True if the window is "cloaked" (Windows 8+ DWM concept): technically
    satisfies IsWindowVisible() but isn't actually shown to the user.

    This is what window-detection accidentally picks up without this check:
    Chrome (and many Electron/UWP apps) keep extra hidden helper windows
    alive — often sharing the visible tab's title — that IsWindowVisible()
    reports as visible but that the user never sees. DWM also cloaks any
    window sitting on a different virtual desktop. Filtering these out is
    the standard fix for the "detected rectangle doesn't match anything
    actually on screen" problem.
    """
    try:
        import ctypes
        from ctypes import wintypes
        DWMWA_CLOAKED = 14
        val = ctypes.c_int(0)
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_CLOAKED),
            ctypes.byref(val),
            ctypes.sizeof(val),
        )
        return hr == 0 and val.value != 0
    except Exception:
        return False

def _is_wayland_session() -> bool:
    """Wykrywa, czy bieżąca sesja graficzna to Wayland (np. przyszła sesja
    Cinnamon-Wayland w Linux Mint 23 / Cinnamon 6.8), a nie X11."""
    if IS_WINDOWS:
        return False
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session_type == "wayland":
        return True
    if session_type == "x11":
        return False
    # XDG_SESSION_TYPE bywa puste (np. w niektórych DM-ach) - dodatkowy fallback:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _overlay_window_flags(base=None):
    """Flagi okien-nakładek (wybór obszaru, edytor zrzutu), które muszą mieć
    fokus klawiatury (ESC, Ctrl+Z, ...).

    X11: BypassWindowManagerHint (nakładka nad wszystkim, bez WM).
    Wayland/XWayland: okna 'override-redirect' NIE dostają fokusu klawiatury
    od kompozytora, więc klawisze (nawet ESC) nie docierają. Tam okno musi
    być zwykłym, zarządzanym oknem (bez Bypass i bez Tool)."""
    W = Qt.WindowType
    flags = W.FramelessWindowHint | W.WindowStaysOnTopHint
    if not _is_wayland_session():
        flags |= W.BypassWindowManagerHint
        if base is not None:
            flags |= base
    return flags


# ══════════════════════════════════════════════════════════════════════════
#  WAYLAND BACKEND
#  Na Waylandzie aplikacja nie może czytać pikseli ekranu (mss/X11 zwraca
#  czerń albo tylko okna XWayland). Jedyna dozwolona droga to portal
#  xdg-desktop-portal (org.freedesktop.portal.Screenshot) albo narzędzie
#  kompozytora. Poniżej: (1) pobranie zrzutu całego pulpitu, (2) "zamrożona
#  klatka" dla nakładki wyboru obszaru, (3) cienka nakładka udająca
#  `mss.MSS()`, dzięki której cały istniejący kod (ok. 35 miejsc) działa
#  bez zmian. Na Windows/X11 nic z tego nie jest używane.
# ══════════════════════════════════════════════════════════════════════════
import shutil as _shutil
import json as _json
from urllib.parse import urlparse as _urlparse, unquote as _unquote

_WL_LOCK    = threading.Lock()
_WL_FROZEN  = None        # PIL.Image (RGB) zrzutu pulpitu albo None
_WL_GI_PY   = None        # interpreter, w którym działa `import gi`


def _wl_log(msg):
    print(f"[PyshareX][wayland] {msg}", flush=True)


# Pomocnik uruchamiany w osobnym procesie: Gio ma własną pętlę GLib, a sygnał
# Response portalu jest wysyłany TYLKO do połączenia, które wywołało metodę
# (dlatego nie da się go złapać przez `gdbus monitor`).
_WL_PORTAL_HELPER = r'''
import sys, os, json
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
interactive = sys.argv[1] == "1"
timeout = int(sys.argv[2])
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
sender = bus.get_unique_name()[1:].replace(".", "_")
token = "pysharex%d" % os.getpid()
handle = "/org/freedesktop/portal/desktop/request/%s/%s" % (sender, token)
loop = GLib.MainLoop()
result = {"code": None, "uri": None}
def on_response(conn, sender_name, path, iface, signal, params, *a):
    code, res = params.unpack()
    result["code"] = code
    result["uri"] = res.get("uri")
    loop.quit()
bus.signal_subscribe("org.freedesktop.portal.Desktop",
                     "org.freedesktop.portal.Request", "Response", handle,
                     None, Gio.DBusSignalFlags.NONE, on_response)
opts = {"handle_token": GLib.Variant("s", token),
        "interactive":  GLib.Variant("b", interactive),
        "modal":        GLib.Variant("b", False)}
bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
              "org.freedesktop.portal.Screenshot", "Screenshot",
              GLib.Variant("(sa{sv})", ("", opts)), None,
              Gio.DBusCallFlags.NONE, -1, None)
def on_timeout():
    loop.quit()
    return False
GLib.timeout_add_seconds(timeout, on_timeout)
loop.run()
print(json.dumps(result))
'''


def _wl_find_gi_python():
    """Pierwszy interpreter, w którym działa `import gi` (venv z PySide6 często
    go nie ma, a systemowy /usr/bin/python3 w Mint - tak)."""
    global _WL_GI_PY
    if _WL_GI_PY is not None:
        return _WL_GI_PY or None
    for py in (sys.executable, "/usr/bin/python3", _shutil.which("python3")):
        if not py:
            continue
        try:
            r = subprocess.run(
                [py, "-c", "import gi; gi.require_version('Gio','2.0'); "
                           "from gi.repository import Gio"],
                capture_output=True, timeout=10)
            if r.returncode == 0:
                _WL_GI_PY = py
                return py
        except Exception:
            pass
    _WL_GI_PY = ""
    _wl_log("brak PyGObject (gi) -> sudo apt install python3-gi gir1.2-glib-2.0")
    return None


def _wl_open_image(path, delete=False):
    if not PIL_AVAILABLE:
        return None
    try:
        with Image.open(path) as im:
            img = im.convert("RGB")
            img.load()
        return img
    except Exception as e:
        _wl_log(f"nie mogę otworzyć {path}: {e}")
        return None
    finally:
        if delete:
            try: os.unlink(path)
            except Exception: pass


def _wl_portal_image(interactive=False, timeout=30):
    """Zrzut przez xdg-desktop-portal. Zwraca PIL.Image (RGB) albo None.
    interactive=True: kompozytor pokazuje własny wybór okna/obszaru."""
    py = _wl_find_gi_python()
    if not py:
        return None
    try:
        r = subprocess.run(
            [py, "-c", _WL_PORTAL_HELPER, "1" if interactive else "0", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 10)
    except Exception as e:
        _wl_log(f"portal: wyjątek {e}")
        return None
    if r.returncode != 0:
        _wl_log(f"portal: błąd helpera: {r.stderr.strip()[-300:]}")
        return None
    try:
        res = _json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        _wl_log(f"portal: nieczytelna odpowiedź: {r.stdout!r}")
        return None
    if res.get("code") != 0 or not res.get("uri"):
        _wl_log(f"portal: odmowa/anulowanie (code={res.get('code')})")
        return None
    path = _unquote(_urlparse(res["uri"]).path)
    # Plik zrobił portal na nasze życzenie - po wczytaniu go sprzątamy.
    return _wl_open_image(path, delete=True)


def _wl_cli_image():
    """Zapasowe narzędzia, gdy portal nie działa (zależnie od pulpitu)."""
    tmp = str(Path(tempfile.gettempdir()) / f"pysharex_wl_{os.getpid()}_{int(time.time()*1000)}.png")
    candidates = [
        ["gnome-screenshot", "-f", tmp],                  # GNOME / Cinnamon (Mint)
        ["spectacle", "-b", "-n", "-f", "-o", tmp],       # KDE Plasma
        ["grim", tmp],                                    # sway / Hyprland (wlroots)
    ]
    for cmd in candidates:
        if not _shutil.which(cmd[0]):
            continue
        try:
            subprocess.run(cmd, capture_output=True, timeout=15)
        except Exception:
            continue
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            img = _wl_open_image(tmp, delete=True)
            if img is not None:
                _wl_log(f"zrzut przez {cmd[0]}")
                return img
    return None


def _wl_capture_full_image():
    """Cały pulpit (wszystkie monitory) jako PIL.Image RGB, w pikselach fizycznych."""
    with _WL_LOCK:
        img = _wl_portal_image(interactive=False)
        if img is not None:
            return img
        _wl_log("portal nie dał zrzutu - próbuję narzędzi CLI")
        img = _wl_cli_image()
        if img is None:
            _wl_log("NIE UDAŁO SIĘ zrobić zrzutu (portal ani CLI). "
                    "Sprawdź: xdg-desktop-portal + backend pulpitu "
                    "(np. xdg-desktop-portal-gtk / -xapp / -gnome).")
        return img


def _wl_freeze():
    """Robi jeden zrzut i trzyma go, dopóki nakładka jest otwarta - cała
    seria operacji (wybór obszaru, narzędzie Clip, Pixelate) pracuje na tej
    samej klatce, a nakładka nie wchodzi do zrzutu."""
    global _WL_FROZEN
    try:
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        time.sleep(0.15)    # daj kompozytorowi odrysować pulpit po ukryciu okna głównego
    except Exception:
        pass
    _WL_FROZEN = _wl_capture_full_image()
    if _WL_FROZEN is not None:
        _wl_log(f"zamrożona klatka {_WL_FROZEN.width}x{_WL_FROZEN.height}")


def _wl_unfreeze():
    global _WL_FROZEN
    _WL_FROZEN = None


def _wl_frozen_qimage(logical_size):
    """Zamrożona klatka jako QImage w rozmiarze logicznym nakładki (albo None)."""
    img = _WL_FROZEN
    if img is None:
        return None
    try:
        qi = QImage(img.tobytes(), img.width, img.height, img.width * 3,
                    QImage.Format.Format_RGB888).copy()
        return qi.scaled(logical_size, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    except Exception as e:
        _wl_log(f"frozen->QImage: {e}")
        return None


def _wl_screen_geoms():
    """Prostokąty ekranów w układzie Qt (przy xcb == układ X), jako (x, y, w, h)."""
    out = []
    for s in QApplication.screens():
        g = s.geometry()
        out.append((g.x(), g.y(), g.width(), g.height()))
    return out


class _WaylandShot:
    """Minimalny odpowiednik obiektu zwracanego przez mss.grab()."""
    def __init__(self, pil_rgb):
        self.size   = (pil_rgb.width, pil_rgb.height)
        self.width  = pil_rgb.width
        self.height = pil_rgb.height
        r, g, b = pil_rgb.split()
        a = Image.new("L", pil_rgb.size, 255)
        self.bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
        self.rgb  = pil_rgb.tobytes()


class _WaylandMSS:
    """Zamiennik `mss.MSS()` na Waylandzie: monitors[] i grab({...}) w
    współrzędnych Qt/X, a piksele z portalu (albo z zamrożonej klatki)."""

    def __init__(self):
        geoms = _wl_screen_geoms() or [(0, 0, 1920, 1080)]
        ux = min(g[0] for g in geoms); uy = min(g[1] for g in geoms)
        ur = max(g[0] + g[2] for g in geoms); ub = max(g[1] + g[3] for g in geoms)
        self._origin = (ux, uy)
        self._extent = (ur - ux, ub - uy)
        self.monitors = [{"left": ux, "top": uy, "width": ur - ux, "height": ub - uy}]
        for (x, y, w, h) in geoms:
            self.monitors.append({"left": x, "top": y, "width": w, "height": h})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def grab(self, mon):
        img = _WL_FROZEN if _WL_FROZEN is not None else _wl_capture_full_image()
        if img is None:
            raise RuntimeError("Wayland: brak zrzutu ekranu (portal/CLI)")
        ux, uy = self._origin
        ew, eh = self._extent
        sx, sy = img.width / max(1, ew), img.height / max(1, eh)   # skala fizyczna/logiczna
        l = int(round((mon["left"] - ux) * sx))
        t = int(round((mon["top"]  - uy) * sy))
        r = int(round((mon["left"] + mon["width"]  - ux) * sx))
        b = int(round((mon["top"]  + mon["height"] - uy) * sy))
        return _WaylandShot(img.crop((l, t, r, b)))


class _WaylandMSSModule:
    """Udaje moduł `mss` (kod używa wyłącznie `mss.MSS()`)."""
    MSS = _WaylandMSS


if _is_wayland_session() and IS_LINUX:
    mss = _WaylandMSSModule()
    MSS_AVAILABLE = PIL_AVAILABLE
    print("[PyshareX] sesja Wayland: piksele ekranu przez xdg-desktop-portal "
          "(zamiast mss/X11)", flush=True)


_X11_WARNED = set()

def _x11_warn(key, msg):
    """Wypisz powód, dla którego nie ma detekcji okien, tylko raz."""
    if key not in _X11_WARNED:
        _X11_WARNED.add(key)
        print(f"[PyshareX] wykrywanie okien (X11): {msg}")


def _get_x11_window_rects():
    """
    Widoczne okna najwyższego poziomu na sesji X11 (Xorg), przez EWMH.
    Działa na Linux Mint / Cinnamon, MATE, XFCE, KDE Plasma (X11) i każdym
    menedżerze okien zgodnym z EWMH.

    Zwraca listę (left, top, right, bottom) w pikselach roota, w kolejności
    z-order: NAJWYŻSZE okno pierwsze (z _NET_CLIENT_LIST_STACKING; jeśli WM
    go nie ma - z _NET_CLIENT_LIST, bez gwarancji kolejności). Nigdy nie
    rzuca wyjątku; przy braku X11 / python-xlib / EWMH zwraca [] i (raz)
    wypisuje w konsoli powód.
    """
    if not IS_LINUX:
        return []
    if _is_wayland_session():
        _x11_warn("wayland",
                  "sesja Wayland - aplikacje nie widzą pozycji cudzych okien. "
                  "Zaloguj się do sesji X11 (Cinnamon domyślnie jest na X11).")
        return []
    try:
        from Xlib import display, X
        from Xlib.error import XError
    except ImportError:
        _x11_warn("xlib", "brak python-xlib -> zainstaluj: pip install python-xlib "
                          "(albo: sudo apt install python3-xlib)")
        return []

    try:
        d    = display.Display()
        root = d.screen().root
        atom = d.intern_atom
        net_stacking        = atom('_NET_CLIENT_LIST_STACKING')
        net_client_list     = atom('_NET_CLIENT_LIST')
        net_wm_state        = atom('_NET_WM_STATE')
        net_wm_state_hidden = atom('_NET_WM_STATE_HIDDEN')
        net_frame_extents   = atom('_NET_FRAME_EXTENTS')

        ids, top_first = None, True
        prop = root.get_full_property(net_stacking, X.AnyPropertyType)
        if prop and prop.value is not None and len(prop.value):
            ids = list(prop.value)[::-1]          # stacking = od dołu do góry
        else:
            prop = root.get_full_property(net_client_list, X.AnyPropertyType)
            if prop and prop.value is not None and len(prop.value):
                ids, top_first = list(prop.value), False
        if not ids:
            _x11_warn("ewmh", "menedżer okien nie udostępnia _NET_CLIENT_LIST "
                              "(brak EWMH) - wykrywanie okien niemożliwe.")
            return []

        rects = []
        for wid in ids:
            try:
                win = d.create_resource_object('window', wid)

                attrs = win.get_attributes()
                if attrs.map_state != X.IsViewable:
                    continue  # minimized or unmapped

                state_prop = win.get_full_property(net_wm_state, X.AnyPropertyType)
                if state_prop and net_wm_state_hidden in (state_prop.value or []):
                    continue  # minimized (some WMs use _NET_WM_STATE_HIDDEN instead)

                geom = win.get_geometry()
                # Pozycja lewego-górnego rogu okna w układzie ROOTA.
                # UWAGA: translate_coords(src_window, x, y) tłumaczy punkt z
                # src_window NA układ obiektu, na którym jest wywołane - więc
                # wołamy je na root, a nie na oknie (odwrotnie dawało -x, -y).
                origin = root.translate_coords(win, 0, 0)
                x, y   = origin.x, origin.y
                w, h   = geom.width, geom.height

                # Client geometry excludes the WM decorations (title bar /
                # borders); expand by _NET_FRAME_EXTENTS when provided.
                extents = win.get_full_property(net_frame_extents, X.AnyPropertyType)
                if extents and extents.value is not None and len(extents.value) >= 4:
                    left, right, top, bottom = list(extents.value)[:4]
                    x -= left
                    y -= top
                    w += left + right
                    h += top + bottom

                if w < 20 or h < 20:
                    continue
                rects.append((x, y, x + w, y + h))
            except XError:
                continue
        d.close()
        if not top_first:
            _x11_warn("stacking", "brak _NET_CLIENT_LIST_STACKING - kolejność "
                                  "okien (z-order) może być niedokładna.")
        if not rects:
            _x11_warn("empty", "lista okien jest pusta.")
        return rects
    except Exception as e:
        _x11_warn("exc", f"błąd przy odczycie okien: {e!r}")
        return []


def _physical_rects_to_local(phys_rects, geo: QRect):
    """
    Convert a list of (left, top, right, bottom) PHYSICAL desktop-pixel
    rects into LOCAL QRects relative to `geo` (the union of all screens'
    logical geometry, i.e. EnhancedRegionSelector's own widget geometry).

    Each rect is matched to whichever screen's physical bounds contain its
    origin, then converted using THAT screen's own devicePixelRatio — so
    multi-monitor setups where different monitors use different DPI scale
    factors (mixed-DPI) still land each window at the correct local
    position and size. A rect that can't be matched to any screen falls
    back to the primary screen's scale factor.

    Used for both the Win32 (GetWindowRect/DwmGetWindowAttribute) and X11
    (EWMH via python-xlib) window-detection backends, which both report
    physical pixels.
    """
    screens = QApplication.screens()
    out = []
    for (pl, pt, pr, pb) in phys_rects:
        pw, ph = pr - pl, pb - pt
        if pw < 20 or ph < 20:
            continue

        scr = None
        for s in screens:
            lg  = s.geometry()
            dpr = s.devicePixelRatio()
            sx  = round(lg.x()      * dpr)
            sy  = round(lg.y()      * dpr)
            sw  = round(lg.width()  * dpr)
            sh  = round(lg.height() * dpr)
            if sx <= pl < sx + sw and sy <= pt < sy + sh:
                scr = s
                break
        if scr is None:
            scr = QApplication.primaryScreen()

        lg  = scr.geometry()
        dpr = scr.devicePixelRatio()
        ox  = round(lg.x() * dpr)
        oy  = round(lg.y() * dpr)

        # physical → logical global
        lx = lg.x() + (pl - ox) / dpr
        ly = lg.y() + (pt - oy) / dpr
        lw = max(1, pw / dpr)
        lh = max(1, ph / dpr)

        # logical global → LOCAL widget
        out.append(QRect(int(lx) - geo.x(), int(ly) - geo.y(), int(lw), int(lh)))
    return out


class _DialogDragMover(QObject):
    """Lets a window-manager-less dialog be moved with the mouse.

    On Linux the dialogs opened above the Capture overlay use X11BypassWindowManagerHint
    (otherwise they hide behind it), which also removes the title bar, so they could not
    be moved at all. While the dialog is shown, pressing the left button on its background,
    on a label or on a group-box frame and dragging moves the whole window. Buttons,
    sliders, spin boxes, colour pickers etc. are never hijacked."""
    _GRAB = (QDialog, QLabel, QGroupBox)

    def __init__(self, dlg):
        super().__init__(dlg)
        self._dlg = dlg
        self._start = None            # (global press point, window pos at press)
        self._on = True
        app = QApplication.instance()
        app.installEventFilter(self)
        # Bez metody-odbiorcy: ten obiekt jest dzieckiem `dlg`, więc przy niszczeniu okna
        # bywa już usunięty, zanim dotrze sygnał -> PySide6 wypisywał
        # "AttributeError: Slot '_DialogDragMover::' not found". Funkcja bez odbiorcy
        # QObject nie ma tego problemu.
        dlg.finished.connect(lambda *_a, _s=self: _s._remove())
        dlg.destroyed.connect(lambda *_a, _s=self: _s._remove())

    def _remove(self, *_):
        if not self._on:
            return
        self._on = False
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except RuntimeError:
                pass

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                     QEvent.Type.MouseButtonRelease):
            return False
        try:
            if not isinstance(obj, QWidget) or obj.window() is not self._dlg or not self._dlg.isVisible():
                return False
            if t == QEvent.Type.MouseButtonPress:
                if ev.button() == Qt.MouseButton.LeftButton and isinstance(obj, self._GRAB):
                    self._start = (ev.globalPosition().toPoint(), self._dlg.pos())
                return False                       # the widget still gets its normal press
            if t == QEvent.Type.MouseMove:
                if self._start is not None and (ev.buttons() & Qt.MouseButton.LeftButton):
                    gp = ev.globalPosition().toPoint()
                    self._dlg.move(self._start[1] + (gp - self._start[0]))
                    return True
                self._start = None
                return False
            self._start = None                      # release
            return False
        except RuntimeError:                        # dialog already deleted
            self._remove()
            return False


def _install_drag_move(dlg):
    """Make *dlg* draggable when it has no window-manager title bar (Linux overlay dialogs)."""
    if IS_LINUX and dlg is not None and getattr(dlg, '_drag_mover', None) is None:
        dlg._drag_mover = _DialogDragMover(dlg)


def _set_dialog_on_top(dlg):
    """Set dialog window flags so it appears above fullscreen overlays on all platforms.
    On Linux, X11BypassWindowManagerHint is required to float above fullscreen windows."""
    flags = Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
    if IS_LINUX:
        flags |= Qt.WindowType.X11BypassWindowManagerHint
    dlg.setWindowFlags(flags)
    _install_drag_move(dlg)
    dlg.raise_()
    dlg.activateWindow()


def _show_color_dialog(initial_color: QColor, parent=None,
                       alpha: bool = True, force_opaque: bool = False) -> QColor | None:
    """Show a QColorDialog that stays above fullscreen overlays on Linux.
    Returns the selected QColor, or None if cancelled.
    If force_opaque=True, alpha is forced to 255 regardless of user selection."""
    color_to_show = QColor(initial_color)
    if force_opaque:
        color_to_show.setAlpha(255)
    elif IS_LINUX and alpha and color_to_show.alpha() == 0:
        # On Linux, QColorDialog defaults alpha to 0 if the initial color has
        # alpha=0. Force it to 255 so the picker opens fully opaque by default.
        color_to_show.setAlpha(255)

    dlg = QColorDialog(color_to_show, parent)
    dlg.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, alpha)
    dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
    if IS_LINUX:
        dlg.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.X11BypassWindowManagerHint
        )
        _install_drag_move(dlg)
        # On Linux the alpha spin-box may still show 0 even after passing a
        # color with alpha=255 to the constructor — set it explicitly.
        if alpha:
            dlg.setCurrentColor(color_to_show)
    dlg.raise_()
    dlg.activateWindow()
    if dlg.exec():
        c = dlg.selectedColor()
        if c.isValid():
            if force_opaque:
                c.setAlpha(255)
            return c
    return None

# ── Hide console window on Windows ──────────────────────────────────────────
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


def _popen(cmd, **kw):
    kw.setdefault("creationflags", _NO_WINDOW)
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    return subprocess.Popen(cmd, **kw)


def _run(cmd, **kw):
    kw.setdefault("creationflags", _NO_WINDOW)
    kw.setdefault("stdout", subprocess.DEVNULL)
    kw.setdefault("stderr", subprocess.DEVNULL)
    return subprocess.run(cmd, **kw)


# ─────────────────────────────────────────────
#  MONITOR HELPER
# ─────────────────────────────────────────────

def _edid_model_name(edid: bytes) -> str:
    """Extract monitor model name from EDID bytes (descriptor block type 0xFC)."""
    for d in range(4):
        off = 54 + d * 18
        if len(edid) >= off + 18:
            desc = edid[off: off + 18]
            if desc[0] == 0 and desc[1] == 0 and desc[2] == 0 and desc[3] == 0xFC:
                raw = desc[5:].decode("ascii", errors="replace")
                return raw.split("\n")[0].strip()
    return ""


def _win_monitor_names() -> dict:
    """
    Windows: return {mss_index: "Monitor Model Name"}.
    Strategy (in order):
      1. WMI via PowerShell Win32_DesktopMonitor
      2. HKLM EDID registry
      3. EnumDisplayDevices monitor-level DeviceString
    """
    names = {}

    # ── Method 1a: Get-PnpDevice -Class Monitor (Device Manager names) ─────────
    # This is the most reliable — same names as Device Manager shows.
    try:
        ps_cmd = (
            "(Get-PnpDevice -Class Monitor -Status OK "
            "| Sort-Object -Property FriendlyName "
            "| Select-Object -ExpandProperty FriendlyName)"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=8,
            creationflags=_NO_WINDOW,
        )
        if r.returncode == 0:
            lines = [l.strip() for l in r.stdout.strip().splitlines()
                     if l.strip()
                     and "Generic" not in l
                     and "PnP" not in l
                     and "Default" not in l]
            if lines:
                for i, n in enumerate(lines):
                    names[i] = n
    except Exception:
        pass

    if names:
        return names

    # ── Method 1b: WMI Win32_PnPEntity filtered to monitor class ─────────────
    try:
        ps_cmd = (
            "Get-WmiObject -Query \"Select * From Win32_PnPEntity "
            "Where PNPClass = 'Monitor'\" "
            "| Where-Object {$_.Name -notlike '*Generic*' "
            "-and $_.Name -notlike '*PnP*'} "
            "| Select-Object -ExpandProperty Name"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=8,
            creationflags=_NO_WINDOW,
        )
        if r.returncode == 0:
            lines = [l.strip() for l in r.stdout.strip().splitlines()
                     if l.strip()
                     and "Generic" not in l
                     and "PnP" not in l]
            if lines:
                for i, n in enumerate(lines):
                    names[i] = n
    except Exception:
        pass

    if names:
        return names

    # ── Method 2: Registry EDID ───────────────────────────────────────────────
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
        monitor_idx = 0
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as disp:
            mfr_i = 0
            while True:
                try:
                    mfr = winreg.EnumKey(disp, mfr_i); mfr_i += 1
                    with winreg.OpenKey(disp, mfr) as mk:
                        inst_i = 0
                        while True:
                            try:
                                inst = winreg.EnumKey(mk, inst_i); inst_i += 1
                                param = mfr + chr(92) + inst + chr(92) + "Device Parameters"
                                try:
                                    with winreg.OpenKey(disp, param) as pk:
                                        edid_data, _ = winreg.QueryValueEx(pk, "EDID")
                                        model = _edid_model_name(bytes(edid_data))
                                        if model:
                                            names[monitor_idx] = model
                                        monitor_idx += 1
                                except (FileNotFoundError, OSError):
                                    monitor_idx += 1
                            except OSError:
                                break
                except OSError:
                    break
    except Exception:
        pass

    if names:
        return names

    # ── Method 3: EnumDisplayDevices ─────────────────────────────────────────
    try:
        import win32api
        ai = 0
        while True:
            try:
                adapter = win32api.EnumDisplayDevices(None, ai, 0)
                if not adapter.DeviceName:
                    break
                try:
                    mon = win32api.EnumDisplayDevices(adapter.DeviceName, 0, 0)
                    model = mon.DeviceString.strip()
                    if model and "Generic" not in model and "PnP" not in model:
                        names[ai] = model
                except Exception:
                    pass
                ai += 1
            except Exception:
                break
    except Exception:
        pass

    return names


def _linux_monitor_names() -> list:
    """
    Linux: parse EDID from xrandr --verbose to get real model names.
    Returns ordered list matching mss connected-monitor order.
    """
    port_model = {}
    port_order = []
    try:
        out = subprocess.check_output(
            ["xrandr", "--verbose"], stderr=subprocess.DEVNULL, text=True, timeout=5)
        current_port = None
        edid_hex = ""
        collecting = False

        for line in out.splitlines():
            stripped = line.strip()
            if " connected" in line and not line[0].isspace():
                # Save previous port EDID
                if current_port and edid_hex:
                    try:
                        edid = bytes.fromhex(edid_hex)
                        for d in range(4):
                            off = 54 + d * 18
                            if len(edid) >= off + 18:
                                desc = edid[off:off+18]
                                if desc[0]==0 and desc[1]==0 and desc[2]==0 and desc[3]==0xFC:
                                    raw = desc[5:].decode("ascii", errors="replace")
                                    m = raw.split("\n")[0].strip()
                                    if m:
                                        port_model[current_port] = m
                                    break
                    except Exception:
                        pass
                current_port = line.split()[0]
                port_order.append(current_port)
                edid_hex = ""
                collecting = False
            elif stripped.lower() == "edid:":
                collecting = True
            elif collecting and stripped and all(c in "0123456789abcdefABCDEF" for c in stripped):
                edid_hex += stripped
            elif collecting and stripped:
                collecting = False  # end of EDID block

        # Last port
        if current_port and edid_hex:
            try:
                edid = bytes.fromhex(edid_hex)
                for d in range(4):
                    off = 54 + d * 18
                    if len(edid) >= off + 18:
                        desc = edid[off:off+18]
                        if desc[0]==0 and desc[1]==0 and desc[2]==0 and desc[3]==0xFC:
                            raw = desc[5:].decode("ascii", errors="replace")
                            m = raw.split("\n")[0].strip()
                            if m:
                                port_model[current_port] = m
                            break
            except Exception:
                pass

    except Exception:
        pass

    return [port_model.get(p, p) for p in port_order]


def get_monitors():
    """Returns list of {index, name, width, height, x, y}.
    Name format: "1 (Main)" / "2 (Left)" / "3 (Right)" / "4 (Center)" etc.
    """
    monitors = []
    try:
        with mss.MSS() as sct:
            mons = sct.monitors[1:]  # skip combined "all monitors" entry

            # Find primary via Qt
            primary_x, primary_y = 0, 0
            try:
                ps = QApplication.primaryScreen()
                if ps:
                    primary_x, primary_y = ps.geometry().x(), ps.geometry().y()
            except Exception:
                pass

            # Sort by X to determine Left/Center/Right order
            sorted_by_x = sorted(enumerate(mons), key=lambda t: (t[1]["left"], t[1]["top"]))

            positions = {}  # mss_index → position_label
            n = len(mons)
            primary_mss_idx = None

            for rank, (mss_idx, m) in enumerate(sorted_by_x):
                if abs(m["left"] - primary_x) < 4 and abs(m["top"] - primary_y) < 4:
                    primary_mss_idx = mss_idx

            for rank, (mss_idx, m) in enumerate(sorted_by_x):
                if mss_idx == primary_mss_idx:
                    positions[mss_idx] = "Main"
                elif n == 1:
                    positions[mss_idx] = "Main"
                elif n == 2:
                    if rank == 0:
                        positions[mss_idx] = "Left" if primary_mss_idx != mss_idx else "Main"
                    else:
                        positions[mss_idx] = "Right" if primary_mss_idx != mss_idx else "Main"
                else:
                    if rank == 0:
                        positions[mss_idx] = "Left"
                    elif rank == n - 1:
                        positions[mss_idx] = "Right"
                    else:
                        positions[mss_idx] = "Center"

            for i, m in enumerate(mons):
                pos = positions.get(i, f"Display {i+1}")
                name = f"{i + 1} ({pos})  {m['width']}×{m['height']}"
                monitors.append({"index": i, "name": name,
                                  "width": m["width"], "height": m["height"],
                                  "x": m["left"], "y": m["top"]})
    except Exception:
        monitors = [{"index": 0, "name": "1 (Main)  1920×1080",
                     "width": 1920, "height": 1080, "x": 0, "y": 0}]
    return monitors

class FFmpegConverterThread(QThread):
    log_signal = Signal(str)
    finished_signal = Signal(int)

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd
        self._is_cancelled = False
        self.process = None

    def run(self):
        try:
            # Flaga CREATE_NO_WINDOW ukrywa konsolę CMD na Windowsie
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.process = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags
            )
            
            for line in self.process.stdout:
                if self._is_cancelled:
                    break
                self.log_signal.emit(line.strip())
                
            self.process.wait()
            
            if self._is_cancelled:
                self.finished_signal.emit(-99) # Znak, że anulowano
            else:
                self.finished_signal.emit(self.process.returncode)
                
        except FileNotFoundError:
            self.log_signal.emit("ERROR: ffmpeg not found. Make sure ffmpeg is installed and added to your PATH environment variables.")
            self.finished_signal.emit(-1)
        except Exception as e:
            self.log_signal.emit(f"ERROR: {str(e)}")
            self.finished_signal.emit(-1)

    def cancel(self):
        self._is_cancelled = True
        if self.process:
            self.process.terminate()

from PySide6.QtWidgets import (QGridLayout, QFormLayout, QProgressBar)
from PySide6.QtWidgets import QSlider

class VideoConverterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Video Converter")
        self.resize(750, 650)
        self.thread = None

        layout = QVBoxLayout(self)

        # --- FILE PATHS ---
        file_group = QGroupBox("File paths")
        file_layout = QGridLayout()

        self.input_edit = QLineEdit()
        self.btn_browse_input = QPushButton("Browse...")
        self.btn_browse_input.clicked.connect(self.browse_input)
        
        self.output_dir_edit = QLineEdit()
        self.btn_browse_output = QPushButton("Browse...")
        self.btn_browse_output.clicked.connect(self.browse_output)
        
        self.output_name_edit = QLineEdit()

        file_layout.addWidget(QLabel("Input file:"), 0, 0)
        file_layout.addWidget(self.input_edit, 0, 1)
        file_layout.addWidget(self.btn_browse_input, 0, 2)
        file_layout.addWidget(QLabel("Output folder:"), 1, 0)
        file_layout.addWidget(self.output_dir_edit, 1, 1)
        file_layout.addWidget(self.btn_browse_output, 1, 2)
        file_layout.addWidget(QLabel("Output name:"), 2, 0)
        file_layout.addWidget(self.output_name_edit, 2, 1)
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)

        
        # --- VIDEO OPTIONS ---
        video_group = QGroupBox("Video options")
        v_main_layout = QVBoxLayout()
        video_form = QFormLayout()
        
        self.video_codec_combo = QComboBox()
        self.video_codec_combo.addItems([
            "H.264/AVC (libx264)", "H.265/HEVC (libx265)", 
            "VP8 (libvpx)", "VP9 (libvpx-vp9)", 
            "AV1 (libaom-av1)", "Copy (no re-compression)", "None"
        ])
        
        # 1. Quality Controls
        self.quality_check = QCheckBox("Set custom video quality (CRF)")
        
        quality_widget = QWidget()
        quality_vbox = QVBoxLayout(quality_widget)
        quality_vbox.setContentsMargins(0, 5, 0, 5)

        self.quality_label = QLabel("Quality: Standard (CRF 23)")
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(0, 51)
        self.quality_slider.setValue(28) # Value 28 corresponds to CRF 23
        
        quality_hint_layout = QHBoxLayout()
        low_lbl = QLabel("Smaller file / Lower quality"); low_lbl.setStyleSheet("font-size: 10px; color: #888;")
        high_lbl = QLabel("Higher quality / Bigger file"); high_lbl.setStyleSheet("font-size: 10px; color: #888;")
        quality_hint_layout.addWidget(low_lbl)
        quality_hint_layout.addStretch()
        quality_hint_layout.addWidget(high_lbl)

        quality_vbox.addWidget(self.quality_label)
        quality_vbox.addWidget(self.quality_slider)
        quality_vbox.addLayout(quality_hint_layout)

        # Quality logic
        def update_quality_ui(val):
            crf = 51 - val
            desc = "Standard"
            if crf <= 17: desc = "Excellent"
            elif crf <= 23: desc = "Standard"
            elif crf <= 28: desc = "Medium"
            else: desc = "Low"
            self.quality_label.setText(f"Quality: {desc} (CRF {crf})")

        self.quality_slider.valueChanged.connect(update_quality_ui)
        self.quality_slider.setEnabled(False)
        self.quality_label.setEnabled(False)
        self.quality_check.toggled.connect(self.quality_slider.setEnabled)
        self.quality_check.toggled.connect(self.quality_label.setEnabled)

        # 2. Scale (Resize)
        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["Original", "1920x1080", "1280x720", "854x480", "640x360"])

        # 3. FPS Controls
        self.fps_orig_check = QCheckBox("Use video’s original framerate")
        self.fps_orig_check.setChecked(True)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 240)
        self.fps_spin.setValue(60)
        self.fps_spin.setEnabled(False)
        self.fps_orig_check.toggled.connect(lambda checked: self.fps_spin.setEnabled(not checked))
        # 4. Assemble the Form (NO DUPLICATES)
        video_form.addRow("Video codec:", self.video_codec_combo)
        video_form.addRow(self.quality_check)
        video_form.addRow(quality_widget)
        video_form.addRow("Scale (Resize):", self.scale_combo)
        video_form.addRow(self.fps_orig_check)
        video_form.addRow("Custom FPS:", self.fps_spin)
        
        v_main_layout.addLayout(video_form)
        video_group.setLayout(v_main_layout)

        # --- AUDIO & FORMAT ---
        audio_group = QGroupBox("Audio & Format")
        audio_form = QFormLayout()
        
        self.audio_codec_combo = QComboBox()
        self.audio_codec_combo.addItems([
            "AAC (aac)", "MP3 (libmp3lame)", "Opus (libopus)", 
            "Vorbis (libvorbis)", "Copy (no re-compression)", "None"
        ])
        
        self.format_combo = QComboBox()
        self.format_combo.addItems(["MP4", "WebM", "MKV", "AVI", "GIF"])
        
        audio_form.addRow("Audio codec:", self.audio_codec_combo)
        audio_form.addRow("Output format:", self.format_combo)
        audio_group.setLayout(audio_form)

        settings_layout = QHBoxLayout()
        settings_layout.addWidget(video_group)
        settings_layout.addWidget(audio_group)
        layout.addLayout(settings_layout)

        # --- LOGS & BUTTONS ---
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9) if sys.platform=="win32" else QFont("Monospace", 9))
        layout.addWidget(self.log_text)

        button_layout = QHBoxLayout()
        self.btn_start = QPushButton("Start encoding")
        self.btn_start.clicked.connect(self.start_conversion)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_conversion)
        
        button_layout.addStretch()
        button_layout.addWidget(self.btn_start)
        button_layout.addWidget(self.btn_cancel)
        layout.addLayout(button_layout)
        

    def browse_input(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select video file", "", "Video Files (*.mp4 *.mkv *.avi *.webm *.mov);;All Files (*)")
        if file_path:
            self.input_edit.setText(file_path)
            p = Path(file_path)
            self.output_dir_edit.setText(str(p.parent))
            self.output_name_edit.setText(f"{p.stem}_converted")

    def browse_output(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select output folder")
        if dir_path:
            self.output_dir_edit.setText(dir_path)

    def get_ffmpeg_args(self):
        v_codecs = {"H.264/AVC (libx264)": "libx264", "H.265/HEVC (libx265)": "libx265", 
                    "VP8 (libvpx)": "libvpx", "VP9 (libvpx-vp9)": "libvpx-vp9", 
                    "AV1 (libaom-av1)": "libaom-av1", "Copy (no re-compression)": "copy"}
        
        a_codecs = {"AAC (aac)": "aac", "MP3 (libmp3lame)": "libmp3lame", 
                    "Opus (libopus)": "libopus", "Vorbis (libvorbis)": "libvorbis", 
                    "Copy (no re-compression)": "copy"}

        v_val = self.video_codec_combo.currentText()
        a_val = self.audio_codec_combo.currentText()
        args = ["-map", "0:v:0", "-map", "0:a?"]
        
        # --- VIDEO ---
        # --- VIDEO ---
        if v_val == "None":
            args.append("-vn")
        else:
            codec = v_codecs.get(v_val, "libx264")
            args.extend(["-c:v", codec])
            if codec != "copy":
                args.extend(["-pix_fmt", "yuv420p"])
                
                # --- Quality Logic ---
                if self.quality_check.isChecked():
                    # Invert the slider value back to FFmpeg CRF
                    real_crf = 51 - self.quality_slider.value()
                    args.extend(["-crf", str(real_crf)])
                
                # Resizing
                scale = self.scale_combo.currentText()
                if scale != "Original":
                    w, h = scale.split('x')
                    args.extend(["-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"])

                # --- Update FPS Logic ---
                if not self.fps_orig_check.isChecked():
                    args.extend(["-r", str(self.fps_spin.value())])

        # --- AUDIO ---
        if a_val == "None":
            args.append("-an")
        else:
            codec = a_codecs.get(a_val, "aac")
            args.extend(["-c:a", codec])
            if codec != "copy":
                args.extend(["-b:a", "128k"])

        if self.format_combo.currentText().lower() == "mp4":
            args.extend(["-movflags", "+faststart"])

        return args

    def start_conversion(self):
        input_file = self.input_edit.text().strip()
        output_dir = self.output_dir_edit.text().strip()
        output_name = self.output_name_edit.text().strip()
        ext = self.format_combo.currentText().lower()

        if not input_file or not os.path.exists(input_file):
            QMessageBox.warning(self, "Error", "Invalid input file!")
            return

        output_path = os.path.join(output_dir, f"{output_name}.{ext}")
        cmd = ["ffmpeg", "-y", "-i", input_file] + self.get_ffmpeg_args() + [output_path]

        self.log_text.clear()
        self.log_text.append(f"Command: {' '.join(cmd)}\n")
        
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.thread = FFmpegConverterThread(cmd)
        self.thread.log_signal.connect(self.append_log)
        self.thread.finished_signal.connect(self.conversion_finished)
        self.thread.start()

    def append_log(self, text):
        self.log_text.append(text)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def cancel_conversion(self):
        if self.thread and self.thread.isRunning():
            self.thread.cancel()

    def conversion_finished(self, code):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        msg = "[✓] Done!" if code == 0 else "[✗] Failed or Cancelled."
        self.log_text.append(f"\n{msg}")


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

class Config:
    DEFAULT_SHORTCUTS = [
        {"name": "Capture region",           "action": "capture_region",           "shortcut": "Ctrl+Alt+Print Screen",  "enabled": True},
        {"name": "Capture active monitor",   "action": "capture_active_monitor",   "shortcut": "Alt+Print Screen",       "enabled": True},
        {"name": "Capture active window",    "action": "capture_active_window",    "shortcut": "Ctrl+Print Screen",      "enabled": True},
        {"name": "Capture selected monitor", "action": "capture_selected_monitor", "shortcut": "Ctrl+Alt+M",             "enabled": True},
        {"name": "Scrolling capture",        "action": "capture_scrolling",        "shortcut": "Shift+Print Screen",     "enabled": True},
        {"name": "Start/Stop recording",     "action": "toggle_recording",         "shortcut": "Ctrl+Shift+Print Screen","enabled": True},
        {"name": "Record GIF",               "action": "record_gif",               "shortcut": "Ctrl+Shift+G",           "enabled": True},
        {"name": "Recognize text",           "action": "ocr_text",                "shortcut": "Ctrl+Alt+O",             "enabled": True},
        {"name": "Recognize QR code",        "action": "ocr_code",                "shortcut": "Ctrl+Alt+K",             "enabled": True},
        {"name": "OCR/QR Toolbox",           "action": "ocr_qr_toolbox",          "shortcut": "Ctrl+Alt+Q",             "enabled": True},
    ]
    DEFAULT_AFTER = {"copy_to_clipboard": True,  "save_to_file": True,
                     "show_in_explorer": False, "scan_qr": False, "ocr_recognize": False,
                     "open_in_editor": False}
    DEFAULT_NOTIF = {"enabled": True, "sound": True, "thumbnail": True,
                      "show_path": True, "click_open_file": True, "click_open_folder": False}

    def __init__(self):
        cfg_dir = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation))
        cfg_dir.mkdir(parents=True, exist_ok=True)
        self.path = cfg_dir / "pysharex.json"
        self.data = self._load()

    def _defaults(self):
        pics = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation))
        return {
            "shortcuts":        self.DEFAULT_SHORTCUTS.copy(),
            "save_folder":      str(pics / "PyshareX"),
            "after_capture":    self.DEFAULT_AFTER.copy(),
            "notifications":    self.DEFAULT_NOTIF.copy(),
            "image_format":     "png",
            "jpeg_quality":     90,
            "show_cursor":      False,
            "delay":            0,
            "gif_fps":          10,
            "gif_duration":     5,
            "record_audio":     False,
            "selected_monitor": 0,
            "ocr_engine": "paddleocr",
            # Canonical (ISO 639-1-ish) OCR language codes — see OCR_LANGUAGES
            # near the top of the file. Each engine maps these to its own
            # code scheme in _paddle_lang()/_tesseract_lang()/etc, and each
            # engine combines multiple selected languages differently:
            #   • Tesseract: recognizes all of them in one pass (-l a+b+c)
            #   • EasyOCR:   recognizes all of them in one pass, but only if
            #                they belong to a compatible character-set group
            #   • PaddleOCR: can only load one language model at a time, so
            #                we run each selected language separately and
            #                keep whichever result has the highest confidence
            "ocr_languages": ["pl"],
            # Which engine "Capture Region" uses to grab the final screenshot:
            #   "ffmpeg"   — original method (gdigrab/x11grab), requires ffmpeg
            #   "standard" — new ffmpeg-free method (mss + PIL), DPI-aware
            #                across monitors with different scaling
            "region_capture_method": "standard",
            # "Capture region": while hovering (no mouse button held) before a
            # drag starts, automatically outline the window / UI element under
            # the cursor with a dashed rectangle; a plain click captures it
            # immediately. Holding the left button and dragging still falls
            # back to the normal manual rectangle-selection mode.
            "detect_windows_on_capture": True,
        }

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                for k, v in self._defaults().items():
                    d.setdefault(k, v)
                # Auto-migrate: reset shortcuts to English if any Polish names found
                scs = d.get("shortcuts", [])
                if scs and any(
                    any(pl in s.get("name", "")
                        for pl in ("Przechwyt", "Nagryw", "Rozpoznaj", "Rozpocz"))
                    for s in scs
                ):
                    d["shortcuts"] = self.DEFAULT_SHORTCUTS.copy()
                    try:
                        with open(self.path, "w", encoding="utf-8") as fw:
                            json.dump(d, fw, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
                # Auto-migrate: force a valid engine; prefer easyocr on Linux
                # (PaddleOCR crashes with SIGILL on CPUs without AVX instructions)
                valid_engines = {"paddleocr", "easyocr", "tesseract"}
                if d.get("ocr_engine") not in valid_engines:
                    d["ocr_engine"] = "easyocr" if IS_LINUX else "paddleocr"
                    try:
                        with open(self.path, "w", encoding="utf-8") as fw:
                            json.dump(d, fw, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
                # Auto-migrate: convert legacy single "ocr_language" string
                # into the new "ocr_languages" list, and make sure the list
                # only contains codes we actually know about.
                legacy_lang = d.get("ocr_language")
                if "ocr_languages" not in d and legacy_lang in OCR_LANGUAGE_CODES:
                    d["ocr_languages"] = [legacy_lang]
                langs = d.get("ocr_languages")
                if not isinstance(langs, list) or not langs:
                    d["ocr_languages"] = ["pl"]
                else:
                    cleaned = [c for c in langs if c in OCR_LANGUAGE_CODES]
                    d["ocr_languages"] = cleaned or ["pl"]
                d.pop("ocr_language", None)
                try:
                    with open(self.path, "w", encoding="utf-8") as fw:
                        json.dump(d, fw, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                return d
            except Exception:
                pass
        return self._defaults()

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Config save error: {e}")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()


# ─────────────────────────────────────────────
#  BEEP / NOTIFICATION
# ─────────────────────────────────────────────

def _play_beep():
    try:
        if IS_WINDOWS:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        else:
            sr, dur, freq = 22050, 0.12, 880
            samples = [int(32767 * math.sin(2 * math.pi * freq * t / sr))
                       for t in range(int(sr * dur))]
            buf = io.BytesIO()
            with wv.open(buf, "w") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
                wf.writeframes(b"".join(struct.pack("<h", s) for s in samples))
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(buf.getvalue()); tmp.close()
            for player in ("paplay", "aplay", "play"):
                if _run(["which", player], timeout=1).returncode == 0:
                    _popen([player, tmp.name])
                    break
    except Exception:
        pass


class NotificationToast(QWidget):
    def __init__(self, title, message, pixmap=None, filepath=None, on_click_open=True, on_click_folder=False):
        super().__init__(None,
                         Qt.WindowType.Tool |
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.BypassWindowManagerHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._opacity = 1.0
        self._filepath = filepath
        self._on_click_open   = on_click_open
        self._on_click_folder = on_click_folder

        self.setStyleSheet("""
            QWidget#MainFrame {
                background-color: #1f1f1f;
                border: 1px solid #3a3a3a;
            }
            QLabel#Title {
                color: #00a2ed;
                font-weight: bold;
                font-size: 14px;
            }
            QLabel#Message {
                color: #bbbbbb;
                font-size: 12px;
            }
            QWidget#AccentBar {
                background-color: #00a2ed;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Niebieski pasek
        self.accent = QFrame()
        self.accent.setObjectName("AccentBar")
        self.accent.setFixedWidth(4)
        layout.addWidget(self.accent)

        # Główny kontener
        self.frame = QFrame()
        self.frame.setObjectName("MainFrame")
        layout.addWidget(self.frame)

        f_layout = QHBoxLayout(self.frame)
        f_layout.setContentsMargins(5, 5, 15, 5) # Bardzo małe marginesy dla obrazka
        f_layout.setSpacing(15)

        # DUŻA MINIATURKA - teraz 125x125
        self.img_label = QLabel()
        tsize = 125
        self.img_label.setFixedSize(tsize, tsize)
        if pixmap and not pixmap.isNull():
            # Skalowanie wypełniające całe pole (Crop)
            scaled = pixmap.scaled(tsize, tsize, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            self.img_label.setPixmap(scaled)
        else:
            self.img_label.setStyleSheet("background-color: #2a2a2a;")
        f_layout.addWidget(self.img_label)

        # Tekst
        t_layout = QVBoxLayout()
        t_layout.setSpacing(2)
        self.l_title = QLabel(title); self.l_title.setObjectName("Title")
        self.l_msg = QLabel(message); self.l_msg.setObjectName("Message"); self.l_msg.setWordWrap(True)
        t_layout.addStretch()
        t_layout.addWidget(self.l_title)
        t_layout.addWidget(self.l_msg)
        t_layout.addStretch()
        f_layout.addLayout(t_layout, 1)

        self.setFixedSize(480, 145) # Szerokie i czytelne
        sg = QApplication.primaryScreen().availableGeometry()
        self.move(sg.right() - self.width() - 20, sg.bottom() - self.height() - 20)
        self.show()

        self._ftimer = QTimer(self)
        self._ftimer.timeout.connect(self._fade)
        QTimer.singleShot(5000, self._ftimer.start)
        self._ftimer.setInterval(20)

    def _fade(self):
        self._opacity -= 0.05
        if self._opacity <= 0: self.close()
        else: self.setWindowOpacity(self._opacity)

    def mousePressEvent(self, e):
        if not self._filepath:
            self.close()
            return
        p = self._filepath
        folder = str(Path(p).parent)
        sys_name = platform.system()

        if self._on_click_open and Path(p).exists():
            # Open the file itself — cross-platform
            if sys_name == "Windows":
                os.startfile(p)
            elif sys_name == "Darwin":
                _popen(["open", p])
            else:
                _popen(["xdg-open", p])

        if self._on_click_folder and Path(folder).exists():
            # Open containing folder — cross-platform
            if sys_name == "Windows":
                _popen(["explorer", "/select,", p.replace("/", "\\")])
            elif sys_name == "Darwin":
                _popen(["open", folder])
            else:
                _popen(["xdg-open", folder])

        self.close()

def notify(config: Config, filepath: str, pixmap=None):
    notif = config.get("notifications", {})
    if not notif.get("enabled", True): return
    
    if notif.get("sound", True):
        threading.Thread(target=_play_beep, daemon=True).start()

    p = Path(filepath)
    title = "Task completed"
    msg = f"Image saved to:\n{p.name}" # Formatted like ShareX
    
    if pixmap is None:
        p = Path(filepath)
        if PIL_AVAILABLE and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
            try:
                img = Image.open(filepath)
                # ... tutaj kod ładowania obrazka z pliku ...
            except: pass
    if PIL_AVAILABLE and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
        try:
            # Ładujemy obrazek w wyższej rozdzielczości do miniatury
            img = Image.open(filepath)
            img.thumbnail((250, 250)) 
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            # Force deep copy to prevent Segmentation Fault when bytes are garbage collected
            qi = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888).copy()
            pixmap = QPixmap.fromImage(qi)
        except: pass

    click_open_file   = notif.get("click_open_file",   True)
    click_open_folder = notif.get("click_open_folder", False)
    QTimer.singleShot(0, lambda: _show_toast(title, msg, pixmap, filepath,
                                             click_open_file, click_open_folder))

_toasts = []
def _show_toast(title, msg, pixmap, filepath,
                on_click_open=True, on_click_folder=False):
    global _toasts
    _toasts = [t for t in _toasts if t.isVisible()]
    t = NotificationToast(title, msg, pixmap, filepath,
                          on_click_open=on_click_open,
                          on_click_folder=on_click_folder)
    _toasts.append(t)


# ─────────────────────────────────────────────
#  RECORDING BORDER OVERLAY
# ─────────────────────────────────────────────

def physical_to_logical_rect(phys_x, phys_y, phys_w, phys_h) -> QRect:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QRect
    
    # Sortujemy ekrany tak samo jak w RegionSelector, by indeksy się zgadzały
    with mss.MSS() as sct:
        target_mon    = None
        target_screen = None

        # Find MSS monitor containing the physical point, then find the
        # matching Qt screen by physical-origin comparison (not by index).
        # This handles all layouts including portrait monitors (Case 1/3),
        # mirrored variants, and 3+ monitor setups (Case 5).
        for mon in sct.monitors[1:]:
            if (mon["left"] <= phys_x < mon["left"] + mon["width"] and
                    mon["top"]  <= phys_y < mon["top"]  + mon["height"]):
                target_mon = mon
                break

        if target_mon is None:
            # Point is outside all monitors — use primary as fallback
            target_mon    = sct.monitors[1] if len(sct.monitors) > 1 else {"left": 0, "top": 0, "width": 1920, "height": 1080}
            target_screen = QApplication.primaryScreen()

        if target_screen is None:
            # Match Qt screen whose physical origin equals target_mon's origin.
            # Use a wider tolerance (8 px) to handle rounding differences in
            # mixed-DPI setups with 3+ monitors where the center monitor's
            # logical x * DPR may not land exactly on the MSS left value.
            best_screen = None
            best_dist   = 9999
            for q_scr in QApplication.screens():
                lg  = q_scr.geometry()
                dpr = q_scr.devicePixelRatio()
                qpx = round(lg.x() * dpr)
                qpy = round(lg.y() * dpr)
                dist = abs(qpx - target_mon["left"]) + abs(qpy - target_mon["top"])
                if dist < best_dist:
                    best_dist   = dist
                    best_screen = q_scr
            # Accept the closest match within 16 px to avoid picking a wrong monitor
            if best_dist <= 16:
                target_screen = best_screen

        if target_screen is None:
            target_screen = QApplication.primaryScreen()

        ratio        = target_screen.devicePixelRatio()
        logical_geom = target_screen.geometry()

        # Physical offset inside the owning monitor → logical offset
        local_phys_x = phys_x - target_mon["left"]
        local_phys_y = phys_y - target_mon["top"]

        local_log_x = local_phys_x / ratio
        local_log_y = local_phys_y / ratio
        log_w       = phys_w / ratio
        log_h       = phys_h / ratio

        return QRect(
            int(logical_geom.x() + local_log_x),
            int(logical_geom.y() + local_log_y),
            int(log_w),
            int(log_h)
        )


class RecordingBorder(QWidget):
    def __init__(self, phys_x, phys_y, phys_w, phys_h):
        super().__init__(None,
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool |
                         Qt.WindowType.BypassWindowManagerHint)

        # Convert physical MSS coords → logical Qt coords for window placement.
        # We find the Qt screen whose physical origin is closest to (phys_x, phys_y)
        # using a best-match search rather than exact equality, which avoids the
        # "border appears on wrong monitor" bug in 3-monitor setups where rounding
        # differences cause physical_to_logical_rect to pick the wrong screen.
        rect = self._phys_to_logical(phys_x, phys_y, phys_w, phys_h)

        B = 5  # border margin in logical px
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Expand border slightly outside the recorded area
        self.setGeometry(rect.x() - B, rect.y() - B, rect.width() + B * 2, rect.height() + B * 2)

        self._dash = 0
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(80)
        self._t = t
        self.show()

    @staticmethod
    def _phys_to_logical(phys_x, phys_y, phys_w, phys_h) -> QRect:
        """Convert physical MSS pixel rect to logical Qt window coords.

        On Windows with per-monitor DPI, Qt geometry().x() does NOT equal
        mss_left / dpr — Qt uses its own logical coordinate space that does
        not simply divide physical coords by a single ratio.

        The only reliable approach: use QApplication.screenAt() which accepts
        logical Qt coordinates. We find the primary screen's DPR and use it
        to get a rough logical point, call screenAt() to find the right screen,
        then use that screen's geometry + its true DPR (mss_w / qt_w) to
        compute the final accurate logical rect.
        """
        try:
            with mss.MSS() as sct:
                mss_mons = sct.monitors[1:]

                # Step 1: find the MSS monitor containing the physical point
                owner_mon = None
                for mon in mss_mons:
                    if (mon["left"] <= phys_x < mon["left"] + mon["width"] and
                            mon["top"]  <= phys_y < mon["top"]  + mon["height"]):
                        owner_mon = mon
                        break
                if owner_mon is None:
                    owner_mon = mss_mons[0] if mss_mons else {
                        "left": 0, "top": 0, "width": 1920, "height": 1080}

                # Step 2: find the primary screen's DPR to get a rough logical pt
                primary = QApplication.primaryScreen()
                primary_dpr = primary.devicePixelRatio() if primary else 1.0

                # Step 3: convert physical point to a rough logical point using
                # primary DPR, then ask Qt which screen owns that point
                rough_lx = phys_x / primary_dpr
                rough_ly = phys_y / primary_dpr
                owner_qt = QApplication.screenAt(QPoint(int(rough_lx), int(rough_ly)))
                if owner_qt is None:
                    owner_qt = primary

                # Step 4: compute true DPR for this specific monitor from sizes
                lg = owner_qt.geometry()
                if lg.width() > 0 and lg.height() > 0:
                    true_dpr = (owner_mon["width"] / lg.width() +
                                owner_mon["height"] / lg.height()) / 2.0
                else:
                    true_dpr = primary_dpr

                # Step 5: local offset inside the monitor in physical px → logical
                local_phys_x = phys_x - owner_mon["left"]
                local_phys_y = phys_y - owner_mon["top"]
                local_log_x  = local_phys_x / true_dpr
                local_log_y  = local_phys_y / true_dpr
                log_w        = phys_w / true_dpr
                log_h        = phys_h / true_dpr

                result = QRect(
                    int(lg.x() + local_log_x),
                    int(lg.y() + local_log_y),
                    int(log_w),
                    int(log_h)
                )
                print(f"[RecordingBorder] phys({phys_x},{phys_y},{phys_w},{phys_h})"
                      f" own_mon=({owner_mon['left']},{owner_mon['top']})"
                      f" qt_geo={lg} true_dpr={true_dpr:.3f}"
                      f" → logical={result}")
                return result

        except Exception as e:
            print(f"[RecordingBorder] coord conversion error: {e}")
            return QRect(phys_x, phys_y, phys_w, phys_h)

    def _tick(self):
        self._dash = (self._dash + 2) % 20
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(0, 230, 0), 3)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([6, 4])
        pen.setDashOffset(self._dash)
        p.setPen(pen)
        p.drawRect(2, 2, self.width() - 4, self.height() - 4)

    def stop(self):
        self._t.stop(); self.close()


# ─────────────────────────────────────────────
#  RECORDING BAR  (Stop / Abort / clock)
# ─────────────────────────────────────────────

class RecordingBar(QWidget):
    stop_clicked  = Signal()
    abort_clicked = Signal()

    def __init__(self):
        super().__init__(None,
                         Qt.WindowType.Tool |
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(286, 38)
        
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        B = "QPushButton{background:#2a2a3e;border:1px solid #45475a;border-radius:5px;padding:0 10px;font-size:12px;min-height:26px;}"
        B_h = "QPushButton:hover{background:#45475a;}"

        s_btn = QPushButton("⏹ Stop")
        s_btn.setStyleSheet(B + B_h + "QPushButton{color:#cdd6f4;}")
        s_btn.clicked.connect(self.stop_clicked)

        a_btn = QPushButton("✕ Abort")
        a_btn.setStyleSheet(B + B_h + "QPushButton{color:#f38ba8;}")
        a_btn.clicked.connect(self.abort_clicked)

        self.lbl = QLabel("00:00:00")
        self.lbl.setStyleSheet("color:#a6e3a1;font-family:Consolas,monospace;"
                               "font-size:13px;font-weight:bold;")
        self.lbl.setFixedWidth(74)

        lay.addWidget(s_btn); lay.addWidget(a_btn); lay.addWidget(self.lbl)

        self._et = QElapsedTimer()
        self._tt = QTimer(self); self._tt.timeout.connect(self._tick)

    def start_display(self, region=None):
        self._et.start(); self._tt.start(500)
        if region:
            # region is in physical MSS pixels — convert to logical Qt coords
            # before calling move(), which works in logical pixels.
            rx, ry, rw, rh = region
            log_rect = RecordingBorder._phys_to_logical(rx, ry, rw, rh)
            bx = log_rect.x() + log_rect.width() // 2 - self.width() // 2
            by = log_rect.y() + log_rect.height() - self.height() - 8
            # Clamp to the virtual desktop so bar stays fully visible
            sg = QApplication.primaryScreen().availableVirtualGeometry()
            bx = max(sg.left(), min(bx, sg.right()  - self.width()))
            by = max(sg.top(),  min(by, sg.bottom() - self.height()))
            self.move(bx, by)
        else:
            sg = QApplication.primaryScreen().geometry()
            self.move(sg.center().x() - self.width() // 2,
                      sg.bottom() - self.height() - 6)
        self.show()

    def _tick(self):
        ms = self._et.elapsed(); s = ms // 1000
        h, r = divmod(s, 3600); m, sec = divmod(r, 60)
        self.lbl.setText(f"{h:02d}:{m:02d}:{sec:02d}")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 8, 8)
        p.fillPath(path, QColor(24, 24, 37, 225))
        p.setPen(QPen(QColor(69, 71, 90), 1))
        p.drawPath(path)

    def stop_display(self):
        self._tt.stop(); self.close()

# ─────────────────────────────────────────────
#  REGION SELECTOR
# ─────────────────────────────────────────────

class RegionSelector(QWidget):
    region_selected = Signal(int, int, int, int)
    cancelled       = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(_overlay_window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.start_pos = self.end_pos = None
        self.drawing = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._geo = QRect()
        for s in QApplication.screens():
            self._geo = self._geo.united(s.geometry())
        self.setGeometry(self._geo)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        # Force keyboard focus even when triggered from a global hotkey
        QTimer.singleShot(50, self._force_focus)

    def _force_focus(self):
        if _is_wayland_session():
            self.setGeometry(self._geo)   # zarządzane okno: WM mógł je przesunąć
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self.drawing and self.start_pos and self.end_pos:
            r = QRect(self.start_pos, self.end_pos).normalized()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(r, QColor(0, 0, 0, 255))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(0, 174, 255), 2))
            p.drawRect(r)
            p.setPen(QColor(255, 255, 255))
            p.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
            p.drawText(r.x() + 4, r.y() - 6, f"{r.width()} × {r.height()}")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close(); self.cancelled.emit()

    def _abs(self, local_pt: QPoint) -> QPoint:
        return self.mapToGlobal(local_pt)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.start_pos = self.end_pos = e.position().toPoint()
            self.global_start = self.global_end = e.globalPosition().toPoint()
            self.drawing = True

    def mouseMoveEvent(self, e):
        if self.drawing:
            self.end_pos = e.position().toPoint()
            self.global_end = e.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.drawing:
            self.end_pos = e.position().toPoint()
            self.global_end = e.globalPosition().toPoint()
            self.drawing = False
            self.close()

            r = QRect(self.global_start, self.global_end).normalized()

            if r.width() > 0 and r.height() > 0:
                # Use topLeft for screen detection — more reliable than center
                # when selection starts near a monitor boundary.
                screen = QApplication.screenAt(r.topLeft())
                if not screen:
                    screen = QApplication.screenAt(r.center())
                if not screen:
                    screen = QApplication.primaryScreen()

                ratio        = screen.devicePixelRatio()
                logical_geom = screen.geometry()

                # Offset of selection inside the owning screen (logical px)
                local_x = r.x() - logical_geom.x()
                local_y = r.y() - logical_geom.y()

                # Convert offset + size to physical px
                phys_local_x = int(local_x * ratio)
                phys_local_y = int(local_y * ratio)
                phys_w       = int(r.width()  * ratio)
                phys_h       = int(r.height() * ratio)

                # Safe fallback (used only when MSS matching fails)
                final_x = int(logical_geom.x() * ratio) + phys_local_x
                final_y = int(logical_geom.y() * ratio) + phys_local_y

                try:
                    with mss.MSS() as sct:
                        # Use _emit_region_from_global_rect logic — match by
                        # physical origin (geometry * ratio) not by list order,
                        # so all monitor layouts including mixed-DPI and 3+
                        # monitors work correctly (same as EnhancedRegionSelector).
                        qt_phys_x = round(logical_geom.x() * ratio)
                        qt_phys_y = round(logical_geom.y() * ratio)
                        # Sort both lists the same way EnhancedRegionSelector does
                        # so index-based fallback also works correctly.
                        mss_mons_sorted = sorted(sct.monitors[1:],
                                                 key=lambda m: (m["left"], m["top"]))
                        matched = False
                        for mon in mss_mons_sorted:
                            if (abs(mon["left"] - qt_phys_x) <= 2 and
                                    abs(mon["top"]  - qt_phys_y) <= 2):
                                final_x = mon["left"] + phys_local_x
                                final_y = mon["top"]  + phys_local_y
                                matched = True
                                break
                        if not matched:
                            # Fallback: index-match sorted Qt screens → sorted MSS
                            qt_screens_sorted = sorted(
                                QApplication.screens(),
                                key=lambda s: (s.geometry().x(), s.geometry().y()))
                            screen_idx = qt_screens_sorted.index(screen) \
                                if screen in qt_screens_sorted else 0
                            if screen_idx < len(mss_mons_sorted):
                                mon = mss_mons_sorted[screen_idx]
                                final_x = mon["left"] + phys_local_x
                                final_y = mon["top"]  + phys_local_y
                except Exception as ex:
                    print(f"[PyshareX] Monitor matching error: {ex}")

                self.region_selected.emit(final_x, final_y, phys_w, phys_h)


# ─────────────────────────────────────────────
#  ENHANCED REGION SELECTOR (direct user capture only)
# ─────────────────────────────────────────────

def _emit_region_from_global_rect(r: QRect, signal):
    """
    Convert a global-logical QRect to physical MSS coordinates and emit
    region_selected(x, y, w, h).

    Matching strategy: compare the Qt screen's physical top-left origin
    (geometry * devicePixelRatio) against each MSS monitor's left/top.
    This works correctly for all monitor layouts shown in the reference
    diagram (Cases 1–6) including portrait monitors, negative-coordinate
    layouts, mirrored variants, and 3-monitor setups (Case 5).
    """
    screen = QApplication.screenAt(r.topLeft())
    if not screen:
        screen = QApplication.screenAt(r.center())
    if not screen:
        screen = QApplication.primaryScreen()

    ratio        = screen.devicePixelRatio()
    logical_geom = screen.geometry()

    # Offset of selection inside the owning screen (logical px)
    local_x = r.x() - logical_geom.x()
    local_y = r.y() - logical_geom.y()

    # Convert offset + size to physical px
    phys_local_x = int(local_x * ratio)
    phys_local_y = int(local_y * ratio)
    phys_w       = int(r.width()  * ratio)
    phys_h       = int(r.height() * ratio)

    # Safe fallback — physical origin of this Qt screen + local offset
    final_x = int(logical_geom.x() * ratio) + phys_local_x
    final_y = int(logical_geom.y() * ratio) + phys_local_y

    try:
        with mss.MSS() as sct:
            qt_phys_x = round(logical_geom.x() * ratio)
            qt_phys_y = round(logical_geom.y() * ratio)
            mss_mons_sorted = sorted(sct.monitors[1:],
                                     key=lambda m: (m["left"], m["top"]))
            matched = False
            for mon in mss_mons_sorted:
                if (abs(mon["left"] - qt_phys_x) <= 2 and
                        abs(mon["top"]  - qt_phys_y) <= 2):
                    final_x = mon["left"] + phys_local_x
                    final_y = mon["top"]  + phys_local_y
                    matched = True
                    break
            if not matched:
                qt_screens_sorted = sorted(
                    QApplication.screens(),
                    key=lambda s: (s.geometry().x(), s.geometry().y()))
                screen_idx = qt_screens_sorted.index(screen) \
                    if screen in qt_screens_sorted else 0
                if screen_idx < len(mss_mons_sorted):
                    mon = mss_mons_sorted[screen_idx]
                    final_x = mon["left"] + phys_local_x
                    final_y = mon["top"]  + phys_local_y
    except Exception as ex:
        print(f"[PyshareX] Monitor matching error: {ex}")

    signal.emit(final_x, final_y, phys_w, phys_h)

class _SelectionLayer(QWidget):
    """Transparent, mouse-transparent child of the Capture overlay stacked ABOVE the annotation
    canvas (but below the toolbar). It paints the selection frame and its size label, so
    annotation items can never cover them."""
    def __init__(self, overlay):
        super().__init__(overlay)
        self._ov = overlay
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setGeometry(overlay.rect())

    def paintEvent(self, _e):
        p = QPainter(self)
        self._ov._paint_selection_layer(p)
        p.end()


class _OverlayCanvas(QGraphicsView):
    """
    Transparent annotation canvas used by EnhancedRegionSelector.
    Uses QGraphicsScene so items are fully selectable, movable and resizable
    (same infrastructure as the Image Editor).

    Mouse routing strategy
    ──────────────────────
    The canvas sits on top of EnhancedRegionSelector (the dark overlay).
    We always keep WA_TransparentForMouseEvents=True so every click reaches
    the overlay parent first.  The parent's mouse-event handlers call back
    into the canvas methods (add_rect, begin_freehand, …) and also call
    scene.sendEvent() for SELECT-tool interactions so QGraphicsScene still
    handles item drag/selection without us having to flip the transparency flag.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setStyleSheet("background: transparent; border: none;")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setInteractive(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Always transparent — parent routes events manually via send_mouse_to_scene()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._marker_count = 0
        self._current_freehand_item = None
        self._freehand_path = None

        # Undo / Redo history — same snapshot-based approach as EditorCanvas
        # (see EditorCanvas.push_undo/undo/redo for the rationale).
        self._undo_stack = []
        self._redo_stack = []
        self._undo_limit = 40

    def send_mouse_to_scene(self, qevent):
        """
        Forward a QMouseEvent from the parent overlay into the QGraphicsScene
        so that item selection, dragging and resize handles work even though
        WA_TransparentForMouseEvents is set.
        """
        # Map the event position from parent-widget coords → viewport coords
        vp_pos  = self.mapFrom(self.parent(), qevent.position().toPoint())
        scene_p = self.mapToScene(vp_pos)

        from PySide6.QtCore import QEvent
        etype = qevent.type()

        fake = QMouseEvent(
            etype,
            QPointF(vp_pos),
            qevent.globalPosition(),
            qevent.button(),
            qevent.buttons(),
            qevent.modifiers(),
        )
        # Let QGraphicsView process it — this triggers item hit-testing,
        # rubber-band selection, and the scene's own event dispatch.
        QGraphicsView.mousePressEvent(self, fake)   if etype == QMouseEvent.Type.MouseButtonPress   else None
        QGraphicsView.mouseMoveEvent(self, fake)    if etype == QMouseEvent.Type.MouseMove          else None
        QGraphicsView.mouseReleaseEvent(self, fake) if etype == QMouseEvent.Type.MouseButtonRelease else None

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def sync_geometry(self):
        """Call after parent resize to keep canvas covering the whole overlay."""
        self.setGeometry(self.parent().rect())
        self._scene.setSceneRect(QRectF(self.parent().rect()))

    # ------------------------------------------------------------------
    # Item factories (called by EnhancedRegionSelector)
    # ------------------------------------------------------------------
    def add_rect(self, rect: QRectF, color: QColor, width: int, radius: float = 0.0,
                 fill_color: QColor = None):
        item = ResizableRectItem()
        item.setRect(rect)
        item.corner_radius = float(radius)
        self._apply_props(item, color, width)
        if fill_color is not None:
            item._fill_color = QColor(fill_color)
            item.setBrush(QBrush(fill_color))
        item.setAcceptHoverEvents(True)
        self._scene.addItem(item)
        return item

    def add_ellipse(self, rect: QRectF, color: QColor, width: int,
                    fill_color: QColor = None):
        item = ResizableEllipseItem()
        item.setRect(rect)
        self._apply_props(item, color, width)
        if fill_color is not None:
            item._fill_color = QColor(fill_color)
            item.setBrush(QBrush(fill_color))
        item.setAcceptHoverEvents(True)
        self._scene.addItem(item)
        return item

    def add_line(self, line: QLineF, color: QColor, width: int):
        item = LineItem(line, self)
        self._apply_props(item, color, width)
        self._scene.addItem(item)
        return item

    def add_highlight(self, rect: QRectF, color: QColor = None):
        item = HighlightRectItem()
        item.setRect(rect)
        if color is not None:
            item._color = QColor(color)
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self._scene.addItem(item)
        return item

    def add_pixelate_blur(self, rect: QRectF, mode: str = 'pixelate',
                          pixel_size: int = 12, blur_radius: int = 10):
        item = PixelateBlurItem()
        item.setRect(rect)
        item.mode = mode
        item.pixel_size = int(pixel_size)
        item.blur_radius = int(blur_radius)
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self._scene.addItem(item)
        return item

    def add_smart_eraser(self, rect: QRectF, color: QColor):
        item = SmartEraserItem()
        item.setRect(rect)
        item.fill_color = QColor(color)
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self._scene.addItem(item)
        return item

    def add_spotlight(self, rect: QRectF, blur_radius: int = 2,
                      outline_on: bool = False, outline_w: int = 3, outline_color=None,
                      defer_draw: bool = True):
        item = SpotlightItem()
        item.setRect(rect)
        item.blur_radius = int(blur_radius)
        item.outline_enabled = bool(outline_on)
        item.outline_width = int(outline_w)
        item.defer_draw = bool(defer_draw)
        if outline_color is not None:
            item.outline_color = QColor(outline_color)
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self._scene.addItem(item)
        return item

    def paint_effect_base(self, painter, rect: QRectF) -> bool:
        """Base layer under Pixelate/Blur items: the desktop as it looked when the
        overlay opened (scene coords == overlay-local coords == snapshot pixels)."""
        snap = getattr(self.parent(), '_desktop_snapshot', None)
        if snap is None or snap.isNull():
            return False
        painter.drawImage(rect, snap, rect)
        return True

    def add_arrow(self, line: QLineF, color: QColor, width: int, head_style: str = 'single'):
        item = ArrowItem(line, self)
        item.head_style = head_style
        self._apply_props(item, color, width)
        self._scene.addItem(item)
        return item

    def add_bubble(self, pos: QPointF, text: str, fg_color: QColor, bg_color: QColor):
        item = TextBubbleItem(text, fg_color, bg_color)
        item.setPos(pos)
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self._scene.addItem(item)
        return item

    def begin_freehand(self, pos: QPointF, color: QColor, width: int):
        path = QPainterPath()
        path.moveTo(pos)
        item = FreehandItem(path)
        self._apply_props(item, color, width)
        self._scene.addItem(item)
        self._current_freehand_item = item
        self._freehand_path = path
        return item

    def extend_freehand(self, pos: QPointF):
        if self._freehand_path and self._current_freehand_item:
            self._freehand_path.lineTo(pos)
            self._current_freehand_item.setPath(self._freehand_path)
            if hasattr(self._current_freehand_item, 'update_base_path'):
                self._current_freehand_item.update_base_path()

    def end_freehand(self):
        self._current_freehand_item = None
        self._freehand_path = None

    def add_marker(self, pos: QPointF, color: QColor, text_color: QColor = None,
                   scale: float = None):
        # Always number after the highest existing marker so re-selecting
        # the tool and adding more markers continues the sequence correctly.
        existing = [it for it in self._scene.items() if isinstance(it, _MarkerItem)]
        self._marker_count = max((it.number for it in existing), default=0) + 1

        # Size is inherited from the most recently placed marker (so a resized
        # marker keeps its size for the next ones); colours always come from the
        # Marker tool's own settings, so changing them takes effect immediately.
        last_marker = next(
            (it for it in sorted(existing, key=lambda m: m.number, reverse=True)),
            None)
        item = _MarkerItem(pos, self._marker_count)
        item._scale      = float(scale) if scale is not None else (
            last_marker._scale if last_marker else 1.0)
        item._bg_color   = QColor(color)
        item._text_color = QColor(text_color) if text_color is not None else QColor(Qt.GlobalColor.white)
        self._scene.addItem(item)
        return item

    def add_text(self, pos: QPointF, text: str, font_size: int,
                 color: QColor, highlight: QColor):
        item = HighlightTextItem(text)
        item.highlight_color = highlight
        item.setDefaultTextColor(color)
        item.setFont(QFont("Arial", font_size))
        item.setPos(pos)
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self._scene.addItem(item)
        return item

    def add_pixmap(self, pos: QPointF, pixmap: QPixmap):
        item = ResizablePixmapItem(pixmap)
        item.setPos(pos)
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        item.setAcceptHoverEvents(True)
        self._scene.addItem(item)
        return item

    def delete_selected(self):
        for item in self._scene.selectedItems():
            self._scene.removeItem(item)

    def clear(self):
        self._scene.clear()
        self._marker_count = 0
        self._current_freehand_item = None
        self._freehand_path = None

    def has_items(self) -> bool:
        return len(self._scene.items()) > 0

    # ------------------------------------------------------------------
    # Resize handle detection (mirrors EditorCanvas.get_handle_at)
    # ------------------------------------------------------------------
    def get_handle_at(self, scene_pos: QPointF):
        """Return (item, handle_name) if pos is over a resize handle."""
        for item in self._scene.selectedItems():
            # Include all annotation item types that support handles
            # Allow native event handling for Line and Arrow items to avoid conflicts
            if isinstance(item, (ArrowItem, LineItem)):
                continue

            # Include all other annotation item types that support bounding-box handles
            if not isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem,
                                    ResizablePixmapItem, FreehandItem,
                                    TextBubbleItem,
                                    _MarkerItem)):
                continue
            local_pos = item.mapFromScene(scene_pos)
            rect = item.rect()
            m = 12.0  # slightly larger hit zone for easier interaction

            # _MarkerItem: only scale handle (left edge of bounding rect)
            if isinstance(item, _MarkerItem):
                r = item.RADIUS * item._scale
                if math.hypot(local_pos.x() + r, local_pos.y()) <= item.HANDLE_RADIUS + 6:
                    return item, 'TL'  # reuse TL to trigger uniform scale
                continue

            # Corner-radius handle (purple dot, top-left corner) — Rectangle only
            if isinstance(item, ResizableRectItem):
                rp = item._radius_handle_pos()
                if math.hypot(local_pos.x() - rp.x(), local_pos.y() - rp.y()) <= 10:
                    return item, 'RADIUS'

            # Rotation handle — above top edge (all rect/ellipse/pixmap/freehand items;
            # not for Pixelate/Blur, whose effect is always axis-aligned)
            rot_pt = QPointF(rect.center().x(), rect.top() - 30)
            if (not isinstance(item, PixelateBlurItem) and
                    abs(local_pos.x() - rot_pt.x()) < m * 1.5 and
                    abs(local_pos.y() - rot_pt.y()) < m * 1.5):
                return item, 'ROTATE'

            # Width handle (yellow dot below bottom edge) for Rect and Ellipse
            if isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
                wp = item._width_handle_pos()
                if math.hypot(local_pos.x() - wp.x(), local_pos.y() - wp.y()) <= 14:
                    return item, 'WIDTH'

            # Corner & edge resize handles
            L = abs(local_pos.x() - rect.left())  < m
            R = abs(local_pos.x() - rect.right()) < m
            T = abs(local_pos.y() - rect.top())   < m
            B = abs(local_pos.y() - rect.bottom()) < m

            # TextBubbleItem: only bottom-right corner (BR) for scaling
            if isinstance(item, TextBubbleItem):
                if R and B: return item, 'BR'
            else:
                if L and T: return item, 'TL'
                if R and T: return item, 'TR'
                if L and B: return item, 'BL'
                if R and B: return item, 'BR'
                if L: return item, 'L'
                if R: return item, 'R'
                if T: return item, 'T'
                if B: return item, 'B'
        return None, None

    def translate_item(self, item, delta: QPointF):
        """Move *item* by *delta* (scene units) — used by the Shift-to-move
        modifier while a handle is being dragged."""
        item.setPos(item.pos() + delta)
        item.update()

    def handle_resize(self, item, handle, scene_pos: QPointF, proportional=False,
                      from_center=False):
        """Resize/rotate item — full unified logic."""
        # Corner-radius handle — drag right = rounder, left = sharper
        if handle == 'RADIUS' and isinstance(item, ResizableRectItem):
            local_pos = item.mapFromScene(scene_pos)
            r = item.rect()
            if item._radius_drag_off is None:
                item._radius_drag_off = local_pos.x() - (r.left() + item._effective_radius())
            new_r = max(0.0, min(local_pos.x() - item._radius_drag_off - r.left(),
                                 item._max_radius()))
            item.prepareGeometryChange()
            item.corner_radius = new_r
            item.update()
            owner = self.parent()
            if hasattr(owner, '_prefs'):
                owner._prefs.set('rect', 'radius', new_r)   # new rectangles inherit the last value
            return

        # Width handle — drag vertically to change pen width
        if handle == 'WIDTH' and isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
            local_pos = item.mapFromScene(scene_pos)
            if not getattr(item, '_width_drag_active', False):
                item._width_drag_active = True
                item._width_drag_start_pos = local_pos
                item._width_drag_start_w   = item.pen().width()
            dy = local_pos.y() - item._width_drag_start_pos.y()
            new_w = max(1, int(item._width_drag_start_w + dy * 0.3))
            pen = item.pen()
            pen.setWidth(new_w)
            item.setPen(pen)
            item.prepareGeometryChange()
            item.update()
            return

            # TextBubbleItem

        # TextBubbleItem
        if isinstance(item, TextBubbleItem):
            local_pos = item.mapFromScene(scene_pos)
            if handle == 'BR':
                new_w = max(item.MIN_SIZE, local_pos.x())
                new_h = max(item.MIN_SIZE, local_pos.y())
                item.prepareGeometryChange()
                item._w = new_w
                item._h = new_h
                item._auto_grow()
                item.update()
            return

        # _MarkerItem: uniform scale
        if isinstance(item, _MarkerItem):
            local_pos = item.mapFromScene(scene_pos)
            dist = math.hypot(local_pos.x(), local_pos.y())
            dist = max(dist, item.RADIUS * 0.2)
            item.prepareGeometryChange()
            item._scale = dist / item.RADIUS
            item.update()
            _hp = _host_prefs(item)
            if _hp is not None:
                _hp.set('marker', 'scale', item._scale)
            return

        # --- ROTATION ---
        if handle == 'ROTATE':
            item.update() 
            center_scene = item.mapToScene(item.rect().center())
            diff = scene_pos - center_scene
            angle = math.degrees(math.atan2(diff.y(), diff.x())) + 90
            if proportional:
                angle = round(angle / 45) * 45
            item.setTransformOriginPoint(item.rect().center())
            item.setRotation(angle)
            item.update()
            return

        # --- STANDARD SCALING (Rect, Ellipse, Pixmap, Freehand) ---
        if hasattr(item, 'rect'):
            old_rect = item.rect()
            local_pos = item.mapFromScene(scene_pos)
            
            fixed_local = QPointF()
            if 'L' in handle: fixed_local.setX(old_rect.right())
            elif 'R' in handle: fixed_local.setX(old_rect.left())
            else: fixed_local.setX(old_rect.center().x())

            if 'T' in handle: fixed_local.setY(old_rect.bottom())
            elif 'B' in handle: fixed_local.setY(old_rect.top())
            else: fixed_local.setY(old_rect.center().y())
            
            if from_center:
                fixed_local = old_rect.center()
            old_scene_fixed = item.mapToScene(fixed_local)

            left, top, right, bottom = old_rect.left(), old_rect.top(), old_rect.right(), old_rect.bottom()
            
            if 'L' in handle: left = local_pos.x()
            if 'R' in handle: right = local_pos.x()
            if 'T' in handle: top = local_pos.y()
            if 'B' in handle: bottom = local_pos.y()
            
            new_rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
            
            if proportional:
                ratio = _pixmap_item_ratio(item)
                if ratio:
                    new_rect = _aspect_locked_rect(new_rect, handle, ratio)
                else:
                    side = max(new_rect.width(), new_rect.height())
                    if 'L' in handle: left = right - side
                    else: right = left + side
                    if 'T' in handle: top = bottom - side
                    else: bottom = top + side
                    new_rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()

            if from_center:
                # Alt: grow/shrink symmetrically around the rect's center
                c = old_rect.center()
                hw = abs(local_pos.x() - c.x()) if ('L' in handle or 'R' in handle) else old_rect.width() / 2
                hh = abs(local_pos.y() - c.y()) if ('T' in handle or 'B' in handle) else old_rect.height() / 2
                if proportional:
                    ratio = _pixmap_item_ratio(item)
                    if ratio:
                        h_ax = 'L' in handle or 'R' in handle
                        v_ax = 'T' in handle or 'B' in handle
                        if h_ax and v_ax:
                            hw = max(hw, hh * ratio)
                        elif v_ax:
                            hw = hh * ratio
                        hw = max(hw, 0.5)
                        hh = hw / ratio
                    else:
                        hw = hh = max(hw, hh)
                new_rect = QRectF(c.x() - hw, c.y() - hh, hw * 2, hh * 2)

            item.prepareGeometryChange()
            item.setRect(new_rect)
            item.setTransformOriginPoint(new_rect.center())
            
            new_scene_fixed = item.mapToScene(fixed_local)
            delta = old_scene_fixed - new_scene_fixed
            item.setPos(item.pos() + delta)
            item.update()

    # ------------------------------------------------------------------
    def _apply_props(self, item, color: QColor, width: int):
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        pen = QPen(color, width)
        if hasattr(item, 'setPen'):
            item.setPen(pen)
        if hasattr(item, 'setBrush'):
            item.setBrush(Qt.GlobalColor.transparent)

    # ── Undo / Redo ───────────────────────────────────────────────────────
    # Snapshot-based history — mirrors EditorCanvas (Image Editor) exactly:
    # push_undo() captures a detached copy of every item on the canvas right
    # before a mutating action runs; undo/redo simply swap the live scene
    # for a stored snapshot. There's no background pixmap here (the overlay
    # sits transparently on top of the desktop), so a snapshot is just the
    # list of annotation items.
    def _clone_item(self, item):
        """Return a standalone (detached) copy of a supported annotation item."""
        if isinstance(item, PixelateBlurItem):
            return _clone_pixblur(item)
        if isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
            cls = type(item)
            dup = cls()
            dup.setRect(QRectF(item.rect()))
            dup.setPen(QPen(item.pen()))
            dup.setBrush(QBrush(item.brush()))
            if isinstance(item, ResizableRectItem):
                dup.corner_radius = float(getattr(item, 'corner_radius', 0.0))
            dup.setRotation(item.rotation())
            dup.setTransformOriginPoint(item.transformOriginPoint())
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, HighlightRectItem):
            dup = HighlightRectItem()
            dup.setRect(QRectF(item.rect()))
            dup._color = QColor(item._color)
            dup.setPen(QPen(item.pen()))
            dup.setBrush(QBrush(item.brush()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, FreehandItem):
            dup = FreehandItem(QPainterPath(item.path()))
            dup.setPen(QPen(item.pen()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, LineItem):
            dup = LineItem(QLineF(item.line()), self)
            dup.setPen(QPen(item.pen()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, ArrowItem):
            dup = ArrowItem(QLineF(item.line()), self)
            dup.setPen(QPen(item.pen()))
            dup.head_style = getattr(item, 'head_style', ArrowItem.HEAD_SINGLE)
            dup._bend = QPointF(getattr(item, '_bend', QPointF(0.0, 0.0)))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, TextBubbleItem):
            dup = TextBubbleItem(item._text, QColor(item._fg_color), QColor(item._bg_color))
            dup._w = item._w
            dup._h = item._h
            dup._cone_rel = QPointF(item._cone_rel)
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
            dup = HighlightTextItem(item.toPlainText())
            dup.setFont(QFont(item.font()))
            dup.setDefaultTextColor(QColor(item.defaultTextColor()))
            dup.highlight_color   = QColor(getattr(item, 'highlight_color', QColor(0, 0, 0, 0)))
            dup.highlight_enabled = getattr(item, 'highlight_enabled', True)
            dup.highlight_padding = getattr(item, 'highlight_padding', 0)
            dup.outline_enabled   = getattr(item, 'outline_enabled', False)
            dup.outline_width     = getattr(item, 'outline_width', 2)
            dup.outline_color     = QColor(getattr(item, 'outline_color', QColor(0, 0, 0)))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, ResizablePixmapItem):
            dup = ResizablePixmapItem(QPixmap(item.pixmap))
            dup.setRect(QRectF(item.rect()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, _MarkerItem):
            dup = _MarkerItem(QPointF(0, 0), item.number)
            dup._scale = item._scale
            dup._spike_offset = QPointF(item._spike_offset)
            dup._bg_color = QColor(getattr(item, '_bg_color', QColor(220, 50, 50)))
            dup._text_color = QColor(getattr(item, '_text_color', QColor(Qt.GlobalColor.white)))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        return None

    def _snapshot(self):
        """Capture a standalone clone of every real item on the canvas."""
        items = list(self._scene.items())
        items.reverse()  # scene.items() is topmost-first; keep paint order
        clones = []
        for it in items:
            clone = self._clone_item(it)
            if clone is not None:
                clone.setZValue(it.zValue())
                clones.append(clone)
        return {'items': clones}

    def push_undo(self):
        """Call this right BEFORE a mutating action so it can be undone.
        Any fresh action invalidates the redo stack."""
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._notify_history_changed()

    def _restore_snapshot(self, snapshot):
        self._scene.clearSelection()
        for it in list(self._scene.items()):
            self._scene.removeItem(it)

        self._marker_count = 0
        for stored in snapshot['items']:
            # Clone again on the way back in — the objects held in the
            # snapshot must stay pristine so they can be restored again
            # later (e.g. redo after undo after redo...).
            fresh = self._clone_item(stored)
            if fresh is None:
                continue
            fresh.setZValue(stored.zValue())
            self._scene.addItem(fresh)
            if isinstance(fresh, _MarkerItem):
                self._marker_count = max(self._marker_count, fresh.number)

        self._current_freehand_item = None
        self._freehand_path = None
        self._notify_history_changed()

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        snapshot = self._undo_stack.pop()
        self._restore_snapshot(snapshot)

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        snapshot = self._redo_stack.pop()
        self._restore_snapshot(snapshot)

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def _notify_history_changed(self):
        owner = self.parent()
        if hasattr(owner, '_update_undo_redo_buttons'):
            owner._update_undo_redo_buttons()


# ─────────────────────────────────────────────────────────────────────────────
#  NEW ANNOTATION ITEMS
# ─────────────────────────────────────────────────────────────────────────────

# ═════════════════════════════════════════════════════════════════════════════
#  Tool options — shared infrastructure
#  ---------------------------------------------------------------------------
#  Every annotation tool (Capture Region overlay AND Image Editor) uses the
#  same options window:
#     ┌────────────────────────────────┐
#     │ tool-specific main settings    │  ← width, head style, radius, text…
#     │ [ Colour A ][ Colour B ]       │  ← tabs, only if the object has 2+ colours
#     │ embedded QColorDialog          │  ← ONE panel, re-pointed at the active tab
#     │ [ OK ] [ Cancel ]              │
#     └────────────────────────────────┘
#  and every tool remembers its own settings in a _ToolPrefs store, so
#  switching tools never leaks one tool's colour / width into another.
# ═════════════════════════════════════════════════════════════════════════════
def _swatch_icon(color: QColor, size: int = 14) -> QIcon:
    """Tiny filled-square icon (with checkerboard under transparent colours)
    used on the colour tabs so each tab shows its current value."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    half = size // 2
    p.fillRect(0, 0, size, size, QColor(255, 255, 255))
    p.fillRect(0, 0, half, half, QColor(190, 190, 190))
    p.fillRect(half, half, size - half, size - half, QColor(190, 190, 190))
    p.fillRect(0, 0, size, size, color)
    p.setPen(QPen(QColor(0, 0, 0, 200), 1))
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    return QIcon(pm)


def _swatch_css(c: QColor) -> str:
    """Stylesheet fragment painting a colour swatch (alpha-aware)."""
    return (f"background:rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"border:1px solid white; border-radius:3px;")


class _ToolPrefs:
    """Remembers the settings of every annotation tool *separately*.

    Keys are tool names ('rect', 'circle', 'line', 'arrow', 'freehand',
    'highlight', 'bubble', 'marker', 'text'). Values are plain dicts; QColor
    values are always copied on the way in and out, so no two tools (or a
    tool and an item on the canvas) ever share a mutable colour object.
    """
    def __init__(self, width: int = 3, font_size: int = 18):
        red = QColor(255, 0, 0)
        self._d = {
            'rect':      {'color': QColor(red), 'width': width, 'radius': 0.0, 'fill': False,
                          'fill_color': None},
            'circle':    {'color': QColor(red), 'width': width, 'fill': False,
                          'fill_color': None},
            'line':      {'color': QColor(red), 'width': width},
            'arrow':     {'color': QColor(red), 'width': round(width * 1.6),
                          'head': ArrowItem.HEAD_SINGLE},
            'freehand':  {'color': QColor(red), 'width': round(width * 1.6)},
            'highlight': {'color': QColor(255, 255, 0, 100)},
            'pixelate_blur': {'mode': 'pixelate', 'pixel': 12, 'blur': 10},
            'spotlight': {'blur': 2, 'ol_on': False, 'ol_w': 3, 'ol': QColor(255, 0, 0), 'defer': True},
            'bubble':    {'fg': QColor(red), 'bg': QColor(230, 230, 230, 240)},
            'marker':    {'bg': QColor(255, 0, 0), 'fg': QColor(255, 255, 255), 'scale': 1.0},
            'text':      {'color': QColor(red), 'size': font_size,
                          'hl': QColor(255, 255, 0, 255), 'hl_on': True, 'hl_pad': 0,
                          'ol': QColor(0, 0, 0), 'ol_on': False, 'ol_w': 2},
        }

    @staticmethod
    def _cp(v):
        return QColor(v) if isinstance(v, QColor) else v

    def get(self, tool: str, key: str):
        return self._cp(self._d[tool][key])

    def set(self, tool: str, key: str, value):
        if tool in self._d:
            self._d[tool][key] = self._cp(value)

    def update(self, tool: str, **kw):
        for k, v in kw.items():
            self.set(tool, k, v)

    def all(self, tool: str) -> dict:
        return {k: self._cp(v) for k, v in self._d[tool].items()}


def _prefs_key_for_item(item):
    """Which tool's prefs does a canvas item belong to? (None = no tool)"""
    if isinstance(item, ArrowItem):             return 'arrow'
    if isinstance(item, LineItem):              return 'line'
    if isinstance(item, FreehandItem):          return 'freehand'
    if isinstance(item, HighlightRectItem):     return 'highlight'
    if isinstance(item, SmartEraserItem):       return None      # no options
    if isinstance(item, SpotlightItem):         return 'spotlight'
    if isinstance(item, PixelateBlurItem):      return 'pixelate_blur'
    if isinstance(item, TextBubbleItem):        return 'bubble'
    if isinstance(item, _MarkerItem):           return 'marker'
    if isinstance(item, ResizableEllipseItem):  return 'circle'
    if isinstance(item, ResizableRectItem):     return 'rect'
    if isinstance(item, QGraphicsTextItem):     return 'text'
    return None


def _host_prefs(item):
    """_ToolPrefs of the window (overlay / editor) that owns *item*, or None."""
    host = _find_options_host(item)
    if host is None:
        return None
    if hasattr(host, '_prefs'):
        return host._prefs
    return getattr(getattr(host, 'canvas', None), 'prefs', None)


def _find_options_host(item):
    """Window (Capture Region overlay / Image Editor) that owns *item*'s scene
    and knows how to edit it through the shared options window."""
    sc = item.scene()
    if sc is not None:
        for view in sc.views():
            win = view.window()
            if win is not None and hasattr(win, '_edit_item'):
                return win
    return None


class _ToolOptionsDialog(QDialog):
    """Base window for every tool's options.

    Subclass recipe::

        class MyDialog(_ToolOptionsDialog):
            def __init__(self, ..., parent=None, target_item=None):
                super().__init__("Edit thing", parent, target_item)
                self.main_lay.addWidget(...)          # tool-specific settings
                self.add_color_slot('a', "Line", color, alpha=False)
                self.add_color_slot('b', "Fill", color2, alpha=True)
                self.finish()                         # builds tabs + colour panel + OK/Cancel

    * With ONE colour slot there are no tabs; with 2+ the user gets one tab per
      colour and the single embedded colour panel is re-pointed at the active
      slot (values are stored per slot, so nothing is lost when switching).
    * Live preview: override ``_apply_preview()`` (called on every change) and
      ``_restore_original()`` (called on Cancel).
    """
    SHOW_OPACITY = True       # subclasses set False to drop the opacity slider

    def __init__(self, title: str, parent=None, target_item=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(title)
        self._target_item = target_item
        self._positioned = False
        self._slots = {}          # key -> {'color': QColor, 'alpha': bool, 'label': str}
        self._order = []          # slot keys in tab order
        self._active = None       # key of the slot the colour panel currently edits
        self._loading = False     # True while the panel is being re-pointed (ignore its signal)
        self._finished = False
        self._root = QVBoxLayout(self)
        self.main_lay = QVBoxLayout()
        self.main_lay.setContentsMargins(0, 0, 0, 0)
        self._root.addLayout(self.main_lay)

    # ── colour slots ─────────────────────────────────────────────────────────
    def add_color_slot(self, key: str, label: str, color: QColor, alpha: bool = True):
        self._slots[key] = {'color': QColor(color), 'alpha': alpha, 'label': label}
        self._order.append(key)

    def slot_color(self, key: str) -> QColor:
        return QColor(self._slots[key]['color'])

    def set_slot_color(self, key: str, color: QColor):
        """Programmatically change a slot (also refreshes the panel / tab)."""
        self._slots[key]['color'] = QColor(color)
        if self._finished:
            self._refresh_tab(key)
            if key == self._active:
                self._point_panel_at(key)

    def _coerce_color(self, key: str, c: QColor) -> QColor:
        """Panel colours are RGB only — keep the opacity the slider gave this slot."""
        c = QColor(c)
        c.setAlpha(self._slots[key]['color'].alpha())
        return c

    # ── build the bottom half (tabs + colour panel + buttons) ────────────────
    def finish(self):
        self._tabs = QTabBar()
        self._tabs.setExpanding(False)
        self._tabs.setDrawBase(False)            # no baseline under the tabs
        for key in self._order:
            self._tabs.addTab(_swatch_icon(self._slots[key]['color']),
                              self._slots[key]['label'])
        # A single colour needs no tab strip — just a caption.
        if len(self._order) == 1:
            self._tabs.hide()
            self._root.addWidget(QLabel(self._slots[self._order[0]]['label'] + ":"))
        else:
            self._root.addWidget(self._tabs)
            self._restyle_tabs(self.palette().window().color())

        # Opacity of the colour currently shown in the panel (per tab)
        self._op_row = QHBoxLayout()
        self._op_row.addWidget(QLabel("Opacity:"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(255)
        self.opacity_label = QLabel("100%")
        self.opacity_label.setFixedWidth(40)
        self._op_row.addWidget(self.opacity_slider, 1)
        self._op_row.addWidget(self.opacity_label)
        self._root.addLayout(self._op_row)

        # Embedded colour panel: a QColorDialog is a QWidget underneath, so with
        # plain Widget flags it folds into this dialog; its own OK/Cancel row is
        # hidden (NoButtons) because this dialog's button box covers that. Its
        # alpha field is hidden too — the opacity slider above replaces it.
        self.color_dialog = QColorDialog(self)
        self.color_dialog.setWindowFlags(Qt.WindowType.Widget)
        self.color_dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        self.color_dialog.setOption(QColorDialog.ColorDialogOption.NoButtons, True)
        self.color_dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        self._root.addWidget(self.color_dialog)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self._root.addWidget(bb)

        self._finished = True
        if self._order:
            self._active = self._order[0]
            self._point_panel_at(self._active)
            self.color_dialog.currentColorChanged.connect(self._on_panel_color_changed)
            self._tabs.currentChanged.connect(self._on_tab_changed)
            self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        else:
            self.color_dialog.hide()
        if not self._order or not self.SHOW_OPACITY:
            for i in range(self._op_row.count()):
                w = self._op_row.itemAt(i).widget()
                if w is not None:
                    w.hide()

    def _restyle_tabs(self, bg: QColor):
        """Active tab = exactly the dialog background, no outline underneath,
        so it reads as part of the page; inactive tabs are a shade apart."""
        dark = bg.lightness() < 128
        inactive = bg.darker(140) if dark else bg.darker(110)
        hover = bg.darker(120) if dark else bg.darker(105)
        fg_on = "#e6e6e6" if dark else "#141414"
        fg_off = "#8d8fa0" if dark else "#6c6c6c"
        # subtle outline on left / top / right only (never underneath)
        edge = bg.lighter(235) if dark else bg.darker(130)
        edge_off = bg.lighter(190) if dark else bg.darker(118)
        self._tabs.setStyleSheet(f"""
            QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
            QTabBar::tab {{ background:{inactive.name()}; color:{fg_off}; border:none;
                border-top:1px solid {edge_off.name()}; border-left:1px solid {edge_off.name()};
                border-right:1px solid {edge_off.name()}; border-bottom:none;
                padding:6px 13px; margin:0 2px 0 0;
                border-top-left-radius:7px; border-top-right-radius:7px;
                border-bottom-left-radius:0; border-bottom-right-radius:0; }}
            QTabBar::tab:selected {{ background:{bg.name()}; color:{fg_on}; border:none;
                border-top:1px solid {edge.name()}; border-left:1px solid {edge.name()};
                border-right:1px solid {edge.name()}; border-bottom:none; }}
            QTabBar::tab:hover:!selected {{ background:{hover.name()}; }}
        """)

    def _sample_bg_and_restyle(self):
        """Read the dialog's real background (global QSS or palette) from a
        rendered pixel so the active tab matches it exactly."""
        if len(self._order) < 2:
            return
        try:
            bg = self.grab().toImage().pixelColor(1, 1)
        except Exception:
            bg = self.palette().window().color()
        if bg.alpha() == 0:
            bg = self.palette().window().color()
        self._restyle_tabs(bg)

    def _point_panel_at(self, key: str):
        """Load a slot's value into the (single) colour panel + opacity slider
        without triggering the change handlers."""
        self._loading = True
        try:
            col = self._slots[key]['color']
            shown = QColor(col)
            shown.setAlpha(255)                 # panel edits RGB only
            self.color_dialog.setCurrentColor(shown)
            self.opacity_slider.blockSignals(True)
            self.opacity_slider.setValue(col.alpha())
            self.opacity_slider.blockSignals(False)
            self.opacity_label.setText(f"{int(round(col.alpha() / 255 * 100))}%")
        finally:
            self._loading = False

    def _on_opacity_changed(self, val: int):
        if self._loading or self._active is None:
            return
        c = self.slot_color(self._active)
        c.setAlpha(int(val))
        self._slots[self._active]['color'] = c
        self.opacity_label.setText(f"{int(round(val / 255 * 100))}%")
        self._refresh_tab(self._active)
        self._on_slot_changed(self._active)
        self._apply_preview()

    def _refresh_tab(self, key: str):
        idx = self._order.index(key)
        self._tabs.setTabIcon(idx, _swatch_icon(self._slots[key]['color']))

    def _on_tab_changed(self, idx: int):
        if idx < 0 or idx >= len(self._order):
            return
        self._active = self._order[idx]
        self._point_panel_at(self._active)

    def _on_panel_color_changed(self, color: QColor):
        if self._loading or self._active is None:
            return
        self._slots[self._active]['color'] = self._coerce_color(self._active, color)
        self._refresh_tab(self._active)
        self._on_slot_changed(self._active)
        self._apply_preview()

    def _on_slot_changed(self, key: str):
        """Hook for subclasses (e.g. refresh a preview label)."""

    # ── live preview plumbing ────────────────────────────────────────────────
    def _apply_preview(self, *_):
        """Override: push the dialog's current state onto ``self._target_item``."""

    def _restore_original(self):
        """Override: put ``self._target_item`` back as it was before previewing."""

    def restore_original(self):
        """Public: hosts call this on OK so the undo snapshot is taken from the
        pristine (pre-preview) state."""
        self._restore_original()

    def reject(self):
        self._restore_original()
        super().reject()

    # ── show on whichever monitor holds the cursor ───────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        if not self._positioned:
            self._positioned = True
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            if screen is not None:
                geo = screen.geometry()
                size = self.sizeHint()
                self.move(geo.x() + (geo.width() - size.width()) // 2,
                          geo.y() + (geo.height() - size.height()) // 2)
            QTimer.singleShot(0, self._sample_bg_and_restyle)

    # ── small helper used by several subclasses ──────────────────────────────
    def _make_width_row(self, label: str, value: int, lo: int = 1, hi: int = 100):
        """Label + slider + spin box kept in sync. Returns (row_layout, spin)."""
        self.main_lay.addWidget(QLabel(label))
        row = QHBoxLayout()
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(max(lo, min(hi, int(value))))
        spin.setSuffix(" px")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(spin.value())
        row.addWidget(slider, 1)
        row.addWidget(spin)
        self.main_lay.addLayout(row)

        def _spin_changed(v):
            if slider.value() != v:
                slider.blockSignals(True); slider.setValue(v); slider.blockSignals(False)
            self._apply_preview()

        def _slider_changed(v):
            if spin.value() != v:
                spin.blockSignals(True); spin.setValue(v); spin.blockSignals(False)
            self._apply_preview()

        spin.valueChanged.connect(_spin_changed)
        slider.valueChanged.connect(_slider_changed)
        return row, spin


class _ColorSwatch(QWidget):
    """Small toolbar indicator showing the colour(s) of the active tool or the
    selected object. Two-colour objects show two halves, three-colour objects
    three stripes — so the swatch always mirrors what the options window edits."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = [QColor(255, 0, 0)]
        self.setFixedSize(18, 18)
        self.setToolTip("Colour(s) of the active tool / selected object")

    def set_colors(self, colors):
        colors = [QColor(c) for c in colors] or [QColor(255, 0, 0)]
        self._colors = colors
        n = len(colors)
        self.setFixedSize(18 if n == 1 else 14 * n + 4, 18)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(r), 3, 3)
        p.setClipPath(clip)
        # checkerboard so transparent colours are visible as such
        p.fillRect(r, QColor(255, 255, 255))
        step = 5
        for yy in range(0, r.height() + 1, step):
            for xx in range(0, r.width() + 1, step):
                if ((xx // step) + (yy // step)) % 2:
                    p.fillRect(xx, yy, step, step, QColor(200, 200, 200))
        n = len(self._colors)
        w = r.width() / n
        for i, c in enumerate(self._colors):
            p.fillRect(QRectF(i * w, 0, w + 1, r.height() + 1), c)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.drawRoundedRect(QRectF(r), 3, 3)
        p.end()


class HighlightEditDialog(_ToolOptionsDialog):
    """Highlight: background colour + opacity (the shared opacity slider)."""
    def __init__(self, current_color: QColor, parent=None, target_item=None):
        super().__init__("Edit Highlight", parent, target_item)
        self._orig_color = QColor(current_color)
        self.add_color_slot('bg', "Background color", current_color)
        self.finish()

    @property
    def color(self) -> QColor:
        return self.slot_color('bg')

    def _apply_preview(self, *_):
        it = self._target_item
        if it is None:
            return
        c = self.result_color()
        it._color = c
        it.setBrush(QBrush(c))
        it.update()

    def restore_original(self):
        it = self._target_item
        if it is None:
            return
        it._color = QColor(self._orig_color)
        it.setBrush(QBrush(self._orig_color))
        it.update()

    _restore_original = restore_original

    def result_color(self) -> QColor:
        return self.slot_color('bg')


class ArrowEditDialog(_ToolOptionsDialog):
    SHOW_OPACITY = False      # no transparency slider for this object
    """Arrow: head style + line width (main settings) and line colour.

    If *target_item* (an ArrowItem already on the canvas) is given, every
    change is applied to it live; Cancel restores the original values."""
    def __init__(self, current_width: int, current_color: QColor,
                 current_head_style: str = 'single', parent=None,
                 target_item=None):
        super().__init__("Edit Arrow", parent, target_item)
        self._orig_width = current_width
        self._orig_color = QColor(current_color)
        self._orig_head = current_head_style

        self.main_lay.addWidget(QLabel("Arrowhead:"))
        head_row = QHBoxLayout()
        self.rb_single       = QRadioButton("➡️ One head")
        self.rb_single_start = QRadioButton("⬅️ One head (other side)")
        self.rb_double       = QRadioButton("↔️ Two heads")
        self.rb_none         = QRadioButton("— No head")
        self._head_group = QButtonGroup(self)
        for rb in (self.rb_single, self.rb_single_start, self.rb_double, self.rb_none):
            self._head_group.addButton(rb)
            head_row.addWidget(rb)
        self.main_lay.addLayout(head_row)
        if current_head_style == ArrowItem.HEAD_DOUBLE:
            self.rb_double.setChecked(True)
        elif current_head_style == ArrowItem.HEAD_NONE:
            self.rb_none.setChecked(True)
        elif current_head_style == ArrowItem.HEAD_SINGLE_START:
            self.rb_single_start.setChecked(True)
        else:
            self.rb_single.setChecked(True)
        for rb in (self.rb_single, self.rb_single_start, self.rb_double, self.rb_none):
            rb.toggled.connect(self._apply_preview)

        _, self.spin_width = self._make_width_row("Line width:", current_width, 1, 100)
        self.add_color_slot('line', "Line color", current_color, alpha=False)
        self.finish()

    @property
    def _color(self) -> QColor:
        return self.slot_color('line')

    def _apply_preview(self, *_):
        it = self._target_item
        if it is None:
            return
        pen = it.pen()
        pen.setWidth(self.spin_width.value())
        pen.setColor(self.slot_color('line'))
        it.setPen(pen)
        it.head_style = self.result_head_style()
        it.prepareGeometryChange()
        it.update()
        if it.scene() is not None:
            it.scene().update()

    def restore_original(self):
        it = self._target_item
        if it is None:
            return
        pen = it.pen()
        pen.setWidth(self._orig_width)
        pen.setColor(self._orig_color)
        it.setPen(pen)
        it.head_style = self._orig_head
        it.prepareGeometryChange()
        it.update()
        if it.scene() is not None:
            it.scene().update()

    _restore_original = restore_original

    def result_head_style(self) -> str:
        if self.rb_double.isChecked():
            return ArrowItem.HEAD_DOUBLE
        if self.rb_none.isChecked():
            return ArrowItem.HEAD_NONE
        if self.rb_single_start.isChecked():
            return ArrowItem.HEAD_SINGLE_START
        return ArrowItem.HEAD_SINGLE

    def result_data(self):
        """Returns (width, color, head_style)."""
        return self.spin_width.value(), self.slot_color('line'), self.result_head_style()


class _FillTabsMixin:
    """Shared by the dialogs of fillable shapes: a 'Fill shape' checkbox plus a
    second colour tab ('Background color') that is the fill. The tab is marked
    '(off)' while the checkbox is unchecked."""
    def _add_fill_controls(self, fill, fill_color, outline_color):
        self.chk_fill = None
        if fill is None:
            return
        self.chk_fill = QCheckBox("Fill shape")
        self.chk_fill.setChecked(bool(fill))
        self.main_lay.addWidget(self.chk_fill)
        self._fill_init = QColor(fill_color) if fill_color is not None else QColor(outline_color)

    def _add_fill_slot(self):
        if self.chk_fill is not None:
            self.add_color_slot('fill', "Background color", self._fill_init)

    def _connect_fill(self):
        if self.chk_fill is not None:
            self.chk_fill.toggled.connect(self._on_fill_toggled)
            self._update_fill_tab_title()

    def _on_fill_toggled(self, *_):
        self._update_fill_tab_title()
        self._apply_preview()

    def _update_fill_tab_title(self):
        if self.chk_fill is not None and 'fill' in self._order:
            i = self._order.index('fill')
            self._tabs.setTabText(i, "Background color" +
                                  ("" if self.chk_fill.isChecked() else " (off)"))

    def result_fill(self):
        return None if self.chk_fill is None else self.chk_fill.isChecked()

    def result_fill_color(self):
        return QColor(self.slot_color('fill')) if self.chk_fill is not None else None

    def _brush_for_preview(self):
        if self.chk_fill is not None and self.chk_fill.isChecked():
            return QBrush(self.slot_color('fill'))
        return QBrush(Qt.GlobalColor.transparent)


class StrokeEditDialog(_FillTabsMixin, _ToolOptionsDialog):
    """Width + colour for Line / Circle / Freehand. With ``fill`` given (not
    None) it also gets "Fill shape" and a second colour tab for the fill."""
    def __init__(self, title: str, current_width: int, current_color: QColor,
                 parent=None, target_item=None, alpha: bool = True,
                 fill=None, max_width: int = 100, color_label: str = "Line color",
                 fill_color=None):
        super().__init__(title, parent, target_item)
        self._orig_pen = QPen(target_item.pen()) if target_item is not None else None
        self._orig_brush = (QBrush(target_item.brush())
                            if target_item is not None and hasattr(target_item, 'brush') else None)
        _, self.spin_width = self._make_width_row("Line width:", current_width, 1, max_width)
        self._add_fill_controls(fill, fill_color, current_color)
        self.add_color_slot('line', color_label, current_color)
        self._add_fill_slot()
        self.finish()
        self._connect_fill()

    def _apply_preview(self, *_):
        it = self._target_item
        if it is None:
            return
        pen = it.pen()
        pen.setWidth(self.spin_width.value())
        pen.setColor(self.slot_color('line'))
        it.setPen(pen)
        if self.chk_fill is not None and hasattr(it, 'setBrush'):
            it.setBrush(self._brush_for_preview())
        if hasattr(it, 'prepareGeometryChange'):
            it.prepareGeometryChange()
        it.update()

    def restore_original(self):
        it = self._target_item
        if it is None or self._orig_pen is None:
            return
        it.setPen(QPen(self._orig_pen))
        if self._orig_brush is not None and hasattr(it, 'setBrush'):
            it.setBrush(QBrush(self._orig_brush))
        if hasattr(it, 'prepareGeometryChange'):
            it.prepareGeometryChange()
        it.update()

    _restore_original = restore_original

    def result_data(self):
        """Returns (width, color)."""
        return self.spin_width.value(), self.slot_color('line')


class FreehandEditDialog(StrokeEditDialog):
    """Freehand strokes — line width and colour."""
    def __init__(self, current_width: int, current_color: QColor, parent=None,
                 target_item=None):
        super().__init__("Edit Freehand", current_width, current_color, parent, target_item)


class LineEditDialog(StrokeEditDialog):
    """Straight line — line width and colour."""
    def __init__(self, current_width: int, current_color: QColor, parent=None,
                 target_item=None):
        super().__init__("Edit Line", current_width, current_color, parent, target_item)


class CircleEditDialog(StrokeEditDialog):
    """Ellipse — width, outline colour, optional fill + background colour."""
    def __init__(self, current_width: int, current_color: QColor, parent=None,
                 target_item=None, fill=None, fill_color=None):
        super().__init__("Edit Circle", current_width, current_color, parent,
                         target_item, fill=fill, fill_color=fill_color,
                         color_label="Outline color")


class RectEditDialog(_FillTabsMixin, _ToolOptionsDialog):
    """Rectangle: line width + corner rounding + optional fill (main settings),
    'Outline color' and 'Background color' tabs.

    If `target_item` is given, changes are previewed live and restored on
    Cancel. On OK the caller should call `restore_original()` (so the undo
    snapshot is taken from the untouched state), push undo, then apply
    `result_data()` itself."""
    def __init__(self, current_width: int, current_color: QColor,
                 current_radius: float, parent=None, target_item=None,
                 fill=None, fill_color=None):
        super().__init__("Rectangle settings", parent, target_item)
        if target_item is not None:
            self._orig_pen = QPen(target_item.pen())
            self._orig_brush = QBrush(target_item.brush())
            self._orig_radius = float(getattr(target_item, 'corner_radius', 0.0))
        _, self.spin_width = self._make_width_row("Line width:", current_width, 1, 200)

        max_r = 1000
        if target_item is not None and hasattr(target_item, '_max_radius'):
            max_r = max(1, int(math.ceil(target_item._max_radius())))
        rad_row = QHBoxLayout()
        rad_row.addWidget(QLabel("Corner rounding:"))
        self.slider_radius = QSlider(Qt.Orientation.Horizontal)
        self.slider_radius.setRange(0, max_r)
        self.spin_radius = QSpinBox()
        self.spin_radius.setRange(0, max_r)
        self.spin_radius.setSuffix(" px")
        start_r = max(0, min(max_r, int(round(current_radius))))
        self.slider_radius.setValue(start_r)
        self.spin_radius.setValue(start_r)
        self.slider_radius.setToolTip(
            "Also adjustable by dragging the purple dot at the top-left corner "
            "of a selected rectangle (right = rounder)")
        rad_row.addWidget(self.slider_radius, 1)
        rad_row.addWidget(self.spin_radius)
        self.main_lay.addLayout(rad_row)
        self.slider_radius.valueChanged.connect(self._on_slider_radius)
        self.spin_radius.valueChanged.connect(self._on_spin_radius)

        self._add_fill_controls(fill, fill_color, current_color)
        self.add_color_slot('line', "Outline color", current_color)
        self._add_fill_slot()
        self.finish()
        self._connect_fill()

    def _on_slider_radius(self, v):
        self.spin_radius.blockSignals(True)
        self.spin_radius.setValue(v)
        self.spin_radius.blockSignals(False)
        self._apply_preview()

    def _on_spin_radius(self, v):
        self.slider_radius.blockSignals(True)
        self.slider_radius.setValue(v)
        self.slider_radius.blockSignals(False)
        self._apply_preview()

    def _apply_preview(self, *_):
        it = self._target_item
        if it is None:
            return
        pen = it.pen()
        pen.setWidth(self.spin_width.value())
        pen.setColor(self.slot_color('line'))
        it.setPen(pen)
        if self.chk_fill is not None:
            it.setBrush(self._brush_for_preview())
        it.corner_radius = float(self.spin_radius.value())
        it.prepareGeometryChange()
        it.update()

    def restore_original(self):
        it = self._target_item
        if it is None:
            return
        it.setPen(QPen(self._orig_pen))
        it.setBrush(QBrush(self._orig_brush))
        it.corner_radius = self._orig_radius
        it.prepareGeometryChange()
        it.update()

    _restore_original = restore_original

    def result_data(self):
        """(line width, outline colour, corner radius in px)"""
        return (self.spin_width.value(), self.slot_color('line'),
                float(self.spin_radius.value()))


class _BubbleEditDialog(_ToolOptionsDialog):
    SHOW_OPACITY = False      # no transparency slider for this object
    """Text bubble: text (main setting) + two colour tabs (text / background).

    ``show_text=False`` is used when editing a tool's *defaults* (no text)."""
    def __init__(self, fg_color: QColor = None, bg_color: QColor = None,
                 text: str = "", parent=None, target_item=None,
                 title: str = "Edit Text Bubble", show_text: bool = True):
        super().__init__(title, parent, target_item)
        fg = QColor(fg_color) if fg_color else QColor(Qt.GlobalColor.black)
        bg = QColor(bg_color) if bg_color else QColor(230, 230, 230, 240)
        self._orig_fg, self._orig_bg = QColor(fg), QColor(bg)
        self.edit = QTextEdit()
        self.edit.setPlainText(text)
        self.edit.setFixedHeight(80)
        if show_text:
            self.main_lay.addWidget(QLabel("Text:"))
            self.main_lay.addWidget(self.edit)
        self.add_color_slot('fg', "Text color", fg, alpha=False)
        self.add_color_slot('bg', "Background color", bg, alpha=False)
        self.finish()

    fg_color = property(lambda s: s.slot_color('fg'),
                        lambda s, c: s.set_slot_color('fg', c))
    bg_color = property(lambda s: s.slot_color('bg'),
                        lambda s, c: s.set_slot_color('bg', c))

    def _update_btn_styles(self):          # legacy hook — panel/tabs refresh themselves
        self._refresh_tab('fg'); self._refresh_tab('bg')

    def _apply_preview(self, *_):
        it = self._target_item
        if it is None:
            return
        it._fg_color = self.slot_color('fg')
        it._bg_color = self.slot_color('bg')
        it.update()

    def restore_original(self):
        it = self._target_item
        if it is None:
            return
        it._fg_color = QColor(self._orig_fg)
        it._bg_color = QColor(self._orig_bg)
        it.update()

    _restore_original = restore_original

    def result_data(self):
        return self.edit.toPlainText(), self.slot_color('fg'), self.slot_color('bg')


class _BubbleInputDialog(_BubbleEditDialog):
    """New text bubble (same window as editing one, but empty)."""
    def __init__(self, fg_color: QColor = None, parent=None, bg_color: QColor = None):
        super().__init__(fg_color, bg_color, "", parent, None, "Text Bubble", True)


class _MarkerPreview(QWidget):
    """Little numbered-circle preview for the marker options window."""
    def __init__(self, dlg, parent=None):
        super().__init__(parent)
        self._dlg = dlg
        self.setFixedHeight(46)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = 16
        cx, cy = self.width() / 2, self.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._dlg.slot_color('bg')))
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(QPen(self._dlg.slot_color('fg'), 1))
        p.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        p.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, "1")
        p.end()


class _MarkerEditDialog(_ToolOptionsDialog):
    """Numbered marker: Size (main setting — the same value the item's round
    resize handle sets) + preview, and two colour tabs (background / number)."""
    SHOW_OPACITY = False      # no transparency slider for this object

    def __init__(self, bg_color: QColor = None, text_color: QColor = None,
                 parent=None, target_item=None, scale: float = None):
        super().__init__("Edit Marker", parent, target_item)
        bg = QColor(bg_color) if bg_color else QColor(220, 50, 50)
        fg = QColor(text_color) if text_color else QColor(Qt.GlobalColor.white)
        self._orig_bg, self._orig_fg = QColor(bg), QColor(fg)
        if scale is None:
            scale = target_item._scale if target_item is not None else 1.0
        self._orig_scale = float(scale)

        # Size in % of the default marker (100% = default size). The resize
        # handle on the marker sets the very same value (_scale).
        self.main_lay.addWidget(QLabel("Size:"))
        row = QHBoxLayout()
        # The marker's resize handle has no upper limit, so neither does this:
        # the range always covers the current value (min. 2000 %), otherwise
        # opening + OK-ing the dialog would silently shrink a big marker.
        lo = 20
        hi = max(2000, int(math.ceil(scale * 100)))
        pct = max(lo, int(round(scale * 100)))
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(lo, hi)
        self.size_slider.setValue(pct)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(lo, hi)
        self.size_spin.setSuffix(" %")
        self.size_spin.setValue(pct)
        self.size_slider.setToolTip("Same as dragging the round handle on the left "
                                    "edge of a selected marker")
        row.addWidget(self.size_slider, 1)
        row.addWidget(self.size_spin)
        self.main_lay.addLayout(row)
        self.size_slider.valueChanged.connect(self._on_size_slider)
        self.size_spin.valueChanged.connect(self._on_size_spin)

        self.add_color_slot('bg', "Background color", bg)
        self.add_color_slot('fg', "Text color", fg)
        self._preview = _MarkerPreview(self)
        self.main_lay.addWidget(self._preview)
        self.finish()

    def _on_size_slider(self, v):
        self.size_spin.blockSignals(True); self.size_spin.setValue(v); self.size_spin.blockSignals(False)
        self._apply_preview()

    def _on_size_spin(self, v):
        self.size_slider.blockSignals(True); self.size_slider.setValue(v); self.size_slider.blockSignals(False)
        self._apply_preview()

    def result_scale(self) -> float:
        return self.size_spin.value() / 100.0

    bg_color = property(lambda s: s.slot_color('bg'),
                        lambda s, c: s.set_slot_color('bg', c))
    text_color = property(lambda s: s.slot_color('fg'),
                          lambda s, c: s.set_slot_color('fg', c))

    def _on_slot_changed(self, key):
        self._preview.update()

    def _update_styles(self):              # legacy hook
        self._preview.update()

    def _apply_preview(self, *_):
        it = self._target_item
        if it is None:
            return
        it._bg_color = self.slot_color('bg')
        it._text_color = self.slot_color('fg')
        it.prepareGeometryChange()
        it._scale = self.result_scale()
        it.update()

    def restore_original(self):
        it = self._target_item
        if it is None:
            return
        it._bg_color = QColor(self._orig_bg)
        it._text_color = QColor(self._orig_fg)
        it.prepareGeometryChange()
        it._scale = self._orig_scale
        it.update()

    _restore_original = restore_original

    def result_data(self):
        return self.slot_color('bg'), self.slot_color('fg')


class _TextInputDialog(_ToolOptionsDialog):
    """Text annotation: text, size, highlight and outline options (main
    settings) + three colour tabs (text / highlight / outline).

    ``show_text=False`` is used when editing the Text tool's *defaults*."""
    def __init__(self, color, parent=None, show_text: bool = True, state: dict = None):
        super().__init__("Add Text", parent)
        self.edit = QTextEdit()
        self.edit.setFixedHeight(80)
        if show_text:
            self.main_lay.addWidget(QLabel("Text:"))
            self.main_lay.addWidget(self.edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("Font size:"))
        self.sz = QSpinBox(); self.sz.setRange(6, 120); self.sz.setValue(18)
        row.addWidget(self.sz)
        row.addStretch()
        self.main_lay.addLayout(row)

        hl_row = QHBoxLayout()
        self.chk_hl = QCheckBox("Enable highlight")
        self.chk_hl.setChecked(True)
        hl_row.addWidget(self.chk_hl)
        hl_row.addWidget(QLabel("Padding:"))
        self.hl_pad = QSpinBox(); self.hl_pad.setRange(0, 40); self.hl_pad.setValue(0)
        self.hl_pad.setSuffix(" px")
        hl_row.addWidget(self.hl_pad)
        hl_row.addStretch()
        self.main_lay.addLayout(hl_row)

        ol_row = QHBoxLayout()
        self.chk_ol = QCheckBox("Enable outline")
        self.chk_ol.setChecked(False)
        ol_row.addWidget(self.chk_ol)
        ol_row.addWidget(QLabel("Width:"))
        self.ol_width = QSpinBox(); self.ol_width.setRange(1, 20); self.ol_width.setValue(2)
        self.ol_width.setSuffix(" px")
        ol_row.addWidget(self.ol_width)
        ol_row.addStretch()
        self.main_lay.addLayout(ol_row)

        self.add_color_slot('text', "Text color", QColor(color), alpha=False)
        self.add_color_slot('hl', "Highlight", QColor(255, 255, 0, 255), alpha=True)
        self.add_color_slot('ol', "Outline", QColor(0, 0, 0), alpha=False)
        self.finish()
        self.chk_hl.toggled.connect(self._update_tab_titles)
        self.chk_ol.toggled.connect(self._update_tab_titles)
        if state:
            self.apply_state(state)
        self._update_tab_titles()

    # legacy attribute names used by older call sites
    color = property(lambda s: s.slot_color('text'),
                     lambda s, c: s.set_slot_color('text', c))
    highlight = property(lambda s: s.slot_color('hl'),
                         lambda s, c: s.set_slot_color('hl', c))
    outline_color = property(lambda s: s.slot_color('ol'),
                             lambda s, c: s.set_slot_color('ol', c))

    def _update_btn_color(self):           # legacy hook
        for k in self._order:
            self._refresh_tab(k)
        self._point_panel_at(self._active)

    def _update_tab_titles(self, *_):
        names = {'text': "Text color",
                 'hl': "Highlight" + ("" if self.chk_hl.isChecked() else " (off)"),
                 'ol': "Outline" + ("" if self.chk_ol.isChecked() else " (off)")}
        for i, k in enumerate(self._order):
            self._tabs.setTabText(i, names[k])

    def apply_state(self, s: dict):
        """Load remembered Text-tool settings (see _ToolPrefs 'text')."""
        self.sz.setValue(int(s['size']))
        self.chk_hl.setChecked(bool(s['hl_on']))
        self.hl_pad.setValue(int(s['hl_pad']))
        self.chk_ol.setChecked(bool(s['ol_on']))
        self.ol_width.setValue(int(s['ol_w']))
        self.set_slot_color('text', s['color'])
        self.set_slot_color('hl', s['hl'])
        self.set_slot_color('ol', s['ol'])
        self._update_tab_titles()

    def state(self) -> dict:
        return {'color': self.slot_color('text'), 'size': self.sz.value(),
                'hl': self.slot_color('hl'), 'hl_on': self.chk_hl.isChecked(),
                'hl_pad': self.hl_pad.value(),
                'ol': self.slot_color('ol'), 'ol_on': self.chk_ol.isChecked(),
                'ol_w': self.ol_width.value()}

    def result_data(self):
        return (self.edit.toPlainText(), self.sz.value(), self.slot_color('text'),
                self.slot_color('hl'), self.chk_hl.isChecked(), self.hl_pad.value(),
                self.chk_ol.isChecked(), self.ol_width.value(), self.slot_color('ol'))


# ─────────────────────────────────────────────────────────────────────────────
#  Shared "build dialog / commit result" logic, used by BOTH the Capture Region
#  overlay and the Image Editor so the two behave identically.
# ─────────────────────────────────────────────────────────────────────────────
def _item_is_filled(item) -> bool:
    b = item.brush()
    return b.style() != Qt.BrushStyle.NoBrush and b.color().alpha() > 0


def _item_fill_color(item) -> QColor:
    """Background colour of a rect/ellipse: its current fill if filled, else the
    last one it had, else (legacy behaviour) its outline colour."""
    b = item.brush()
    if b.style() != Qt.BrushStyle.NoBrush and b.color().alpha() > 0:
        return QColor(b.color())
    c = getattr(item, '_fill_color', None)
    return QColor(c) if c is not None else QColor(item.pen().color())


def _prefs_fill_color(prefs, key) -> QColor:
    """Fill colour a tool uses for new shapes (None setting → follows outline)."""
    c = prefs.get(key, 'fill_color')
    return QColor(c) if c is not None else prefs.get(key, 'color')


def _build_options_dialog(key, parent, prefs, item=None, allow_fill=False):
    """Return the options window for tool *key*.

    * ``item`` given  → opens on that object's current values, with live preview.
    * ``item`` None   → opens on the tool's remembered defaults (no text box).
    """
    if key == 'rect':
        if item is not None:
            return RectEditDialog(item.pen().width(), item.pen().color(),
                                  item.corner_radius, parent, target_item=item,
                                  fill=_item_is_filled(item) if allow_fill else None,
                                  fill_color=_item_fill_color(item))
        p = prefs.all('rect')
        return RectEditDialog(p['width'], p['color'], p['radius'], parent,
                              fill=p['fill'] if allow_fill else None,
                              fill_color=_prefs_fill_color(prefs, 'rect'))
    if key == 'circle':
        if item is not None:
            return CircleEditDialog(item.pen().width(), item.pen().color(), parent,
                                    item, fill=_item_is_filled(item) if allow_fill else None,
                                    fill_color=_item_fill_color(item))
        p = prefs.all('circle')
        return CircleEditDialog(p['width'], p['color'], parent,
                                fill=p['fill'] if allow_fill else None,
                                fill_color=_prefs_fill_color(prefs, 'circle'))
    if key == 'line':
        if item is not None:
            return LineEditDialog(item.pen().width(), item.pen().color(), parent, item)
        p = prefs.all('line')
        return LineEditDialog(p['width'], p['color'], parent)
    if key == 'arrow':
        if item is not None:
            return ArrowEditDialog(item.pen().width(), item.pen().color(),
                                   getattr(item, 'head_style', ArrowItem.HEAD_SINGLE),
                                   parent, target_item=item)
        p = prefs.all('arrow')
        return ArrowEditDialog(p['width'], p['color'], p['head'], parent)
    if key == 'freehand':
        if item is not None:
            return FreehandEditDialog(item.pen().width(), item.pen().color(), parent, item)
        p = prefs.all('freehand')
        return FreehandEditDialog(p['width'], p['color'], parent)
    if key == 'highlight':
        if item is not None:
            return HighlightEditDialog(item._color, parent, target_item=item)
        return HighlightEditDialog(prefs.get('highlight', 'color'), parent)
    if key == 'spotlight':
        if item is not None:
            return SpotlightEditDialog(item.blur_radius, parent, target_item=item,
                                       outline_on=item.outline_enabled,
                                       outline_width=item.outline_width,
                                       outline_color=item.outline_color,
                                       defer_draw=item.defer_draw)
        return SpotlightEditDialog(prefs.get('spotlight', 'blur'), parent,
                                   outline_on=prefs.get('spotlight', 'ol_on'),
                                   outline_width=prefs.get('spotlight', 'ol_w'),
                                   outline_color=prefs.get('spotlight', 'ol'),
                                   defer_draw=prefs.get('spotlight', 'defer'))
    if key == 'pixelate_blur':
        if item is not None:
            return PixelateBlurEditDialog(item.mode, item.pixel_size, item.blur_radius,
                                          parent, target_item=item)
        p = prefs.all('pixelate_blur')
        return PixelateBlurEditDialog(p['mode'], p['pixel'], p['blur'], parent)
    if key == 'bubble':
        if item is not None:
            return _BubbleEditDialog(item._fg_color, item._bg_color, item._text,
                                     parent, target_item=item)
        p = prefs.all('bubble')
        return _BubbleEditDialog(p['fg'], p['bg'], "", parent,
                                 title="Text Bubble — defaults", show_text=False)
    if key == 'marker':
        if item is not None:
            return _MarkerEditDialog(
                QColor(item._bg_color),
                QColor(getattr(item, '_text_color', QColor(Qt.GlobalColor.white))),
                parent, target_item=item, scale=item._scale)
        p = prefs.all('marker')
        return _MarkerEditDialog(p['bg'], p['fg'], parent, scale=p['scale'])
    if key == 'text':
        p = prefs.all('text')
        if item is not None:
            dlg = _TextInputDialog(item.defaultTextColor(), parent, show_text=True)
            dlg.setWindowTitle("Edit Text")
            dlg.edit.setPlainText(item.toPlainText())
            dlg.sz.setValue(item.font().pointSize())
            dlg.chk_hl.setChecked(getattr(item, 'highlight_enabled', True))
            dlg.hl_pad.setValue(getattr(item, 'highlight_padding', 0))
            dlg.chk_ol.setChecked(getattr(item, 'outline_enabled', False))
            dlg.ol_width.setValue(getattr(item, 'outline_width', 2))
            dlg.set_slot_color('hl', QColor(getattr(item, 'highlight_color', p['hl'])))
            dlg.set_slot_color('ol', QColor(getattr(item, 'outline_color', p['ol'])))
            dlg._update_tab_titles()
            return dlg
        dlg = _TextInputDialog(p['color'], parent, show_text=False, state=p)
        dlg.setWindowTitle("Text — defaults")
        return dlg
    return None


def _store_dialog_in_prefs(key, dlg, prefs, allow_fill=False):
    """Remember what the user chose as the tool's defaults ("last used")."""
    if key == 'rect':
        w, c, r = dlg.result_data()
        prefs.update('rect', width=int(w), color=c, radius=float(r))
        if allow_fill and dlg.result_fill() is not None:
            prefs.set('rect', 'fill', bool(dlg.result_fill()))
            prefs.set('rect', 'fill_color', dlg.result_fill_color())
    elif key == 'circle':
        w, c = dlg.result_data()
        prefs.update('circle', width=int(w), color=c)
        if allow_fill and dlg.result_fill() is not None:
            prefs.set('circle', 'fill', bool(dlg.result_fill()))
            prefs.set('circle', 'fill_color', dlg.result_fill_color())
    elif key in ('line', 'freehand'):
        w, c = dlg.result_data()
        prefs.update(key, width=int(w), color=c)
    elif key == 'arrow':
        w, c, h = dlg.result_data()
        prefs.update('arrow', width=int(w), color=c, head=h)
    elif key == 'highlight':
        prefs.set('highlight', 'color', dlg.result_color())
    elif key == 'spotlight':
        b, ol_on, ol_w, ol_c, dfr = dlg.result_data()
        prefs.update('spotlight', blur=int(b), ol_on=bool(ol_on), ol_w=int(ol_w), ol=ol_c,
                     defer=bool(dfr))
    elif key == 'pixelate_blur':
        m, px, br = dlg.result_data()
        prefs.update('pixelate_blur', mode=m, pixel=int(px), blur=int(br))
    elif key == 'bubble':
        _t, fg, bg = dlg.result_data()
        prefs.update('bubble', fg=fg, bg=bg)
    elif key == 'marker':
        bg, fg = dlg.result_data()
        prefs.update('marker', bg=bg, fg=fg, scale=dlg.result_scale())
    elif key == 'text':
        prefs._d['text'].update(dlg.state())


def _commit_item_dialog(key, dlg, item, allow_fill=False):
    """Apply an accepted dialog's values to *item* (call AFTER restore_original
    + push_undo so the undo snapshot is the pristine state)."""
    if key in ('rect', 'circle'):
        if key == 'rect':
            w, c, r = dlg.result_data()
        else:
            (w, c), r = dlg.result_data(), None
        pen = item.pen(); pen.setWidth(int(w)); pen.setColor(QColor(c)); item.setPen(pen)
        fill = dlg.result_fill() if allow_fill else None
        if fill is not None:
            fc = dlg.result_fill_color()
            item._fill_color = QColor(fc)       # remembered even while fill is off
            item.setBrush(QBrush(fc) if fill else QBrush(Qt.GlobalColor.transparent))
        elif _item_is_filled(item):
            item.setBrush(QBrush(QColor(c)))
        if r is not None:
            item.corner_radius = float(r)
        item.prepareGeometryChange(); item.update()
    elif key in ('line', 'freehand'):
        w, c = dlg.result_data()
        pen = item.pen(); pen.setWidth(int(w)); pen.setColor(QColor(c)); item.setPen(pen)
        if hasattr(item, 'prepareGeometryChange'):
            item.prepareGeometryChange()
        item.update()
    elif key == 'arrow':
        w, c, h = dlg.result_data()
        pen = item.pen(); pen.setWidth(int(w)); pen.setColor(QColor(c)); item.setPen(pen)
        item.head_style = h
        item.prepareGeometryChange(); item.update()
    elif key == 'spotlight':
        b, ol_on, ol_w, ol_c, dfr = dlg.result_data()
        item.prepareGeometryChange()
        item.defer_draw = bool(dfr)
        item.blur_radius = int(b)
        item.outline_enabled, item.outline_width, item.outline_color = bool(ol_on), int(ol_w), QColor(ol_c)
        item.update()
    elif key == 'pixelate_blur':
        m, px, br = dlg.result_data()
        item.mode, item.pixel_size, item.blur_radius = m, int(px), int(br)
        item.update()
    elif key == 'highlight':
        c = dlg.result_color()
        item._color = c
        item.setPen(QPen(Qt.PenStyle.NoPen))
        item.setBrush(QBrush(c))
        item.update()
    elif key == 'bubble':
        txt, fg, bg = dlg.result_data()
        item._text, item._fg_color, item._bg_color = txt, fg, bg
        if hasattr(item, '_auto_grow'):
            item._auto_grow()
        item.prepareGeometryChange(); item.update()
    elif key == 'marker':
        bg, fg = dlg.result_data()
        item._bg_color, item._text_color = bg, fg
        item.prepareGeometryChange()
        item._scale = dlg.result_scale()
        item.update()
    elif key == 'text':
        txt, fsz, col, hl, hl_on, hl_pad, ol_on, ol_w, ol_col = dlg.result_data()
        item.setPlainText(txt)
        item.setFont(QFont("Arial", fsz))
        item.setDefaultTextColor(col)
        item.highlight_color, item.highlight_enabled = hl, hl_on
        item.highlight_padding = hl_pad
        item.outline_enabled, item.outline_width, item.outline_color = ol_on, ol_w, ol_col
        item.update()


def _item_swatch_colors(key, item):
    """Colours to show on the toolbar swatch for a selected object."""
    if key in ('rect', 'circle') and _item_is_filled(item):
        return [item.pen().color(), _item_fill_color(item)]
    if key in ('rect', 'circle', 'line', 'arrow', 'freehand'):
        return [item.pen().color()]
    if key == 'highlight':
        return [item._color]
    if key == 'bubble':
        return [item._fg_color, item._bg_color]
    if key == 'marker':
        return [item._bg_color, getattr(item, '_text_color', QColor(Qt.GlobalColor.white))]
    if key == 'text':
        cols = [item.defaultTextColor()]
        if getattr(item, 'highlight_enabled', False):
            cols.append(getattr(item, 'highlight_color', QColor(255, 255, 0)))
        return cols
    return []


def _prefs_swatch_colors(key, prefs):
    """Colours to show on the toolbar swatch for the active tool (no selection)."""
    if key in ('rect', 'circle') and prefs.get(key, 'fill'):
        return [prefs.get(key, 'color'), _prefs_fill_color(prefs, key)]
    if key in ('rect', 'circle', 'line', 'arrow', 'freehand', 'highlight'):
        return [prefs.get(key, 'color')]
    if key == 'bubble':
        return [prefs.get('bubble', 'fg'), prefs.get('bubble', 'bg')]
    if key == 'marker':
        return [prefs.get('marker', 'bg'), prefs.get('marker', 'fg')]
    if key == 'text':
        cols = [prefs.get('text', 'color')]
        if prefs.get('text', 'hl_on'):
            cols.append(prefs.get('text', 'hl'))
        return cols
    return []


class HighlightRectItem(QGraphicsRectItem):
    """Semi-transparent yellow highlight rectangle — with rotation handle."""
    HIGHLIGHT_COLOR = QColor(255, 255, 0, 90)

    def __init__(self, rect=QRectF(), parent=None):
        super().__init__(rect, parent)
        self._color = QColor(255, 255, 0, 100)  # Semi-transparent yellow
        self.setPen(QPen(Qt.PenStyle.NoPen))

    def boundingRect(self):
        r = super().boundingRect()
        # Guard: if rect is empty/zero-size, return a minimal valid rect
        # to prevent Qt painter crash on zero-dimension geometry
        if r.width() < 1 or r.height() < 1:
            return QRectF(r.x() - 5, r.y() - 5, 10, 10)
        return r.adjusted(-5, -50, 5, 5)

    def paint(self, painter, option, widget=None):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Use fillRect instead of drawRect to safely support QRectF bounding boxes without crashing
        painter.fillRect(self.rect(), QBrush(self._color))
        # Draw dashed selection border + handles when selected
        if self.isSelected():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            sel_pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
            sel_pen.setDashPattern([4, 3])
            painter.setPen(sel_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect())

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = self.rect()
            # Corner & edge resize handles
            handles = [r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight(),
                       QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()),
                       QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y())]
            for hp in handles:
                painter.setBrush(QBrush(Qt.GlobalColor.white))
                painter.setPen(QPen(QColor(60, 120, 255), 1.5))
                painter.drawEllipse(hp, 5, 5)
            # Rotation handle above top edge
            rot_pt = QPointF(r.center().x(), r.top() - 30)
            painter.setBrush(QBrush(QColor(180, 255, 180, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(rot_pt, 6, 6)
            painter.setPen(QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(r.center().x(), r.top()), rot_pt)
        painter.restore()

    # Width handle support (same as ResizableRectItem)
    def rect(self):
        return super().rect()

    def setRect(self, r):
        super().setRect(r)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_edit_dialog(event)
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu()
        edit_act = menu.addAction("✏️ Edit")
        del_act  = menu.addAction("🗑️ Delete")
        action = menu.exec(event.screenPos())
        if action == edit_act:
            self._open_edit_dialog(event)
        elif action == del_act:
            if self.scene():
                self.scene().removeItem(self)
        event.accept()

    def _open_edit_dialog(self, event=None):
        host = _find_options_host(self)
        if host is not None:
            host._edit_item(self)
            return
        dlg = HighlightEditDialog(self._color, target_item=self)   # standalone fallback
        _set_dialog_on_top(dlg)
        if event:
            dlg.move(event.screenPos())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.restore_original()
            self._color = dlg.result_color()
            self.setPen(Qt.PenStyle.NoPen)
            self.setBrush(QBrush(self._color))
            self.update()


# ═════════════════════════════════════════════════════════════════════════════
#  Pixelate / Blur tool
#  ---------------------------------------------------------------------------
#  A rectangle that pixelates or blurs *whatever lies underneath it*:
#    • Capture Region overlay → the desktop snapshot taken when the overlay
#      opened (plus any annotations drawn below the rectangle),
#    • Image Editor           → the background image and every item below it.
#  The effect is computed live in paint(), so moving / resizing the rectangle,
#  or changing whatever is under it, always shows the correct result — and it
#  ends up in the saved image / screen grab exactly as it is shown.
# ═════════════════════════════════════════════════════════════════════════════
_pixblur_depth = [0]      # guards against runaway recursion between stacked effect items

_OVERLAY_DIM_ALPHA = 90   # darkening the Capture Region overlay lays over the desktop while annotating


def _overlay_dim_active(item) -> bool:
    """True while *item* sits on the Capture Region overlay and its dark dimming layer is
    visible. Annotations that reproduce the desktop (Smart Eraser, Pixelate/Blur, Spotlight)
    are computed from the UNdimmed snapshot, so on screen they must be dimmed too —
    otherwise they look lighter than their surroundings. (The dim is hidden before the real
    capture, so the saved image is unaffected.)"""
    sc = item.scene()
    if sc is None:
        return False
    for v in sc.views():
        ov = v.parent()
        if ov is not None and getattr(ov, '_desktop_snapshot', None) is not None:
            return not getattr(ov, '_hide_bg', True)
    return False


def _is_screen_painter(painter) -> bool:
    """On-screen painting (a widget viewport) as opposed to export / backdrop images."""
    return not isinstance(painter.device(), (QImage, QPixmap))


def _pixblur_effect_backdrop(item, scene_rect: QRectF):
    """Pixels lying UNDER *item* inside *scene_rect* (1 image px = 1 scene unit).

    Paints the host's base layer (desktop snapshot in the overlay; nothing in
    the editor, where the background image is an ordinary item) and then every
    item stacked BELOW *item*, so annotations above the effect stay crisp.
    Returns a QImage, or None when no base layer is available.
    """
    if _pixblur_depth[0] > 4:
        return None
    sc = item.scene()
    if sc is None or scene_rect.width() <= 0 or scene_rect.height() <= 0:
        return None
    from PySide6.QtWidgets import QStyleOptionGraphicsItem
    w = max(1, int(math.ceil(scene_rect.width())))
    h = max(1, int(math.ceil(scene_rect.height())))
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.scale(w / scene_rect.width(), h / scene_rect.height())
    p.translate(-scene_rect.topLeft())
    _pixblur_depth[0] += 1
    try:
        for view in sc.views():
            hook = getattr(view, 'paint_effect_base', None)
            if hook is not None:
                if not hook(p, scene_rect):
                    return None
                break
        for it in sc.items(Qt.SortOrder.AscendingOrder):      # back → front
            if it is item:
                break                                          # everything above stays untouched
            # NOTE: do NOT call it.parentItem() here. In PySide6 it makes shiboken
            # drop ownership of a top-level item that only the scene references,
            # so Python then DELETES it → every annotation (Smart Eraser, rectangles…)
            # silently vanished the first time a Pixelate/Spotlight/Smart-Eraser
            # backdrop was computed. topLevelItem() is side-effect free.
            if (not it.isVisible() or it.topLevelItem() is not it
                    or isinstance(it, CropOverlayItem)):
                continue
            if not it.sceneBoundingRect().intersects(scene_rect):
                continue
            opt = QStyleOptionGraphicsItem()
            opt.exposedRect = it.boundingRect()
            p.save()
            p.setOpacity(it.opacity())
            p.setTransform(it.sceneTransform(), True)
            it.paint(p, opt, None)
            p.restore()
    finally:
        _pixblur_depth[0] -= 1
        p.end()
    return img


def _pixblur_pixelate(img: QImage, block: int) -> QImage:
    """Mosaic: average every block×block cell, then blow it up with no smoothing."""
    w, h = img.width(), img.height()
    block = max(2, int(block))
    nx = max(1, int(round(w / block)))
    ny = max(1, int(round(h / block)))
    small = img.scaled(nx, ny, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.FastTransformation)


def _pixblur_blur(img: QImage, radius: int) -> QImage:
    """Gaussian blur (Pillow when available, a Qt down/up-scale fallback otherwise).
    The region is edge-extended first, so its borders don't fade out."""
    w, h = img.width(), img.height()
    f = 1.0
    if w * h > 2_500_000:                  # huge region: blur a reduced copy (keeps dragging smooth)
        f = math.sqrt(w * h / 2_500_000.0)
    if radius >= 8:                        # a wide Gaussian is smooth anyway → blur a reduced copy (~4px sigma)
        f = max(f, float(radius) / 4.0)
    work = img
    if f > 1.0:
        work = img.scaled(max(1, int(w / f)), max(1, int(h / f)),
                          Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    r = max(0.5, float(radius) / f)
    pad = int(math.ceil(r * 3)) + 1
    ww, wh = work.width(), work.height()

    padded = QImage(ww + 2 * pad, wh + 2 * pad, QImage.Format.Format_ARGB32_Premultiplied)
    padded.fill(Qt.GlobalColor.transparent)
    pp = QPainter(padded)
    pp.drawImage(pad, pad, work)
    pp.drawImage(QRect(pad, 0, ww, pad),             work, QRect(0, 0, ww, 1))
    pp.drawImage(QRect(pad, pad + wh, ww, pad),      work, QRect(0, wh - 1, ww, 1))
    pp.drawImage(QRect(0, pad, pad, wh),             work, QRect(0, 0, 1, wh))
    pp.drawImage(QRect(pad + ww, pad, pad, wh),      work, QRect(ww - 1, 0, 1, wh))
    for dx, dy, sx, sy in ((0, 0, 0, 0), (pad + ww, 0, ww - 1, 0),
                           (0, pad + wh, 0, wh - 1), (pad + ww, pad + wh, ww - 1, wh - 1)):
        pp.drawImage(QRect(dx, dy, pad, pad), work, QRect(sx, sy, 1, 1))
    pp.end()

    blurred = None
    if PIL_AVAILABLE:
        try:
            from PIL import ImageFilter
            rgba = padded.convertToFormat(QImage.Format.Format_RGBA8888)
            pil = Image.frombytes("RGBA", (rgba.width(), rgba.height()), bytes(rgba.constBits()))
            pil = pil.filter(ImageFilter.GaussianBlur(r))
            blurred = QImage(pil.tobytes(), pil.width, pil.height, pil.width * 4,
                             QImage.Format.Format_RGBA8888).copy()
        except Exception as ex:
            print(f"[PyshareX] blur (Pillow) failed, using Qt fallback: {ex}")
            blurred = None
    if blurred is None:
        s = max(1.0, r)
        small = padded.scaled(max(1, int(padded.width() / s)), max(1, int(padded.height() / s)),
                              Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        blurred = small.scaled(padded.width(), padded.height(),
                               Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
    out = blurred.copy(pad, pad, ww, wh)
    if f > 1.0:
        out = out.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return out


def _pixblur_draw_replacing(item, painter, target: QRectF, img: QImage, source: QRectF = None):
    """Draw an effect image so that it REPLACES what lies below instead of being laid on top.

    The effect is computed from the items underneath; with plain SourceOver, wherever the
    base is transparent (Image Editor canvas without a background picture) the effect
    comes out semi-transparent and the original sharp content shines through it — a
    "ghost" of the un-pixelated/un-blurred pixels. Alpha-capable targets (export /
    clipboard / backdrop images) therefore use CompositionMode_Source; the on-screen
    viewport (no alpha) first gets the view's own transparency background re-painted
    in the region (view hook ``paint_effect_clear``) and the effect is drawn over that.
    """
    painter.save()
    try:
        dev = painter.device()
        if isinstance(dev, (QImage, QPixmap)) and dev.hasAlphaChannel():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        else:
            sc = item.scene()
            hook = None
            for v in (sc.views() if sc is not None else ()):
                hook = getattr(v, 'paint_effect_clear', None)
                if hook is not None:
                    break
            if hook is not None:
                inv, ok = item.sceneTransform().inverted()
                if ok:
                    painter.save()
                    painter.setClipRect(target, Qt.ClipOperation.IntersectClip)
                    painter.setWorldTransform(inv * painter.worldTransform())
                    hook(painter, item.mapRectToScene(target))
                    painter.restore()
        if source is not None:
            painter.drawImage(target, img, source)
        else:
            painter.drawImage(target, img)
        if _is_screen_painter(painter) and _overlay_dim_active(item):
            painter.fillRect(target, QColor(0, 0, 0, _OVERLAY_DIM_ALPHA))
    finally:
        painter.restore()


class PixelateBlurItem(QGraphicsRectItem):
    """Rectangle that pixelates ('pixelate') or blurs ('blur') everything below it.

    ``pixel_size`` = mosaic block size in px, ``blur_radius`` = Gaussian radius in px
    (both are the tool's "intensity"). Not rotatable — the effect is always
    axis-aligned — but movable and resizable like the other rectangles.
    """
    MODE_PIXELATE = 'pixelate'
    MODE_BLUR = 'blur'

    def __init__(self, rect=QRectF(), parent=None):
        super().__init__(rect, parent)
        self.mode = self.MODE_PIXELATE
        self.pixel_size = 12
        self.blur_radius = 10
        self.setPen(QPen(Qt.PenStyle.NoPen))

    def boundingRect(self):
        r = super().boundingRect()
        if r.width() < 1 or r.height() < 1:          # zero-size guard (see HighlightRectItem)
            return QRectF(r.x() - 8, r.y() - 8, 16, 16)
        return r.adjusted(-8, -8, 8, 8)              # room for the selection handles

    def _effect_image(self):
        r = self.rect()
        if r.width() < 1 or r.height() < 1:
            return None
        base = _pixblur_effect_backdrop(self, self.mapRectToScene(r))
        if base is None:
            return None
        if self.mode == self.MODE_BLUR:
            return _pixblur_blur(base, self.blur_radius)
        return _pixblur_pixelate(base, self.pixel_size)

    def paint(self, painter, option, widget=None):
        r = self.rect()
        if r.width() >= 1 and r.height() >= 1:
            img = self._effect_image()
            painter.save()
            if img is not None:
                _pixblur_draw_replacing(self, painter, r, img)
            else:                                     # no backdrop available → neutral placeholder
                painter.fillRect(r, QColor(110, 110, 110, 200))
            painter.restore()
        self._paint_selection(painter, r)

    def _paint_selection(self, painter, r):
        """Selection frame + resize handles (shared with SpotlightItem)."""
        if self.isSelected() and _pixblur_depth[0] == 0:   # never bake the frame/handles into a backdrop
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 170), 1))
            painter.drawRect(r)
            sel_pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
            sel_pen.setDashPattern([4, 3])
            painter.setPen(sel_pen)
            painter.drawRect(r)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for hp in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight(),
                       QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()),
                       QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y())):
                painter.setBrush(QBrush(Qt.GlobalColor.white))
                painter.setPen(QPen(QColor(60, 120, 255), 1.5))
                painter.drawEllipse(hp, 5, 5)
            painter.restore()


def _clone_pixblur(item):
    """Detached copy of a PixelateBlurItem (used by every _clone_item)."""
    dup = type(item)()
    dup.setRect(QRectF(item.rect()))
    dup.mode = item.mode
    dup.pixel_size = int(item.pixel_size)
    dup.blur_radius = int(item.blur_radius)
    if hasattr(item, 'fill_color'):
        dup.fill_color = QColor(item.fill_color)
    if isinstance(item, SpotlightItem):
        dup.outline_enabled = bool(item.outline_enabled)
        dup.outline_width = int(item.outline_width)
        dup.outline_color = QColor(item.outline_color)
        dup.defer_draw = bool(item.defer_draw)
    dup.setFlags(item.flags())
    dup.setPos(item.pos())
    return dup


def _pixblur_add_mode_actions(menu, item):
    """Context-menu block:   Mode / ✓ Pixelate / Blur / ───.
    Returns (pixelate_action, blur_action); the active mode carries the check mark."""
    hdr = menu.addAction("Mode")
    hdr.setEnabled(False)
    act_pix = menu.addAction("Pixelate")
    act_pix.setCheckable(True)
    act_pix.setChecked(item.mode != PixelateBlurItem.MODE_BLUR)
    act_blur = menu.addAction("Blur")
    act_blur.setCheckable(True)
    act_blur.setChecked(item.mode == PixelateBlurItem.MODE_BLUR)
    menu.addSeparator()
    return act_pix, act_blur


def _pixblur_set_mode(item, mode, prefs=None):
    """Switch one item's mode (and remember it as the tool's default for new ones)."""
    item.mode = mode
    item.update()
    if prefs is not None:
        prefs.set('pixelate_blur', 'mode', mode)


class PixelateBlurEditDialog(_ToolOptionsDialog):
    """Pixelate / Blur: mode + one intensity slider for each mode."""
    SHOW_OPACITY = False

    def __init__(self, mode, pixel_size, blur_radius, parent=None, target_item=None):
        super().__init__("Pixelate / Blur", parent, target_item)
        self._orig = (mode, int(pixel_size), int(blur_radius))

        self.main_lay.addWidget(QLabel("Mode:"))
        mode_row = QHBoxLayout()
        self.rb_pixelate = QRadioButton("Pixelate")
        self.rb_blur = QRadioButton("Blur")
        (self.rb_blur if mode == PixelateBlurItem.MODE_BLUR else self.rb_pixelate).setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.rb_pixelate)
        self._mode_group.addButton(self.rb_blur)
        mode_row.addWidget(self.rb_pixelate)
        mode_row.addWidget(self.rb_blur)
        mode_row.addStretch(1)
        self.main_lay.addLayout(mode_row)

        _, self.spin_pixel = self._make_width_row("Pixelate intensity (block size):",
                                                  pixel_size, 2, 100)
        _, self.spin_blur = self._make_width_row("Blur intensity (radius):",
                                                 blur_radius, 1, 100)
        self.rb_pixelate.toggled.connect(self._apply_preview)
        self.finish()
        self._tabs.hide()            # no colours here → no (empty) tab strip

    def current_mode(self) -> str:
        return PixelateBlurItem.MODE_BLUR if self.rb_blur.isChecked() else PixelateBlurItem.MODE_PIXELATE

    def result_data(self):
        """(mode, pixelate block size px, blur radius px)"""
        return (self.current_mode(), self.spin_pixel.value(), self.spin_blur.value())

    def _apply_preview(self, *_):
        it = self._target_item
        if it is None:
            return
        it.mode, it.pixel_size, it.blur_radius = self.result_data()
        it.update()

    def restore_original(self):
        it = self._target_item
        if it is None:
            return
        it.mode, it.pixel_size, it.blur_radius = self._orig
        it.update()

    _restore_original = restore_original


# ═════════════════════════════════════════════════════════════════════════════
#  Spotlight tool
#  ---------------------------------------------------------------------------
#  The inverse of Pixelate / Blur: the rectangle itself stays sharp, while
#  EVERYTHING ELSE on the canvas (the whole scene rect) is blurred. Built on
#  PixelateBlurItem so that handles, resizing, cloning, undo/redo and deleting
#  all work exactly like they do for the Pixelate / Blur rectangle.
#  Annotations stacked above the spotlight stay crisp, those below get blurred.
# ═════════════════════════════════════════════════════════════════════════════
def _spotlight_area(sc) -> QRectF:
    """Area a Spotlight blurs: the background image in the Image Editor (whose scene rect
    is a huge 100000×100000 free-pan rect — far too big to render), else the scene rect
    (Capture Region overlay: the overlay itself)."""
    for v in sc.views():
        bg = getattr(v, 'bg_item', None)
        if bg is not None:
            return bg.sceneBoundingRect()
    return sc.sceneRect()


class SpotlightItem(PixelateBlurItem):
    """Sharp rectangle; everything outside it (inside the scene rect) is blurred.
    ``blur_radius`` is the intensity."""
    MODE_SPOTLIGHT = 'spotlight'

    def __init__(self, rect=QRectF(), parent=None):
        super().__init__(rect, parent)
        self.mode = self.MODE_SPOTLIGHT
        self.blur_radius = 2
        self.outline_enabled = False            # optional frame around the sharp rectangle
        self.outline_width = 3
        self.outline_color = QColor(255, 0, 0)
        self.defer_draw = True       # while dragging: only a white dashed frame, blur on release
        self._manip = False          # True while the rectangle is being drawn / moved / resized
        self._last_geom = None
        self._poll_on = False
        self._blur_cache = None      # (radius, backdrop QImage, blurred QImage)

    def _canvas_rect(self):
        """Whole scene rect expressed in this item's own coordinates."""
        sc = self.scene()
        if sc is None:
            return QRectF()
        return self.mapRectFromScene(_spotlight_area(sc))

    def boundingRect(self):
        r = self.rect()
        if r.width() < 1 or r.height() < 1:
            base = QRectF(r.x() - 8, r.y() - 8, 16, 16)
        else:
            m = 8 + (self.outline_width if self.outline_enabled else 0)
            base = r.adjusted(-m, -m, m, m)
        c = self._canvas_rect()
        return base.united(c) if not c.isNull() else base

    def shape(self):
        # Only the sharp rectangle is clickable — the blurred surroundings must
        # not swallow clicks meant for other items or for the Select tool.
        path = QPainterPath()
        path.addRect(self.rect())
        return path

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            self.prepareGeometryChange()      # bounding rect follows the scene rect
        return super().itemChange(change, value)

    def _blurred(self, base: QImage) -> QImage:
        """Blurred copy of *base*, reused while neither the content underneath nor the
        intensity changed. Moving / resizing the spotlight rectangle does not change what
        is blurred (only which part is cut out), so dragging costs no blur at all."""
        radius = int(self.blur_radius)
        c = self._blur_cache
        if c is not None and c[0] == radius and c[1].size() == base.size() and c[1] == base:
            return c[2]
        img = _pixblur_blur(base, radius)
        self._blur_cache = (radius, base, img)
        return img

    def _poll_release(self):
        """Runs while a drag is in progress; repaints with the real effect once the left
        mouse button is let go (nothing else would trigger a repaint on release)."""
        try:
            if self.scene() is None:      # preview copy already replaced / removed → stop polling
                self._poll_on = False
                return
            if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
                QTimer.singleShot(30, self._poll_release)
                return
            self._poll_on = False
            self._manip = False
            self.update()
        except RuntimeError:              # item already deleted
            self._poll_on = False

    def _dragging_now(self) -> bool:
        """True while this spotlight's geometry changes with the left button held down."""
        pos = self.pos()
        r = self.rect()
        geom = (r.x(), r.y(), r.width(), r.height(), pos.x(), pos.y())
        down = bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        # First paint with the button already held = the item is being drawn right now
        # (the Capture overlay builds a fresh preview item on every mouse move, so it
        # never has a previous geometry to compare with).
        if down and (self._last_geom is None or geom != self._last_geom):
            self._manip = True
        self._last_geom = geom
        if not down:
            self._manip = False
        if self._manip and not self._poll_on:
            self._poll_on = True
            QTimer.singleShot(30, self._poll_release)
        return self._manip

    def paint(self, painter, option, widget=None):
        r = self.rect()
        sc = self.scene()
        if (self.defer_draw and _pixblur_depth[0] == 0 and _is_screen_painter(painter)
                and self._dragging_now()):
            # fast path: no backdrop render, no blur — just the frame
            if r.width() >= 1 and r.height() >= 1 and not self.isSelected():
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(0, 0, 0, 170), 1))
                painter.drawRect(r)
                dash = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
                dash.setDashPattern([4, 3])
                painter.setPen(dash)
                painter.drawRect(r)
                painter.restore()
            self._paint_selection(painter, r)
            return
        if sc is not None and r.width() >= 1 and r.height() >= 1:
            scene_rect = _spotlight_area(sc)
            c = self.mapRectFromScene(scene_rect)
            # Only the part Qt actually asks for needs drawing (a drag repaints small areas)
            exp = option.exposedRect.intersected(c) if option is not None else c
            if not exp.isEmpty() and not (exp.width() <= r.width() and exp.height() <= r.height()
                                          and r.contains(exp)):      # fully inside the sharp rect → nothing to blur
                _t0 = time.monotonic()
                base = _pixblur_effect_backdrop(self, scene_rect)
                if base is not None:
                    img = self._blurred(base)
                    _dt = time.monotonic() - _t0
                    if _dt > 0.3:
                        print(f"[PyshareX] Spotlight: przeliczenie rozmycia {base.width()}x{base.height()} "
                              f"zajęło {_dt:.2f}s (po undo/redo liczone od nowa)", flush=True)
                    sx = img.width() / max(c.width(), 1e-6)
                    sy = img.height() / max(c.height(), 1e-6)
                    src = QRectF((exp.x() - c.x()) * sx, (exp.y() - c.y()) * sy,
                                 exp.width() * sx, exp.height() * sy)
                    outside = QPainterPath()
                    outside.setFillRule(Qt.FillRule.OddEvenFill)
                    outside.addRect(c)
                    outside.addRect(r)
                    painter.save()
                    painter.setClipPath(outside, Qt.ClipOperation.IntersectClip)
                    _pixblur_draw_replacing(self, painter, exp, img, src)
                    painter.restore()
        if self.outline_enabled and r.width() >= 1 and r.height() >= 1:
            # frame sits just OUTSIDE the sharp area, so the spotlight content stays uncovered
            w = float(max(1, int(self.outline_width)))
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(QColor(self.outline_color), w)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.drawRect(r.adjusted(-w / 2, -w / 2, w / 2, w / 2))
            painter.restore()
        self._paint_selection(painter, r)


class SpotlightEditDialog(_ToolOptionsDialog):
    """Spotlight options: blur intensity of everything outside the selection,
    plus an optional outline (on/off, width, colour) around the sharp area."""
    SHOW_OPACITY = True

    def __init__(self, blur_radius, parent=None, target_item=None,
                 outline_on=False, outline_width=3, outline_color=None, defer_draw=True):
        super().__init__("Spotlight", parent, target_item)
        self._orig = (int(blur_radius), bool(outline_on), int(outline_width),
                      QColor(outline_color) if outline_color is not None else QColor(255, 0, 0),
                      bool(defer_draw))
        self.main_lay.addWidget(QLabel("Everything outside the selected area is blurred."))
        _, self.spin_blur = self._make_width_row("Blur intensity (radius):",
                                                 blur_radius, 1, 100)
        self.chk_defer = QCheckBox("Fast drawing: blur only after releasing the mouse "
                                   "(white dashed frame while dragging)")
        self.chk_defer.setChecked(bool(defer_draw))
        self.chk_defer.toggled.connect(self._apply_preview)
        self.main_lay.addWidget(self.chk_defer)
        self.chk_outline = QCheckBox("Outline around the selected area")
        self.chk_outline.setChecked(bool(outline_on))
        self.chk_outline.toggled.connect(self._apply_preview)
        self.main_lay.addWidget(self.chk_outline)
        _, self.spin_ol = self._make_width_row("Outline width:", outline_width, 1, 50)
        self.add_color_slot('ol', "Outline color", self._orig[3], alpha=True)
        self.finish()

    def result_data(self):
        """(blur radius px, outline on, outline width px, outline colour, deferred drawing)"""
        return (self.spin_blur.value(), self.chk_outline.isChecked(),
                self.spin_ol.value(), self.slot_color('ol'), self.chk_defer.isChecked())

    def _push(self, it, vals):
        b, on, w, c, dfr = vals
        it.prepareGeometryChange()
        it.defer_draw = bool(dfr)
        it.blur_radius = int(b)
        it.outline_enabled, it.outline_width, it.outline_color = bool(on), int(w), QColor(c)
        it.update()

    def _apply_preview(self, *_):
        it = self._target_item
        if it is None or not self._finished:
            return
        self._push(it, self.result_data())

    def restore_original(self):
        it = self._target_item
        if it is None:
            return
        self._push(it, self._orig)

    _restore_original = restore_original


# ═════════════════════════════════════════════════════════════════════════════
#  Smart Eraser tool
#  ---------------------------------------------------------------------------
#  Click = sample the colour of the pixel under the cursor (as currently shown,
#  annotations included); drag = a rectangle filled with exactly that colour,
#  drawn without antialiasing / outline so it blends seamlessly into flat areas.
#  Built on PixelateBlurItem → same handles, resizing, cloning, undo and delete.
# ═════════════════════════════════════════════════════════════════════════════
class SmartEraserItem(PixelateBlurItem):
    """Rectangle filled with one sampled colour (``fill_color``). No options."""
    MODE_SMART_ERASER = 'smart_eraser'

    def __init__(self, rect=QRectF(), parent=None):
        super().__init__(rect, parent)
        self.mode = self.MODE_SMART_ERASER
        self.fill_color = QColor(128, 128, 128)

    def paint(self, painter, option, widget=None):
        r = self.rect()
        if r.width() >= 1 and r.height() >= 1:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.fillRect(r.toAlignedRect(), self.fill_color)
            if _is_screen_painter(painter) and _overlay_dim_active(self):
                painter.fillRect(r.toAlignedRect(), QColor(0, 0, 0, _OVERLAY_DIM_ALPHA))
            painter.restore()
        self._paint_selection(painter, r)


_SMART_ERASER_SAMPLE_RADIUS = 3      # sampling window = (2*r+1)² px around the click


def _smart_eraser_sample(scene, pt: QPointF) -> QColor:
    """Background colour at scene point *pt* as currently displayed (host base layer +
    every item in the scene).

    A single pixel is a poor sample: anti-aliased text edges, JPEG noise or a soft
    gradient make it differ from the surrounding flat area, so the patch stood out.
    Instead a small window around the click is read and the per-channel MEDIAN is used —
    stray text/edge pixels are ignored and the dominant background colour wins."""
    rad = _SMART_ERASER_SAMPLE_RADIUS
    fx, fy = math.floor(pt.x()), math.floor(pt.y())
    n = 2 * rad + 1
    probe = SmartEraserItem()
    probe.setRect(QRectF(pt.x(), pt.y(), 1, 1))
    scene.addItem(probe)                    # topmost → everything else is "below" it
    try:
        img = _pixblur_effect_backdrop(probe, QRectF(fx - rad, fy - rad, n, n))
    finally:
        scene.removeItem(probe)
    if img is None or img.isNull():
        return QColor(128, 128, 128)
    rs, gs, bs = [], [], []
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)          # un-premultiplied
            if c.alpha() < 200:               # outside the image / transparent → ignore
                continue
            rs.append(c.red()); gs.append(c.green()); bs.append(c.blue())
    if not rs:
        return QColor(128, 128, 128)
    mid = len(rs) // 2
    return QColor(sorted(rs)[mid], sorted(gs)[mid], sorted(bs)[mid])


class ArrowItem(QGraphicsLineItem):
    """Line (optionally curved) with an arrowhead at one or both ends, or no head at all.

    Handles when selected:
      - p1 / p2:  white-fill / blue-border circles at the two endpoints.
      - bend:     yellow-fill / dark-border circle at the curve's control point
                  (starts at the midpoint) — drag it to bend the arrow into a curve,
                  the same way a curve's break/control point works.
      - width:    black-fill / white-outline circle sitting between p1 and the
                  center of the arrow — drag it vertically to change stroke width.
    """
    ARROW_BASE_SIZE = 14  # base arrow size at pen width=1
    # Extra distance (local units) the shaft is drawn past the head's exact
    # geometric base, so the tail tucks a little way *under* the head
    # instead of stopping right at its edge. The head is painted on top of
    # the shaft, so this overlap is invisible — it just guarantees the seam
    # is always fully covered, whatever angle the head ends up at.
    SHAFT_HEAD_OVERLAP = 5.0
    HEAD_SINGLE   = 'single'          # head at p2 (end) only
    HEAD_SINGLE_START = 'single_start'  # head at p1 (start) only — the other side
    HEAD_DOUBLE   = 'double'          # head at both ends
    HEAD_NONE     = 'none'

    def __init__(self, line, canvas=None):
        super().__init__(line)
        self.canvas = canvas
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.active_handle = None
        self.head_style = self.HEAD_SINGLE
        # Offset of the curve's control point relative to the p1-p2 midpoint,
        # in item-local coordinates. (0, 0) == perfectly straight line.
        self._bend = QPointF(0.0, 0.0)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def _control_point(self):
        """The bend handle's position — this is a true break/kink point:
        the curve is built so it actually passes through this point (see
        _bezier_control_point), not just gets pulled toward it."""
        p1, p2 = self.line().p1(), self.line().p2()
        mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
        bend = getattr(self, '_bend', QPointF(0.0, 0.0))
        return QPointF(mid.x() + bend.x(), mid.y() + bend.y())

    def _bezier_control_point(self):
        """The actual quadratic-bezier control point used to draw the curve.

        A quadratic bezier does NOT pass through its control point — at
        t=0.5 it only reaches halfway there (0.25*p1 + 0.5*C + 0.25*p2).
        That mismatch is what made the yellow bend handle look 'detached'
        from the drawn curve, especially on a big bend. To make the curve
        pass exactly through the handle position Q, mirror Q through the
        p1-p2 midpoint: C = 2*Q - mid.
        """
        p1, p2 = self.line().p1(), self.line().p2()
        mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
        q = self._control_point()
        return QPointF(2 * q.x() - mid.x(), 2 * q.y() - mid.y())

    def _build_path(self):
        """Quadratic bezier path from p1 to p2 that passes through the
        bend/break point at its midpoint."""
        p1, p2 = self.line().p1(), self.line().p2()
        path = QPainterPath(p1)
        path.quadTo(self._bezier_control_point(), p2)
        return path

    def _width_handle_pos(self):
        """Position of the thickness handle: 1/4 of the way along the curve
        from p1 toward the center."""
        path = self._build_path()
        if path.length() <= 0:
            return self.line().p1()
        return path.pointAtPercent(0.25)

    def boundingRect(self):
        extra = (self.pen().width() + self._arrow_size() + 30) / (self.canvas.transform().m11() if self.canvas else 1)
        r = super().boundingRect().adjusted(-extra, -extra, extra, extra)
        # Include both the handle's own position and the real (further-out)
        # bezier control point, since the curve is bounded by the convex
        # hull of p1 / bezier-control / p2, not by the handle position alone.
        for pt in (self._control_point(), self._bezier_control_point()):
            r = r.united(QRectF(pt.x() - extra, pt.y() - extra, extra * 2, extra * 2))
        return r

    def _arrow_size(self):
        """Arrow head size scales with pen width."""
        w = max(1, self.pen().width())
        return self.ARROW_BASE_SIZE + (w - 1) * 3

    def _head_length(self):
        """Distance from the arrowhead's tip back to its flat base — shared
        by the triangle geometry and by the shaft-cut clip below, so the two
        always agree on exactly where the head's base sits."""
        return self._arrow_size() * 1.15

    def _head_triangle(self, tip, fwd):
        """Return the two base points of an arrowhead triangle whose point is
        at *tip*, facing along unit vector *fwd* (pointing from the head's
        back toward the tip).

        *fwd* is deliberately the straight chord from the shaft's cut point
        to the tip — NOT the curve's own tangent at the cut point. Tangent-
        based orientation was tried and reverted: on a sharp bend the curve's
        tangent at the cut point can point tens of degrees away from where
        the tip actually is, which swings the whole triangle off to one
        side — the head then folds back over the shaft in a self-
        intersecting sliver that looks like it's been twisted out of the
        page (in 3D) rather than just rotated flat. The chord always points
        the triangle straight at the tip, so it can never fold like that,
        on any bend. The (much smaller) seam gap that a chord-oriented head
        can leave against the shaft's flat-capped edge is closed separately
        by drawing the shaft a little way past this triangle's base, tucked
        underneath it (see SHAFT_HEAD_OVERLAP / _shaft_trim_percents)
        instead of trying to make the two edges perfectly coincide.

        Length and half-width are set independently (instead of deriving
        both from one sweep angle) so the head comes out as a blocky,
        flat-backed triangle — length roughly equal to width — matching a
        classic arrow-icon silhouette rather than a thin, wide sliver."""
        sz = self._arrow_size()
        length = self._head_length()
        half_width = sz * 0.62
        back = QPointF(tip.x() - length * fwd.x(), tip.y() - length * fwd.y())
        perp = QPointF(-fwd.y(), fwd.x())
        p_left  = QPointF(back.x() + half_width * perp.x(), back.y() + half_width * perp.y())
        p_right = QPointF(back.x() - half_width * perp.x(), back.y() - half_width * perp.y())
        return p_left, p_right

    @staticmethod
    def _unit_vector(src, dst):
        """Unit vector pointing from *src* to *dst*, or None if they coincide."""
        dx, dy = dst.x() - src.x(), dst.y() - src.y()
        d = math.hypot(dx, dy)
        if d <= 1e-6:
            return None
        return QPointF(dx / d, dy / d)

    @staticmethod
    def _angle_vector(angle_rad):
        """Unit vector for an angle in standard math convention (radians)."""
        return QPointF(math.cos(angle_rad), -math.sin(angle_rad))

    def _shaft_endpoints(self, path):
        """Compute (t_start, t_end) — the arc-length percentAtLength cut
        points shared by both the shaft (_shaft_paint_path) and the
        arrowhead geometry (paint), so the two always meet at the exact
        same on-curve point instead of being computed independently by
        two different methods (arc-length walk vs. straight projection)."""
        total_len = path.length()
        if total_len <= 0:
            return 0.0, 1.0
        head_style = getattr(self, 'head_style', self.HEAD_SINGLE)
        length = self._head_length()
        t_start, t_end = 0.0, 1.0
        if head_style in (self.HEAD_SINGLE_START, self.HEAD_DOUBLE) and total_len > length:
            t_start = path.percentAtLength(length)
        if head_style in (self.HEAD_SINGLE, self.HEAD_DOUBLE) and total_len > length:
            t_end = path.percentAtLength(total_len - length)
        return t_start, t_end

    def _shaft_trim_percents(self, path):
        """Like _shaft_endpoints, but the shaft is trimmed
        SHAFT_HEAD_OVERLAP units short of the head's exact base instead of
        flush with it — i.e. the shaft is drawn a little way *into* the
        head's footprint. The head is always painted on top afterwards, so
        that overlap is invisible; what it buys is a seam that's always
        fully covered no matter how the head's base happens to be angled,
        instead of depending on the shaft's edge and the head's edge
        landing on exactly the same line."""
        total_len = path.length()
        if total_len <= 0:
            return 0.0, 1.0
        head_style = getattr(self, 'head_style', self.HEAD_SINGLE)
        length = max(0.0, self._head_length() - self.SHAFT_HEAD_OVERLAP)
        t_start, t_end = 0.0, 1.0
        if head_style in (self.HEAD_SINGLE_START, self.HEAD_DOUBLE) and total_len > length:
            t_start = path.percentAtLength(length)
        if head_style in (self.HEAD_SINGLE, self.HEAD_DOUBLE) and total_len > length:
            t_end = path.percentAtLength(total_len - length)
        return t_start, t_end

    def _shaft_paint_path(self, path):
        """The actual path used to stroke the shaft: the same curve as
        *path*, but shortened by the arrowhead's length (minus a small
        overlap — see _shaft_trim_percents) at whichever end(s) have a
        head — so the shaft simply never reaches past the head's zone,
        rather than being drawn there and then patched up.

        First attempt at this used a straight guillotine clip (a flat cut
        line positioned via the tip's tangent direction). That works for
        straight or gently-bent arrows, but falls apart once the bend
        gets sharp: the cut line is derived from the tangent *at the
        tip*, projected backward in a straight line, while the curve
        itself may hook back through a completely different area of the
        canvas over that same stretch. On a tight hook, the straight cut
        can end up slicing through the wrong part of the curve entirely
        — chopping out a visible chunk of shaft instead of just the bit
        behind the head — which is exactly the "head looks torn away
        from the line" glitch this caused.

        Fix: don't approximate with a straight line at all — walk the
        correct arc-length distance back *along the curve itself* using
        Qt's own arc-length parametrization (percentAtLength +
        pointAtPercent, which are defined consistently with each other
        regardless of how sharply the path bends), then rebuild that
        trimmed span as a sampled polyline. This follows the actual
        curve shape no matter how tightly it's hooked."""
        total_len = path.length()
        if total_len <= 0:
            return None

        t_start, t_end = self._shaft_trim_percents(path)
        if t_end <= t_start:
            # The arrow is shorter than its head(s) — no shaft left to draw
            # (the head triangle itself is drawn separately regardless).
            return None

        # Sample the trimmed span as a polyline. Step count scales with
        # the fraction of the curve being drawn, so a full-length arrow
        # still gets a smooth curve while a mostly-clipped sliver doesn't
        # waste samples.
        steps = max(2, int(24 * (t_end - t_start)) + 2)
        shaft = QPainterPath()
        for i in range(steps + 1):
            t = t_start + (t_end - t_start) * (i / steps)
            pt = path.pointAtPercent(t)
            if i == 0:
                shaft.moveTo(pt)
            else:
                shaft.lineTo(pt)
        return shaft

    def paint(self, painter, option, widget=None):
        path = self._build_path()

        # Draw the shaft (straight or curved), trimmed short of any head
        # end (see _shaft_paint_path) so it never has to be patched up
        # after the fact. IMPORTANT: force a flat cap — Qt's default pen
        # cap is SquareCap, which extends the stroke by half the pen
        # width *past* whichever endpoint it's drawn to; FlatCap ends the
        # stroke exactly there instead.
        shaft_pen = QPen(self.pen())
        shaft_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(shaft_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        shaft_path = self._shaft_paint_path(path)
        if shaft_path is not None:
            painter.drawPath(shaft_path)

        # Draw arrowhead(s) on top, per the selected head style
        head_style = getattr(self, 'head_style', self.HEAD_SINGLE)
        draw_head_p2 = head_style in (self.HEAD_SINGLE, self.HEAD_DOUBLE)
        draw_head_p1 = head_style in (self.HEAD_SINGLE_START, self.HEAD_DOUBLE)
        if (draw_head_p2 or draw_head_p1) and path.length() > 0:
            painter.setBrush(QBrush(self.pen().color()))
            painter.setPen(Qt.PenStyle.NoPen)

            # Same arc-length cut points used to trim the shaft (see
            # _shaft_endpoints). The head's direction is derived from the
            # vector *cut-point -> tip*, not from the curve's own tangent
            # at the tip, so the head always points exactly at the spot
            # where the visible shaft ends, even on a sharp bend — see
            # _head_triangle for why the chord (not the tangent) is what
            # keeps the head flat/2D instead of folding on tight bends.
            t_start, t_end = self._shaft_endpoints(path)
            has_length_for_head = path.length() > self._head_length()

            if draw_head_p2:
                # Head at p2 — faces from the shaft's cut point toward p2
                tip2 = self.line().p2()
                fwd2 = None
                if has_length_for_head:
                    cut2 = path.pointAtPercent(t_end)
                    fwd2 = self._unit_vector(cut2, tip2)
                if fwd2 is None:
                    fwd2 = self._angle_vector(math.radians(path.angleAtPercent(1.0)))
                pl, pr = self._head_triangle(tip2, fwd2)
                head_path = QPainterPath()
                head_path.moveTo(tip2); head_path.lineTo(pl); head_path.lineTo(pr)
                head_path.closeSubpath()
                painter.drawPath(head_path)

            if draw_head_p1:
                # Head at p1 — faces from the shaft's cut point toward p1
                tip1 = self.line().p1()
                fwd1 = None
                if has_length_for_head:
                    cut1 = path.pointAtPercent(t_start)
                    fwd1 = self._unit_vector(cut1, tip1)
                if fwd1 is None:
                    fwd1 = self._angle_vector(math.radians(path.angleAtPercent(0.0)) + math.pi)
                pl, pr = self._head_triangle(tip1, fwd1)
                head_path = QPainterPath()
                head_path.moveTo(tip1); head_path.lineTo(pl); head_path.lineTo(pr)
                head_path.closeSubpath()
                painter.drawPath(head_path)

        if self.isSelected():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # Handle size: 16px screen-space, scale-independent
            s = 16 / (self.canvas.transform().m11() if self.canvas else 1)
            r = s / 2
            # p1 endpoint handle — white fill, blue border
            painter.setBrush(QBrush(Qt.GlobalColor.white))
            painter.setPen(QPen(Qt.GlobalColor.blue, 2.0))
            painter.drawEllipse(self.line().p1(), r, r)
            # p2 endpoint handle — white fill, blue border
            painter.drawEllipse(self.line().p2(), r, r)
            # Bend/curve control handle — black fill, white outline
            painter.setBrush(QBrush(Qt.GlobalColor.black))
            painter.setPen(QPen(Qt.GlobalColor.white, 2.0))
            painter.drawEllipse(self._control_point(), r, r)
            # Width (thickness) handle — yellow fill, dark border (matches
            # the width-handle color used by every other shape)
            painter.setBrush(QBrush(QColor(255, 220, 50, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 2.0))
            painter.drawEllipse(self._width_handle_pos(), r, r)

    def _handle_hit_radius(self):
        """Hit-test radius for endpoint/width handles, in item-local coords.
        Fixed at 18px screen-space so handles are easy to grab regardless of zoom."""
        return 18 / (self.canvas.transform().m11() if getattr(self, 'canvas', None) else 1)

    def shape(self):
        """Override shape() so Qt's scene hit-testing covers the full handle surfaces,
        not just the thin line geometry. Includes a fat stroke along the curve body
        plus circular regions at p1, p2, the bend control point, and the width handle."""
        r = self._handle_hit_radius()
        path = self._build_path()
        p1, p2 = self.line().p1(), self.line().p2()
        ctrl = self._control_point()
        whandle = self._width_handle_pos()

        # Fat stroke along the curve body
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self.pen().width() + 4, r * 2))
        result = stroker.createStroke(path)

        # Add circular hit zones for each handle
        for pt in (p1, p2, ctrl, whandle):
            circle = QPainterPath()
            circle.addEllipse(pt, r, r)
            result = result.united(circle)

        return result

    def mousePressEvent(self, event):
        p = event.pos()
        p1, p2 = self.line().p1(), self.line().p2()
        ctrl = self._control_point()
        whandle = self._width_handle_pos()
        r = self._handle_hit_radius()

        # Pick the closest handle within the hit radius (checked in priority
        # order: endpoints first, then the width handle, then the bend point,
        # since the width handle sits close to p1).
        candidates = [
            ('p1', p1),
            ('p2', p2),
            ('width', whandle),
            ('bend', ctrl),
        ]
        best_name, best_d = None, r
        for name, pt in candidates:
            d = math.hypot(p.x() - pt.x(), p.y() - pt.y())
            if d <= best_d:
                best_name, best_d = name, d

        if best_name:
            self.active_handle = best_name
            if best_name == 'width':
                self._width_drag_start_pos = p
                self._width_drag_start_w = self.pen().width()
            event.accept()
        else:
            self.active_handle = None
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        handle = getattr(self, 'active_handle', None)
        if handle in ('p1', 'p2'):
            # Shift held: move the whole arrow (as if dragging it with the mouse)
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                d = event.scenePos() - event.lastScenePos()
                self.setPos(self.pos() + d)
                event.accept()
                return
            self.prepareGeometryChange()
            line = self.line()
            new_pos = event.pos()

            # 45-degree angle snapping constraint
            alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            mid = QPointF((line.p1().x() + line.p2().x()) / 2,
                          (line.p1().y() + line.p2().y()) / 2)
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                # Alt: the midpoint is the anchor (line is resized from its center)
                anchor = mid if alt else (line.p2() if handle == 'p1' else line.p1())
                dx, dy = new_pos.x() - anchor.x(), new_pos.y() - anchor.y()
                snapped_angle = round(math.degrees(math.atan2(dy, dx)) / 45) * 45
                d = math.hypot(dx, dy)
                new_pos = QPointF(anchor.x() + d * math.cos(math.radians(snapped_angle)),
                                  anchor.y() + d * math.sin(math.radians(snapped_angle)))

            mirrored = QPointF(2 * mid.x() - new_pos.x(), 2 * mid.y() - new_pos.y())
            if handle == 'p1':
                line.setP1(new_pos)
                if alt:
                    line.setP2(mirrored)
            else:
                line.setP2(new_pos)
                if alt:
                    line.setP1(mirrored)

            self.setLine(line)
            event.accept()

        elif handle == 'bend':
            self.prepareGeometryChange()
            p1, p2 = self.line().p1(), self.line().p2()
            mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
            new_pos = event.pos()
            self._bend = QPointF(new_pos.x() - mid.x(), new_pos.y() - mid.y())
            self.update()
            event.accept()

        elif handle == 'width':
            line = self.line()
            if line.length() > 0:
                start_pos = getattr(self, '_width_drag_start_pos', event.pos())
                start_w = getattr(self, '_width_drag_start_w', self.pen().width())
                dy = event.pos().y() - start_pos.y()
                new_w = max(1, int(start_w + dy * 0.3))

                pen = self.pen()
                pen.setWidth(new_w)
                self.setPen(pen)
                self.prepareGeometryChange()
                self.update()

                if hasattr(self, 'canvas') and self.canvas:
                    win = self.canvas.window() if hasattr(self.canvas, 'window') else None
                    if win and hasattr(win, 'spin'):
                        win.spin.blockSignals(True)
                        win.spin.setValue(new_w)
                        win.spin.blockSignals(False)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # Always fully release the state flag on mouse up
        if getattr(self, 'active_handle', None):
            self.active_handle = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class TextBubbleItem(QGraphicsItem):
    """Square text bubble with a draggable cone (spike) handle.
    Cone offset is stored relative to the box centre so it scales
    automatically when the box is resized — same logic as _MarkerItem spike.
    Double-click or right-click → Edit to change text and colors.
    """
    

    # ── Class-level constants ──────────────────────────────────────────
    MIN_SIZE  = 40     # minimum box width / height in pixels
    PADDING   = 10     # inner padding between box edge and text
    HANDLE_R  = 7      # radius of the cone-tip and resize handles

    def __init__(self, text: str, fg_color: QColor = None, bg_color: QColor = None):
        super().__init__()
        self._text     = text
        self._fg_color = fg_color or QColor(Qt.GlobalColor.black)
        self._bg_color = bg_color or QColor(230, 230, 230, 240)
        self._w = 140
        self._h = 70
       
        # Cone offset stored relative to box centre (like _MarkerItem._spike_offset
        # relative to circle centre). Default: cone tip points to bottom-right,
        # placed just outside the box at (w*0.9, h*1.4) from centre.
        self._cone_rel = QPointF(self._w * 0.9, self._h * 1.4)
        self._drag_cone = False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)

    # ------------------------------------------------------------------
    # Helpers — cone tip in local (item) coordinates
    # ------------------------------------------------------------------
    def _cone_tip_local(self) -> QPointF:
        """Cone tip in local coords: centre + relative offset."""
        return QPointF(self._w / 2 + self._cone_rel.x(),
                       self._h / 2 + self._cone_rel.y())

    # ------------------------------------------------------------------
    # rect / setRect — for resize machinery compatibility
    # ------------------------------------------------------------------
    def rect(self) -> QRectF:
        return QRectF(0, 0, self._w, self._h)

    def setRect(self, r: QRectF):
        """Resize box. Cone offset is relative to centre so it scales
        automatically with the box — no manual adjustment needed."""
        self.prepareGeometryChange()
        self._w = max(self.MIN_SIZE, r.width())
        self._h = max(self.MIN_SIZE, r.height())
        self._auto_grow()
        self.update()

    def _auto_grow(self):
        """Expand the box height only if text cannot fit at the minimum font
        size — so we never shrink a manually-resized box."""
        if not self._text:
            return
        pad = self.PADDING * 2
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(QFont("Arial", self.MIN_SIZE // 5 or 8))
        text_w = max(1, int(self._w) - pad)
        needed_h = fm.boundingRect(
            0, 0, text_w, 0,
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            self._text
        ).height() + pad
        if needed_h > self._h:
            self.prepareGeometryChange()
            self._h = needed_h

    def boundingRect(self) -> QRectF:
        tip = self._cone_tip_local()
        hr  = self.HANDLE_R + 5
        left   = min(0.0, tip.x()) - hr
        top    = min(0.0, tip.y()) - hr - 50   # room for rotate handle above
        right  = max(self._w, tip.x()) + hr
        bottom = max(self._h, tip.y()) + hr
        return QRectF(left, top, right - left, bottom - top)

    # ------------------------------------------------------------------
    # Cone geometry — mirrors _MarkerItem._spike_path() logic
    # ------------------------------------------------------------------
    def _cone_path(self) -> QPainterPath:
        tip = self._cone_tip_local()
        cx, cy = self._w / 2, self._h / 2
        dx, dy = tip.x() - cx, tip.y() - cy
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        # Base half-width scales with box size (like _MarkerItem hw = r*0.45)
        hw = min(self._w, self._h) * 0.12
        b1 = QPointF(cx + nx * hw, cy + ny * hw)
        b2 = QPointF(cx - nx * hw, cy - ny * hw)
        path = QPainterPath()
        path.moveTo(b1)
        path.quadTo(QPointF(tip.x() * 0.6 + cx * 0.4 + nx * hw * 0.3,
                            tip.y() * 0.6 + cy * 0.4 + ny * hw * 0.3), tip)
        path.quadTo(QPointF(tip.x() * 0.6 + cx * 0.4 - nx * hw * 0.3,
                            tip.y() * 0.6 + cy * 0.4 - ny * hw * 0.3), b2)
        path.lineTo(b1)
        return path

    def _over_cone_handle(self, local_pos: QPointF) -> bool:
        tip = self._cone_tip_local()
        return math.hypot(local_pos.x() - tip.x(),
                          local_pos.y() - tip.y()) <= self.HANDLE_R + 6

    # ------------------------------------------------------------------
    def paint(self, painter, option, widget=None):
        from PySide6.QtWidgets import QStyle
        from PySide6.QtGui import QFontMetrics
        option.state &= ~QStyle.StateFlag.State_Selected
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = QColor(self._bg_color)
        bg.setAlpha(255)  # force fully opaque background

        # Cone (behind box) — draw first so box covers the base
        painter.setBrush(QBrush(bg))
        painter.setPen(Qt.PenStyle.NoPen)  # No border on cone
        painter.drawPath(self._cone_path())

        # Box — opaque fill covers the cone base cleanly
        box = QRectF(0, 0, self._w, self._h)
        painter.setBrush(QBrush(bg))
        painter.setPen(Qt.PenStyle.NoPen)  # No border on box
        painter.drawRoundedRect(box, 6, 6)

        # Text — binary-search the largest font that fits the box both in
        # width and height, then draw centred so it fills the box visually.
        MIN_FONT = 8
        MAX_FONT = 200
        text_box = box.adjusted(self.PADDING, self.PADDING, -self.PADDING, -self.PADDING)
        tw = int(text_box.width())
        th = int(text_box.height())
        if tw > 0 and th > 0 and self._text:
            lo, hi = MIN_FONT, MAX_FONT
            while lo < hi - 1:
                mid = (lo + hi) // 2
                fm = QFontMetrics(QFont("Arial", mid))
                needed = fm.boundingRect(
                    0, 0, tw, 0,
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap),
                    self._text)
                if needed.width() <= tw and needed.height() <= th:
                    lo = mid
                else:
                    hi = mid
            font_size = max(MIN_FONT, lo)
        else:
            font_size = MIN_FONT
        painter.setPen(QPen(self._fg_color))
        painter.setFont(QFont("Arial", font_size))
        # AlignVCenter + AlignHCenter: text block is centred inside the box,
        # filling it proportionally from top to bottom as seen in the reference.
        painter.drawText(text_box.toRect(),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter |
                             Qt.TextFlag.TextWordWrap),
                         self._text)

        if self.isSelected():
            # Dashed selection rect around the box
            painter.setPen(QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(box)
            # Cone handle — same visual style as _MarkerItem spike handle
            tip = self._cone_tip_local()
            painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(tip, self.HANDLE_R, self.HANDLE_R)
            # Resize handle — bottom-right corner (BR)
            br = QPointF(box.right(), box.bottom())
            painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawRect(QRectF(br.x() - self.HANDLE_R, br.y() - self.HANDLE_R,
                                    self.HANDLE_R * 2, self.HANDLE_R * 2))

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._over_cone_handle(event.pos()):
                self._drag_cone = True
                event.accept()
                return
        self._drag_cone = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_cone:
            self.prepareGeometryChange()
            # Store offset relative to box centre (so it survives resize)
            tip = event.pos()
            self._cone_rel = QPointF(tip.x() - self._w / 2,
                                     tip.y() - self._h / 2)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_cone = False
        super().mouseReleaseEvent(event)

    def _over_resize_handle_br(self, local_pos: QPointF) -> bool:
        """Check if pos is over the bottom-right resize handle."""
        br = QPointF(self._w, self._h)
        return (abs(local_pos.x() - br.x()) <= self.HANDLE_R + 4 and
                abs(local_pos.y() - br.y()) <= self.HANDLE_R + 4)

    def hoverMoveEvent(self, event):
        if self.isSelected() and self._over_cone_handle(event.pos()):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self.isSelected() and self._over_resize_handle_br(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Trigger edit via the scene/overlay's handler
        event.accept()

# ─── Dialogs for new items ────────────────────────────────────────────────────

class _MarkerItem(QGraphicsItem):
    """Numbered circular marker with:
    - a draggable spike handle (visible when selected, drag to reposition tip)
    - a scale handle on the left edge (visible when selected, drag to resize)
    Scaling is always uniform (equal W/H).
    """
    RADIUS = 21          # default marker radius in px (was 14; 1.5x larger)
    HANDLE_RADIUS = 6

    def __init__(self, pos: QPointF, number: int):
        super().__init__()
        self.setPos(pos)
        self.number = number
        self._scale = 1.0           # uniform scale factor
        self._spike_offset = QPointF(0, 0)  # hidden inside circle by default
        self._dragging_spike = False
        self._dragging_scale = False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)

    # ------------------------------------------------------------------
    # rect / setRect — kept for compatibility with resize machinery
    # ------------------------------------------------------------------
    def rect(self) -> QRectF:
        r = self.RADIUS * self._scale
        return QRectF(-r, -r, r * 2, r * 2)

    def setRect(self, new_rect: QRectF):
        self.prepareGeometryChange()
        half = (new_rect.width() + new_rect.height()) / 4.0
        self._scale = max(0.2, half / self.RADIUS)
        self.update()

    # ------------------------------------------------------------------
    # Scale handle position — left edge of circle, in local coords
    # ------------------------------------------------------------------
    def _scale_handle_pos(self) -> QPointF:
        r = self.RADIUS * self._scale
        return QPointF(-r, 0)

    def _over_scale_handle(self, local_pos: QPointF) -> bool:
        hp = self._scale_handle_pos()
        return math.hypot(local_pos.x() - hp.x(),
                          local_pos.y() - hp.y()) <= self.HANDLE_RADIUS + 4

    # ------------------------------------------------------------------
    def boundingRect(self):
        r = self.RADIUS * self._scale + 4
        sx, sy = self._spike_offset.x(), self._spike_offset.y()
        hr = self.HANDLE_RADIUS + 2
        # also cover the scale handle on the left
        left  = min(-r, sx - hr, self._scale_handle_pos().x() - hr) - 2
        top   = min(-r, sy - hr) - 2
        right  = max(r, abs(sx) + hr) + 2
        bottom = max(r, abs(sy) + hr) + 2
        return QRectF(left, top, right - left, bottom - top)

    def _spike_path(self):
        """Build the teardrop spike from circle edge to tip."""
        r = self.RADIUS * self._scale
        tip = self._spike_offset
        dx, dy = tip.x(), tip.y()
        length = math.hypot(dx, dy) or 1
        nx, ny = -dy / length, dx / length
        hw = r * 0.45
        p1 = QPointF(nx * hw, ny * hw)
        p2 = QPointF(-nx * hw, -ny * hw)
        path = QPainterPath()
        path.moveTo(p1)
        path.quadTo(QPointF(tip.x() * 0.6 + nx * hw * 0.3,
                            tip.y() * 0.6 + ny * hw * 0.3), tip)
        path.quadTo(QPointF(tip.x() * 0.6 - nx * hw * 0.3,
                            tip.y() * 0.6 - ny * hw * 0.3), p2)
        path.lineTo(p1)
        return path

    def paint(self, painter, option, widget=None):
        r = self.RADIUS * self._scale
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = getattr(self, '_bg_color', QColor(220, 50, 50))

        # Spike (behind circle)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(self._spike_path())

        # Circle
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0, 0), r, r)

        # Number
        text_col = getattr(self, '_text_color', QColor(Qt.GlobalColor.white))
        painter.setPen(QPen(text_col, 1))
        painter.setFont(QFont("Arial", max(8, int(r - 2)), QFont.Weight.Bold))
        painter.drawText(QRectF(-r, -r, r * 2, r * 2),
                         Qt.AlignmentFlag.AlignCenter, str(self.number))

        if self.isSelected():
            hr = self.HANDLE_RADIUS

            # Spike handle
            tip = self._spike_offset
            painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(tip, hr, hr)

            # Scale handle (left edge, white with arrows hint)
            sp = self._scale_handle_pos()
            painter.setBrush(QBrush(QColor(255, 220, 50, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(sp, hr, hr)
            # small arrows inside scale handle to hint resize
            painter.setPen(QPen(QColor(60, 60, 60), 1.2))
            painter.drawLine(QPointF(sp.x() - hr + 2, sp.y()),
                             QPointF(sp.x() + hr - 2, sp.y()))

            # Dashed bounding rect
            painter.setPen(QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(QRectF(-r, -r, r * 2, r * 2))

    def _over_spike_handle(self, local_pos: QPointF) -> bool:
        tip = self._spike_offset
        return math.hypot(local_pos.x() - tip.x(),
                          local_pos.y() - tip.y()) <= self.HANDLE_RADIUS + 4

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            if self._over_scale_handle(event.pos()):
                self._dragging_scale = True
                event.accept()
                return
            if self._over_spike_handle(event.pos()):
                self._dragging_spike = True
                event.accept()
                return
        self._dragging_spike = False
        self._dragging_scale = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging_scale:
            # Distance from centre → new radius → new scale
            dist = math.hypot(event.pos().x(), event.pos().y())
            dist = max(dist, self.RADIUS * 0.2)
            self.prepareGeometryChange()
            self._scale = dist / self.RADIUS
            self.update()
            _hp = _host_prefs(self)
            if _hp is not None:
                _hp.set('marker', 'scale', self._scale)
            event.accept()
            return
        if self._dragging_spike:
            self.prepareGeometryChange()
            self._spike_offset = event.pos()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging_spike = False
        self._dragging_scale = False
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event):
        if self.isSelected():
            if self._over_scale_handle(event.pos()):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                super().hoverMoveEvent(event)
                return
            if self._over_spike_handle(event.pos()):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                super().hoverMoveEvent(event)
                return
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

class StandardRegionSnapshotThread(QThread):
    """
    Ffmpeg-free "Capture Region" grabber ("standard method").

    Grabs the selection with a single mss call using ABSOLUTE PHYSICAL
    desktop coordinates (the same (final_x, final_y, phys_w, phys_h) region
    already computed for the ffmpeg method / its mss fallback). This is
    deliberately NOT done by stitching together separate per-monitor grabs:
    on Windows, when monitors use different DPI scaling, Qt's *logical*
    coordinate space is not guaranteed to be contiguous between monitors
    (there can be small gaps or overlaps versus the real physical layout).
    Stitching per-screen slices using those logical offsets can therefore
    leave a visible black gap where two monitors meet. A single grab in
    absolute physical pixels sidesteps that entirely: mss reads real
    framebuffer pixels directly, with no notion of "logical"/scaled
    coordinates at all, so a region spanning several differently-scaled
    monitors comes back as one continuous, gap-free image — exactly like
    the ffmpeg method's virtual-desktop grab + crop, just without needing
    ffmpeg installed.

    Runs on a background QThread so the UI/overlay never blocks, mirroring
    the signal contract of RecordingThread(snapshot_mode=True):
      region_ready(x, y, w, h) — physical bounding box, for the recording border
      finished(path)           — success, PNG written to `output_path`
      error(msg)                — failure (caller falls back to a plain mss grab)
    """
    finished     = Signal(str)
    error        = Signal(str)
    region_ready = Signal(int, int, int, int)

    def __init__(self, region, output_path: str, overlay_rgba=None, overlay_origin=(0, 0)):
        super().__init__()
        self.region      = region   # (x, y, w, h) in absolute physical pixels
        self.output_path = output_path
        self.overlay_rgba   = overlay_rgba     # Wayland: adnotacje (PIL RGBA, układ nakładki)
        self.overlay_origin = overlay_origin   # lewy-górny róg nakładki w układzie pulpitu

    def run(self):
        try:
            path = self._grab()
        except Exception as e:
            self.error.emit(f"Standard capture error: {e}")
            return
        if path:
            self.finished.emit(path)
        else:
            self.error.emit("Standard capture produced no output.")

    def _grab(self):
        if not (MSS_AVAILABLE and PIL_AVAILABLE):
            raise RuntimeError("mss / Pillow not available")

        x, y, w, h = self.region
        if w < 1 or h < 1:
            raise RuntimeError("Empty selection")

        with mss.MSS() as sct:
            shot = sct.grab({"left": int(x), "top": int(y),
                              "width": int(w), "height": int(h)})
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

        if self.overlay_rgba is not None:
            ox, oy = self.overlay_origin
            lx, ly = int(x) - ox, int(y) - oy
            part = self.overlay_rgba.crop((lx, ly, lx + int(w), ly + int(h)))
            if part.size != img.size:
                part = part.resize(img.size, Image.LANCZOS)
            img = Image.alpha_composite(img.convert("RGBA"), part).convert("RGB")

        # NOTE: region_ready (which draws the green dashed RecordingBorder)
        # is intentionally emitted AFTER the pixel grab above, not before.
        # Unlike the ffmpeg method — where a separate OS process does the
        # real screen grab, unaffected by anything Qt renders — here the
        # same process both grabs pixels via mss AND draws the border via
        # Qt. Emitting the signal first could race the border's own
        # on-screen paint against the mss grab and let the border sneak
        # into the captured image. Emitting it after guarantees that can
        # never happen; the border is then purely a brief post-capture
        # visual confirmation before the overlay closes.
        self.region_ready.emit(x, y, w, h)

        img.save(self.output_path, "PNG")
        return self.output_path


class EnhancedRegionSelector(QWidget):
    """
    Advanced region capture overlay.
    - DETECT mode: hover highlights windows, click captures.
    - DRAW mode: annotation tools (rect/ellipse/line/freehand/marker/text/image).
      In draw mode a dedicated 📷 Capture button triggers the screenshot.
    - ESC cancels at any time.
    - All annotation items are fully selectable, movable and resizable
      (uses the same QGraphicsScene infrastructure as the Image Editor).
    """
    region_selected = Signal(int, int, int, int)
    cancelled       = Signal()
    _esc_sig        = Signal()  # emitted from pynput thread, handled on main thread
    _close_sig      = Signal()  # emitted from background thread, closes overlay on main thread

    TOOL_DETECT    = "detect"
    TOOL_SELECT    = "select"
    TOOL_RECT      = "rect"
    TOOL_CIRCLE    = "circle"
    TOOL_FREEHAND  = "freehand"
    TOOL_LINE      = "line"
    TOOL_ARROW     = "arrow"
    TOOL_HIGHLIGHT = "highlight"
    TOOL_PIXBLUR   = "pixelate_blur"
    TOOL_SPOTLIGHT = "spotlight"
    TOOL_SMART_ERASER = "smart_eraser"
    TOOL_BUBBLE    = "bubble"
    TOOL_MARKER    = "marker"
    TOOL_TEXT      = "text"
    TOOL_IMAGE     = "image"
    TOOL_COLOR     = "color"
    TOOL_CLIP      = "clip"     # cut a rectangle out of the screen -> image item

    def __init__(self):
        super().__init__()
        self.setWindowFlags(_overlay_window_flags(Qt.WindowType.Tool))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMouseTracking(True)

        # Cover all screens
        self._geo = QRect()
        for s in QApplication.screens():
            self._geo = self._geo.united(s.geometry())
        self.setGeometry(self._geo)

        # Wayland: jedna zamrożona klatka pulpitu (portal), zanim nakładka się pokaże.
        if _is_wayland_session():
            _wl_freeze()

        # Desktop as it looks right now (before the overlay is shown) — the base
        # layer the Pixelate / Blur tool works on.
        self._desktop_snapshot = self._grab_desktop_snapshot()

        # ── State ──────────────────────────────────────────────────────────────
        self._current_tool      = self.TOOL_RECT
        self._detected_rect     = QRect()   # LOCAL widget coords
        self._drag_start        = None      # QPoint local
        self._drag_end          = None
        self._dragging          = False
        # Every annotation tool keeps its OWN colour/width/etc. here (see _ToolPrefs)
        self._prefs             = _ToolPrefs(width=3, font_size=18)
        self._last_draw_key     = 'rect'    # last tool that has options (for ⚙ with Select active)
        self._clip_start        = None      # Screen-clip tool drag (global Qt coords)
        self._clip_end          = None
        self._hide_bg           = False

        # Drawing state
        self._draw_start_scene   = None     # QPointF scene coords
        self._preview_item       = None     # live shape while dragging
        self._resizing_item      = None
        self._resize_handle      = None
        self._resize_start_pos   = None
        self._last_detected_global = QRect()   # instance-level, updated on each capture
        # Inline capture-selection state
        self._inline_selecting   = False
        self._inline_start       = None
        self._inline_end         = None
        self._prev_tool          = self.TOOL_RECT
        self._capture_geo        = QRect()

        # ── Annotation canvas ─────────────────────────────────────────────────
        self._canvas = _OverlayCanvas(self)
        self._canvas.sync_geometry()
        self._sel_layer = _SelectionLayer(self)      # selection frame above all annotations
        self._canvas.raise_()
        self._raise_toolbar()
        # Sync color swatch and freehand defaults when selection changes
        self._canvas._scene.selectionChanged.connect(self._on_selection_changed)

        # ── Window / UI-element detection (hover-to-outline, click-to-capture) ──
        # Enabled by default; can be turned off in Settings → "Capture Region method".
        self._detect_windows_enabled = Config().get("detect_windows_on_capture", True)
        self._inline_dragging   = False   # True once a real drag (not just a click) starts
        self._win_rects: list[QRect] = []
        self._win_tops: list = []           # [(hwnd, QRect)] z-order, Windows
        self._win_children: list[QRect] = []
        self._win_child_owner = None
        self._child_refresh_pending = False
        self._cache_timer = QTimer(self)
        self._cache_timer.timeout.connect(self._refresh_win_rects)
        if self._detect_windows_enabled:
            self._cache_timer.start(300)
            self._refresh_win_rects()

        # ── Toolbar ───────────────────────────────────────────────────────────
        self._toolbar = self._build_toolbar()
        
        # Place toolbar on the screen where the cursor is currently located
        cursor_screen = QApplication.screenAt(QCursor.pos())
        if not cursor_screen:
            cursor_screen = QApplication.primaryScreen()
            
        screen_geo = cursor_screen.geometry()
        tx = screen_geo.x() - self._geo.x() + screen_geo.width() // 2 - self._toolbar.width() // 2
        ty = screen_geo.y() - self._geo.y() + 8
        self._toolbar.move(tx, ty)
        self._toolbar.show()
        self._raise_toolbar()

        # Open directly in "drag to select a region and capture it" mode —
        # the same mode as the 📷 Capture ▾ → "Region" menu item — instead
        # of defaulting to the red-rectangle annotation tool. Unlike that
        # menu item, the toolbar stays visible here (this only sets the
        # same internal state, without the toolbar.hide() that menu item
        # normally does), so annotation tools are still reachable by simply
        # clicking a toolbar button before dragging.
        self._prev_tool        = self.TOOL_RECT
        self._current_tool     = "_capture_select"
        self._inline_selecting = True
        self._inline_start     = None
        self._inline_end       = None

        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        # grabKeyboard() must be deferred — it only works once the window
        # is truly active at the OS level (e.g. when triggered via global hotkey
        # the window isn't active yet at __init__ time, so ESC wouldn't fire
        # until the user clicked, which finally made Qt the active window).
        self._esc_sig.connect(self._esc_pressed)
        self._close_sig.connect(self.close)
        self._raise_toolbar()
        QTimer.singleShot(150, self._activate_and_grab)
        QTimer.singleShot(80, self._update_detection_at_cursor)

    def _grab_desktop_snapshot(self):
        """Logical-resolution image of the whole overlay area (all monitors), or None
        when the platform won't let us grab the screen (e.g. Wayland)."""
        if _is_wayland_session():
            frozen = _wl_frozen_qimage(self._geo.size())
            if frozen is not None:
                return frozen
        try:
            snap = QImage(self._geo.size(), QImage.Format.Format_RGB32)
            snap.fill(Qt.GlobalColor.black)
            p = QPainter(snap)
            got = False
            for scr in QApplication.screens():
                pm = scr.grabWindow(0)
                if pm.isNull():
                    continue
                g = scr.geometry()
                p.drawPixmap(QRect(g.x() - self._geo.x(), g.y() - self._geo.y(),
                                   g.width(), g.height()), pm)
                got = True
            p.end()
            return snap if got else None
        except Exception as ex:
            print(f"[PyshareX] desktop snapshot failed: {ex}")
            return None

    def _raise_toolbar(self):
        """Keep the toolbar above the annotation canvas (and every item in it).

        The toolbar and the canvas are sibling child widgets of this overlay, so
        whichever was raised last wins. Every canvas.raise_() / toolbar.show()
        must therefore be followed by this call, otherwise annotation items
        end up painted over the toolbar.
        """
        layer = getattr(self, "_sel_layer", None)
        if layer is not None:
            layer.setGeometry(self.rect())
            layer.raise_()                           # above the canvas, below the toolbar
        tb = getattr(self, "_toolbar", None)
        if tb is not None:
            tb.raise_()
            drag_icon = getattr(tb, "_drag_icon", None)
            if drag_icon is not None and drag_icon.isVisible():
                drag_icon.raise_()

    def _activate_and_grab(self):
        if _is_wayland_session():
            self.setGeometry(self._geo)   # zarządzane okno: WM mógł je przesunąć
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.grabKeyboard()
        # BypassWindowManagerHint prevents the OS from giving this window
        # real keyboard focus, so grabKeyboard() alone won't catch ESC when
        # triggered via a global hotkey. Use pynput as a fallback listener.
        self._start_esc_listener()

    def _start_esc_listener(self):
        if not PYNPUT_AVAILABLE:
            return
        # Connect only once
        try:
            self._esc_sig.disconnect()
        except Exception:
            pass
        self._esc_sig.connect(self._esc_pressed)
        from pynput import keyboard as _kb
        def on_press(key):
            try:
                if key == _kb.Key.esc:
                    self._esc_sig.emit()  # thread-safe signal, no QTimer needed
                    return False  # stop listener
            except Exception:
                pass
        self._esc_listener = _kb.Listener(on_press=on_press)
        self._esc_listener.start()

    def _esc_pressed(self):
        self._stop_esc_listener()
        if self.isVisible():
            self.releaseKeyboard()
            self.close()
            self.cancelled.emit()

    def _stop_esc_listener(self):
        listener = getattr(self, '_esc_listener', None)
        if listener:
            try:
                listener.stop()
            except Exception:
                pass
            self._esc_listener = None

    # ══════════════════════════════════════════════════════════════════════════
    #  Window detection
    # ══════════════════════════════════════════════════════════════════════════

    def _refresh_win_rects(self):
        """
        Collect visible window rects, in LOCAL widget coords.
        Win32 (GetWindowRect/DwmGetWindowAttribute) and X11 (EWMH, via
        python-xlib) both report PHYSICAL desktop pixels, so both go through
        _physical_rects_to_local() to become correct LOCAL rects even when
        different monitors use different DPI scale factors. Falls back to
        per-screen full-rect entries when neither backend is available
        (Wayland, missing python-xlib, a non-EWMH window manager, or macOS).
        """
        rects = []
        tops, children, child_owner = [], [], None
        if IS_WINDOWS:
            try:
                import win32gui
                import win32process
                cur = win32gui.GetCursorPos()        # fizyczne px
                phys_tops = []                        # [(hwnd, (l,t,r,b))] z-order: góra → dół
                my_pid   = os.getpid()
                my_hwnd  = int(self.winId())

                def _is_own_overlay(hwnd) -> bool:
                    # Sam overlay (i inne nasze okna "zawsze na wierzchu": pasek
                    # narzędzi, ramki) leży NAJWYŻEJ w z-order i pokrywa cały
                    # ekran - bez tego filtra zawsze on wygrywa i zaznaczany
                    # jest "cały ekran".
                    if hwnd == my_hwnd:
                        return True
                    try:
                        if win32process.GetWindowThreadProcessId(hwnd)[1] != my_pid:
                            return False
                        exstyle = win32gui.GetWindowLong(hwnd, -20)   # GWL_EXSTYLE
                        return bool(exstyle & 0x00000008)             # WS_EX_TOPMOST
                    except Exception:
                        return False

                def _cb(hwnd, _):
                    if not (win32gui.IsWindowVisible(hwnd) and
                            win32gui.GetWindowText(hwnd)):
                        return True
                    if _is_own_overlay(hwnd):
                        return True
                    if win32gui.IsIconic(hwnd):
                        return True   # minimized — its rect is meaningless/off-screen
                    if _is_window_cloaked(hwnd):
                        return True   # ghost/helper window (see _is_window_cloaked),
                                      # or a window on a different virtual desktop
                    try:
                        phys_tops.append((hwnd, _get_visible_window_bounds(hwnd)))
                    except Exception:
                        pass
                    return True

                win32gui.EnumWindows(_cb, None)

                for hwnd, pb in phys_tops:
                    loc = _physical_rects_to_local([pb], self._geo)
                    if loc:
                        tops.append((hwnd, loc[0]))

                # Okna potomne liczymy tylko dla okna, które jest faktycznie
                # NA WIERZCHU pod kursorem (EnumChildWindows dla wszystkich
                # okien co 300 ms byłoby zbędnie drogie).
                for hwnd, pb in phys_tops:
                    if pb[0] <= cur[0] < pb[2] and pb[1] <= cur[1] < pb[3]:
                        child_owner = hwnd
                        phys_children = _get_child_window_bounds(hwnd, pb)
                        children = _physical_rects_to_local(phys_children, self._geo)
                        break

                rects = [r for _, r in tops] + children
            except Exception:
                pass
        elif IS_LINUX:
            x11_rects = _get_x11_window_rects()
            if x11_rects:
                for i, pr in enumerate(x11_rects):          # najwyższe okno pierwsze
                    loc = _physical_rects_to_local([pr], self._geo)
                    if loc:
                        tops.append((i, loc[0]))
                rects = [r for _, r in tops]
            else:
                # Not an X11 session (e.g. Wayland), python-xlib missing, or
                # no EWMH support — fall back to whole-screen rects.
                for s in QApplication.screens():
                    lg = s.geometry()
                    rects.append(QRect(
                        lg.x() - self._geo.x(), lg.y() - self._geo.y(),
                        lg.width(), lg.height()))
        else:
            for s in QApplication.screens():
                lg = s.geometry()
                rects.append(QRect(
                    lg.x() - self._geo.x(), lg.y() - self._geo.y(),
                    lg.width(), lg.height()))
        self._win_rects = rects
        self._win_tops = tops
        self._win_has_children = IS_WINDOWS
        self._win_children = children
        self._win_child_owner = child_owner
        self._update_detection_at_cursor()

    def _update_detection_at_cursor(self):
        # Active in the dedicated DETECT tool, and also in the default
        # "_capture_select" mode (the one "Capture region" opens into) as
        # long as window detection is enabled in Settings and the user
        # isn't currently dragging out a manual selection rectangle.
        in_capture_hover = (self._current_tool == "_capture_select" and
                             self._detect_windows_enabled and
                             not self._inline_dragging)
        if not (self._current_tool == self.TOOL_DETECT or in_capture_hover):
            return
        if self._dragging:
            return
        gpos  = QCursor.pos()
        found = self._best_rect_at_global(gpos)
        if found != self._detected_rect:
            self._detected_rect = found
            self.update()

    def _best_rect_at_global(self, gpos: QPoint) -> QRect:
        """LOCAL-coord rect under global pos gpos.

        Windows: bierzemy okno NAJWYŻEJ w z-order pod kursorem (zasłonięte
        okna już nie "wygrywają" tylko dlatego, że są mniejsze), a w nim
        najmniejszy element potomny (fragment okna) zawierający kursor;
        gdy brak potomka - całe okno. Inne systemy: najmniejszy prostokąt.
        """
        local = gpos - self._geo.topLeft()
        tops = getattr(self, "_win_tops", [])
        if tops:
            top_hwnd, top_rect = None, QRect()
            for hwnd, r in tops:
                if r.contains(local):
                    top_hwnd, top_rect = hwnd, r
                    break
            if top_hwnd is None:
                return QRect()
            if (getattr(self, "_win_has_children", False) and
                    top_hwnd != getattr(self, "_win_child_owner", None)):
                # dzieci policzone dla innego okna - odśwież zaraz (raz)
                if not getattr(self, "_child_refresh_pending", False):
                    self._child_refresh_pending = True
                    def _again():
                        self._child_refresh_pending = False
                        self._refresh_win_rects()
                    QTimer.singleShot(0, _again)
                return top_rect
            best, best_area = top_rect, top_rect.width() * top_rect.height()
            for r in self._win_children:
                a = r.width() * r.height()
                if r.contains(local) and a < best_area:
                    best, best_area = r, a
            return best

        best, best_area = QRect(), 10**9
        for r in self._win_rects:
            if r.contains(local) and r.width() * r.height() < best_area:
                best, best_area = r, r.width() * r.height()
        return best

    # ══════════════════════════════════════════════════════════════════════════
    #  Toolbar
    # ══════════════════════════════════════════════════════════════════════════

    _TOOL_KEYS = {
        TOOL_RECT: 'rect', TOOL_CIRCLE: 'circle', TOOL_LINE: 'line',
        TOOL_ARROW: 'arrow', TOOL_FREEHAND: 'freehand', TOOL_HIGHLIGHT: 'highlight',
        TOOL_PIXBLUR: 'pixelate_blur', TOOL_SPOTLIGHT: 'spotlight',
        TOOL_BUBBLE: 'bubble', TOOL_MARKER: 'marker', TOOL_TEXT: 'text',
    }

    def _tool_key(self, tool=None):
        """_ToolPrefs key of a drawing tool (None for Select/Clip/Image/…)."""
        return self._TOOL_KEYS.get(tool if tool is not None else self._current_tool)

    def _active_key(self):
        """Tool whose options the ⚙ button edits when nothing is selected."""
        return self._tool_key() or self._last_draw_key

    def _selected_editable_item(self):
        for it in self._canvas._scene.selectedItems():
            if _prefs_key_for_item(it):
                return it
        return None

    def _refresh_swatch(self):
        """Toolbar swatch = colours of the selected object, else of the active tool."""
        if not hasattr(self, '_color_preview'):
            return
        item = self._selected_editable_item()
        colors = (_item_swatch_colors(_prefs_key_for_item(item), item)
                  if item is not None else
                  _prefs_swatch_colors(self._active_key(), self._prefs))
        self._color_preview.set_colors(colors or [QColor(128, 128, 128)])

    def _on_selection_changed(self):
        """Scene selection changed → show the selected object's colours."""
        self._refresh_swatch()

    def _build_toolbar(self):
        bar = QWidget(self,
                      Qt.WindowType.FramelessWindowHint |
                      Qt.WindowType.WindowStaysOnTopHint)
        bar.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        bar.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        bar.setStyleSheet("""
            QWidget { background: rgba(24,24,37,220); border-radius: 10px; }
            QPushButton {
                background: rgba(40,40,60,200); color: #cdd6f4;
                border: 1px solid #45475a; border-radius: 6px;
                font-size: 16px; min-width: 36px; min-height: 36px;
                max-width: 36px; max-height: 36px;
            }
            QPushButton:checked { background: #313264; border: 2px solid #89b4fa; }
            QPushButton:hover   { background: #45475a; }
            QPushButton#captureBtn {
                background: #1e6e1e; border: 2px solid #4caf50;
                min-width: 110px; max-width: 110px; font-size: 13px;
            }
            QPushButton#captureBtn:hover { background: #2e9e2e; }
            QPushButton#colorPickerBtn {
                min-width: 58px; max-width: 58px; font-size: 18px;
            }
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        # ── Drag handle (left side) ───────────────────────────────────────────
        # Lets the user grab and reposition the toolbar anywhere on screen.
        drag_handle = QLabel("⠿")
        drag_handle.setFixedSize(18, 36)
        drag_handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drag_handle.setToolTip("Drag to move toolbar")
        drag_handle.setStyleSheet(
            "color: #6c7086; font-size: 20px; background: transparent; border: none;"
        )
        drag_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        lay.addWidget(drag_handle)
        lay.addSpacing(4)

        # Track drag state on the bar widget itself
        bar._drag_active = False
        # _drag_icon: a top-level label that follows the cursor during drag
        drag_icon = QLabel("⠿", None)
        drag_icon.setWindowFlags(
            Qt.WindowType.ToolTip |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.X11BypassWindowManagerHint
        )
        drag_icon.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        drag_icon.setStyleSheet(
            "color: #cdd6f4; font-size: 22px; background: rgba(40,40,60,200);"
            "border: 1px solid #89b4fa; border-radius: 6px; padding: 2px 4px;"
        )
        drag_icon.setFixedSize(28, 36)
        drag_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar._drag_icon = drag_icon

        def _handle_mouse_press(event, b=bar, h=drag_handle):
            if event.button() == Qt.MouseButton.LeftButton:
                b._drag_active = True
                # Show floating drag icon exactly at cursor
                gpos = event.globalPosition().toPoint()
                b._drag_icon.move(gpos.x(), gpos.y())
                b._drag_icon.show()
                b._drag_icon.raise_()
                event.accept()

        def _handle_mouse_move(event, b=bar):
            if b._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
                gpos = event.globalPosition().toPoint()
                # Floating icon follows cursor exactly (top-level, global coords)
                b._drag_icon.move(gpos.x(), gpos.y())
                # Toolbar is a child widget — must convert global → parent-local coords
                b.move(b.parent().mapFromGlobal(gpos))
                # Mark that user manually positioned toolbar (suppress auto-monitor logic)
                b._user_dragged = True
                event.accept()

        def _handle_mouse_release(event, b=bar):
            if event.button() == Qt.MouseButton.LeftButton:
                b._drag_active = False
                b._drag_icon.hide()
                event.accept()

        # Mouse events must be tracked on the handle; enable mouse tracking so
        # mouseMoveEvent fires even when the cursor drifts outside the label.
        drag_handle.setMouseTracking(True)
        drag_handle.mousePressEvent   = _handle_mouse_press
        drag_handle.mouseMoveEvent    = _handle_mouse_move
        drag_handle.mouseReleaseEvent = _handle_mouse_release

        # Also track on the bar itself — if cursor moves fast and leaves the
        # handle widget, the bar continues receiving move/release events.
        bar._user_dragged = False

        _orig_bar_mouse_press   = bar.mousePressEvent
        _orig_bar_mouse_move    = bar.mouseMoveEvent
        _orig_bar_mouse_release = bar.mouseReleaseEvent

        def _bar_mouse_move(event, b=bar):
            if b._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
                gpos = event.globalPosition().toPoint()
                b._drag_icon.move(gpos.x(), gpos.y())
                b.move(b.parent().mapFromGlobal(gpos))
                b._user_dragged = True
                event.accept()
            else:
                _orig_bar_mouse_move(event)

        def _bar_mouse_release(event, b=bar):
            if b._drag_active and event.button() == Qt.MouseButton.LeftButton:
                b._drag_active = False
                b._drag_icon.hide()
                event.accept()
            else:
                _orig_bar_mouse_release(event)

        bar.setMouseTracking(True)
        bar.mouseMoveEvent    = _bar_mouse_move
        bar.mouseReleaseEvent = _bar_mouse_release

        tools = [
            (self.TOOL_SELECT,    None,  "Select / move / resize annotations (Del to delete)"),
            (self.TOOL_RECT,      None,  "Draw rectangle annotation (Ctrl = square, Alt = from center, Shift = move)"),
            (self.TOOL_CIRCLE,    "⭕",  "Draw ellipse annotation (Ctrl = circle, Alt = from center, Shift = move)"),
            (self.TOOL_HIGHLIGHT, "🟨",  "Draw highlight (semi-transparent yellow rectangle) (Ctrl = square, Alt = from center, Shift = move)"),
            (self.TOOL_PIXBLUR,   None,  "Pixelate / Blur an area (mode + intensity: ⚙ options or right-click) (Ctrl = square, Alt = from center, Shift = move)"),
            (self.TOOL_SPOTLIGHT, None,  "Spotlight — blur everything OUTSIDE the selected area (intensity: ⚙ options) (Ctrl = square, Alt = from center, Shift = move)"),
            (self.TOOL_SMART_ERASER, None, "Smart Eraser — click picks the colour under the cursor, drag fills a rectangle with exactly that colour (Ctrl = square, Alt = from center, Shift = move)"),
            (self.TOOL_FREEHAND,  "✏️",  "Freehand drawing"),
            (self.TOOL_LINE,      "📏",  "Draw straight line (Ctrl = snap 45°, Alt = from center, Shift = move)"),
            (self.TOOL_ARROW,     None,  "Draw arrow (Ctrl = snap 45°, Alt = from center, Shift = move)"),
            (self.TOOL_BUBBLE,    "💬",  "Add text bubble"),
            (self.TOOL_MARKER,    "📍",  "Add numbered marker"),
            (self.TOOL_TEXT,      "T",   "Add text annotation"),
            (self.TOOL_IMAGE,     "🖼️",  "Import image onto canvas"),
            (self.TOOL_CLIP,      None,  "Cut a rectangular piece of the screen and place it on the canvas as an image"),
            (self.TOOL_COLOR,     None,  "Tool options — colours, width, …  (edits the selected object, or the active tool's defaults)"),
        ]
        self._tool_btns = {}
        for tid, icon, tip in tools:
            btn = QPushButton()
            btn.setCheckable(tid not in (self.TOOL_COLOR, self.TOOL_IMAGE))
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _, t=tid: self._select_tool(t))
            if tid == self.TOOL_SELECT:
                btn.setIcon(_svg_icon(_SVG_SELECT, 32))
                btn.setIconSize(QSize(28, 28))
            elif tid == self.TOOL_RECT:
                btn.setIcon(_svg_icon(_SVG_RECT_TOOL, 32))
                btn.setIconSize(QSize(28, 28))
            elif tid == self.TOOL_CIRCLE:
                btn.setIcon(_svg_icon(_SVG_CIRCLE_TOOL, 32))
                btn.setIconSize(QSize(28, 28))
            elif tid == self.TOOL_PIXBLUR:
                btn.setIcon(_svg_icon(_SVG_PIXELATE_TOOL, 32))
                btn.setIconSize(QSize(28, 28))
            elif tid == self.TOOL_SPOTLIGHT:
                btn.setIcon(_svg_icon(_SVG_SPOTLIGHT_TOOL, 32))
                btn.setIconSize(QSize(28, 28))
            elif tid == self.TOOL_SMART_ERASER:
                btn.setIcon(_svg_icon(_SVG_SMART_ERASER_TOOL, 32))
                btn.setIconSize(QSize(28, 28))
            elif tid == self.TOOL_LINE:
                btn.setIcon(_svg_icon(_SVG_LINE_TOOL, 32))
                btn.setIconSize(QSize(28, 28))
            elif tid == self.TOOL_ARROW:
                btn.setIcon(_svg_icon(_SVG_ARROW_TOOL, 32))
                btn.setIconSize(QSize(28, 28))
            elif tid == self.TOOL_CLIP:
                btn.setIcon(_svg_icon(_SVG_SCREEN_CLIP, 32))
                btn.setIconSize(QSize(28, 28))
            elif tid == self.TOOL_COLOR:
                btn.setObjectName("colorPickerBtn")
                btn.setIcon(_svg_icon(_SVG_COLOR_TOOLS, 104, 48))
                btn.setIconSize(QSize(46, 22))
            else:
                btn.setText(icon)
            lay.addWidget(btn)
            self._tool_btns[tid] = btn
        #self._tool_btns[self.TOOL_RECT].setChecked(True)

        # Colour swatch (mirrors the colours the ⚙ options window edits)
        self._color_preview = _ColorSwatch()
        lay.addWidget(self._color_preview)
        self._refresh_swatch()

        lay.addSpacing(6)

        # ── Undo / Redo — same as the Image Editor ────────────────────────────
        self.btn_undo = QPushButton("↩️")
        self.btn_undo.setToolTip("Undo (Ctrl+Z)")
        self.btn_undo.setCheckable(False)
        self.btn_undo.clicked.connect(self.canvas_undo)
        self.btn_undo.setEnabled(False)
        lay.addWidget(self.btn_undo)

        self.btn_redo = QPushButton("↪️")
        self.btn_redo.setToolTip("Redo (Ctrl+Y)")
        self.btn_redo.setCheckable(False)
        self.btn_redo.clicked.connect(self.canvas_redo)
        self.btn_redo.setEnabled(False)
        lay.addWidget(self.btn_redo)

        lay.addSpacing(6)

        # ── 📷 Capture button — hover shows dropdown menu ─────────────────────
        class _CaptureMenuBtn(QPushButton):
            """Capture button that pops a dropdown on hover and on click."""
            def __init__(self, owner, parent=None):
                super().__init__("📷 Capture ▾", parent)
                self._owner = owner

            def _show_menu(self):
                menu = QMenu(self)
                menu.setStyleSheet("""
                    QMenu {
                        background: #1e1e2e; color: #cdd6f4;
                        border: 1px solid #45475a; border-radius: 6px;
                        padding: 4px 0px;
                    }
                    QMenu::item { padding: 6px 18px; font-size: 13px; }
                    QMenu::item:selected { background: #313264; }
                """)
                menu.addAction("Region",          self._owner._capture_with_annotations)
                menu.addAction("Current monitor", self._owner._capture_current_monitor)
                menu.addAction("All monitors",    self._owner._capture_all_monitors)
                # Show menu directly below the button
                gpos = self.mapToGlobal(self.rect().bottomLeft())
                menu.exec(gpos)

            def enterEvent(self, event):
                self._show_menu()
                super().enterEvent(event)

            def mousePressEvent(self, event):
                if event.button() == Qt.MouseButton.LeftButton:
                    self._show_menu()
                else:
                    super().mousePressEvent(event)

        self._capture_btn = _CaptureMenuBtn(self)
        self._capture_btn.setObjectName("captureBtn")
        self._capture_btn.setToolTip("Capture options")
        self._capture_btn.show()
        lay.addWidget(self._capture_btn)

        bar.adjustSize()
        return bar

    # ── helpers ───────────────────────────────────────────────────────────────
    def _is_draw_tool(self, tid=None):
        t = tid if tid is not None else self._current_tool
        return t in (self.TOOL_RECT, self.TOOL_CIRCLE, self.TOOL_FREEHAND,
                     self.TOOL_LINE, self.TOOL_ARROW, self.TOOL_HIGHLIGHT, self.TOOL_PIXBLUR, self.TOOL_SPOTLIGHT, self.TOOL_SMART_ERASER,
                     self.TOOL_BUBBLE, self.TOOL_MARKER, self.TOOL_TEXT,
                     self.TOOL_IMAGE, self.TOOL_CLIP, self.TOOL_SELECT)

    def _select_tool(self, tool_id):
        if tool_id == self.TOOL_IMAGE:
            self._import_image(); return
        if tool_id == self.TOOL_COLOR:
            self._open_tool_options()
            return

        # Reset drawing state when switching tools to avoid stale start position
        self._draw_start_scene = None
        self._clip_start = self._clip_end = None
        # Clear any stale resize state (e.g. after handle drag on Line/Arrow)
        self._resizing_item  = None
        self._resize_handle  = None
        if self._preview_item is not None:
            try:
                self._canvas._scene.removeItem(self._preview_item)
            except Exception:
                pass
            self._preview_item = None

        self._current_tool = tool_id
        for tid, btn in self._tool_btns.items():
            if btn.isCheckable():
                btn.setChecked(tid == tool_id)
        # Remember the last tool that has options, then re-point the swatch at
        # the newly selected tool's own (independent) settings.
        _k = self._tool_key(tool_id)
        if _k:
            self._last_draw_key = _k
        self._refresh_swatch()

        # Show/hide capture button
        if self._is_draw_tool(tool_id):
            self._capture_btn.show()
        else:
            self._capture_btn.hide()
        # Canvas is ALWAYS transparent — mouse routing is done manually

        cross = (self.TOOL_DETECT, self.TOOL_RECT, self.TOOL_CIRCLE,
                 self.TOOL_LINE, self.TOOL_ARROW, self.TOOL_HIGHLIGHT, self.TOOL_PIXBLUR, self.TOOL_SPOTLIGHT, self.TOOL_SMART_ERASER,
                 self.TOOL_FREEHAND, self.TOOL_CLIP)
        cur = Qt.CursorShape.CrossCursor if tool_id in cross else Qt.CursorShape.ArrowCursor
        self.setCursor(QCursor(cur))
        self._toolbar.adjustSize()

        if tool_id == self.TOOL_DETECT:
            self._update_detection_at_cursor()

    def _exec_dialog(self, dlg) -> int:
        """Run a modal dialog while the overlay is active.
        Switches to SELECT tool before opening so any stray mouse events that
        land on the overlay after the dialog closes are harmless (select does
        not create new annotations). The previous tool is restored afterwards."""
        prev_tool = self._current_tool
        # Switch to SELECT so stray clicks don't spawn duplicate annotations
        self._current_tool = self.TOOL_SELECT
        for tid, btn in self._tool_btns.items():
            if btn.isCheckable():
                btn.setChecked(tid == self.TOOL_SELECT)
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self._toolbar.hide()
        self.releaseKeyboard()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        QApplication.processEvents()
        # On Linux the fullscreen overlay sits above normal WindowStaysOnTop dialogs;
        # X11BypassWindowManagerHint forces the dialog above everything.
        flags = Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
        if IS_LINUX:
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        dlg.setWindowFlags(flags)
        _install_drag_move(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        result = dlg.exec()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        # Canvas MUST remain transparent so the parent overlay can route mouse events properly
        self._canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Clear any stale resize/draw state before restoring the previous tool
        self._resizing_item    = None
        self._resize_handle    = None
        self._draw_start_scene = None
        self._preview_item     = None
        # Restore the tool that was active before the dialog
        # Use direct assignment to avoid re-triggering IMAGE/COLOR side effects
        if prev_tool not in (self.TOOL_IMAGE, self.TOOL_COLOR):
            self._current_tool = prev_tool
            for tid, btn in self._tool_btns.items():
                if btn.isCheckable():
                    btn.setChecked(tid == prev_tool)
            cross = (self.TOOL_DETECT, self.TOOL_RECT, self.TOOL_CIRCLE,
                     self.TOOL_LINE, self.TOOL_ARROW, self.TOOL_HIGHLIGHT, self.TOOL_PIXBLUR, self.TOOL_SPOTLIGHT, self.TOOL_SMART_ERASER,
                     self.TOOL_FREEHAND, self.TOOL_CLIP)
            cur = Qt.CursorShape.CrossCursor if prev_tool in cross else Qt.CursorShape.ArrowCursor
            self.setCursor(QCursor(cur))
        else:
            self._current_tool = self.TOOL_SELECT
        self._toolbar.show()
        self._raise_toolbar()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.grabKeyboard()
        return result

    

    # ── Tool options (same window + logic as the Image Editor) ───────────────
    def _open_tool_options(self):
        """⚙ button: edit the selected object — or, with nothing selected, the
        remembered defaults of the active tool."""
        item = self._selected_editable_item()
        if item is not None:
            self._open_item_edit_dialog(item)
        else:
            self._open_defaults_dialog(self._active_key())

    def _pick_color(self):                 # legacy name
        self._open_tool_options()

    def _open_defaults_dialog(self, key):
        dlg = _build_options_dialog(key, self, self._prefs, allow_fill=True)
        if dlg is None:
            return
        if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
            _store_dialog_in_prefs(key, dlg, self._prefs, allow_fill=True)
        self._refresh_swatch()

    def _open_item_edit_dialog(self, item) -> bool:
        """Options window for *item* with live preview. On OK the new values
        are applied (one undo step) and remembered as that tool's defaults.
        Returns True if a dialog was shown."""
        key = _prefs_key_for_item(item)
        if key is None:
            return False
        dlg = _build_options_dialog(key, self, self._prefs, item=item, allow_fill=True)
        if dlg is None:
            return False
        if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
            dlg.restore_original()          # undo snapshot = state before the edit
            if key in ('bubble', 'text') and not dlg.edit.toPlainText().strip():
                if key == 'text':           # emptied text annotation = delete it
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            else:
                self._canvas.push_undo()
                _commit_item_dialog(key, dlg, item, allow_fill=True)
                _store_dialog_in_prefs(key, dlg, self._prefs, allow_fill=True)
            self._canvas._scene.update()
        self._refresh_swatch()
        return True

    def _edit_rect(self, item):
        self._open_item_edit_dialog(item)

    def _edit_item(self, item) -> bool:    # public name shared with ImageEditorWindow
        return self._open_item_edit_dialog(item)

    # ── Screen clip tool: cut a piece of the real screen onto the canvas ──────
    def _logical_rect_to_physical(self, global_rect: QRect):
        """Logical global Qt rect -> physical (mss) desktop rect (x, y, w, h).
        Each corner is converted with the DPI scale of the monitor that owns
        it (same mapping as _grab_composite_and_close)."""
        start_screen = (QApplication.screenAt(global_rect.topLeft())
                        or QApplication.screenAt(global_rect.center())
                        or QApplication.primaryScreen())
        end_screen = QApplication.screenAt(global_rect.bottomRight()) or start_screen
        x, y = global_rect.x(), global_rect.y()
        w, h = max(1, global_rect.width()), max(1, global_rect.height())
        try:
            with mss.MSS() as sct:
                qt_screens = sorted(QApplication.screens(),
                                    key=lambda s: (s.geometry().x(), s.geometry().y()))
                mss_mons = sorted(sct.monitors[1:],
                                  key=lambda m: (m["left"], m["top"]))

                def _mon_for(scr):
                    if scr in qt_screens:
                        idx = qt_screens.index(scr)
                        if idx < len(mss_mons):
                            return mss_mons[idx]
                    return {"left": 0, "top": 0}

                def _to_phys(px, py, scr):
                    r = scr.devicePixelRatio() or 1.0
                    geo = scr.geometry()
                    mon = _mon_for(scr)
                    return (mon["left"] + round((px - geo.x()) * r),
                            mon["top"] + round((py - geo.y()) * r))

                x, y = _to_phys(global_rect.x(), global_rect.y(), start_screen)
                ex, ey = _to_phys(global_rect.x() + global_rect.width(),
                                  global_rect.y() + global_rect.height(), end_screen)
                w, h = max(1, ex - x), max(1, ey - y)
        except Exception as ex:
            print(f"[PyshareX] clip coord mapping error: {ex}")
        return x, y, w, h

    def _clip_screen_region(self, global_rect: QRect):
        """Hide the overlay (dim layer, toolbar and annotations), grab the
        selected piece of the real screen, bring the overlay back and insert
        the grab as a normal image item (ResizablePixmapItem)."""
        phys = self._logical_rect_to_physical(global_rect)
        self.releaseKeyboard()
        self.hide()
        QApplication.processEvents()
        # Give the compositor a moment to actually remove the overlay
        QTimer.singleShot(200, lambda: self._finish_clip(global_rect, phys))

    def _finish_clip(self, global_rect: QRect, phys):
        px, py, pw, ph = phys
        pix = None
        try:
            with mss.MSS() as sct:
                shot = sct.grab({"left": px, "top": py, "width": pw, "height": ph})
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            qim = QImage(img.tobytes(), img.width, img.height, img.width * 3,
                         QImage.Format.Format_RGB888).copy()
            pix = QPixmap.fromImage(qim)
        except Exception as ex:
            print(f"[PyshareX] screen clip error: {ex}")

        # Restore the overlay first, whatever happened
        self.show()
        self.raise_()
        self._canvas.raise_()
        self._raise_toolbar()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.grabKeyboard()
        self._toolbar.show()
        self._raise_toolbar()

        if pix is not None and not pix.isNull():
            # Item is sized in logical units (so it covers exactly the area it
            # was cut from) while keeping the full physical-resolution pixels.
            pos = QPointF(self.mapFromGlobal(global_rect.topLeft()))
            self._canvas.push_undo()
            item = self._canvas.add_pixmap(pos, pix)
            item.setRect(QRectF(0, 0, global_rect.width(), global_rect.height()))
            self._select_tool(self.TOOL_SELECT)
            self._canvas._scene.clearSelection()
            item.setSelected(True)
        else:
            self._select_tool(self.TOOL_SELECT)

    def _place_imported_image(self, path: str):
        """Put the picture at *path* in the middle of the overlay as a movable item."""
        pix = QPixmap(path)
        if not pix.isNull():
            center = QPointF(self._geo.width() / 2 - pix.width()  / 2,
                             self._geo.height()/ 2 - pix.height() / 2)
            self._canvas.push_undo()
            self._canvas.add_pixmap(center, pix)

    def _import_image(self):
        """Pick a picture with the normal file dialog (the same one the Image Editor uses)
        and drop it in the middle of the overlay."""
        self._toolbar.hide()
        self.releaseKeyboard()
        if IS_LINUX:
            # The overlay is a topmost window without a window manager, so a regular dialog
            # would open BEHIND it. Hide the overlay while the dialog is open (the same way
            # the screen-clip tool does) and bring it back afterwards.
            self.hide()
            QApplication.processEvents()
            dlg_parent = None
        else:
            # Windows / macOS: a dialog owned by the topmost overlay is always shown above it.
            dlg_parent = self
        try:
            path, _ = QFileDialog.getOpenFileName(
                dlg_parent, "Import Image", getattr(self, 'import_dir', ""),
                "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        finally:
            if IS_LINUX:
                self.show()
                self.raise_()
                self._canvas.raise_()
        if path:
            self._place_imported_image(path)
        self._select_tool(self.TOOL_SELECT)
        self._toolbar.show()
        self._raise_toolbar()
        self.activateWindow(); self.setFocus()
        self.grabKeyboard()

    def _capture_with_annotations(self):
        """
        Capture button clicked: hide the toolbar, keep the canvas visible so
        annotations remain, let the user drag a selection rectangle on top of
        the current overlay, then composite annotations + screenshot and save.
        """
        self._toolbar.hide()
        self.releaseKeyboard()
        # Enter inline-selection mode: next mouse drag will define the capture rect
        self._inline_selecting = True
        self._inline_start = None
        self._inline_end   = None
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        # Temporarily switch to a neutral tool so drawing tools don't fire
        self._prev_tool = self._current_tool
        self._current_tool = "_capture_select"
        self.grabKeyboard()

    def _capture_current_monitor(self):
        """Capture the monitor under the cursor with canvas annotations.
        Hides toolbar + dim overlay, grabs via MSS (canvas still visible),
        then closes the overlay and notifies the main window."""
        self._toolbar.hide()
        self._hide_bg = True
        self.update()
        QApplication.processEvents()

        def _do():
            time.sleep(0.15)   # let toolbar + dim layer repaint disappear
            path = None
            try:
                cx, cy = QCursor.pos().x(), QCursor.pos().y()
                with mss.MSS() as sct:
                    target = sct.monitors[1]
                    for m in sct.monitors[1:]:
                        if (m["left"] <= cx < m["left"] + m["width"] and
                                m["top"] <= cy < m["top"] + m["height"]):
                            target = m
                            break
                    shot = sct.grab(target)
                    img  = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                for _w in QApplication.topLevelWidgets():
                    if hasattr(_w, "engine") and hasattr(_w.engine, "_save"):
                        path = _w.engine._save(img, "monitor")
                        break
            except Exception as ex:
                print(f"[PyshareX] capture_current_monitor error: {ex}")
            self._close_sig.emit()
            if path:
                for _w in QApplication.topLevelWidgets():
                    if hasattr(_w, "_notify_sig"):
                        _w._notify_sig.emit(path, None)
                        break

        threading.Thread(target=_do, daemon=True).start()

    def _capture_all_monitors(self):
        """Capture all monitors as one combined image with canvas annotations.
        Hides toolbar + dim overlay, grabs via MSS (canvas still visible),
        then closes the overlay and notifies the main window."""
        self._toolbar.hide()
        self._hide_bg = True
        self.update()
        QApplication.processEvents()

        def _do():
            time.sleep(0.15)   # let toolbar + dim layer repaint disappear
            path = None
            try:
                with mss.MSS() as sct:
                    shot = sct.grab(sct.monitors[0])  # index 0 = all monitors combined
                    img  = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                for _w in QApplication.topLevelWidgets():
                    if hasattr(_w, "engine") and hasattr(_w.engine, "_save"):
                        path = _w.engine._save(img, "fullscreen")
                        break
            except Exception as ex:
                print(f"[PyshareX] capture_all_monitors error: {ex}")
            self._close_sig.emit()
            if path:
                for _w in QApplication.topLevelWidgets():
                    if hasattr(_w, "_notify_sig"):
                        _w._notify_sig.emit(path, None)
                        break

        threading.Thread(target=_do, daemon=True).start()

    def _open_sub_selector(self):
        """Unused — kept for compatibility."""
        pass

    def _on_sub_selector_cancelled(self):
        """User pressed Escape — restore the annotation overlay."""
        self._sub_sel = None
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()
        self._toolbar.show()
        self._raise_toolbar()

    def _on_sub_region_selected(self, x, y, w, h):
        """
        Called with physical MSS coordinates after the user drew the selection.
        Composite the annotations over that region and save.
        """
        self._sub_sel = None

        if w < 5 or h < 5:
            self._on_sub_selector_cancelled()
            return

        # Convert physical MSS coords back to a logical global QRect
        # so _do_capture_global_rect can work with it.
        # We need the logical rect for rendering canvas annotations.
        # Strategy: find the Qt screen that owns (x,y) in physical space,
        # then convert physical→logical.
        logical_x, logical_y, logical_w, logical_h = x, y, w, h
        try:
            with mss.MSS() as sct:
                qt_screens = sorted(QApplication.screens(),
                                    key=lambda s: (s.geometry().x(), s.geometry().y()))
                mss_mons   = sorted(sct.monitors[1:],
                                    key=lambda m: (m["left"], m["top"]))
                for qt_scr, mss_mon in zip(qt_screens, mss_mons):
                    mon_x = mss_mon["left"]
                    mon_y = mss_mon["top"]
                    mon_w = mss_mon["width"]
                    mon_h = mss_mon["height"]
                    if (mon_x <= x < mon_x + mon_w and
                            mon_y <= y < mon_y + mon_h):
                        dpr = qt_scr.devicePixelRatio()
                        lg  = qt_scr.geometry()
                        off_phys_x = x - mon_x
                        off_phys_y = y - mon_y
                        logical_x = lg.x() + int(off_phys_x / dpr)
                        logical_y = lg.y() + int(off_phys_y / dpr)
                        logical_w = int(w / dpr)
                        logical_h = int(h / dpr)
                        break
        except Exception as ex:
            print(f"[PyshareX] coord conversion error: {ex}")

        global_rect = QRect(logical_x, logical_y, logical_w, logical_h)

        # Now composite annotations + screenshot and save directly
        # (same logic as _emit_and_close but we have the rect already)
        self._do_capture_global_rect(global_rect)

    # ══════════════════════════════════════════════════════════════════════════
    #  Paint (background overlay only — annotations are drawn by _canvas)
    # ══════════════════════════════════════════════════════════════════════════

    def _selection_geometries(self):
        """Selection-like rectangles currently shown, in overlay-local coords:
        [(kind, QRect)] with kind 'detect' | 'inline' | 'clip' | 'drag'."""
        out = []
        if (self._current_tool == "_capture_select" and self._detect_windows_enabled
                and not self._inline_dragging and self._detected_rect.isValid()
                and self._detected_rect.width() > 0 and self._detected_rect.height() > 0):
            out.append(('detect', QRect(self._detected_rect)))
        if (self._current_tool == "_capture_select" and
                self._inline_start and self._inline_end):
            # _inline_start/end are in global Qt coords — convert to widget-local
            out.append(('inline', QRect(self.mapFromGlobal(self._inline_start),
                                        self.mapFromGlobal(self._inline_end)).normalized()))
        if (self._current_tool == self.TOOL_CLIP and
                self._clip_start is not None and self._clip_end is not None):
            out.append(('clip', QRect(self.mapFromGlobal(self._clip_start),
                                      self.mapFromGlobal(self._clip_end)).normalized()))
        if self._dragging and self._drag_start and self._drag_end:
            out.append(('drag', QRect(self._drag_start, self._drag_end).normalized()))
        return out

    def paintEvent(self, e):
        # The selection frames + size labels live on _sel_layer (above the annotation
        # canvas) so no annotation can cover them; this widget only dims the desktop and
        # cuts the near-transparent "holes" that keep the selected area clickable.
        layer = getattr(self, '_sel_layer', None)
        if layer is not None:
            layer.update()
        if self._hide_bg:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, _OVERLAY_DIM_ALPHA))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for kind, r in self._selection_geometries():
            if kind != 'detect':
                p.fillRect(r, QColor(0, 0, 0, 1))
        p.end()

    def _paint_selection_layer(self, p):
        """Frames and labels of every selection-like rectangle (drawn topmost)."""
        if self._hide_bg:
            return
        font = QFont("Consolas", 11, QFont.Weight.Bold)
        for kind, r in self._selection_geometries():
            if kind == 'detect':
                # Window / UI-element detection highlight — shown while hovering before a drag
                pen = QPen(QColor(0, 220, 130), 2, Qt.PenStyle.DashLine)
                pen.setDashPattern([6, 4])
                label = (f"{r.width()} × {r.height()} px — click to capture or hold LMB -> "
                         f"drag and release for a custom region capture")
                ty = r.y() - 6 if r.y() > 20 else r.bottom() + 14
            elif kind == 'inline':
                pen = QPen(QColor(0, 200, 255), 2)
                label = f"{r.width()} × {r.height()} px"
                ty = r.y() - 6 if r.y() > 20 else r.bottom() + 14
            elif kind == 'clip':
                pen = QPen(QColor(0, 200, 255), 2, Qt.PenStyle.DashLine)
                pen.setDashPattern([6, 4])
                label = f"{r.width()} × {r.height()} px — release to cut"
                ty = r.y() - 6 if r.y() > 20 else r.bottom() + 14
            else:   # 'drag'
                pen = QPen(QColor(0, 174, 255), 2)
                label = f"{r.width()} × {r.height()} px"
                ty = r.y() - 6 if r.y() > 20 else r.bottom() + 14
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.setPen(QColor(255, 255, 255))
            p.setFont(font)
            p.drawText(r.x() + 4, ty, label)

    # ══════════════════════════════════════════════════════════════════════════
    #  Mouse — DETECT / DRAG mode (only when canvas is transparent)
    # ══════════════════════════════════════════════════════════════════════════

    def _toolbar_contains(self, gpos: QPoint) -> bool:
        return QRect(self._toolbar.mapToGlobal(QPoint(0, 0)),
                     self._toolbar.size()).contains(gpos)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        gpos = e.globalPosition().toPoint()
        if self._toolbar_contains(gpos):
            return
        lpos = e.position().toPoint()

        # ── Inline capture-selection mode ─────────────────────────────────────
        if self._current_tool == "_capture_select":
            self._inline_start   = gpos   # global Qt coords — same as RegionSelector
            self._inline_end     = gpos
            self._inline_dragging = False   # becomes True only once the mouse actually moves
            return

        # ── DETECT mode ───────────────────────────────────────────────────────
        if self._current_tool == self.TOOL_DETECT:
            return

        # ── SELECT mode — route into QGraphicsScene for item picking ──────────
        if self._current_tool == self.TOOL_SELECT:
            scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))
            item, handle = self._canvas.get_handle_at(scene_pos)
            if item and handle:
                self._canvas.push_undo()
                self._resizing_item = item
                self._resize_handle = handle
                self._last_move_scene = scene_pos
                # Send a synthetic press at the item's position so the scene
                # registers it as the current mouse grabber — this ensures the
                # paired synthetic release in mouseReleaseEvent will correctly
                # clear the grabber rather than leaving it in a stale state.
                self._canvas.send_mouse_to_scene(e)
                return
            # If the press lands directly on an existing item (not empty
            # space), it's about to be dragged/moved — snapshot first so
            # that move can be undone. (If the click turns out to be a
            # no-op, e.g. just a select with no drag, this leaves a
            # harmless undo step that restores an identical state.)
            clicked_item = self._canvas._scene.itemAt(scene_pos, self._canvas.transform())
            if clicked_item is not None:
                self._canvas.push_undo()
            self._canvas.send_mouse_to_scene(e)
            return

        # ── Screen-clip tool: start dragging the cut rectangle ─────────────────
        if self._current_tool == self.TOOL_CLIP:
            self._clip_start = gpos
            self._clip_end = gpos
            self.update()
            return

        # ── Drawing tools ─────────────────────────────────────────────────────
        scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))
        self._draw_start_scene = scene_pos
        self._last_move_scene = scene_pos
        self._preview_item = None
        self._canvas.push_undo()

        if self._current_tool == self.TOOL_SMART_ERASER:
            # first click: pick the colour under the cursor
            self._eraser_color = _smart_eraser_sample(self._canvas._scene, scene_pos)

        if self._current_tool == self.TOOL_FREEHAND:
            self._canvas.begin_freehand(scene_pos, self._prefs.get('freehand', 'color'),
                                        self._prefs.get('freehand', 'width'))

        elif self._current_tool == self.TOOL_MARKER:
            self._canvas.add_marker(scene_pos, self._prefs.get('marker', 'bg'),
                                    self._prefs.get('marker', 'fg'),
                                    self._prefs.get('marker', 'scale'))

        elif self._current_tool == self.TOOL_TEXT:
            _tp = self._prefs.all('text')
            dlg = _TextInputDialog(_tp['color'], self, state=_tp)
            if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
                _store_dialog_in_prefs('text', dlg, self._prefs)
                txt, fsz, col, hl, hl_on, hl_pad, ol_on, ol_w, ol_col = dlg.result_data()
                if txt.strip():
                    item = self._canvas.add_text(scene_pos, txt, fsz, col, hl)
                    item.highlight_enabled = hl_on
                    item.highlight_padding = hl_pad
                    item.outline_enabled   = ol_on
                    item.outline_width     = ol_w
                    item.outline_color     = ol_col
            # After placing text, switch to SELECT so user can immediately move it
            self._select_tool(self.TOOL_SELECT)

        elif self._current_tool == self.TOOL_BUBBLE:
            dlg = _BubbleInputDialog(self._prefs.get('bubble', 'fg'), self,
                                     self._prefs.get('bubble', 'bg'))
            if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
                txt, fg_col, bg_col = dlg.result_data()
                self._prefs.update('bubble', fg=fg_col, bg=bg_col)
                if txt.strip():
                    self._canvas.add_bubble(scene_pos, txt, fg_col, bg_col)
            # After placing bubble, switch to SELECT so other tools work normally
            self._select_tool(self.TOOL_SELECT)

    def mouseMoveEvent(self, e):
        gpos = e.globalPosition().toPoint()
        lpos = e.position().toPoint()

        # Dynamically move the toolbar to the monitor where the cursor currently is,
        # but only if the user has not manually repositioned it via the drag handle.
        if not getattr(self._toolbar, '_user_dragged', False):
            cursor_screen = QApplication.screenAt(gpos)
            if cursor_screen:
                screen_geo = cursor_screen.geometry()
                tx = screen_geo.x() - self._geo.x() + screen_geo.width() // 2 - self._toolbar.width() // 2
                ty = screen_geo.y() - self._geo.y() + 8
                # Only move if the toolbar is on the wrong monitor (avoids micro-stutters)
                if abs(self._toolbar.x() - tx) > 100 or abs(self._toolbar.y() - ty) > 100:
                    self._toolbar.move(tx, ty)

        # ── Inline capture-selection mode ─────────────────────────────────────
        if self._current_tool == "_capture_select":
            if e.buttons() & Qt.MouseButton.LeftButton and self._inline_start is not None:
                self._inline_end = gpos   # global Qt coords — same as RegionSelector
                # Once the mouse has moved far enough from the press point,
                # this is a real drag — switch out of hover/detect mode and
                # into the normal manual rectangle selection (unchanged
                # behaviour from before this feature was added).
                if not self._inline_dragging:
                    moved = (gpos - self._inline_start).manhattanLength()
                    if moved > 4:
                        self._inline_dragging = True
                        self._detected_rect = QRect()
                self.update()
            elif self._detect_windows_enabled:
                # No button held: hovering — refresh the detected-window outline.
                self._update_detection_at_cursor()
            return

        # ── DETECT mode disabled
        if self._current_tool == self.TOOL_DETECT:
            return

        # ── SELECT mode — route into QGraphicsScene ───────────────────────────
        if self._current_tool == self.TOOL_SELECT:
            scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))

            # Handle Active Resizing
            if getattr(self, '_resizing_item', None) and getattr(self, '_resize_handle', None):
                # Shift held: move the item instead of resizing/rotating it
                last = getattr(self, '_last_move_scene', None)
                self._last_move_scene = scene_pos
                if (e.modifiers() & Qt.KeyboardModifier.ShiftModifier) and last is not None:
                    self._canvas.translate_item(self._resizing_item, scene_pos - last)
                    return
                proportional = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self._canvas.handle_resize(self._resizing_item, self._resize_handle, scene_pos,
                                           proportional,
                                           bool(e.modifiers() & Qt.KeyboardModifier.AltModifier))
                return

            # Handle Cursor Hover Updates for Handles
            _, handle = self._canvas.get_handle_at(scene_pos)
            if handle:
                if handle == 'ROTATE': self.setCursor(Qt.CursorShape.PointingHandCursor)
                elif handle == 'WIDTH': self.setCursor(Qt.CursorShape.SizeVerCursor)
                elif handle == 'RADIUS': self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif handle in ['TL', 'BR']: self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif handle in ['TR', 'BL']: self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                elif handle in ['L', 'R']: self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif handle in ['T', 'B']: self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

            self._canvas.send_mouse_to_scene(e)
            return

        # ── Screen-clip tool: grow the cut rectangle ───────────────────────────
        if self._current_tool == self.TOOL_CLIP:
            if (e.buttons() & Qt.MouseButton.LeftButton) and self._clip_start is not None:
                self._clip_end = gpos
                self.update()
            return

        # ── Drawing tools (LMB held) ──────────────────────────────────────────
        if not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))

        if self._current_tool == self.TOOL_FREEHAND:
            self._canvas.extend_freehand(scene_pos)

        elif self._current_tool in (self.TOOL_RECT, self.TOOL_CIRCLE, self.TOOL_LINE, self.TOOL_HIGHLIGHT, self.TOOL_ARROW,
                                    self.TOOL_PIXBLUR, self.TOOL_SPOTLIGHT, self.TOOL_SMART_ERASER):
            if self._draw_start_scene is None:
                return
            # Shift held: drag the whole shape (start point follows the mouse)
            last = getattr(self, '_last_move_scene', None)
            self._last_move_scene = scene_pos
            if (e.modifiers() & Qt.KeyboardModifier.ShiftModifier) and last is not None:
                self._draw_start_scene = self._draw_start_scene + (scene_pos - last)
            end_scene = scene_pos
            # Ctrl held: constrain rect/circle to equal width and height (perfect square/circle)
            if (self._current_tool in (self.TOOL_RECT, self.TOOL_CIRCLE, self.TOOL_PIXBLUR,
                                         self.TOOL_SPOTLIGHT, self.TOOL_SMART_ERASER) and
                    e.modifiers() & Qt.KeyboardModifier.ControlModifier):
                dx = end_scene.x() - self._draw_start_scene.x()
                dy = end_scene.y() - self._draw_start_scene.y()
                side = min(abs(dx), abs(dy))
                end_scene = QPointF(
                    self._draw_start_scene.x() + math.copysign(side, dx),
                    self._draw_start_scene.y() + math.copysign(side, dy))
            r = QRectF(self._draw_start_scene, end_scene).normalized()
            # Alt held: the start point is the CENTER of the shape (works with Ctrl too)
            if (self._current_tool in (self.TOOL_RECT, self.TOOL_CIRCLE, self.TOOL_HIGHLIGHT,
                                       self.TOOL_PIXBLUR, self.TOOL_SPOTLIGHT, self.TOOL_SMART_ERASER) and
                    e.modifiers() & Qt.KeyboardModifier.AltModifier):
                c = self._draw_start_scene
                r = QRectF(c.x() - r.width(), c.y() - r.height(),
                           r.width() * 2, r.height() * 2)
            if self._preview_item is not None:
                try:
                    if self._preview_item.scene() is self._canvas._scene:
                        self._canvas._scene.removeItem(self._preview_item)
                except RuntimeError:
                    pass                      # element już usunięty (np. przez undo)
                self._preview_item = None
            if self._current_tool == self.TOOL_RECT:
                self._preview_item = self._canvas.add_rect(
                    r, self._prefs.get('rect', 'color'), self._prefs.get('rect', 'width'),
                    self._prefs.get('rect', 'radius'),
                    _prefs_fill_color(self._prefs, 'rect') if self._prefs.get('rect', 'fill') else None)
            elif self._current_tool == self.TOOL_HIGHLIGHT:
                self._preview_item = self._canvas.add_highlight(r, self._prefs.get('highlight', 'color'))
            elif self._current_tool == self.TOOL_SMART_ERASER:
                self._preview_item = self._canvas.add_smart_eraser(
                    r, getattr(self, '_eraser_color', QColor(128, 128, 128)))
            elif self._current_tool == self.TOOL_SPOTLIGHT:
                self._preview_item = self._canvas.add_spotlight(
                    r, self._prefs.get('spotlight', 'blur'),
                    self._prefs.get('spotlight', 'ol_on'),
                    self._prefs.get('spotlight', 'ol_w'),
                    self._prefs.get('spotlight', 'ol'),
                    self._prefs.get('spotlight', 'defer'))
            elif self._current_tool == self.TOOL_PIXBLUR:
                self._preview_item = self._canvas.add_pixelate_blur(
                    r, self._prefs.get('pixelate_blur', 'mode'),
                    self._prefs.get('pixelate_blur', 'pixel'),
                    self._prefs.get('pixelate_blur', 'blur'))
            elif self._current_tool == self.TOOL_CIRCLE:
                self._preview_item = self._canvas.add_ellipse(
                    r, self._prefs.get('circle', 'color'), self._prefs.get('circle', 'width'),
                    _prefs_fill_color(self._prefs, 'circle') if self._prefs.get('circle', 'fill') else None)
            elif self._current_tool == self.TOOL_LINE:
                end_pos = scene_pos
                if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    dx = end_pos.x() - self._draw_start_scene.x()
                    dy = end_pos.y() - self._draw_start_scene.y()
                    angle = math.atan2(dy, dx)
                    snapped = round(math.degrees(angle) / 45) * 45
                    dist = math.hypot(dx, dy)
                    end_pos = QPointF(
                        self._draw_start_scene.x() + dist * math.cos(math.radians(snapped)),
                        self._draw_start_scene.y() + dist * math.sin(math.radians(snapped)))
                self._preview_item = self._canvas.add_line(
                    QLineF(self._alt_line_start(end_pos, e), end_pos),
                    self._prefs.get('line', 'color'), self._prefs.get('line', 'width'))
            elif self._current_tool == self.TOOL_ARROW:
                end_pos = scene_pos
                if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    dx = end_pos.x() - self._draw_start_scene.x()
                    dy = end_pos.y() - self._draw_start_scene.y()
                    angle = math.atan2(dy, dx)
                    snapped = round(math.degrees(angle) / 45) * 45
                    dist = math.hypot(dx, dy)
                    end_pos = QPointF(
                        self._draw_start_scene.x() + dist * math.cos(math.radians(snapped)),
                        self._draw_start_scene.y() + dist * math.sin(math.radians(snapped)))
                self._preview_item = self._canvas.add_arrow(
                    QLineF(self._alt_line_start(end_pos, e), end_pos),
                    self._prefs.get('arrow', 'color'), self._prefs.get('arrow', 'width'),
                    self._prefs.get('arrow', 'head'))

    def _alt_line_start(self, end_pos, e):
        """Line/arrow start point: the press point, or (Alt held) its mirror
        across the press point so the press point becomes the line's center."""
        s = self._draw_start_scene
        if e.modifiers() & Qt.KeyboardModifier.AltModifier:
            return QPointF(2 * s.x() - end_pos.x(), 2 * s.y() - end_pos.y())
        return s

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        # Reset WIDTH drag state on any selected ResizableRect/Ellipse
        for item in self._canvas._scene.selectedItems():
            if hasattr(item, '_width_drag_active'):
                item._width_drag_active = False
            if hasattr(item, '_radius_drag_off'):
                item._radius_drag_off = None
        lpos = e.position().toPoint()

        # ── Inline capture-selection mode — finalize rect ─────────────────────
        if self._current_tool == "_capture_select":
            # A plain click (no real drag) on a detected window/element:
            # capture that rect immediately (ShareX-style window detection).
            if (self._detect_windows_enabled and not self._inline_dragging and
                    self._detected_rect.isValid() and
                    self._detected_rect.width() > 4 and self._detected_rect.height() > 4):
                global_rect = self._detected_rect.translated(self._geo.topLeft())
                self._current_tool = self._prev_tool
                self._inline_selecting = False
                self._inline_start = None
                self._inline_end = None
                self._inline_dragging = False
                self._detected_rect = QRect()
                self._do_capture_global_rect(global_rect)
                return
            if self._inline_start and self._inline_end:
                global_rect = QRect(self._inline_start, self._inline_end).normalized()
                if global_rect.width() > 4 and global_rect.height() > 4:
                    self._current_tool = self._prev_tool
                    self._inline_selecting = False
                    self._inline_start = None
                    self._inline_end = None
                    self._inline_dragging = False
                    self._do_capture_global_rect(global_rect)
                    return
            # Selection too small — cancel and restore toolbar
            self._current_tool = self._prev_tool
            self._inline_selecting = False
            self._inline_start = None
            self._inline_end = None
            self._inline_dragging = False
            self._toolbar.show()
            self._raise_toolbar()
            self.activateWindow()
            self.setFocus()
            self.grabKeyboard()
            return

        # DETECT mode disabled
        if self._current_tool == self.TOOL_DETECT:
            return

        # ── SELECT mode ───────────────────────────────────────────────────────
        if self._current_tool == self.TOOL_SELECT:
            if getattr(self, '_resizing_item', None):
                item = self._resizing_item
                self._resizing_item = None
                self._resize_handle = None
                # Clear any width-drag state on the item
                if hasattr(item, '_width_drag_active'):
                    item._width_drag_active = False
                # Clear active_handle on Line/Arrow items (safety)
                if hasattr(item, 'active_handle'):
                    item.active_handle = None
                # CRITICAL: send a synthetic release to the QGraphicsScene so it
                # clears its internal mouse-grabber state. Without this, the scene
                # keeps routing all future send_mouse_to_scene calls to the old
                # grabbed item, making every subsequently placed object uneditable.
                self._canvas.send_mouse_to_scene(e)
                return
            self._canvas.send_mouse_to_scene(e)
            return

        # ── Screen-clip tool: cut the piece out and drop it on the canvas ──────
        if self._current_tool == self.TOOL_CLIP:
            if self._clip_start is not None and self._clip_end is not None:
                r = QRect(self._clip_start, self._clip_end).normalized()
                self._clip_start = self._clip_end = None
                self.update()
                if r.width() > 4 and r.height() > 4:
                    self._clip_screen_region(r)
            return

        # ── Draw tools — finalise ─────────────────────────────────────────────
        _pv = self._preview_item
        if isinstance(_pv, PixelateBlurItem) and (_pv.rect().width() < 3 or _pv.rect().height() < 3):
            self._canvas._scene.removeItem(_pv)     # a plain click, not a drag
        if self._current_tool == self.TOOL_FREEHAND:
            self._canvas.end_freehand()
        self._preview_item     = None
        self._draw_start_scene = None

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        # Allow editing text if we are in SELECT mode
        if self._current_tool == self.TOOL_SELECT:
            lpos = e.position().toPoint()
            scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))
            item = self._canvas._scene.itemAt(scene_pos, self._canvas.transform())
            
            if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
                self._edit_text(item)

            elif isinstance(item, HighlightRectItem):
                self._edit_highlight(item)

            elif isinstance(item, PixelateBlurItem):
                self._open_item_edit_dialog(item)

            elif isinstance(item, TextBubbleItem):
                self._edit_bubble(item)

            elif isinstance(item, _MarkerItem):
                self._edit_marker(item)

            elif isinstance(item, FreehandItem):
                self._edit_freehand(item)

            elif isinstance(item, ArrowItem):
                self._edit_arrow(item)

            elif isinstance(item, (ResizableRectItem, ResizableEllipseItem, LineItem)):
                self._open_item_edit_dialog(item)

    def _edit_arrow(self, item):
        """Arrow options window (live preview) — see _open_item_edit_dialog."""
        self._open_item_edit_dialog(item)

    def _straighten_arrow(self, item):
        """Reset the bend/break point so the arrow is a straight line again."""
        self._canvas.push_undo()
        item.prepareGeometryChange()
        item._bend = QPointF(0.0, 0.0)
        item.update()

    def _edit_freehand(self, item):   self._open_item_edit_dialog(item)
    def _edit_text(self, item):       self._open_item_edit_dialog(item)
    def _edit_bubble(self, item):     self._open_item_edit_dialog(item)
    def _edit_marker(self, item):     self._open_item_edit_dialog(item)
    def _edit_highlight(self, item):  self._open_item_edit_dialog(item)

    def contextMenuEvent(self, e):
        lpos = e.pos()
        scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))
        item = self._canvas._scene.itemAt(scene_pos, self._canvas.transform())
        if item is not None:
            menu = QMenu(self)
            is_marker = isinstance(item, _MarkerItem)
            if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
                edit_act = menu.addAction("✏️ Edit Text")
                dup_act  = menu.addAction("⧉ Duplicate")
                del_act  = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action == edit_act:
                    self._edit_text(item)
                elif action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            elif isinstance(item, TextBubbleItem):
                edit_act = menu.addAction("✏️ Edit")
                dup_act  = menu.addAction("⧉ Duplicate")
                del_act  = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action == edit_act:
                    self._edit_bubble(item)
                elif action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            elif isinstance(item, _MarkerItem):
                edit_act = menu.addAction("✏️ Edit")
                del_act  = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action == edit_act:
                    self._edit_marker(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            elif isinstance(item, HighlightRectItem):
                edit_act = menu.addAction("✏️ Edit Highlight")
                dup_act  = menu.addAction("⧉ Duplicate")
                del_act  = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action == edit_act:
                    self._edit_highlight(item)
                elif action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            elif isinstance(item, SmartEraserItem):
                dup_act  = menu.addAction("⧉ Duplicate")
                del_act  = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            elif isinstance(item, SpotlightItem):
                edit_act = menu.addAction("✏️ Edit Spotlight")
                dup_act  = menu.addAction("⧉ Duplicate")
                del_act  = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action == edit_act:
                    self._open_item_edit_dialog(item)
                elif action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            elif isinstance(item, PixelateBlurItem):
                pix_act, blur_act = _pixblur_add_mode_actions(menu, item)
                edit_act = menu.addAction("✏️ Edit Pixelate / Blur")
                dup_act  = menu.addAction("⧉ Duplicate")
                del_act  = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action in (pix_act, blur_act):
                    self._canvas.push_undo()
                    _pixblur_set_mode(item, PixelateBlurItem.MODE_PIXELATE if action == pix_act
                                      else PixelateBlurItem.MODE_BLUR, self._prefs)
                elif action == edit_act:
                    self._open_item_edit_dialog(item)
                elif action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            elif isinstance(item, FreehandItem):
                edit_act = menu.addAction("✏️ Edit Freehand")
                dup_act  = menu.addAction("⧉ Duplicate")
                del_act  = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action == edit_act:
                    self._edit_freehand(item)
                elif action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            elif isinstance(item, ArrowItem):
                edit_act = menu.addAction("✏️ Edit Arrow")
                straighten_act = menu.addAction("📏 Straighten Arrow")
                dup_act  = menu.addAction("⧉ Duplicate")
                del_act  = menu.addAction("🗑️ Delete")
                straighten_act.setEnabled(getattr(item, '_bend', QPointF(0.0, 0.0)) != QPointF(0.0, 0.0))
                action = menu.exec(e.globalPos())
                if action == edit_act:
                    self._edit_arrow(item)
                elif action == straighten_act:
                    self._straighten_arrow(item)
                elif action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)
            else:
                edit_act = (menu.addAction("✏️ Edit")
                            if _prefs_key_for_item(item) in ('rect', 'circle', 'line') else None)
                dup_act = menu.addAction("⧉ Duplicate")
                del_act = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if edit_act is not None and action == edit_act:
                    self._open_item_edit_dialog(item)
                elif action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas.push_undo()
                    self._canvas._scene.removeItem(item)

    def _duplicate_item_beside(self, item):
        """Duplicate a single item and place it slightly offset beside the original."""
        dup = self._clone_item(item)
        if dup is None:
            return
        self._canvas.push_undo()
        offset = QPointF(20, 20)
        dup.setPos(item.scenePos() + offset)
        self._canvas._scene.addItem(dup)
        for it in self._canvas._scene.selectedItems():
            it.setSelected(False)
        dup.setSelected(True)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._stop_esc_listener()
            self.releaseKeyboard()
            self.close()
            self.cancelled.emit()
            return
        if (e.key() == Qt.Key.Key_Z and
                e.modifiers() & Qt.KeyboardModifier.ControlModifier and
                not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
            self.canvas_undo()
            e.accept()
            return
        if ((e.key() == Qt.Key.Key_Y and
             e.modifiers() & Qt.KeyboardModifier.ControlModifier) or
            (e.key() == Qt.Key.Key_Z and
             e.modifiers() & Qt.KeyboardModifier.ControlModifier and
             e.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
            self.canvas_redo()
            e.accept()
            return
        if (e.key() == Qt.Key.Key_D and
                e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._duplicate_selected_at_cursor()
            e.accept()
            return
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            selected = self._canvas._scene.selectedItems()
            if selected:
                self._canvas.push_undo()
                for item in selected:
                    self._canvas._scene.removeItem(item)
                e.accept()
                return
        super().keyPressEvent(e)

    def _abort_inprogress_draw(self) -> bool:
        """Undo/Redo wciśnięte, gdy rysowanie trwa (przycisk myszy wciąż trzymany):
        undo usuwało wtedy podgląd ze sceny, a obsługa myszy dalej na nim operowała
        (stary, odłączony element -> błędy sceny, "duchy" po puszczeniu przycisku).
        Zamiast tego anulujemy bieżący rysunek i zdejmujemy wpis undo z chwili
        naciśnięcia. Zwraca True, jeśli coś anulowano."""
        if getattr(self, "_draw_start_scene", None) is None and self._preview_item is None:
            return False
        sc = self._canvas._scene
        pv = self._preview_item
        if pv is not None:
            try:
                if pv.scene() is sc:
                    sc.removeItem(pv)
            except RuntimeError:
                pass
        cur = getattr(self._canvas, "_current_freehand_item", None)
        if cur is not None:
            try:
                if cur.scene() is sc:
                    sc.removeItem(cur)
            except RuntimeError:
                pass
            self._canvas._current_freehand_item = None
            self._canvas._freehand_path = None
        self._preview_item = None
        self._draw_start_scene = None
        if self._canvas._undo_stack:
            self._canvas._undo_stack.pop()
        self._canvas._notify_history_changed()
        return True

    def canvas_undo(self):
        """Undo last annotation action (keyboard shortcut / toolbar helper)."""
        if self._abort_inprogress_draw():
            return
        if hasattr(self._canvas, 'undo'):
            self._canvas.undo()

    def canvas_redo(self):
        """Redo last undone annotation action (keyboard shortcut / toolbar helper)."""
        if self._abort_inprogress_draw():
            return
        if hasattr(self._canvas, 'redo'):
            self._canvas.redo()

    def _update_undo_redo_buttons(self):
        """Enable/disable the toolbar Undo/Redo buttons to match the
        canvas's history. Called by _OverlayCanvas whenever its undo/redo
        stacks change (push, undo, redo)."""
        if hasattr(self, 'btn_undo'):
            self.btn_undo.setEnabled(self._canvas.can_undo())
        if hasattr(self, 'btn_redo'):
            self.btn_redo.setEnabled(self._canvas.can_redo())

    def _duplicate_selected_at_cursor(self):
        """Duplicate selected annotation items centred exactly at the cursor position."""
        selected = self._canvas._scene.selectedItems()
        if not selected:
            return
        cursor_global = QCursor.pos()
        # Convert global → local widget coords → scene coords
        local = self.mapFromGlobal(cursor_global)
        cursor_scene = self._canvas.mapToScene(self._canvas.mapFrom(self, local))

        self._canvas.push_undo()
        new_items = []
        for item in selected:
            # Never duplicate Numbered Markers
            if isinstance(item, _MarkerItem):
                continue
            dup = self._clone_item(item)
            if dup is None:
                continue
            # Use the visual rect centre (not boundingRect which has large margins)
            # to position the duplicate exactly under the cursor.
            try:
                item_center_scene = item.mapToScene(item.rect().center())
            except AttributeError:
                try:
                    ln = item.line()
                    item_center_scene = item.mapToScene(
                        QPointF((ln.x1() + ln.x2()) / 2, (ln.y1() + ln.y2()) / 2))
                except AttributeError:
                    item_center_scene = item.scenePos()
            offset = item_center_scene - item.scenePos()
            dup.setPos(cursor_scene - offset)
            self._canvas._scene.addItem(dup)
            new_items.append(dup)

        # Select only the new duplicates
        for item in self._canvas._scene.selectedItems():
            item.setSelected(False)
        for item in new_items:
            item.setSelected(True)

    def _clone_item(self, item):
        """Return a deep copy of a supported annotation item."""
        if isinstance(item, PixelateBlurItem):
            return _clone_pixblur(item)
        if isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
            cls = type(item)
            dup = cls()
            dup.setRect(QRectF(item.rect()))
            dup.setPen(QPen(item.pen()))
            dup.setBrush(QBrush(item.brush()))
            if isinstance(item, ResizableRectItem):
                dup.corner_radius = float(getattr(item, 'corner_radius', 0.0))
            dup.setRotation(item.rotation())
            dup.setTransformOriginPoint(item.transformOriginPoint())
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, HighlightRectItem):
            dup = HighlightRectItem()
            dup.setRect(QRectF(item.rect()))
            dup._color = QColor(item._color)
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, FreehandItem):
            dup = FreehandItem(QPainterPath(item.path()))
            dup.setPen(QPen(item.pen()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, LineItem):
            dup = LineItem(QLineF(item.line()), self._canvas)
            dup.setPen(QPen(item.pen()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, ArrowItem):
            dup = ArrowItem(QLineF(item.line()), self._canvas)
            dup.setPen(QPen(item.pen()))
            dup.head_style = getattr(item, 'head_style', ArrowItem.HEAD_SINGLE)
            dup._bend = QPointF(getattr(item, '_bend', QPointF(0.0, 0.0)))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, TextBubbleItem):
            dup = TextBubbleItem(item._text, QColor(item._fg_color), QColor(item._bg_color))
            dup._w = item._w
            dup._h = item._h
            dup._cone_rel = QPointF(item._cone_rel)
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, HighlightTextItem):
            dup = HighlightTextItem(item.toPlainText())
            dup.setFont(item.font())
            dup.setDefaultTextColor(item.defaultTextColor())
            dup.highlight_color   = QColor(getattr(item, 'highlight_color', QColor(0, 0, 0, 0)))
            dup.highlight_enabled = getattr(item, 'highlight_enabled', True)
            dup.highlight_padding = getattr(item, 'highlight_padding', 0)
            dup.outline_enabled   = getattr(item, 'outline_enabled', False)
            dup.outline_width     = getattr(item, 'outline_width', 2)
            dup.outline_color     = QColor(getattr(item, 'outline_color', QColor(0, 0, 0)))
            dup.setFont(QFont(item.font()))
            dup.setDefaultTextColor(QColor(item.defaultTextColor()))
            dup.highlight_color = QColor(getattr(item, 'highlight_color', QColor(0, 0, 0, 0)))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, ResizablePixmapItem):
            dup = ResizablePixmapItem(QPixmap(item.pixmap))
            dup.setRect(QRectF(item.rect()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        return None

    def closeEvent(self, e):
        self._stop_esc_listener()
        if _is_wayland_session():
            _wl_unfreeze()
        try:
            self._cache_timer.stop()
        except Exception:
            pass
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        super().closeEvent(e)

    # ══════════════════════════════════════════════════════════════════════════
    #  Capture
    # ══════════════════════════════════════════════════════════════════════════

    def _open_region_selector(self):
        """
        Hide the annotation overlay and open the classic RegionSelector crosshair.
        After the user draws a selection, composite annotations on top and save.
        """
        self._toolbar.hide()
        self.releaseKeyboard()
        self.hide()
        QTimer.singleShot(180, self._launch_region_selector)

    def _launch_region_selector(self):
        self._sub_sel = RegionSelector()
        self._sub_sel.region_selected.connect(self._on_sub_region_selected)
        self._sub_sel.cancelled.connect(self._on_sub_selector_cancelled)

    def _on_sub_selector_cancelled(self):
        """User pressed Escape — restore the annotation overlay."""
        self._sub_sel = None
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()
        self._toolbar.show()
        self._raise_toolbar()

    def _on_sub_region_selected(self, x, y, w, h):
        """
        Called with physical MSS coordinates after the user drew the selection.
        Convert to logical Qt coords, then composite annotations and save.
        """
        self._sub_sel = None
        if w < 5 or h < 5:
            self._on_sub_selector_cancelled()
            return

        # Convert physical MSS coords → logical global Qt rect
        logical_x, logical_y, logical_w, logical_h = x, y, w, h
        try:
            with mss.MSS() as sct:
                qt_screens = sorted(QApplication.screens(),
                                    key=lambda s: (s.geometry().x(), s.geometry().y()))
                mss_mons   = sorted(sct.monitors[1:],
                                    key=lambda m: (m["left"], m["top"]))
                for qt_scr, mss_mon in zip(qt_screens, mss_mons):
                    mon_x, mon_y = mss_mon["left"], mss_mon["top"]
                    mon_w, mon_h = mss_mon["width"], mss_mon["height"]
                    if mon_x <= x < mon_x + mon_w and mon_y <= y < mon_y + mon_h:
                        dpr = qt_scr.devicePixelRatio()
                        lg  = qt_scr.geometry()
                        logical_x = lg.x() + int((x - mon_x) / dpr)
                        logical_y = lg.y() + int((y - mon_y) / dpr)
                        logical_w = int(w / dpr)
                        logical_h = int(h / dpr)
                        break
        except Exception as ex:
            print(f"[PyshareX] coord conversion error: {ex}")

        global_rect = QRect(logical_x, logical_y, logical_w, logical_h)
        self._do_capture_global_rect(global_rect)

    def _do_capture_global_rect(self, global_rect: QRect):
        """
        Hide toolbar and dark background, but KEEP the canvas visible so
        ffmpeg captures the screen with annotation objects still showing.
        The overlay itself stays open until the snapshot thread finishes.
        """
        self._last_detected_global = global_rect
        self._toolbar.hide()
        self._hide_bg = True   # hides only the dark dimming layer (paintEvent)
        self.update()
        QApplication.processEvents()
        # Small delay so the toolbar and dim-overlay repaint clears before
        # ffmpeg opens the screen grabber.
        QTimer.singleShot(150, lambda: self._grab_composite_and_close(global_rect))

    def _grab_composite_and_close(self, global_rect: QRect):
        """
        Launch ffmpeg snapshot thread — canvas stays visible so objects are
        captured by ffmpeg.  The recording border shows during the grab.
        Everything runs async via Qt signals; the main thread is never blocked.
        """
        # ── 1. Compute physical coords ────────────────────────────────────────
        # The selection's top-left and bottom-right corners are converted to
        # physical (mss) desktop pixels SEPARATELY, each using the DPI scale
        # of whichever monitor actually owns that corner. A single shared
        # ratio (e.g. always the start monitor's) would misjudge the width/
        # height whenever the selection's other end sits on a differently
        # scaled monitor — the region would come out too narrow/short (if
        # the far monitor is scaled higher than the start one) or too wide/
        # tall (if scaled lower), even though the start corner itself was
        # positioned correctly.
        start_screen = (QApplication.screenAt(global_rect.topLeft())
                         or QApplication.screenAt(global_rect.center())
                         or QApplication.primaryScreen())
        end_screen = (QApplication.screenAt(global_rect.bottomRight())
                      or start_screen)

        final_x, final_y = phys_x, phys_y = global_rect.x(), global_rect.y()
        phys_w = max(1, global_rect.width())
        phys_h = max(1, global_rect.height())

        try:
            with mss.MSS() as sct:
                qt_screens = sorted(QApplication.screens(),
                                    key=lambda s: (s.geometry().x(), s.geometry().y()))
                mss_mons   = sorted(sct.monitors[1:],
                                    key=lambda m: (m["left"], m["top"]))

                def _mon_for(scr):
                    if scr in qt_screens:
                        idx = qt_screens.index(scr)
                        if idx < len(mss_mons):
                            return mss_mons[idx]
                    return {"left": 0, "top": 0}

                def _to_phys(x, y, scr):
                    """Logical Qt point -> absolute physical desktop pixel,
                    using scr's own DPI scale and mss monitor offset."""
                    r   = scr.devicePixelRatio() or 1.0
                    geo = scr.geometry()
                    mon = _mon_for(scr)
                    return (mon["left"] + round((x - geo.x()) * r),
                            mon["top"]  + round((y - geo.y()) * r))

                final_x, final_y = _to_phys(global_rect.x(), global_rect.y(), start_screen)
                end_x, end_y = _to_phys(global_rect.x() + global_rect.width(),
                                        global_rect.y() + global_rect.height(), end_screen)
                phys_w = max(1, end_x - final_x)
                phys_h = max(1, end_y - final_y)
        except Exception as ex:
            print(f"[PyshareX] monitor mapping error: {ex}")

        # ── 2. Build output path ──────────────────────────────────────────────
        snap_path = None
        engine_ref = None
        try:
            for _w in QApplication.topLevelWidgets():
                if hasattr(_w, "engine") and hasattr(_w.engine, "_fp"):
                    engine_ref = _w.engine
                    break
            if engine_ref:
                snap_path = engine_ref._fp("region")
            else:
                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                snap_path = str(Path(tempfile.gettempdir()) / f"region_{ts}.png")
        except Exception as ex:
            print(f"[PyshareX] snap path error: {ex}")
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            snap_path = str(Path(tempfile.gettempdir()) / f"region_{ts}.png")
        snap_path = str(Path(snap_path).with_suffix(".png"))

        # ── 2b. Which capture engine to use? ────────────────────────────────
        # "ffmpeg"   — original method (gdigrab/x11grab)
        # "standard" — new ffmpeg-free method (mss + PIL), DPI-aware across
        #              monitors with different scaling. Chosen in
        #              Settings → Capture region method.
        capture_method = "ffmpeg"
        try:
            if engine_ref is not None:
                capture_method = engine_ref.config.get("region_capture_method", "standard")
        except Exception:
            pass
        if _is_wayland_session():
            capture_method = "standard"   # x11grab na Waylandzie zwraca czerń

        # ── 3. No pre-render needed — the screen is grabbed directly, so any
        #      canvas/annotation objects still on screen are captured as-is ──

        # ── 4. Launch the snapshot thread — canvas visible during the grab ────
        # RecordingBorder (green dashed frame) is shown via region_ready signal.
        # The overlay is NOT hidden so canvas objects appear in the frame.
        # After the thread finishes, _on_snap_done / _on_snap_err fires on the
        # main thread via Qt signal, then _finish_capture() completes the flow.
        border_ref = [None]

        def _on_snap_region(x, y, w, h):
            # The recording border is only useful for the ffmpeg method,
            # where the external process needs a moment to grab the frame.
            # The standard (mss) method finishes essentially instantly, so
            # showing the border there would just be an unnecessary flash.
            if capture_method != "standard":
                border_ref[0] = RecordingBorder(x, y, w, h)

        def _on_snap_done(path):
            if border_ref[0]:
                border_ref[0].stop()
                border_ref[0] = None
            _finish_capture(path, None)

        def _on_snap_err(msg):
            if border_ref[0]:
                border_ref[0].stop()
                border_ref[0] = None
            print(f"[PyshareX] {capture_method} snapshot failed ({msg}), falling back to plain MSS")
            # Last-resort MSS fallback — hide overlay first, grab, then finish
            self.hide()
            QApplication.processEvents()
            try:
                with mss.MSS() as sct:
                    shot = sct.grab({"left": final_x, "top": final_y,
                                     "width": phys_w, "height": phys_h})
                    fallback_img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    fallback_img.save(snap_path, "PNG")
                    _finish_capture(snap_path, None)
            except Exception as ex2:
                print(f"[PyshareX] mss grab error: {ex2}")
                _finish_capture(None, None)

        def _finish_capture(path, _unused):
            """Called on the main Qt thread after ffmpeg (or MSS fallback) finishes.
            ffmpeg already captured the screen including all canvas objects, so
            no compositing is needed — the PNG at `path` is the final image."""
            # Release the thread reference — it has finished its work
            self._snap_th = None

            # ── 5. Save via engine._post (clipboard, explorer, etc.) ─────────
            saved_path = None
            if path and Path(path).exists():
                try:
                    engine = None
                    for _w in QApplication.topLevelWidgets():
                        if hasattr(_w, "engine") and hasattr(_w.engine, "_post"):
                            engine = _w.engine
                            break
                    if engine:
                        saved_path = engine._post(path)
                    else:
                        saved_path = path   # no engine — use path as-is
                except Exception as ex:
                    print(f"[PyshareX] save error: {ex}")

            # ── 6. Close overlay ──────────────────────────────────────────────
            self.close()

            # ── 7. Notify main window ─────────────────────────────────────────
            if saved_path:
                try:
                    for _w in QApplication.topLevelWidgets():
                        if hasattr(_w, "_notify_sig"):
                            thumb = None
                            try:
                                pil_img  = Image.open(saved_path).convert("RGB")
                                raw_data = pil_img.tobytes()
                                qim  = QImage(raw_data,
                                              pil_img.width, pil_img.height,
                                              pil_img.width * 3,
                                              QImage.Format.Format_RGB888).copy()
                                thumb = QPixmap.fromImage(qim)
                            except Exception:
                                pass
                            _w._notify_sig.emit(saved_path, thumb)
                            if getattr(_w, '_win_was_visible', True):
                                QTimer.singleShot(300, _w.show_win)
                            break
                except Exception as ex:
                    print(f"[PyshareX] notify error: {ex}")
            else:
                try:
                    self.region_selected.emit(final_x, final_y, phys_w, phys_h)
                except Exception:
                    pass

        # Keep a strong reference on self so the QThread is not garbage-collected
        # before it finishes.  _finish_capture() clears it once done.
        if capture_method == "standard":
            self._snap_th = StandardRegionSnapshotThread(
                region      = (final_x, final_y, phys_w, phys_h),
                output_path = snap_path,
                overlay_rgba = self._wl_render_annotations() if _is_wayland_session() else None,
                overlay_origin = (self._geo.x(), self._geo.y()),
            )
        else:
            self._snap_th = RecordingThread(
                region        = (final_x, final_y, phys_w, phys_h),
                output_path   = snap_path,
                fps           = 1,
                audio         = False,
                snapshot_mode = True,
            )
        self._snap_th.region_ready.connect(_on_snap_region)
        self._snap_th.finished.connect(_on_snap_done)
        self._snap_th.error.connect(_on_snap_err)
        self._snap_th.start()

    def _wl_render_annotations(self):
        """Wayland: zrzut pochodzi z zamrożonej klatki, więc adnotacje z płótna
        (strzałki, prostokąty, tekst) trzeba dokleić osobno. Zwraca PIL RGBA o
        rozmiarze nakładki (układ lokalny) albo None, gdy nie ma adnotacji."""
        try:
            scene = self._canvas._scene
            if not scene.items():
                return None
            scene.clearSelection()
            w, h = self.width(), self.height()
            qi = QImage(w, h, QImage.Format.Format_RGBA8888)
            qi.fill(Qt.GlobalColor.transparent)
            p = QPainter(qi)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            scene.render(p, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
            p.end()
            return Image.frombytes("RGBA", (w, h), bytes(qi.constBits()))
        except Exception as ex:
            print(f"[PyshareX][wayland] render adnotacji nie powiódł się: {ex}")
            return None

    def _emit_and_close(self, global_rect: QRect):
        """Kept for compatibility — delegates to _grab_composite_and_close."""
        self._grab_composite_and_close(global_rect)


def _month_folder(base_folder) -> Path:
    """Return the subfolder for the current year/month (e.g. '.../PyshareX/2026-08'),
    creating it (and its parent) if needed. All captures are organised this way so a
    new folder appears automatically at the start of every month."""
    f = Path(base_folder) / datetime.now().strftime("%Y-%m")
    f.mkdir(parents=True, exist_ok=True)
    return f


# ─────────────────────────────────────────────
#  CAPTURE ENGINE
# ─────────────────────────────────────────────

class CaptureEngine:
    def __init__(self, config: Config):
        self.config = config

    def _folder(self):
        return _month_folder(self.config.get("save_folder"))

    def _fp(self, prefix):
        ext = self.config.get("image_format", "png")
        ts  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return str(self._folder() / f"{prefix}_{ts}.{ext}")

    def _save(self, img, prefix) -> str:
        fp  = self._fp(prefix)
        fmt = self.config.get("image_format", "png").upper()
        kw  = {}
        if fmt in ("JPG", "JPEG"):
            fmt = "JPEG"; kw["quality"] = self.config.get("jpeg_quality", 90)
            if img.mode == "RGBA":
                # JPEG nie ma kanału alfa (np. przezroczyste rogi po
                # _round_window_corners) - spłaszczamy na biało zamiast
                # dać PIL-owi rzucić wyjątkiem przy zapisie.
                flat = Image.new("RGB", img.size, (255, 255, 255))
                flat.paste(img, mask=img.split()[3])
                img = flat
        img.save(fp, fmt, **kw)
        return self._post(fp)

    def _post(self, fp) -> str:
        ac = self.config.get("after_capture", {})
        if ac.get("copy_to_clipboard") and PIL_AVAILABLE:
            try: self._clipboard(Image.open(fp))
            except Exception: pass
        if ac.get("show_in_explorer"):
            self._explorer(fp)
        return fp

    def _clipboard(self, img):
        if IS_WINDOWS:
            try:
                import win32clipboard
                buf = io.BytesIO(); img.convert("RGB").save(buf, "BMP")
                data = buf.getvalue()[14:]
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                win32clipboard.CloseClipboard()
            except ImportError: pass
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            img.save(tmp.name); tmp.close()
            _clip_cmds = [["xclip", "-selection", "clipboard", "-t", "image/png", "-i", tmp.name],
                          ["wl-copy", "--type", "image/png"]]
            if _is_wayland_session():
                _clip_cmds.reverse()     # natywny schowek Wayland ma pierwszeństwo
            for cmd in _clip_cmds:
                try:
                    if _run(["which", cmd[0]], timeout=1).returncode == 0:
                        if cmd[0] == "wl-copy":
                            with open(tmp.name, "rb") as f:
                                _popen(cmd, stdin=f)
                        else:
                            _popen(cmd)
                        break
                except Exception: pass
            try: os.unlink(tmp.name)
            except Exception: pass

    def _explorer(self, path):
        if IS_WINDOWS: _popen(["explorer", "/select,", path])
        else: _popen(["xdg-open", str(Path(path).parent)])

    def _grab(self, mon) -> "Image.Image":
        with mss.MSS() as sct:
            shot = sct.grab(mon)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def capture_region(self, x, y, w, h) -> str:
        if not MSS_AVAILABLE: return None
        return self._save(self._grab({"left": x, "top": y, "width": w, "height": h}), "region")

    def capture_active_monitor(self) -> str:
        if not MSS_AVAILABLE: return None
        cx, cy = QCursor.pos().x(), QCursor.pos().y()
        with mss.MSS() as sct:
            target = sct.monitors[1]
            for m in sct.monitors[1:]:
                if (m["left"] <= cx < m["left"] + m["width"] and
                        m["top"] <= cy < m["top"] + m["height"]):
                    target = m
                    break
            shot = sct.grab(target)
            return self._save(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), "monitor")

    def capture_specific_monitor(self, idx: int) -> str:
        if not MSS_AVAILABLE: return None
        with mss.MSS() as sct:
            real = min(idx + 1, len(sct.monitors) - 1)
            shot = sct.grab(sct.monitors[real])
            return self._save(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"),
                               f"monitor{idx + 1}")

    def _capture_active_window_x11(self):
        """Przechwytywanie aktywnego okna przez xdotool (sesja X11)."""
        try:
            r = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                info = dict(l.split("=") for l in r.stdout.strip().split("\n") if "=" in l)
                return self.capture_region(int(info.get("X", 0)), int(info.get("Y", 0)),
                                           int(info.get("WIDTH", 800)), int(info.get("HEIGHT", 600)))
        except Exception:
            pass
        return None

    def _capture_active_window_wayland(self):
        """Przechwytywanie aktywnego okna na sesji Wayland (np. Cinnamon-Wayland
        w Cinnamon 6.8 / Linux Mint 23). xdotool tam nie działa, bo Wayland z
        założenia blokuje aplikacjom podglądanie/sterowanie innymi oknami.
        Próbujemy kolejno:
          1. cinnamon-list-windows - narzędzie CLI zapowiedziane w Cinnamon 6.8,
             wypisujące pozycję/rozmiar okien (jeśli już dostępne w systemie).
          2. Portal xdg-desktop-portal (org.freedesktop.portal.Screenshot) w
             trybie interaktywnym - kompozytor pokaże własne narzędzie wyboru
             okna/obszaru, bo aplikacje na Waylandzie nie mogą zrobić tego same.
        Jeśli obie metody zawiodą, wracamy do zrzutu całego aktywnego monitora
        i informujemy o tym użytkownika (zamiast cichej, mylącej "nieprecyzji").
        """
        # 1) cinnamon-list-windows (może jeszcze nie istnieć w starszych wersjach Cinnamon)
        try:
            r = subprocess.run(["cinnamon-list-windows"], capture_output=True,
                                text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                active = None
                for line in r.stdout.strip().splitlines():
                    parts = dict(
                        kv.split("=", 1) for kv in line.split() if "=" in kv
                    )
                    if parts.get("focused") in ("1", "true", "True") or \
                       parts.get("active") in ("1", "true", "True"):
                        active = parts
                        break
                if active and all(k in active for k in ("x", "y", "width", "height")):
                    return self.capture_region(
                        int(active["x"]), int(active["y"]),
                        int(active["width"]), int(active["height"]))
        except Exception:
            pass

        # 2) Interaktywny portal - kompozytor pokazuje własny wybór okna/obszaru
        #    (jedno kliknięcie użytkownika), wynik wczytujemy z pliku portalu.
        try:
            img = _wl_portal_image(interactive=True, timeout=60)
            if img is not None:
                return self._save(img, "window")
        except Exception as ex:
            print(f"[PyshareX][wayland] portal interaktywny: {ex}")

        return None

    def _clamp_to_monitor(self, x, y, w, h):
        """Przycina prostokąt (x, y, w, h) do granic monitora, na którym on
        leży, tak by nie wystawał poza realny obszar ekranu.

        Dla zmaksymalizowanych/przypiętych (Aero Snap) okien
        DWMWA_EXTENDED_FRAME_BOUNDS potrafi być o kilka pikseli większy niż
        faktyczny obszar monitora - mss w takim wypadku dokleja czarny
        margines zamiast rzucić błąd, co wygląda jak "nieprecyzyjny" zrzut."""
        if not MSS_AVAILABLE:
            return x, y, w, h
        try:
            with mss.MSS() as sct:
                cx, cy = x + w // 2, y + h // 2
                mon = None
                for m in sct.monitors[1:]:
                    if (m["left"] <= cx < m["left"] + m["width"] and
                            m["top"] <= cy < m["top"] + m["height"]):
                        mon = m
                        break
                if mon is None:
                    return x, y, w, h
                left   = max(x, mon["left"])
                top    = max(y, mon["top"])
                right  = min(x + w, mon["left"] + mon["width"])
                bottom = min(y + h, mon["top"] + mon["height"])
                if right > left and bottom > top:
                    return left, top, right - left, bottom - top
        except Exception:
            pass
        return x, y, w, h

    @staticmethod
    def _win11_rounded_corners_active(hwnd) -> bool:
        """Windows 11 (build >= 22000) rysuje zaokrąglone rogi okien w
        stanie *normal* - ale NIE dla zmaksymalizowanych/przypiętych do
        krawędzi (te mają zwykłe, kwadratowe rogi). Sprawdzamy oba warunki,
        żeby nie zaokrąglać rogów tam, gdzie realne okno ich nie ma."""
        try:
            if sys.getwindowsversion().build < 22000:
                return False
        except Exception:
            return False
        try:
            import win32gui
            return not win32gui.IsZoomed(hwnd)
        except Exception:
            return True

    @staticmethod
    def _get_window_dpi(hwnd) -> int:
        try:
            import ctypes
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi:
                return dpi
        except Exception:
            pass
        return 96

    def _round_window_corners(self, img: "Image.Image", radius: int) -> "Image.Image":
        """Maskuje 4 rogi obrazu przezroczystością, tak by pasowały do
        realnie zaokrąglonych rogów okna w Windows 11.

        GetWindowRect/DWMWA_EXTENDED_FRAME_BOUNDS zwracają zwykły
        prostokąt, ale samo okno ma ucięte (zaokrąglone) rogi - bez tej
        maski w rogach zrzutu zostają małe czarne trójkąciki (to, co jest
        "pod" zaokrągloną krawędzią, np. pulpit)."""
        if radius <= 0:
            return img
        img = img.convert("RGBA")
        w, h = img.size
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
        img.putalpha(mask)
        return img

    def capture_active_window(self) -> str:
        if IS_WINDOWS:
            try:
                import win32gui
                hwnd = win32gui.GetForegroundWindow()
                x, y, x2, y2 = _get_visible_window_bounds(hwnd)
                x, y, w, h = self._clamp_to_monitor(x, y, x2 - x, y2 - y)
                # DWM lekko antyaliasuje najbrzeżniejszy piksel obramowania
                # okna z tłem pod spodem - nawet przy poprawnym prostokącie
                # z DWMWA_EXTENDED_FRAME_BOUNDS zostaje 1px "obcej" linii
                # na całym obwodzie. Wcinamy region o 1px z każdej strony,
                # żeby jej nie złapać (tak samo robi to ShareX).
                if w > 2 and h > 2:
                    x, y, w, h = x + 1, y + 1, w - 2, h - 2
                if not MSS_AVAILABLE:
                    return None
                img = self._grab({"left": x, "top": y, "width": w, "height": h})
                if self._win11_rounded_corners_active(hwnd):
                    dpi = self._get_window_dpi(hwnd)
                    radius = round(8 * dpi / 96)  # 8px to domyślny promień DWM przy 100% skali
                    img = self._round_window_corners(img, radius)
                return self._save(img, "window")
            except Exception: pass
        elif _is_wayland_session():
            result = self._capture_active_window_wayland()
            if result:
                return result
            # Fallback jawnie sygnalizowany, żeby nie było cichej "nieprecyzji"
            self._notify_wayland_fallback()
        else:
            result = self._capture_active_window_x11()
            if result:
                return result
        return self.capture_active_monitor()

    def _notify_wayland_fallback(self):
        """Informuje (na stderr), że na Waylandzie nie udało się precyzyjnie
        przechwycić aktywnego okna i zrzucono cały monitor zamiast niego.
        CaptureEngine nie ma bezpośredniego dostępu do UI (toasty obsługuje
        moduł notify() na poziomie okna głównego), więc na razie tylko log."""
        print("[PyshareX] Wayland: nie udało się przechwycić samego okna - "
              "zrzucono cały aktywny monitor.", file=sys.stderr)

    def capture_fullscreen(self) -> str:
        if not MSS_AVAILABLE: return None
        with mss.MSS() as sct:
            shot = sct.grab(sct.monitors[0])
            return self._save(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), "fullscreen")

    # Dodajemy opcjonalny parametr region=None
    def capture_scrolling(self, region=None) -> str:
        """
        Scrolling capture: robi zrzuty ekranu podczas przewijania.
        """
        if not (MSS_AVAILABLE and PIL_AVAILABLE): return None
        try:
            import pyautogui
            import numpy as _np
        except ImportError:
            return self.capture_active_monitor()

        FRAMES    = 15       
        SCROLL_PX = 500      
        DELAY     = 0.4      

        # ZMIANA: Jeśli przekazano region, używamy dokładnie tego wycinka
        if region:
            rx, ry, rw, rh = region
            mon = {"left": rx, "top": ry, "width": rw, "height": rh}
            
            # Przesuwamy kursor myszy na środek zaznaczonego obszaru.
            # To kluczowe, żeby symulowany scroll przewijał właściwy element!
            try:
                pyautogui.moveTo(rx + rw // 2, ry + rh // 2)
            except: pass
        else:
            # Fallback (stare zachowanie): cały monitor pod kursorem
            cx, cy = QCursor.pos().x(), QCursor.pos().y()
            with mss.MSS() as sct:
                mon = sct.monitors[1]
                for m in sct.monitors[1:]:
                    if (m["left"] <= cx < m["left"] + m["width"] and
                            m["top"] <= cy < m["top"] + m["height"]):
                        mon = m; break

        # ... CAŁA RESZTA KODU (pętla, mss.grab(mon) i sklejanie) POZOSTAJE BEZ ZMIAN! ...

        frames = []
        with mss.MSS() as sct:
            for i in range(FRAMES):
                shot = sct.grab(mon)
                img1 = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                frames.append(img1)
                
                if i < FRAMES - 1:
                    pyautogui.scroll(-SCROLL_PX)
                    time.sleep(DELAY)
                    
                    # Sprawdzamy czy strona w ogóle się przewinęła
                    shot2 = sct.grab(mon)
                    img2 = Image.frombytes("RGB", shot2.size, shot2.bgra, "raw", "BGRX")
                    
                    a1 = _np.array(img1.convert("L"))
                    a2 = _np.array(img2.convert("L"))
                    
                    # Ucinamy prawy margines z suwakiem systemowym (ok. 60px)
                    w = a1.shape[1]
                    crop_w = w - 60 if w > 100 else w
                    
                    # Fuzzy match: mierzymy średnią różnicę pikseli
                    # (Toleruje migające kursory i cienie)
                    diff = _np.mean(_np.abs(a1[:, :crop_w].astype(int) - a2[:, :crop_w].astype(int)))
                    if diff < 2.0:  # Różnica jest mikroskopijna = dotarliśmy do końca strony
                        print(f"Osiągnięto koniec strony na klatce {i+1}")
                        break

        if not frames: return None
        if len(frames) == 1:
            return self._save(frames[0], "scroll")

        def detect_scroll_offset(img_a, img_b):
            """Wylicza przesunięcie przy użyciu dopasowania bloku z górnej części ekranu."""
            arr_a = _np.array(img_a.convert("L"))
            arr_b = _np.array(img_b.convert("L"))
            h, w = arr_a.shape
            
            crop_w = w - 60 if w > 100 else w
            arr_a = arr_a[:, :crop_w]
            arr_b = arr_b[:, :crop_w]

            # 1. Wysokość górnego, przyklejonego menu (z tolerancją różnic)
            sticky_h = 0
            for y in range(h):
                if _np.mean(_np.abs(arr_a[y].astype(int) - arr_b[y].astype(int))) > 5:
                    break
                sticky_h += 1
            
            if sticky_h > h * 0.5:
                sticky_h = 0

            # 2. Template Matching: bierzemy wycinek świeżego tekstu z nowej klatki
            # Tuż spod przyklejonego menu.
            block_h = min(150, h // 4)
            if sticky_h + block_h >= h:
                return h // 2
                
            block = arr_b[sticky_h : sticky_h + block_h]
            
            best_score = float("inf")
            best_shift = 0
            
            # Przesuwamy ten wycinek po starej klatce, by sprawdzić gdzie był przed chwilą
            for shift in range(10, h - sticky_h - block_h):
                y_in_a = sticky_h + shift
                strip_a = arr_a[y_in_a : y_in_a + block_h]
                score = _np.mean(_np.abs(strip_a.astype(int) - block.astype(int)))
                
                if score < best_score:
                    best_score = score
                    best_shift = shift
                    
            if best_score > 35: # Jeśli nic nie pasuje wcale (wideo full-screen itp.)
                return 50
                
            return best_shift

        # Pierwsza klatka to podstawa
        strips = [frames[0]]
        for i in range(1, len(frames)):
            # Wyliczamy o ile ekran zjechał w dół
            shift = detect_scroll_offset(frames[i-1], frames[i])
            
            if shift < 2:
                shift = 50
                
            # Wycinamy tylko `shift` NOWYCH pikseli z samego dołu klatki
            crop_box = (0, frames[i].height - shift, frames[i].width, frames[i].height)
            strips.append(frames[i].crop(crop_box))

        # Sklejanie wszystkich pasków w jeden wielki obraz
        total_h = sum(s.size[1] for s in strips)
        canvas = Image.new("RGB", (frames[0].width, total_h))
        yo = 0
        for s in strips:
            canvas.paste(s, (0, yo))
            yo += s.size[1]

        return self._save(canvas, "scroll")

    def ocr_region(self, x, y, w, h) -> str:
        path = self.capture_region(x, y, w, h)
        if not path: return ""
        engine = self.config.get("ocr_engine", "paddleocr")
        lang_codes = self.config.get("ocr_languages", ["pl"])
        if engine == "paddleocr":
            return self._ocr_paddleocr(path, lang_codes)
        elif engine == "easyocr":
            return self._ocr_easyocr(path, lang_codes)
        else:
            return self._ocr_tesseract(path, lang_codes)

    def _ocr_paddleocr(self, image_path: str, lang_codes=("pl",)) -> str:
        if isinstance(lang_codes, str):
            lang_codes = [lang_codes]
        lang_codes = list(lang_codes) or ["pl"]

        if not PADDLEOCR_AVAILABLE:
            return ("PaddleOCR is not installed.\n"
                    "Install with:  pip install paddlepaddle paddleocr\n"
                    "Falling back — try EasyOCR or Tesseract in Settings.")

        # On Linux, PaddlePaddle crashes the entire process with SIGILL on CPUs
        # that lack AVX instructions (common in VMs, older hardware).
        # SIGILL cannot be caught by Python try/except — it kills the process.
        # Safe solution: run PaddleOCR in an isolated subprocess so a crash
        # there does not take down PyshareX.
        if IS_LINUX:
            return self._ocr_paddleocr_subprocess(image_path, lang_codes)

        # PaddleOCR expects BGR numpy array (same as OpenCV).
        # Passing a file path triggers the OneDNN loader crash on Windows,
        # so we always convert to BGR array first.
        img_input = image_path  # fallback if numpy unavailable
        try:
            import numpy as _np
            import cv2 as _cv2
            img_input = _cv2.imdecode(
                _np.fromfile(image_path, dtype=_np.uint8), _cv2.IMREAD_COLOR
            )  # result is BGR uint8 — exactly what PaddleOCR expects
        except Exception:
            try:
                import numpy as _np
                from PIL import Image as _PILImage
                _pil = _PILImage.open(image_path).convert("RGB")
                # PIL gives RGB → flip to BGR for PaddleOCR
                img_input = _np.array(_pil)[:, :, ::-1].copy()
            except Exception:
                pass  # last resort: raw path

        # PaddleOCR can only load one language model at a time. With several
        # languages selected, run each one and keep whichever result has the
        # highest average recognition confidence.
        best_lines, best_score, any_reader = [], -1.0, False
        last_err = None
        for code in lang_codes:
            reader = _get_paddleocr_reader(code)
            if reader is None:
                last_err = _paddleocr_init_error
                continue
            any_reader = True
            try:
                if _paddleocr_api_version == "v3":
                    result_obj = reader.predict(img_input)
                else:
                    result_obj = reader.ocr(img_input, cls=True)
                lines, score = _paddle_extract(result_obj, _paddleocr_api_version)
            except Exception as e:
                last_err = str(e)
                continue
            if score > best_score or (score == best_score and len(lines) > len(best_lines)):
                best_lines, best_score = lines, score

        if not any_reader:
            detail = f"\n\nError: {last_err}" if last_err else ""
            return (
                f"PaddleOCR failed to initialize.{detail}\n\n"
                "Possible fixes:\n"
                "  • Upgrade: pip install --upgrade paddlepaddle paddleocr\n"
                "  • Or switch to EasyOCR in Settings → OCR engine."
            )
        return "\n".join(best_lines)

    def _ocr_paddleocr_subprocess(self, image_path: str, lang_codes=("pl",)) -> str:
        """Run PaddleOCR in an isolated subprocess on Linux.
        If PaddlePaddle raises SIGILL (no AVX), only the child process dies —
        PyshareX keeps running and returns a clear error message.
        Tries every selected language inside the one subprocess (to avoid
        paying Python/paddle import cost per language) and keeps whichever
        result has the highest average recognition confidence."""
        if isinstance(lang_codes, str):
            lang_codes = [lang_codes]
        paddle_langs = ",".join(_paddle_lang(c) for c in (lang_codes or ["pl"]))
        # Inline Python script passed via -c — no temp file needed.
        script = r"""
import sys, os, json
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_DISABLE_MKL", "1")
os.environ.setdefault("FLAGS_onednn_cpu_enable", "0")
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
image_path = sys.argv[1]
paddle_langs = sys.argv[2].split(",")
try:
    from paddleocr import PaddleOCR
    def _try_init(kwargs):
        import copy
        kw = copy.copy(kwargs)
        removable = ["show_log", "use_angle_cls", "use_textline_orientation"]
        tried = set()
        while True:
            try:
                return PaddleOCR(**kw)
            except Exception as e:
                msg = str(e)
                removed = False
                for p in removable:
                    if p in kw and p not in tried and ("Unknown argument" in msg or p in msg):
                        del kw[p]; tried.add(p); removed = True; break
                if not removed:
                    raise

    def _extract(result_obj, ver):
        lines, confs = [], []
        if ver == "v3":
            for item in (result_obj or []):
                texts, scores = None, None
                try:
                    texts = item["rec_texts"]; scores = item.get("rec_scores")
                except (KeyError, TypeError, AttributeError):
                    texts = getattr(item, "rec_texts", None)
                    scores = getattr(item, "rec_scores", None)
                if isinstance(texts, (list, tuple)):
                    for i, t in enumerate(texts):
                        if not t: continue
                        lines.append(str(t))
                        if scores and i < len(scores):
                            try: confs.append(float(scores[i]))
                            except Exception: pass
                elif isinstance(texts, str) and texts:
                    lines.append(texts)
                else:
                    try:
                        for ln in item:
                            if ln and len(ln) >= 2:
                                ti = ln[1]
                                if isinstance(ti, (list, tuple)) and ti:
                                    lines.append(str(ti[0]))
                                    if len(ti) > 1:
                                        try: confs.append(float(ti[1]))
                                        except Exception: pass
                                elif isinstance(ti, str):
                                    lines.append(ti)
                    except Exception:
                        pass
        else:
            for block in (result_obj or []):
                if not block: continue
                for ln in block:
                    if ln and len(ln) >= 2:
                        ti = ln[1]
                        if isinstance(ti, (list, tuple)) and ti:
                            lines.append(str(ti[0]))
                            if len(ti) > 1:
                                try: confs.append(float(ti[1]))
                                except Exception: pass
                        elif isinstance(ti, str):
                            lines.append(ti)
        avg = (sum(confs) / len(confs)) if confs else (0.3 if lines else 0.0)
        return lines, avg

    try:
        import numpy as _np, cv2 as _cv2
        img = _cv2.imdecode(_np.fromfile(image_path, dtype=_np.uint8), _cv2.IMREAD_COLOR)
    except Exception:
        from PIL import Image as _PIL
        import numpy as _np
        _p = _PIL.open(image_path).convert("RGB")
        img = _np.array(_p)[:, :, ::-1].copy()

    best_lines, best_score = [], -1.0
    init_errors = []
    for lang in paddle_langs:
        reader, ver = None, None
        for v, kw in [("v3", {"lang": lang, "show_log": False}),
                      ("v2", {"use_angle_cls": True, "lang": lang, "show_log": False})]:
            try:
                reader = _try_init(kw); ver = v; break
            except Exception as e:
                init_errors.append(f"{lang}: {e}")
                continue
        if reader is None:
            continue
        try:
            result_obj = reader.predict(img) if ver == "v3" else reader.ocr(img, cls=True)
            lines, score = _extract(result_obj, ver)
        except Exception as e:
            init_errors.append(f"{lang}: {e}")
            continue
        if score > best_score or (score == best_score and len(lines) > len(best_lines)):
            best_lines, best_score = lines, score

    if not best_lines and init_errors:
        print("ERROR: " + " | ".join(init_errors), file=sys.stderr); sys.exit(1)
    print(json.dumps(best_lines))
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)
"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", script, image_path, paddle_langs],
                capture_output=True, text=True, timeout=90,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip().splitlines()
                last_err = stderr[-1] if stderr else "unknown error"
                # SIGILL shows as returncode -4 on Linux
                if result.returncode in (-4, -6, -11):
                    return (
                        "PaddleOCR crashed (Illegal instruction — SIGILL).\n\n"
                        "Your CPU does not support AVX instructions required by PaddlePaddle.\n"
                        "Switch to EasyOCR in Settings → OCR engine:\n"
                        "  pip install easyocr"
                    )
                return f"PaddleOCR subprocess error: {last_err}"
            import json as _json
            lines = _json.loads(result.stdout.strip())
            return "\n".join(lines)
        except subprocess.TimeoutExpired:
            return "PaddleOCR timed out (>90s). Try fewer languages, or switch to EasyOCR in Settings."
        except Exception as e:
            return f"PaddleOCR subprocess launch error: {e}"

    def _ocr_easyocr(self, image_path: str, lang_codes=("pl",)) -> str:
        if isinstance(lang_codes, str):
            lang_codes = [lang_codes]
        if not EASYOCR_AVAILABLE:
            return ("EasyOCR is not installed.\n"
                    "Install with:  pip install easyocr\n"
                    "Falling back — try Tesseract engine in Settings.")
        reader = _get_easyocr_reader(lang_codes)
        if reader is None:
            return (
                "EasyOCR failed to initialize for this language combination.\n"
                "Some languages can't be mixed in one model — for example "
                "Cyrillic (Russian, Ukrainian) can't be combined with most "
                "Latin-only languages.\n"
                "Try selecting fewer / more compatible languages in Settings."
            )
        try:
            results = reader.readtext(image_path, detail=0, paragraph=True)
            return "\n".join(results)
        except Exception as e:
            return f"EasyOCR error: {e}"

    def _ocr_tesseract(self, image_path: str, lang_codes=("pl",)) -> str:
        if isinstance(lang_codes, str):
            lang_codes = [lang_codes]
        try:
            codes = []
            for c in (lang_codes or ["pl"]):
                tc = _tesseract_lang(c)
                if tc not in codes:
                    codes.append(tc)
            if "eng" not in codes:
                codes.append("eng")
            lang_arg = "+".join(codes)
            r = subprocess.run(["tesseract", image_path, "stdout",
                                 "-l", lang_arg, "--psm", "3"],
                                capture_output=True, text=True, timeout=30)
            return r.stdout.strip()
        except FileNotFoundError:
            return ("Tesseract is not installed.\n"
                    "Linux:   sudo apt install tesseract-ocr tesseract-ocr-pol\n"
                    "Windows: https://github.com/UB-Mannheim/tesseract")
        except Exception as e:
            return f"Tesseract error: {e}"
    def scan_qr(self, x, y, w, h) -> str:
        path = self.capture_region(x, y, w, h)
        if not path: return ""
        
        if not CV2_AVAILABLE:
            return "OpenCV library not found. Install it with:\npip install opencv-python"
            
        try:
            import cv2
            img = cv2.imread(path)
            detector = cv2.QRCodeDetector()
            data, bbox, _ = detector.detectAndDecode(img)
            
            if data:
                return data
            return "No QR code detected in the selected area."
        except Exception as e:
            return f"An error occurred while decoding: {e}"

# ─────────────────────────────────────────────
#  RECORDING THREAD
# ─────────────────────────────────────────────

class RecordingThread(QThread):
    finished     = Signal(str)
    error        = Signal(str)
    region_ready = Signal(int, int, int, int)

    def __init__(self, region, output_path, fps=30, audio=False,
                 gif_mode=False, gif_fps=10, gif_duration=5,
                 snapshot_mode=False):
        super().__init__()
        self.region        = region
        self.output_path   = output_path
        self.fps           = fps
        self.audio         = audio
        self.gif_mode      = gif_mode
        self.gif_fps       = gif_fps
        self.gif_duration  = gif_duration
        self.snapshot_mode = snapshot_mode   # True = grab exactly 1 frame → PNG
        self._stop         = threading.Event()
        self._proc         = None

    def stop(self):
        # IMPORTANT: only set the flag here. This is called directly (not via
        # a queued signal) from the GUI thread's button-click handler, so
        # anything blocking here (like waiting on the ffmpeg process) would
        # freeze the whole UI for several seconds. The actual graceful
        # stop/kill of ffmpeg is handled exclusively inside run()'s own loop,
        # which executes on this worker thread — calling _kill_ffmpeg() here
        # too would also race with that same cleanup on the ffmpeg process.
        self._stop.set()

    def _kill_ffmpeg(self):
        p = self._proc
        if not p: return
        if p.poll() is not None: return  # already exited
        try:
            # Try graceful stop via 'q' (writes moov atom)
            try:
                p.stdin.write(b"q")
                p.stdin.flush()
                p.stdin.close()
            except Exception:
                pass
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()
        except Exception:
            try: p.kill()
            except Exception: pass

    def run(self):
        try:
            if _is_wayland_session() and not self.snapshot_mode:
                self.error.emit(
                    "Nagrywanie wideo/GIF nie działa jeszcze na Waylandzie "
                    "(x11grab i grabowanie klatka-po-klatce nie widzą ekranu; "
                    "potrzebny jest portal ScreenCast + PipeWire). "
                    "Zaloguj się do sesji X11, żeby nagrywać.")
                return
            if self.gif_mode:      self._gif()
            elif self.snapshot_mode: self._snapshot()
            else:                  self._video()
        except Exception as e:
            self.error.emit(f"Unexpected error: {e}")

    def _resolve_region(self):
        if self.region:
            x, y, w, h = self.region
        else:
            # Fallback: primary monitor (region is always set by UI now)
            with mss.MSS() as sct:
                m = sct.monitors[1]
                x, y, w, h = m["left"], m["top"], m["width"], m["height"]
        w = w if w % 2 == 0 else w - 1
        h = h if h % 2 == 0 else h - 1
        return x, y, w, h

    def _gif(self):
        if not (MSS_AVAILABLE and PIL_AVAILABLE):
            self.error.emit("mss or PIL unavailable"); return
        try:
            x, y, w, h = self._resolve_region()
            # mss needs positive non-zero dimensions
            if w < 2 or h < 2:
                self.error.emit("GIF region too small"); return
            mon = {"left": int(x), "top": int(y), "width": int(w), "height": int(h)}
            self.region_ready.emit(x, y, w, h)
        except Exception as e:
            self.error.emit(f"Screen error: {e}"); return

        frames_rgb = []
        interval = 1.0 / max(1, self.gif_fps)

        # Max GIF width 1280 px — keeps file size reasonable and encoding fast
        MAX_W = 1280
        scale = min(1.0, MAX_W / w) if w > MAX_W else 1.0
        out_w = max(2, int(w * scale) & ~1)
        out_h = max(2, int(h * scale) & ~1)

        try:
            with mss.MSS() as sct:
                while not self._stop.is_set():
                    t0 = time.monotonic()
                    shot = sct.grab(mon)
                    img  = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    if scale < 1.0:
                        img = img.resize((out_w, out_h), Image.LANCZOS)
                    frames_rgb.append(img)
                    elapsed = time.monotonic() - t0
                    remaining = interval - elapsed
                    if remaining > 0:
                        time.sleep(remaining)
        except Exception as e:
            self.error.emit(f"GIF capture error: {e}"); return

        if not frames_rgb:
            self.error.emit("No frames captured"); return

        try:
            # Build global palette from first frame, reuse for all frames
            # FASTOCTREE gives better color fidelity than MEDIANCUT
            first_p = frames_rgb[0].quantize(
                colors=256, method=Image.Quantize.FASTOCTREE, dither=1)
            palette_data = first_p.getpalette()

            palettes = [first_p]
            for img in frames_rgb[1:]:
                p = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=1)
                palettes.append(p)

            palettes[0].save(
                self.output_path,
                save_all=True,
                append_images=palettes[1:],
                loop=0,
                duration=int(1000 / max(1, self.gif_fps)),
                optimize=False,   # optimize=True can corrupt multi-frame GIFs
            )
            self.finished.emit(self.output_path)
        except Exception as e:
            self.error.emit(f"GIF save error: {e}")

    def _snapshot(self):
        """Grab exactly one frame from the screen region using ffmpeg and save
        it as a PNG.  Uses the same gdigrab/x11grab input as _video() so the
        capture path is identical — only the output differs (PNG, 1 frame)."""
        try:
            x, y, w, h = self._resolve_region()
        except Exception as e:
            self.error.emit(f"Screen error: {e}"); return

        self.region_ready.emit(x, y, w, h)

        errbuf = tempfile.TemporaryFile()
        try:
            if IS_WINDOWS:
                try:
                    with mss.MSS() as _sct:
                        all_mons = _sct.monitors[1:]
                        virt_x = min(m["left"] for m in all_mons)
                        virt_y = min(m["top"]  for m in all_mons)
                        virt_w = max(m["left"] + m["width"]  for m in all_mons) - virt_x
                        virt_h = max(m["top"]  + m["height"] for m in all_mons) - virt_y
                except Exception:
                    virt_x, virt_y = 0, 0
                    virt_w, virt_h = x + w + 100, y + h + 100
                crop_x = x - virt_x
                crop_y = y - virt_y
                gw = w if w % 2 == 0 else w - 1
                gh = h if h % 2 == 0 else h - 1
                cmd = [
                    "ffmpeg", "-y",
                    "-f",          "gdigrab",
                    "-framerate",  "1",
                    "-rtbufsize",  "256M",
                    "-draw_mouse", "0",
                    "-offset_x",   str(virt_x), "-offset_y", str(virt_y),
                    "-video_size", f"{virt_w}x{virt_h}",
                    "-i",          "desktop",
                    "-vframes",    "1",
                    "-vf",         f"crop={gw}:{gh}:{crop_x}:{crop_y}",
                    self.output_path,
                ]
            else:
                disp = os.environ.get("DISPLAY", ":0")
                cmd = [
                    "ffmpeg", "-y",
                    "-f",         "x11grab",
                    "-framerate", "1",
                    "-video_size", f"{w}x{h}",
                    "-i",         f"{disp}+{x},{y}",
                    "-vframes",   "1",
                    self.output_path,
                ]
            print(f"[PyshareX] snapshot: {' '.join(cmd)}")
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=errbuf,
                creationflags=_NO_WINDOW,
            )
            # ffmpeg exits by itself after -vframes 1; give it up to 10 s
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        except FileNotFoundError:
            errbuf.close()
            self.error.emit("ffmpeg is not installed.\n"
                            "Linux:   sudo apt install ffmpeg\n"
                            "Windows: winget install ffmpeg  (or https://ffmpeg.org)")
            return
        except Exception as e:
            errbuf.close()
            self.error.emit(f"Cannot start ffmpeg: {e}")
            return

        errbuf.seek(0); errtext = errbuf.read().decode(errors="replace"); errbuf.close()

        out = Path(self.output_path)
        if out.exists() and out.stat().st_size > 64:
            self.finished.emit(self.output_path)
        else:
            tail = "\n".join(errtext.strip().splitlines()[-12:])
            self.error.emit(f"ffmpeg snapshot error:\n{tail}")

    def _video(self):
        try:
            x, y, w, h = self._resolve_region()
        except Exception as e:
            self.error.emit(f"Screen error: {e}"); return

        self.region_ready.emit(x, y, w, h)

        if IS_WINDOWS:
            # gdigrab does NOT support negative offset_x/offset_y (monitors to
            # the left or above the primary). Fix: capture the entire virtual
            # desktop and crop to the requested region with a video filter.
            # The virtual desktop origin (min of all monitor lefts/tops) is used
            # to shift the crop rect so it addresses the correct pixels.
            gw = w if w % 2 == 0 else w - 1
            gh = h if h % 2 == 0 else h - 1
            try:
                with mss.MSS() as _sct:
                    all_mons = _sct.monitors[1:]
                    virt_x = min(m["left"] for m in all_mons)
                    virt_y = min(m["top"]  for m in all_mons)
                    virt_w = max(m["left"] + m["width"]  for m in all_mons) - virt_x
                    virt_h = max(m["top"]  + m["height"] for m in all_mons) - virt_y
            except Exception:
                virt_x, virt_y = 0, 0
                virt_w, virt_h = x + w + 100, y + h + 100
            # crop filter coords: origin relative to virtual desktop top-left
            crop_x = x - virt_x
            crop_y = y - virt_y
            vin = ["-f", "gdigrab",
                   "-framerate", str(self.fps),
                   "-rtbufsize", "256M",
                   "-draw_mouse", "1",
                   "-offset_x", str(virt_x), "-offset_y", str(virt_y),
                   "-video_size", f"{virt_w}x{virt_h}",
                   "-i", "desktop"]
            ain = ["-f", "dshow", "-i", "audio=Stereo Mix"] if self.audio else []
        else:
            disp = os.environ.get("DISPLAY", ":0")
            vin  = ["-f", "x11grab", "-framerate", str(self.fps),
                    "-video_size", f"{w}x{h}", "-i", f"{disp}+{x},{y}"]
            ain  = ["-f", "pulse", "-ac", "2", "-i", "default"] if self.audio else []

        # Strategy: record to a temporary MKV (no closing atom needed — every
        # frame is self-contained). Then remux to MP4 with faststart.
        # MKV is robust against process kill; MP4 needs graceful close.
        tmp_mkv = self.output_path.replace(".mp4", ".tmp.mkv")

        if IS_WINDOWS:
            # Crop the virtual-desktop capture down to the requested region.
            # crop=w:h:x:y  (all in pixels relative to captured frame origin)
            vf = ["-vf", f"crop={gw}:{gh}:{crop_x}:{crop_y}"]
        else:
            vf = []

        oops = vf + [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-r", str(self.fps),
            "-g", str(self.fps),   # keyframe every second for clean cuts
        ]
        if self.audio and ain:
            oops += ["-c:a", "aac", "-b:a", "128k"]

        cmd = ["ffmpeg", "-y"] + vin + ain + oops + [tmp_mkv]
        print(f"[PyshareX] {' '.join(cmd)}")

        errbuf = tempfile.TemporaryFile()
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=errbuf,
                creationflags=_NO_WINDOW,
            )
        except FileNotFoundError:
            errbuf.close()
            self.error.emit("ffmpeg is not installed.\n"
                            "Linux:   sudo apt install ffmpeg\n"
                            "Windows: winget install ffmpeg  (or https://ffmpeg.org)")
            return
        except Exception as e:
            errbuf.close(); self.error.emit(f"Cannot start ffmpeg: {e}"); return

        # Wait until stop requested or ffmpeg exits on its own
        while not self._stop.is_set():
            if self._proc.poll() is not None: break
            time.sleep(0.2)

        # Stop ffmpeg: try graceful 'q', then kill
        if self._proc.poll() is None:
            try:
                self._proc.stdin.write(b"q\n")
                self._proc.stdin.flush()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

        errbuf.seek(0); errtext = errbuf.read().decode(errors="replace"); errbuf.close()

        tmp = Path(tmp_mkv)
        # Wait for MKV to be written (up to 3 s)
        for _ in range(30):
            if tmp.exists() and tmp.stat().st_size > 512:
                break
            time.sleep(0.1)

        if not (tmp.exists() and tmp.stat().st_size > 512):
            tail = "\n".join(errtext.strip().splitlines()[-12:])
            self.error.emit(f"ffmpeg error (no output):\n{tail}")
            return

        # Remux MKV → MP4 with faststart (moov at front → any player works)
        remux = ["ffmpeg", "-y", "-i", tmp_mkv,
                 "-c", "copy", "-movflags", "+faststart",
                 self.output_path]
        try:
            r2 = subprocess.run(remux,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=_NO_WINDOW,
                                 timeout=120)
            out = Path(self.output_path)
            if r2.returncode == 0 and out.exists() and out.stat().st_size > 512:
                try: tmp.unlink()
                except Exception: pass
                self.finished.emit(self.output_path)
            else:
                # Remux failed — rename MKV as fallback (still playable in VLC)
                fallback = self.output_path.replace(".mp4", ".mkv")
                try: tmp.rename(fallback)
                except Exception: pass
                self.finished.emit(fallback)
        except Exception as e:
            fallback = self.output_path.replace(".mp4", ".mkv")
            try: tmp.rename(fallback)
            except Exception: pass
            self.finished.emit(fallback)


# ─────────────────────────────────────────────
#  DIALOGS
# ─────────────────────────────────────────────

# All available actions with their display names
AVAILABLE_ACTIONS = [
    ("capture_region",           "Capture region"),
    ("capture_active_monitor",   "Capture active monitor"),
    ("capture_active_window",    "Capture active window"),
    ("capture_selected_monitor", "Capture selected monitor"),
    ("capture_scrolling",        "Scrolling capture"),
    ("capture_fullscreen",       "Capture full screen"),
    ("toggle_recording",         "Start/Stop screen recording"),
    ("record_gif",               "Record GIF"),
    ("ocr_text",                 "OCR – Recognize text"),
    ("ocr_code",                 "OCR – Recognize code"),
    ("video_converter",          "Video Converter")
]
_ACTION_TO_NAME = {a: n for a, n in AVAILABLE_ACTIONS}
_NAME_TO_ACTION = {n: a for a, n in AVAILABLE_ACTIONS}


class ShortcutEditDialog(QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Shortcut")
        self.setMinimumWidth(440)
        self.data = data.copy()
        self.setStyleSheet(parent.styleSheet() if parent else "")
        lay = QVBoxLayout(self); lay.setSpacing(12); lay.setContentsMargins(20, 20, 20, 20)

        # Action dropdown
        lay.addWidget(QLabel("Action:"))
        self.action_cb = QComboBox()
        for action_key, action_name in AVAILABLE_ACTIONS:
            self.action_cb.addItem(action_name, action_key)
        # Select current action
        cur_action = data.get("action", "")
        idx = self.action_cb.findData(cur_action)
        if idx >= 0:
            self.action_cb.setCurrentIndex(idx)
        elif data.get("name", ""):
            # Try to find by name
            idx2 = self.action_cb.findText(data.get("name", ""))
            if idx2 >= 0:
                self.action_cb.setCurrentIndex(idx2)
        self.action_cb.currentIndexChanged.connect(self._on_action_changed)
        lay.addWidget(self.action_cb)

        # Custom name override
        lay.addWidget(QLabel("Custom label (optional, leave blank for default):"))
        self.ne = QLineEdit(data.get("name", ""))
        self.ne.setPlaceholderText("Leave blank to use action name")
        lay.addWidget(self.ne)

        # Shortcut — custom widget using pynput for reliable cross-platform detection
        lay.addWidget(QLabel("Keyboard shortcut (click Capture, press keys, click Stop):"))
        self.ke = _HotkeyEdit(data.get("shortcut", ""))
        lay.addWidget(self.ke)

        # Enabled
        self.en = QCheckBox("Enabled")
        self.en.setChecked(data.get("enabled", True))
        lay.addWidget(self.en)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _on_action_changed(self, idx):
        action_key = self.action_cb.itemData(idx)
        default_name = _ACTION_TO_NAME.get(action_key, "")
        # Only auto-fill name if user hasn't typed something custom
        if not self.ne.text() or self.ne.text() in [n for _, n in AVAILABLE_ACTIONS]:
            self.ne.setText(default_name)

    def _ok(self):
        action_key = self.action_cb.currentData()
        default_name = _ACTION_TO_NAME.get(action_key, self.action_cb.currentText())
        custom_name = self.ne.text().strip()
        self.data["action"]   = action_key
        self.data["name"]     = custom_name if custom_name else default_name
        self.data["shortcut"] = self.ke.shortcut()
        self.data["enabled"]  = self.en.isChecked()
        self.accept()

    def done(self, r):
        try: self.ke._stop_capture()
        except Exception: pass
        super().done(r)

    def get_data(self): return self.data


class _KeyCatchLine(QLineEdit):
    """Pole, które w trybie nagrywania przechwytuje kombinację klawiszy przez
    Qt (działa pod Waylandem/XWayland, gdzie pynput widzi tylko pojedyncze
    klawisze albo nic)."""
    combo_done = Signal(str)     # pełna kombinacja (zakończona klawiszem głównym)
    combo_live = Signal(str)     # podgląd: aktualnie trzymane modyfikatory

    _MODS_ONLY = None  # wypełniane leniwie (enumy Qt)

    @staticmethod
    def _mod_keys():
        K = Qt.Key
        return {int(K.Key_Control), int(K.Key_Alt), int(K.Key_Shift),
                int(K.Key_Meta), int(K.Key_AltGr), int(K.Key_Super_L),
                int(K.Key_Super_R), int(K.Key_Hyper_L), int(K.Key_Hyper_R)}

    @staticmethod
    def _key_name(k: int) -> str:
        K = Qt.Key
        if int(K.Key_A) <= k <= int(K.Key_Z) or int(K.Key_0) <= k <= int(K.Key_9):
            return chr(k)
        if int(K.Key_F1) <= k <= int(K.Key_F35):
            return f"F{k - int(K.Key_F1) + 1}"
        table = {
            K.Key_Print: "Print Screen", K.Key_SysReq: "Print Screen",
            K.Key_Insert: "Insert", K.Key_Delete: "Delete", K.Key_Home: "Home",
            K.Key_End: "End", K.Key_PageUp: "PgUp", K.Key_PageDown: "PgDown",
            K.Key_Up: "Up", K.Key_Down: "Down", K.Key_Left: "Left",
            K.Key_Right: "Right", K.Key_Space: "Space", K.Key_Tab: "Tab",
            K.Key_Backtab: "Tab", K.Key_Return: "Return", K.Key_Enter: "Return",
            K.Key_Backspace: "Backspace", K.Key_Escape: "Escape",
            K.Key_NumLock: "Num Lock", K.Key_ScrollLock: "Scroll Lock",
            K.Key_Pause: "Pause", K.Key_CapsLock: "Caps Lock",
        }
        for qk, nm in table.items():
            if int(qk) == k:
                return nm
        try:
            return QKeySequence(k).toString().upper()
        except Exception:
            return ""

    @staticmethod
    def _mods_list(mods) -> list:
        M = Qt.KeyboardModifier
        out = []
        if mods & M.ControlModifier: out.append("Ctrl")
        if mods & M.AltModifier:     out.append("Alt")
        if mods & M.ShiftModifier:   out.append("Shift")
        if mods & M.MetaModifier:    out.append("Meta")
        return out

    def focusNextPrevChild(self, nxt):      # Tab ma trafić do nas, nie zmieniać fokusu
        return False if getattr(self, "_capturing", False) else super().focusNextPrevChild(nxt)

    def event(self, e):
        if getattr(self, "_capturing", False):
            t = e.type()
            if t == QEvent.Type.ShortcutOverride:
                e.accept(); return True
            if t == QEvent.Type.KeyPress:
                self._on_key(e); return True
            if t == QEvent.Type.KeyRelease:
                self.combo_live.emit("+".join(self._mods_list(e.modifiers())))
                return True
        return super().event(e)

    def _on_key(self, e):
        if e.isAutoRepeat():
            return
        k = int(e.key())
        mods = self._mods_list(e.modifiers())
        if k in self._mod_keys() or k == 0:
            self.combo_live.emit("+".join(mods))
            return
        name = self._key_name(k)
        if not name:
            return
        self.combo_done.emit("+".join(mods + [name]))


class _HotkeyEdit(QWidget):
    """Key-combination recorder that uses pynput for reliable cross-platform
    detection — including Print Screen which Qt never forwards on Windows."""

    _combo_ready = Signal(str)  # emitted from pynput thread, connected to main thread

    # pynput Key → string used by _qt2pk
    _SPECIAL = {
        "print_screen": "Print Screen",
        "f1":  "F1",  "f2":  "F2",  "f3":  "F3",  "f4":  "F4",
        "f5":  "F5",  "f6":  "F6",  "f7":  "F7",  "f8":  "F8",
        "f9":  "F9",  "f10": "F10", "f11": "F11", "f12": "F12",
        "insert": "Insert", "delete": "Delete", "home": "Home",
        "end": "End", "page_up": "PgUp", "page_down": "PgDown",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "space": "Space", "tab": "Tab", "enter": "Return",
        "backspace": "Backspace", "escape": "Escape",
        "num_lock": "Num Lock", "scroll_lock": "Scroll Lock",
        "pause": "Pause", "caps_lock": "Caps Lock",
    }
    _MOD_NAMES = {"ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "alt_gr",
                  "shift", "shift_l", "shift_r", "cmd", "cmd_l", "cmd_r"}

    def __init__(self, initial: str = "", parent=None):
        super().__init__(parent)
        self._shortcut = initial
        self._listener = None
        self._held_mods: set = set()
        self._combo_ready.connect(self._accept_combo)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._display = _KeyCatchLine(initial)
        self._display.setReadOnly(True)
        self._display.setPlaceholderText("Press Capture, then press your key combination")
        self._display.combo_done.connect(self._accept_combo)
        self._display.combo_live.connect(
            lambda t: self._display.setText((t + "+…") if t else "… press keys …"))
        lay.addWidget(self._display)

        self._btn = QPushButton("Capture")
        self._btn.setCheckable(True)
        self._btn.setFixedWidth(80)
        self._btn.clicked.connect(self._toggle)
        lay.addWidget(self._btn)

    def _toggle(self, checked: bool):
        if checked:
            self._start_capture()
        else:
            self._stop_capture()

    def _start_capture(self):
        if not IS_WINDOWS:
            # Linux (X11/Wayland): przechwytywanie przez Qt - widzi pełne
            # kombinacje z modyfikatorami także pod XWayland.
            self._display.setText("… press keys …")
            self._btn.setText("Stop")
            self._display._capturing = True
            self._display.setFocus()
            try: self._display.grabKeyboard()
            except Exception: pass
            return
        if not PYNPUT_AVAILABLE:
            self._display.setPlaceholderText("pynput not installed — type shortcut manually")
            self._display.setReadOnly(False)
            self._btn.setChecked(False)
            return
        self._held_mods.clear()
        self._display.setText("… press keys …")
        self._btn.setText("Stop")
        from pynput import keyboard as kb

        def on_press(key):
            name = self._pynput_key_name(key)
            if name in self._MOD_NAMES:
                self._held_mods.add(name)
                return
            # Non-modifier pressed — emit signal (thread-safe) and stop
            combo = self._build_combo(key)
            self._combo_ready.emit(combo)
            return False  # stop listener

        def on_release(key):
            name = self._pynput_key_name(key)
            self._held_mods.discard(name)

        self._listener = kb.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()

    def _stop_capture(self):
        if getattr(self._display, "_capturing", False):
            self._display._capturing = False
            try: self._display.releaseKeyboard()
            except Exception: pass
        if self._listener:
            try: self._listener.stop()
            except Exception: pass
            self._listener = None
        self._btn.setText("Capture")
        self._btn.setChecked(False)
        self._display.setText(self._shortcut)

    def _accept_combo(self, combo: str):
        self._shortcut = combo
        self._stop_capture()

    @staticmethod
    def _pynput_key_name(key) -> str:
        try:
            return key.name  # pynput Key enum
        except AttributeError:
            return ""

    def _build_combo(self, key) -> str:
        parts = []
        # Modifiers held at the time the main key was pressed
        if any(m in self._held_mods for m in ("ctrl", "ctrl_l", "ctrl_r")):
            parts.append("Ctrl")
        if any(m in self._held_mods for m in ("alt", "alt_l", "alt_r", "alt_gr")):
            parts.append("Alt")
        if any(m in self._held_mods for m in ("shift", "shift_l", "shift_r")):
            parts.append("Shift")
        if any(m in self._held_mods for m in ("cmd", "cmd_l", "cmd_r")):
            parts.append("Meta")
        # The actual key
        name = self._pynput_key_name(key)
        if name in self._SPECIAL:
            parts.append(self._SPECIAL[name])
        else:
            key_char = None
            try:
                # key.char contains the character, but with modifiers like Ctrl
                # it becomes a control character (e.g. Ctrl+P → '\x10').
                # Use the virtual key code to get the plain letter instead.
                vk = getattr(key, 'vk', None)
                if vk is not None and 65 <= vk <= 90:
                    # VK 65-90 = A-Z
                    key_char = chr(vk)
                elif vk is not None and 48 <= vk <= 57:
                    # VK 48-57 = 0-9
                    key_char = chr(vk)
                else:
                    char = key.char
                    if char and char.isprintable() and ord(char) >= 32:
                        key_char = char.upper()
            except AttributeError:
                pass
            if key_char:
                parts.append(key_char.upper())
            elif name:
                parts.append(name.upper())
            else:
                parts.append(str(key))
        return "+".join(parts)

    def shortcut(self) -> str:
        return self._shortcut

    def set_shortcut(self, s: str):
        self._shortcut = s
        self._display.setText(s)

    def closeEvent(self, e):
        self._stop_capture()
        super().closeEvent(e)


class OcrProgressDialog(QWidget):
    """Small non-blocking progress window shown while OCR/QR is running.
    Uses QWidget (not QDialog) to guarantee it never blocks the event loop."""
    def __init__(self, message="Running OCR…", parent=None):
        super().__init__(None,
                         Qt.WindowType.Window |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setObjectName("OcrProgressFrame")
        frame.setStyleSheet("""
            QFrame#OcrProgressFrame {
                background: #1e1e2e;
                border: 1px solid #45475a;
                border-radius: 12px;
            }
            QLabel { color: #cdd6f4; font-size: 13px; background: transparent; }
            QLabel#title_lbl { font-size: 15px; font-weight: bold; color: #89b4fa; }
        """)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(28, 22, 28, 22)
        inner.setSpacing(12)

        title = QLabel("⏳ Processing…")
        title.setObjectName("title_lbl")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(title)

        self._msg_lbl = QLabel(message)
        self._msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg_lbl.setWordWrap(True)
        inner.addWidget(self._msg_lbl)

        # Animated dots
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(400)
        self._dots = 0
        self._base_msg = message
        self._dot_timer.timeout.connect(self._tick)
        self._dot_timer.start()

        outer.addWidget(frame)
        self.setFixedWidth(340)
        self.adjustSize()

        # Centre on primary screen
        sg = QApplication.primaryScreen().availableGeometry()
        self.move(sg.center() - self.rect().center())

    def set_message(self, msg: str):
        self._base_msg = msg
        self._msg_lbl.setText(msg)

    def _tick(self):
        self._dots = (self._dots + 1) % 4
        self._msg_lbl.setText(self._base_msg + "." * self._dots)

    def closeEvent(self, e):
        self._dot_timer.stop()
        super().closeEvent(e)

    def close(self):
        self._dot_timer.stop()
        super().close()


class OcrResultDialog(QDialog):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OCR Result"); self.setMinimumSize(500, 300)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        lay = QVBoxLayout(self)
        te = QTextEdit(); te.setPlainText(text); lay.addWidget(te)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        cp = QPushButton("Copy to clipboard")
        cp.clicked.connect(lambda: QApplication.clipboard().setText(te.toPlainText()))
        bb.addButton(cp, QDialogButtonBox.ButtonRole.ActionRole)
        bb.rejected.connect(self.close); lay.addWidget(bb)


# ─────────────────────────────────────────────
#  STYLESHEET
# ─────────────────────────────────────────────

def _css_with_menubar_font(css: str, font=None) -> str:
    """Pin the menu-bar font size in every item state.

    Once ``QMenuBar::item:selected`` is styled, Qt draws the hovered item with the
    stylesheet font (13px from the QWidget rule) while the other items keep the platform's
    menu-bar font, so the hovered title shrinks. Giving all states the platform menu-bar
    size (read from the style, not hard-coded) keeps the text identical on hover."""
    f = font if font is not None else QApplication.font("QMenuBar")
    if f.pointSizeF() > 0:
        size = f"{f.pointSizeF():g}pt"
    elif f.pixelSize() > 0:
        size = f"{f.pixelSize()}px"
    else:
        size = "9pt"
    return css.replace("__MBFONT__", size)


CSS = """
QWidget { background:#1e1e2e; color:#cdd6f4;
    font-family:'Segoe UI','Ubuntu',sans-serif; font-size:13px; }
QMainWindow { background:#1e1e2e; }
QMenuBar { background:#181825; color:#cdd6f4; border-bottom:1px solid #313244; padding:2px;
    font-size:__MBFONT__; }
QMenuBar::item { font-size:__MBFONT__; }
QMenuBar::item:selected { background:#313244; border-radius:4px; font-size:__MBFONT__; }
QMenu { background:#181825; color:#cdd6f4; border:1px solid #313244; padding:4px; }
QMenu::item { padding:6px 24px 6px 12px; border-radius:4px; }
QMenu::item:selected { background:#313244; }
QMenu::separator { height:1px; background:#313244; margin:4px 8px; }
QPushButton { background:#313244; color:#cdd6f4; border:1px solid #45475a;
    border-radius:6px; padding:5px 14px; min-height:28px; }
QPushButton:hover  { background:#45475a; border-color:#585b70; }
QPushButton:pressed { background:#585b70; }
QPushButton#cap_btn { background:#89b4fa; color:#1e1e2e; font-weight:bold; border:none; }
QPushButton#cap_btn:hover { background:#b4befe; }
QPushButton#rec_btn { background:#f38ba8; color:#1e1e2e; font-weight:bold; border:none; }
QPushButton#rec_btn:hover { background:#eba0ac; }
QPushButton#rec_btn[rec="1"] { background:#a6e3a1; color:#1e1e2e; }
QTableWidget { background:#181825; gridline-color:#313244; border:none;
    selection-background-color:#313244; border-radius:8px; }
QTableWidget::item { padding:6px 10px; border-bottom:1px solid #2a2a3e; }
QTableWidget::item:selected { background:#313244; color:#89b4fa; }
QHeaderView::section { background:#181825; color:#7f849c; border:none;
    border-bottom:2px solid #313244; padding:8px 10px;
    font-weight:bold; font-size:11px; text-transform:uppercase; }
QGroupBox { border:1px solid #313244; border-radius:8px; margin-top:8px;
    padding-top:8px; font-weight:bold; color:#89b4fa; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }
QLineEdit, QKeySequenceEdit { background:#181825; border:1px solid #45475a;
    border-radius:6px; padding:6px 10px; color:#cdd6f4; }
QLineEdit:focus, QKeySequenceEdit:focus { border-color:#89b4fa; }
QComboBox { background:#181825; border:1px solid #45475a; border-radius:6px;
    padding:5px 10px; color:#cdd6f4; min-width:80px; }
QComboBox::drop-down { border:none; }
QComboBox QAbstractItemView { background:#181825; border:1px solid #45475a;
    selection-background-color:#313244; }

QCheckBox::indicator { width:16px; height:16px; border-radius:4px;
    border:1px solid #45475a; background:#181825; }
QCheckBox::indicator:checked { background:#89b4fa; border-color:#89b4fa; }
QTabWidget::pane { border:1px solid #313244; border-radius:8px; background:#181825; }
QTabBar::tab { background:#181825; color:#7f849c; border:none;
    padding:8px 18px; border-radius:6px; margin-right:2px; }
QTabBar::tab:selected { background:#313244; color:#cdd6f4; }
QTabBar::tab:hover:!selected { background:#252535; }
QListWidget { background:#181825; border:none; border-radius:8px; outline:none; }
QListWidget::item { padding:5px 8px; border-radius:4px; }
QListWidget::item:selected { background:#313244; color:#89b4fa; }
QListWidget::item:hover { background:#252535; }
QDialog { background:#1e1e2e; }
QTextEdit { background:#181825; border:1px solid #45475a; border-radius:6px;
    color:#cdd6f4; padding:8px; }
QScrollArea { border:none; }
/* ── Scrollbars ─────────────────── */
QScrollBar:vertical { background:#181825; width:8px; border-radius:4px; margin:0; }
QScrollBar::handle:vertical { background:#45475a; border-radius:4px; min-height:20px; }
QScrollBar::handle:vertical:hover { background:#585b70; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical,  QScrollBar::sub-page:vertical  { background:none; }
QScrollBar:horizontal { background:#181825; height:8px; border-radius:4px; margin:0; }
QScrollBar::handle:horizontal { background:#45475a; border-radius:4px; min-width:20px; }
QScrollBar::handle:horizontal:hover { background:#585b70; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:none; }
QRadioButton {
    spacing: 8px;
    color: #eeeeee;
    background: transparent;
  width: 18px;
    height: 18px;
}

QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border-radius: 9px;
    border: 2px solid #555555;
    background-color: #1a1a1a;
}

QRadioButton::indicator:hover {
    border-color: #00a2ed;
    border-radius: 9px;
}

/* Styl dla zaznaczonego przycisku - większa i wyraźniejsza kropka */
QRadioButton::indicator:checked {
    background-color: #00a2ed; /* Żywy niebieski ShareX */
    border: 2px solid #1f1f1f; /* Ramka w kolorze tła tworzy efekt "wycięcia" */
    image: none;
    width: 14px;
    height: 14px;
    border-radius: 8px;
}

/* Dodatkowa obwódka dla zaznaczonego, żeby bardziej "świecił" */
QRadioButton::indicator:checked:hover {
    border-color: #1f1f1f;
    border-radius: 9px;
    background-color: #33bfff;
  width: 18px;
    height: 18px;
}



/* Gray out disabled SpinBoxes */
QSpinBox:disabled {
    background-color: #2b2b2b;
    color: #555555;
    border: 1px solid #444444;
}

QSpinBox::up-button {
    width: 0px;
} 

QSpinBox::down-button 
{
    width: 0px;
}

QSpinBox { background:#181825; border:1px solid #45475a; border-radius:6px;
    padding:5px 8px; color:#cdd6f4; }

/* Gray out disabled Checkboxes and Labels */
QCheckBox:disabled, QLabel:disabled {
    color: #555555;
}

/* Gray out disabled Sliders */
QSlider::handle:horizontal:disabled {
    background: #444444;
}

/* Gray out disabled Checkboxes and Labels */
QCheckBox:disabled, QLabel:disabled {
    color: #555555;
}

/* Gray out disabled Sliders */
QSlider::handle:horizontal:disabled {
    background: #444444;
}
"""


def _tray_icon():
    pm = QPixmap(32, 32); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor(137, 180, 250))); p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, 32, 32)
    p.setBrush(QBrush(QColor(30, 30, 46)))
    p.drawRoundedRect(4, 10, 24, 16, 3, 3)
    p.setBrush(QBrush(QColor(137, 180, 250))); p.drawEllipse(11, 13, 10, 10)
    p.setBrush(QBrush(QColor(30, 30, 46))); p.drawEllipse(13, 15, 6, 6)
    p.drawRect(22, 7, 5, 4); p.end()
    return QIcon(pm)

import urllib.request

# ─────────────────────────────────────────────────────────────────────────────
#  IMAGE EDITOR COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
#  IMAGE EDITOR COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────


from PySide6.QtWidgets import QInputDialog, QGraphicsScene, QGraphicsView, QGraphicsItem, \
    QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem, \
    QGraphicsTextItem

from PySide6.QtWidgets import QColorDialog, QSpinBox, QCheckBox


class CropOverlayItem(QGraphicsRectItem):
    """
    Specjalny element reprezentujący obszar kadrowania.
    Rysuje przyciemnienie poza obszarem cięcia oraz białą ramkę.
    """
    def __init__(self, full_rect):
        super().__init__(full_rect)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(1000)  # Zawsze na samej górze
        self.setPen(QPen(Qt.GlobalColor.transparent))
        self.setBrush(Qt.GlobalColor.transparent)

    def paint(self, painter, option, widget=None):
        # Maska musi pokrywać też powiększony obszar (poza oryginalną sceną)
        scene_rect = self.scene().sceneRect().united(self.rect()) if self.scene() else self.rect()
        scene_rect = scene_rect.adjusted(-5000, -5000, 5000, 5000) # Ogromny margines, by pokryć wszystko przy oddaleniu
        crop_rect = self.rect()

        # 1. Rysowanie przyciemnionego tła poza wyciętym obszarem (maska)
        path = QPainterPath()
        path.addRect(scene_rect)
        path.addRect(crop_rect)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)  # Metoda nieparzysto-parzysta domyślnie zostawi środek pusty

        # 2. Rysowanie wyraźnej białej ramki kadrowania
        painter.setBrush(Qt.GlobalColor.transparent)
        pen = QPen(Qt.GlobalColor.white, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(crop_rect)

# ─────────────────────────────────────────────────────────────────────────────
#  IMPROVED IMAGE EDITOR COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
#  IMAGE EDITOR COMPONENTS (FIXED)
# ─────────────────────────────────────────────────────────────────────────────

class EmptyCanvasDialog(QDialog):
    """Dialog for creating a blank canvas with a chosen or custom size."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Empty Canvas")
        self.setFixedWidth(480)
        self._aspect_locked = False
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        TEMPLATES = ["1920x1080 px", "1280x720 px", "1024x1024 px"]

        # ── Template section: radio on its own row, combobox below ────────────
        self._rb_template = QRadioButton("Select Template")
        self._rb_template.setChecked(True)
        layout.addWidget(self._rb_template)

        self._cb_template = QComboBox()
        self._cb_template.addItems(TEMPLATES)
        self._cb_template.setEnabled(False)
        self._cb_template.setFixedHeight(32)
        cb_row = QHBoxLayout()
        cb_row.setContentsMargins(28, 0, 0, 0)
        cb_row.addWidget(self._cb_template)
        layout.addLayout(cb_row)

        layout.addSpacing(6)

        # ── Custom section: radio on its own row, inputs below ────────────────
        self._rb_custom = QRadioButton("Custom Size:")
        layout.addWidget(self._rb_custom)

        self._sp_w = QSpinBox()
        self._sp_w.setRange(1, 16000)
        self._sp_w.setValue(800)
        self._sp_w.setSuffix(" px")
        self._sp_w.setFixedHeight(32)
        self._sp_w.setKeyboardTracking(True)
        self._sp_w.lineEdit().setReadOnly(False)

        self._btn_chain = QPushButton("🔗")
        self._btn_chain.setCheckable(True)
        self._btn_chain.setFixedSize(32, 32)
        self._btn_chain.setToolTip("Lock aspect ratio")
        self._btn_chain.setStyleSheet(
            "QPushButton { border: 1px solid #555; border-radius: 6px; font-size: 18px; padding: 0px; }"
            "QPushButton:checked { background: #4a9eff; border-color: #4a9eff; }"
        )

        self._sp_h = QSpinBox()
        self._sp_h.setRange(1, 16000)
        self._sp_h.setValue(600)
        self._sp_h.setSuffix(" px")
        self._sp_h.setFixedHeight(32)
        self._sp_h.setKeyboardTracking(True)
        self._sp_h.lineEdit().setReadOnly(False)

        inputs_row = QHBoxLayout()
        inputs_row.setContentsMargins(28, 0, 0, 0)
        inputs_row.addWidget(QLabel("W:"))
        inputs_row.addWidget(self._sp_w, 1)
        inputs_row.addWidget(self._btn_chain)
        inputs_row.addWidget(QLabel("H:"))
        inputs_row.addWidget(self._sp_h, 1)
        layout.addLayout(inputs_row)

        layout.addSpacing(8)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_box = QHBoxLayout()
        btn_ok = QPushButton("Create")
        btn_ok.setFixedHeight(32)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFixedHeight(32)
        btn_ok.setDefault(True)
        btn_box.addStretch()
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

        # ── Signals ───────────────────────────────────────────────────────────
        self._rb_template.toggled.connect(self._on_mode_changed)
        self._rb_custom.toggled.connect(self._on_mode_changed)
        self._btn_chain.toggled.connect(self._on_chain_toggled)
        self._sp_w.valueChanged.connect(self._on_w_changed)
        self._sp_h.valueChanged.connect(self._on_h_changed)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        # Sync field states with the default-checked radio button
        # (setChecked above was called before the toggled signals were connected).
        self._on_mode_changed()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _on_mode_changed(self):
        template = self._rb_template.isChecked()
        self._cb_template.setEnabled(template)
        self._sp_w.setEnabled(not template)
        self._sp_h.setEnabled(not template)
        self._btn_chain.setEnabled(not template)

    def _on_chain_toggled(self, locked):
        self._aspect_locked = locked

    def _on_w_changed(self, val):
        if self._aspect_locked and not self._updating:
            self._updating = True
            self._sp_h.setValue(val)
            self._updating = False

    def _on_h_changed(self, val):
        if self._aspect_locked and not self._updating:
            self._updating = True
            self._sp_w.setValue(val)
            self._updating = False

    def canvas_size(self):
        """Return (width, height) based on current selection."""
        if self._rb_template.isChecked():
            text = self._cb_template.currentText()          # e.g. "1920x1080 px"
            w, h = text.split(" ")[0].split("x")
            return int(w), int(h)
        return self._sp_w.value(), self._sp_h.value()


class ImageEditorStartDialog(QDialog):
    def __init__(self, parent=None, default_dir=""):
        super().__init__(parent)
        self.setWindowTitle("Image Editor - Select Source")
        self.setFixedSize(320, 220)
        self.result_image = None
        self.default_dir = default_dir

        layout = QVBoxLayout(self)
        btn_file      = QPushButton("📂 Open screenshot file")
        btn_clipboard = QPushButton("📋 Open screenshot from clipboard")
        btn_web       = QPushButton("🌐 Open image from web")
        btn_empty     = QPushButton(" Empty Canvas")
        btn_empty.setIcon(_svg_icon(_SVG_EMPTY_CANVAS, 24))
        btn_empty.setIconSize(QSize(20, 20))

        btn_file.clicked.connect(self.open_file)
        btn_clipboard.clicked.connect(self.open_clipboard)
        btn_web.clicked.connect(self.open_web)
        btn_empty.clicked.connect(self.open_empty_canvas)

        layout.addWidget(btn_file)
        layout.addWidget(btn_clipboard)
        layout.addWidget(btn_web)
        layout.addWidget(btn_empty)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Image", self.default_dir, "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.result_image = QPixmap(path)
            self.accept()

    def open_clipboard(self):
        pixmap = QApplication.clipboard().pixmap()
        if not pixmap.isNull():
            self.result_image = pixmap
            self.accept()
        else:
            QMessageBox.warning(self, "Error", "No image in clipboard!")

    def open_web(self):
        url, ok = QInputDialog.getText(self, "Web Image", "Enter Image URL:")
        if ok and url:
            try:
                data = urllib.request.urlopen(url).read()
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                if not pixmap.isNull():
                    self.result_image = pixmap
                    self.accept()
                else:
                    raise Exception("Invalid image")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed: {e}")

    def open_empty_canvas(self):
        dlg = EmptyCanvasDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            w, h = dlg.canvas_size()
            img = QImage(w, h, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            self.result_image = QPixmap.fromImage(img)
            self.accept()

# ─────────────────────────────────────────────────────────────────────────────
#  ADVANCED IMAGE EDITOR COMPONENTS (WITH ZOOM, PAN, RESIZE & LIVE UPDATES)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
#  ADVANCED IMAGE EDITOR (FIXED POSITIONING, SCALING & ALPHA CHANNEL)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
#  FINAL IMAGE EDITOR (FIXED SCALING, DELETE, SAVE AS & CTRL PROPORTIONS)
# ─────────────────────────────────────────────────────────────────────────────

class FreehandItem(QGraphicsPathItem):
    def __init__(self, path, canvas=None):
        super().__init__(path)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self._rect = path.boundingRect()
        self._base_path = path

    def rect(self):
        return self._rect

    def setRect(self, rect):
        self.prepareGeometryChange()
        self._rect = rect
        base_rect = self._base_path.boundingRect()
        if base_rect.width() == 0 or base_rect.height() == 0:
            return
        sx = rect.width() / base_rect.width()
        sy = rect.height() / base_rect.height()
        
        from PySide6.QtGui import QTransform
        transform = QTransform()
        transform.translate(rect.x(), rect.y())
        transform.scale(sx, sy)
        transform.translate(-base_rect.x(), -base_rect.y())
        self.setPath(transform.map(self._base_path))

    def update_base_path(self):
        # Usunięto prepareGeometryChange(), ponieważ natywne setPath() 
        # wywołane chwilę wcześniej w locie już zaktualizowało drzewo sceny Qt.
        self._base_path = self.path()
        self._rect = self._base_path.boundingRect()

    def _open_edit_dialog(self, screen_pos=None):
        """Open the shared options window through whichever window owns this
        item (Capture Region overlay or Image Editor)."""
        host = _find_options_host(self)
        if host is not None:
            host._edit_item(self)
            return
        dlg = FreehandEditDialog(self.pen().width(), self.pen().color(), target_item=self)
        _set_dialog_on_top(dlg)                       # standalone fallback
        if screen_pos:
            dlg.move(screen_pos)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.restore_original()
            new_width, new_color = dlg.result_data()
            pen = self.pen()
            pen.setWidth(new_width)
            pen.setColor(new_color)
            self.setPen(pen)
            self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_edit_dialog(event.screenPos().toPoint())
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu()
        edit_act = menu.addAction("✏️ Edit")
        del_act  = menu.addAction("🗑️ Delete")
        action = menu.exec(event.screenPos())
        if action == edit_act:
            self._open_edit_dialog(event.screenPos().toPoint())
        elif action == del_act:
            if self.scene():
                self.scene().removeItem(self)
        event.accept()

    def boundingRect(self):
        return self._rect.adjusted(-5, -50, 5, 5)

    def paint(self, painter, option, widget=None):
        from PySide6.QtWidgets import QStyle
        option.state &= ~QStyle.StateFlag.State_Selected
        painter.setPen(self.pen())
        painter.drawPath(self.path())

        if self.isSelected():
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # Dashed bounding box
            painter.setPen(QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(self._rect)
            # Corner & edge resize handles (identical to ResizableRectItem)
            r = self._rect
            for hp in [
                r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight(),
                QPointF(r.center().x(), r.top()),
                QPointF(r.center().x(), r.bottom()),
                QPointF(r.left(),  r.center().y()),
                QPointF(r.right(), r.center().y()),
            ]:
                painter.setBrush(QBrush(Qt.GlobalColor.white))
                painter.setPen(QPen(QColor(60, 120, 255), 1.5))
                painter.drawEllipse(hp, 5, 5)
            # Rotation handle above top edge (identical to ResizableRectItem)
            rot_pt = QPointF(r.center().x(), r.top() - 30)
            painter.setBrush(QBrush(QColor(180, 255, 180, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(rot_pt, 6, 6)
            painter.setPen(QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(r.center().x(), r.top()), rot_pt)
            painter.restore()


class LineItem(QGraphicsLineItem):
    def __init__(self, line, canvas):
        super().__init__(line)
        self.canvas = canvas
     
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.active_handle = None

    def boundingRect(self):
        # Powiększony obszar zapobiega smużeniu przy przemieszczaniu grubego pędzla
        extra = (self.pen().width() + 30) / (self.canvas.transform().m11() if self.canvas else 1)
        return super().boundingRect().adjusted(-extra, -extra, extra, extra)

    def paint(self, painter, option, widget=None):
        # Rysujemy samą linię
        painter.setPen(self.pen())
        painter.drawLine(self.line())
        
        # Draw handles only when selected
        if self.isSelected():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # Handle size: 16px screen-space, scale-independent
            s = 16 / (self.canvas.transform().m11() if self.canvas else 1)
            r = s / 2
            # p1 endpoint handle — white fill, blue border
            painter.setBrush(QBrush(Qt.GlobalColor.white))
            painter.setPen(QPen(Qt.GlobalColor.blue, 2.0))
            painter.drawEllipse(self.line().p1(), r, r)
            # p2 endpoint handle — white fill, blue border
            painter.drawEllipse(self.line().p2(), r, r)
            # Width handle at midpoint — yellow fill, dark border
            mid = QPointF((self.line().p1().x() + self.line().p2().x()) / 2,
                          (self.line().p1().y() + self.line().p2().y()) / 2)
            painter.setBrush(QBrush(QColor(255, 220, 50, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 2.0))
            painter.drawEllipse(mid, r, r)

    def _handle_hit_radius(self):
        """Hit-test radius for endpoint/width handles, in item-local coords.
        Fixed at 18px screen-space so handles are easy to grab regardless of zoom."""
        return 18 / (self.canvas.transform().m11() if getattr(self, 'canvas', None) else 1)

    def shape(self):
        """Override shape() so Qt's scene hit-testing covers the full handle surfaces,
        not just the thin line geometry. Includes a fat stroke along the line body
        plus circular regions at p1, p2, and the midpoint (width handle)."""
        r = self._handle_hit_radius()
        line = self.line()
        p1, p2 = line.p1(), line.p2()
        mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)

        # Fat stroke along the line body
        body = QPainterPath()
        body.moveTo(p1)
        body.lineTo(p2)
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self.pen().width() + 4, r * 2))
        result = stroker.createStroke(body)

        # Add circular hit zones for each handle
        for pt in (p1, p2, mid):
            circle = QPainterPath()
            circle.addEllipse(pt, r, r)
            result = result.united(circle)

        return result

    def mousePressEvent(self, event):
        p = event.pos()
        p1, p2 = self.line().p1(), self.line().p2()
        # Use true Euclidean distance (not manhattanLength) for accurate circular hit zones
        r = self._handle_hit_radius()

        if math.hypot(p.x() - p1.x(), p.y() - p1.y()) <= r:
            self.active_handle = 'p1'
            event.accept()
        elif math.hypot(p.x() - p2.x(), p.y() - p2.y()) <= r:
            self.active_handle = 'p2'
            event.accept()
        else:
            mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
            if math.hypot(p.x() - mid.x(), p.y() - mid.y()) <= r:
                self.active_handle = 'width'
                self._width_drag_start_pos = p
                self._width_drag_start_w = self.pen().width()
                event.accept()
            else:
                self.active_handle = None
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, 'active_handle', None) in ('p1', 'p2'):
            # Shift held: move the whole line (as if dragging it with the mouse)
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                d = event.scenePos() - event.lastScenePos()
                self.setPos(self.pos() + d)
                event.accept()
                return
            self.prepareGeometryChange()
            line = self.line()
            new_pos = event.pos()

            # 45-degree angle snapping constraint
            alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            mid = QPointF((line.p1().x() + line.p2().x()) / 2,
                          (line.p1().y() + line.p2().y()) / 2)
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                # Alt: the midpoint is the anchor (line is resized from its center)
                anchor = mid if alt else (line.p2() if self.active_handle == 'p1' else line.p1())
                dx, dy = new_pos.x() - anchor.x(), new_pos.y() - anchor.y()
                snapped_angle = round(math.degrees(math.atan2(dy, dx)) / 45) * 45
                d = math.hypot(dx, dy)
                new_pos = QPointF(anchor.x() + d * math.cos(math.radians(snapped_angle)),
                                  anchor.y() + d * math.sin(math.radians(snapped_angle)))

            mirrored = QPointF(2 * mid.x() - new_pos.x(), 2 * mid.y() - new_pos.y())
            if self.active_handle == 'p1':
                line.setP1(new_pos)
                if alt:
                    line.setP2(mirrored)
            else:
                line.setP2(new_pos)
                if alt:
                    line.setP1(mirrored)

            self.setLine(line)
            event.accept()

        elif getattr(self, 'active_handle', None) == 'width':
            line = self.line()
            if line.length() > 0:
                start_pos = getattr(self, '_width_drag_start_pos', event.pos())
                start_w = getattr(self, '_width_drag_start_w', self.pen().width())
                dy = event.pos().y() - start_pos.y()
                new_w = max(1, int(start_w + dy * 0.3))

                pen = self.pen()
                pen.setWidth(new_w)
                self.setPen(pen)
                self.prepareGeometryChange()
                self.update()

                if hasattr(self, 'canvas') and self.canvas:
                    win = self.canvas.window() if hasattr(self.canvas, 'window') else None
                    if win and hasattr(win, 'spin'):
                        win.spin.blockSignals(True)
                        win.spin.setValue(new_w)
                        win.spin.blockSignals(False)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # Always fully release the state flag on mouse up
        if getattr(self, 'active_handle', None):
            self.active_handle = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class HighlightTextItem(QGraphicsTextItem):

    def boundingRect(self):
        pad = getattr(self, 'highlight_padding', 0)
        ol_extra = getattr(self, 'outline_width', 2) if getattr(self, 'outline_enabled', False) else 0
        extra = pad + ol_extra
        return super().boundingRect().adjusted(-extra, -extra, extra, extra)

    def paint(self, painter, option, widget=None):
        from PySide6.QtCore import QRectF
        doc_size = self.document().size()
        pad = getattr(self, 'highlight_padding', 0)

        # Draw highlight background before text
        if getattr(self, 'highlight_enabled', True):
            hl = getattr(self, 'highlight_color', None)
            if hl and hl.alpha() > 0:
                painter.save()
                painter.setBrush(QBrush(hl))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(QRectF(-pad, -pad,
                                        doc_size.width() + pad * 2,
                                        doc_size.height() + pad * 2))
                painter.restore()

        # Draw outline stroked onto each glyph (rendered before text so text sits on top)
        if getattr(self, 'outline_enabled', False):
            ol_color = getattr(self, 'outline_color', QColor(0, 0, 0))
            ol_width = getattr(self, 'outline_width', 2)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            doc = self.document()
            block = doc.begin()
            while block.isValid():
                layout = block.layout()
                if layout:
                    lay_pos = layout.position()
                    for li in range(layout.lineCount()):
                        line = layout.lineAt(li)
                        line_x = lay_pos.x() + line.position().x()
                        line_y = lay_pos.y() + line.position().y()
                        it = block.begin()
                        x_cursor = line_x
                        while not it.atEnd():
                            frag = it.fragment()
                            if frag.isValid():
                                fmt = frag.charFormat()
                                fnt = fmt.font()
                                if not fnt.family():
                                    fnt = self.font()
                                fm = QFontMetricsF(fnt)
                                frag_text = frag.text()
                                path = QPainterPath()
                                path.addText(x_cursor, line_y + fm.ascent(), fnt, frag_text)
                                x_cursor += fm.horizontalAdvance(frag_text)
                                stroker = QPainterPathStroker()
                                stroker.setWidth(ol_width * 2)
                                stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                                stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
                                outline_path = stroker.createStroke(path)
                                painter.setPen(Qt.PenStyle.NoPen)
                                painter.setBrush(QBrush(ol_color))
                                painter.drawPath(outline_path)
                            it += 1
                block = block.next()
            painter.restore()

        super().paint(painter, option, widget)

class ResizableRectItem(QGraphicsRectItem):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging_width = False
        # Corner rounding in item units (px). Painting clamps it to half of
        # the shorter side, so it can never produce a malformed shape.
        self.corner_radius = 0.0
        self._radius_drag_off = None   # grab offset while the green handle is dragged

    def boundingRect(self):
        return super().boundingRect().adjusted(-8, -50, 5, 20)

    def _width_handle_pos(self) -> QPointF:
        r = self.rect()
        return QPointF(r.center().x(), r.bottom() + 12)

    def _max_radius(self) -> float:
        r = self.rect()
        return max(0.0, min(r.width(), r.height()) / 2.0)

    def _effective_radius(self) -> float:
        return max(0.0, min(float(self.corner_radius), self._max_radius()))

    def _radius_handle_pos(self) -> QPointF:
        """Purple corner-radius handle: sits just above the top-left corner
        and slides right along the top edge as the rounding grows."""
        r = self.rect()
        return QPointF(r.left() + self._effective_radius(), r.top() - 18)

    def paint(self, painter, option, widget=None):
        from PySide6.QtWidgets import QStyle
        option.state &= ~QStyle.StateFlag.State_Selected
        rad = self._effective_radius()
        if rad > 0:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(self.pen())
            painter.setBrush(self.brush())
            painter.drawRoundedRect(self.rect(), rad, rad)
        else:
            super().paint(painter, option, widget)
        if self.isSelected():
            pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(self.rect())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # Corner & edge resize handles
            r = self.rect()
            handles = [r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight(),
                       QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()),
                       QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y())]
            for hp in handles:
                painter.setBrush(QBrush(Qt.GlobalColor.white))
                painter.setPen(QPen(QColor(60, 120, 255), 1.5))
                painter.drawEllipse(hp, 5, 5)
            # Rotation handle above top edge
            rot_pt = QPointF(r.center().x(), r.top() - 30)
            painter.setBrush(QBrush(QColor(180, 255, 180, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(rot_pt, 6, 6)
            painter.setPen(QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(r.center().x(), r.top()), rot_pt)
            # Width handle below bottom edge
            wp = self._width_handle_pos()
            painter.setBrush(QBrush(QColor(255, 220, 50, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(wp, 8, 8)
            # Corner-radius handle: purple dot with black outline at the top-left corner
            rp = self._radius_handle_pos()
            painter.setBrush(QBrush(QColor(168, 85, 247)))
            painter.setPen(QPen(QColor(0, 0, 0), 1.5))
            painter.drawEllipse(rp, 6, 6)


    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            wp = self._width_handle_pos()
            p  = event.pos()
            if math.hypot(p.x() - wp.x(), p.y() - wp.y()) <= 14:
                self._dragging_width = True
                self._drag_start_pos = p
                self._drag_start_w   = self.pen().width()
                event.accept()
                return
        self._dragging_width = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging_width:
            rect   = self.rect()
            centre = rect.center()
            wp     = self._width_handle_pos()
            ref_dx = wp.x() - centre.x()
            ref_dy = wp.y() - centre.y()
            ref_len = math.hypot(ref_dx, ref_dy) or 1.0
            dp = event.pos() - self._drag_start_pos
            projection = (dp.x() * ref_dx + dp.y() * ref_dy) / ref_len
            new_w = max(1, int(self._drag_start_w + projection * 0.15))
            pen = self.pen()
            pen.setWidth(new_w)
            self.setPen(pen)
            self.prepareGeometryChange()
            self.update()
            # Live-update the Size spinner in the toolbar
            win = self.scene().views()[0].window() if self.scene() and self.scene().views() else None
            if win and hasattr(win, 'spin'):
                win.spin.blockSignals(True)
                win.spin.setValue(new_w)
                win.spin.blockSignals(False)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging_width = False
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event):
        if self.isSelected():
            wp = self._width_handle_pos()
            p  = event.pos()
            if math.hypot(p.x() - wp.x(), p.y() - wp.y()) <= 14:
                self.setCursor(Qt.CursorShape.SizeVerCursor)
                return
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

class ResizableEllipseItem(QGraphicsEllipseItem):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging_width = False

    def boundingRect(self):
        return super().boundingRect().adjusted(-5, -50, 5, 20)

    def _width_handle_pos(self) -> QPointF:
        r = self.rect()
        return QPointF(r.center().x(), r.bottom() + 12)

    def paint(self, painter, option, widget=None):
        from PySide6.QtWidgets import QStyle
        option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, widget)
        if self.isSelected():
            pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(self.rect())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = self.rect()
            handles = [r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight(),
                       QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()),
                       QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y())]
            for hp in handles:
                painter.setBrush(QBrush(Qt.GlobalColor.white))
                painter.setPen(QPen(QColor(60, 120, 255), 1.5))
                painter.drawEllipse(hp, 5, 5)
            rot_pt = QPointF(r.center().x(), r.top() - 30)
            painter.setBrush(QBrush(QColor(180, 255, 180, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(rot_pt, 6, 6)
            painter.setPen(QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(r.center().x(), r.top()), rot_pt)
            wp = self._width_handle_pos()
            painter.setBrush(QBrush(QColor(255, 220, 50, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(wp, 8, 8)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            wp = self._width_handle_pos()
            p  = event.pos()
            if math.hypot(p.x() - wp.x(), p.y() - wp.y()) <= 14:
                self._dragging_width = True
                self._drag_start_pos = p
                self._drag_start_w   = self.pen().width()
                event.accept()
                return
        self._dragging_width = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging_width:
            rect   = self.rect()
            centre = rect.center()
            wp     = self._width_handle_pos()
            ref_dx = wp.x() - centre.x()
            ref_dy = wp.y() - centre.y()
            ref_len = math.hypot(ref_dx, ref_dy) or 1.0
            dp = event.pos() - self._drag_start_pos
            projection = (dp.x() * ref_dx + dp.y() * ref_dy) / ref_len
            new_w = max(1, int(self._drag_start_w + projection * 0.15))
            pen = self.pen()
            pen.setWidth(new_w)
            self.setPen(pen)
            self.prepareGeometryChange()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging_width = False
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event):
        if self.isSelected():
            wp = self._width_handle_pos()
            p  = event.pos()
            if math.hypot(p.x() - wp.x(), p.y() - wp.y()) <= 14:
                self.setCursor(Qt.CursorShape.SizeVerCursor)
                return
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

def _pixmap_item_ratio(item):
    """Native width/height ratio of an image item, or None for other items."""
    if isinstance(item, ResizablePixmapItem):
        pm = item.pixmap
        if pm.height() > 0 and pm.width() > 0:
            return pm.width() / pm.height()
    return None


def _aspect_locked_rect(new_rect: QRectF, handle: str, ratio: float) -> QRectF:
    """Ctrl-resize for images: keep the image's own proportions.
    Corner handles follow the dominant drag direction; edge handles follow the
    dragged axis and grow/shrink symmetrically on the other one."""
    has_h = 'L' in handle or 'R' in handle
    has_v = 'T' in handle or 'B' in handle
    if has_h and has_v:
        w = max(new_rect.width(), new_rect.height() * ratio)
    elif has_v:
        w = new_rect.height() * ratio
    else:
        w = new_rect.width()
    w = max(w, 1.0)
    h = w / ratio
    c = new_rect.center()
    left = new_rect.right() - w if 'L' in handle else (new_rect.left() if 'R' in handle else c.x() - w / 2)
    top = new_rect.bottom() - h if 'T' in handle else (new_rect.top() if 'B' in handle else c.y() - h / 2)
    return QRectF(left, top, w, h)


class ResizablePixmapItem(QGraphicsRectItem):
    """
    Niestandardowy element obrazka oparty na Rectangle, by zapewnić
    kompatybilność z istniejącą mechaniką skalowania obiektów (uchwyty, setRect).
    """
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.pixmap = pixmap
        # Ustaw początkowy rozmiar prostokąta taki jak rozmiar obrazka
        self.setRect(QRectF(pixmap.rect()))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

    def paint(self, painter, option, widget=None):
        # Rysowanie obrazka przeskalowanego dynamicznie do wymiarów rect()
        painter.drawPixmap(self.rect().toRect(), self.pixmap)
        
        # Jeśli obrazek jest zaznaczony, możemy narysować przerywaną ramkę
        if self.isSelected():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(self.rect())
            r = self.rect()
            handles = [r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight(),
                       QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()),
                       QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y())]
            for hp in handles:
                painter.setBrush(QBrush(Qt.GlobalColor.white))
                painter.setPen(QPen(QColor(60, 120, 255), 1.5))
                painter.drawEllipse(hp, 5, 5)
            rot_pt = QPointF(r.center().x(), r.top() - 30)
            painter.setBrush(QBrush(QColor(180, 255, 180, 230)))
            painter.setPen(QPen(QColor(60, 60, 60), 1.5))
            painter.drawEllipse(rot_pt, 6, 6)
            painter.setPen(QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(r.center().x(), r.top()), rot_pt)

    def boundingRect(self):
        return super().boundingRect().adjusted(-5, -50, 5, 5)

class ImageEditorTextDialog(QDialog):
    def __init__(self, text="", font_size=40, parent=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Edit Text")
        lay = QVBoxLayout(self)
        
        lay.addWidget(QLabel("Text:"))
        self.edit = QTextEdit()
        self.edit.setPlainText(text)
        self.edit.setMinimumHeight(100)
        lay.addWidget(self.edit)
        
        row = QHBoxLayout()
        row.addWidget(QLabel("Font size:"))
        self.spin_size = QSpinBox()
        self.spin_size.setRange(1, 200)
        self.spin_size.setValue(font_size)
        row.addWidget(self.spin_size)
        lay.addLayout(row)
        
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

class EditorCanvas(QGraphicsView):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.rotate_icon_pixmap = QPixmap("🔄")
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        # Enable keyboard focus for Delete key
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Allow dropping image files / dragged web images straight onto the canvas
        self.setAcceptDrops(True)

        self.bg_item = self.scene.addPixmap(pixmap)
        self.bg_item.setZValue(-100)
        self.bg_pixmap = pixmap
        
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        
        # State
        self.current_tool = "Select"
        self.start_point = None
        self.current_item = None
        self.is_dirty = False
        self.resizing_item = None
        self.resize_handle = None # 'T', 'B', 'L', 'R', 'TL', 'TR', 'BL', 'BR'
        self.crop_item = None     # Referencja do aktywnej ramki kadrowania
        self._rubber_band_active = False  # True while dragging an empty-space
                                           # selection rectangle (Select tool)

        # Rubber-band (marquee) multi-select: click+drag on empty canvas space
        # with the Select tool selects every item the rectangle touches —
        # same end result as Ctrl+clicking each one individually. Clicking
        # directly on an item still just drags/selects it as before; Qt only
        # starts the rubber band when the press lands on empty space.
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        # ── Snapping (magnet) — enabled by default ──────────────────────────
        self.snap_enabled = True
        self.snap_options = {
            'half':    True,   # snap to canvas half (horizontal/vertical)
            'images':  True,   # snap to edges/center of other images
            'corners': True,   # snap to canvas corners
            'scaling': True,   # snapping also applies while resizing objects
        }
        
        # Drawing props: every tool keeps its OWN settings (colour, width, …)
        self.prefs = _ToolPrefs(width=5, font_size=40)
        self._pan_start = None
        
        # Massive sceneRect allows infinite panning regardless of zoom
        bg_rect = QRectF(pixmap.rect())
        self.scene.setSceneRect(bg_rect.center().x() - 50000, bg_rect.center().y() - 50000, 100000, 100000)

        # Pre-bake checkerboard tile into a QPixmap once — reused every paint call
        self._checker_tile = self._make_checker_tile(10, QColor(65, 65, 65), QColor(96, 96, 96))
        self._checker_cache: QPixmap | None = None   # full-size cache, rebuilt on image resize
        self._checker_cache_size: tuple[int, int] = (-1, -1)

        # Undo / Redo history (see push_undo/undo/redo below)
        self._undo_stack = []
        self._redo_stack = []
        self._undo_limit = 40
        self._erase_stroke_active = False  # avoid one snapshot per erased pixel

    def keyPressEvent(self, event):
        """Handle Delete key to remove selected items, Ctrl+D to duplicate."""
        if event.key() == Qt.Key.Key_Delete:
            to_remove = [i for i in self.scene.selectedItems()
                         if i != self.bg_item and i != self.crop_item]
            if to_remove:
                self.push_undo()
            for item in to_remove:
                self.scene.removeItem(item)
                self.is_dirty = True
        elif (event.key() == Qt.Key.Key_D and
              event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._duplicate_selected_at_cursor()
        super().keyPressEvent(event)

    def _duplicate_selected_at_cursor(self):
        """Duplicate selected items and place copies centred exactly at the cursor position."""
        selected = [i for i in self.scene.selectedItems()
                    if i not in (self.bg_item, self.crop_item)
                    and not isinstance(i, _MarkerItem)]
        if not selected:
            return
        self.push_undo()
        cursor_scene = self.mapToScene(self.mapFromGlobal(QCursor.pos()))
        new_items = []
        for item in selected:
            dup = self._clone_item(item)
            if dup is None:
                continue
            # Use the visual rect centre (not boundingRect which has large margins)
            # to position the duplicate exactly under the cursor.
            try:
                item_center_scene = item.mapToScene(item.rect().center())
            except AttributeError:
                # LineItem / ArrowItem use line(), not rect()
                try:
                    ln = item.line()
                    item_center_scene = item.mapToScene(
                        QPointF((ln.x1() + ln.x2()) / 2, (ln.y1() + ln.y2()) / 2))
                except AttributeError:
                    item_center_scene = item.scenePos()
            offset = item_center_scene - item.scenePos()
            dup.setPos(cursor_scene - offset)
            self.scene.addItem(dup)
            new_items.append(dup)
        for item in self.scene.selectedItems():
            item.setSelected(False)
        for item in new_items:
            item.setSelected(True)
        if new_items:
            self.is_dirty = True

    def _clone_item(self, item):
        """Return a copy of a supported annotation item."""
        if isinstance(item, PixelateBlurItem):
            return _clone_pixblur(item)
        if isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
            cls = type(item)
            dup = cls()
            dup.setRect(QRectF(item.rect()))
            dup.setPen(QPen(item.pen()))
            dup.setBrush(QBrush(item.brush()))
            if isinstance(item, ResizableRectItem):
                dup.corner_radius = float(getattr(item, 'corner_radius', 0.0))
            dup.setRotation(item.rotation())
            dup.setTransformOriginPoint(item.transformOriginPoint())
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, HighlightRectItem):
            dup = HighlightRectItem()
            dup.setRect(QRectF(item.rect()))
            dup._color = QColor(item._color)
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, FreehandItem):
            dup = FreehandItem(QPainterPath(item.path()))
            dup.setPen(QPen(item.pen()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, LineItem):
            dup = LineItem(QLineF(item.line()), self)
            dup.setPen(QPen(item.pen()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, ArrowItem):
            dup = ArrowItem(QLineF(item.line()), self)
            dup.setPen(QPen(item.pen()))
            dup.head_style = getattr(item, 'head_style', ArrowItem.HEAD_SINGLE)
            dup._bend = QPointF(getattr(item, '_bend', QPointF(0.0, 0.0)))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, TextBubbleItem):
            dup = TextBubbleItem(item._text, QColor(item._fg_color), QColor(item._bg_color))
            dup._w = item._w
            dup._h = item._h
            dup._cone_rel = QPointF(item._cone_rel)
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
            dup = HighlightTextItem(item.toPlainText())
            dup.setFont(QFont(item.font()))
            dup.setDefaultTextColor(QColor(item.defaultTextColor()))
            dup.highlight_color   = QColor(getattr(item, 'highlight_color', QColor(0, 0, 0, 0)))
            dup.highlight_enabled = getattr(item, 'highlight_enabled', True)
            dup.highlight_padding = getattr(item, 'highlight_padding', 0)
            dup.outline_enabled   = getattr(item, 'outline_enabled', False)
            dup.outline_width     = getattr(item, 'outline_width', 2)
            dup.outline_color     = QColor(getattr(item, 'outline_color', QColor(0, 0, 0)))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, ResizablePixmapItem):
            dup = ResizablePixmapItem(QPixmap(item.pixmap))
            dup.setRect(QRectF(item.rect()))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        if isinstance(item, _MarkerItem):
            dup = _MarkerItem(QPointF(0, 0), item.number)
            dup._scale = item._scale
            dup._spike_offset = QPointF(item._spike_offset)
            dup._bg_color = QColor(getattr(item, '_bg_color', QColor(220, 50, 50)))
            dup._text_color = QColor(getattr(item, '_text_color', QColor(Qt.GlobalColor.white)))
            dup.setFlags(item.flags())
            dup.setPos(item.pos())
            return dup
        return None

    # ── Undo / Redo ───────────────────────────────────────────────────────
    # Snapshot-based history: instead of tracking every possible mutation as
    # a separate "command" (there are dozens of tools/dialogs that can touch
    # the scene), push_undo() captures a full, detached copy of the current
    # state right before a mutating action runs. Undo/redo simply swap the
    # live scene for a stored snapshot. Simpler to reason about and far less
    # error-prone than a per-action command stack, at the cost of a bit more
    # memory — acceptable for a screenshot annotation tool.
    def _snapshot(self):
        """Capture the background pixmap/position plus a standalone
        (detached) clone of every real item on the canvas. The crop overlay
        is transient UI state, never real content, so it's excluded."""
        items = [it for it in self.scene.items() if it not in (self.bg_item, self.crop_item)]
        items.reverse()  # scene.items() is topmost-first; keep paint order
        clones = []
        for it in items:
            clone = self._clone_item(it)
            if clone is not None:
                clone.setZValue(it.zValue())
                clones.append(clone)
        return {
            'bg_pixmap': self.bg_pixmap,
            'bg_pos': QPointF(self.bg_item.pos()),
            'items': clones,
        }

    def push_undo(self):
        """Call this right BEFORE a mutating action so it can be undone.
        Any fresh action invalidates the redo stack."""
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._notify_history_changed()

    def _restore_snapshot(self, snapshot):
        # Leaving Crop mode avoids a dangling overlay that no longer
        # matches the restored image bounds.
        win = self.window()
        if self.crop_item is not None and hasattr(win, 'select_tool'):
            win.select_tool("Select")

        self.scene.clearSelection()
        for it in list(self.scene.items()):
            if it is not self.bg_item:
                self.scene.removeItem(it)

        self.bg_pixmap = snapshot['bg_pixmap']
        self.bg_item.setPixmap(self.bg_pixmap)
        self.bg_item.setPos(snapshot['bg_pos'])

        bg_rect = self.bg_item.sceneBoundingRect()
        self.scene.setSceneRect(
            bg_rect.center().x() - 50000, bg_rect.center().y() - 50000, 100000, 100000)

        for stored in snapshot['items']:
            # Clone again on the way back in — the objects held in the
            # snapshot must stay pristine so they can be restored again
            # later (e.g. redo after undo after redo...).
            fresh = self._clone_item(stored)
            if fresh is None:
                continue
            fresh.setZValue(stored.zValue())
            self.scene.addItem(fresh)

        self.is_dirty = True
        self._notify_history_changed()

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        snapshot = self._undo_stack.pop()
        self._restore_snapshot(snapshot)

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        snapshot = self._redo_stack.pop()
        self._restore_snapshot(snapshot)

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def _notify_history_changed(self):
        win = self.window()
        if hasattr(win, '_update_undo_redo_buttons'):
            win._update_undo_redo_buttons()

    def wheelEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if event.angleDelta().y() > 0 else 0.8
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)

    # ── Drag & drop images onto the canvas ──────────────────────────────────
    @staticmethod
    def _is_image_path(path: str) -> bool:
        return os.path.splitext(path)[1].lower() in (
            '.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tif', '.tiff', '.ico')

    def _drag_has_image(self, event) -> bool:
        md = event.mimeData()
        if md.hasImage():
            return True
        if md.hasUrls():
            for url in md.urls():
                if url.isLocalFile():
                    if self._is_image_path(url.toLocalFile()):
                        return True
                else:
                    # Remote URL (e.g. an image dragged out of a web browser) —
                    # accept it here and validate/download on drop.
                    return True
        return False

    def dragEnterEvent(self, event):
        if self._drag_has_image(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._drag_has_image(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        md = event.mimeData()
        drop_scene_pos = self.mapToScene(event.position().toPoint())
        pixmaps = []

        if md.hasUrls():
            for url in md.urls():
                pm = None
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if self._is_image_path(path):
                        pm = QPixmap(path)
                else:
                    # Dragged straight from a web page (no local file) — download it.
                    try:
                        req = urllib.request.Request(
                            url.toString(), headers={"User-Agent": "Mozilla/5.0"})
                        data = urllib.request.urlopen(req, timeout=6).read()
                        pm = QPixmap()
                        pm.loadFromData(data)
                    except Exception as e:
                        print(f"[PyshareX] drag&drop: failed to fetch {url.toString()}: {e}")
                        pm = None
                if pm is not None and not pm.isNull():
                    pixmaps.append(pm)
        elif md.hasImage():
            img = md.imageData()
            pm = QPixmap.fromImage(img) if img is not None else None
            if pm is not None and not pm.isNull():
                pixmaps.append(pm)

        if not pixmaps:
            event.ignore()
            return

        self.push_undo()

        # Fan multiple dropped images out a little so they don't land exactly
        # on top of one another.
        offset = QPointF(0, 0)
        for pm in pixmaps:
            self._add_dropped_pixmap(pm, drop_scene_pos + offset)
            offset += QPointF(24, 24)

        event.acceptProposedAction()

    def _add_dropped_pixmap(self, pixmap, center_scene_pos):
        """Add a dropped/dragged-in image to the canvas as a movable,
        resizable layer, centred on the drop position. Images bigger than
        the canvas are scaled down (keeping aspect ratio) so a full-size
        photo doesn't land mostly off-screen."""
        bg_rect = self.bg_item.sceneBoundingRect()
        max_w = max(bg_rect.width(), 200)
        max_h = max(bg_rect.height(), 200)
        if pixmap.width() > max_w or pixmap.height() > max_h:
            pixmap = pixmap.scaled(
                int(max_w), int(max_h),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)

        item = ResizablePixmapItem(pixmap)
        item.setPos(center_scene_pos.x() - pixmap.width() / 2,
                    center_scene_pos.y() - pixmap.height() / 2)
        self.scene.addItem(item)
        self.scene.clearSelection()
        item.setSelected(True)
        self.is_dirty = True

    def _alt_line_start(self, end_pos, event):
        """Line/arrow start point: the press point, or (Alt held) its mirror
        across the press point so the press point becomes the line's center."""
        s = self.start_point
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            return QPointF(2 * s.x() - end_pos.x(), 2 * s.y() - end_pos.y())
        return s

    def _translate_item(self, item, delta: QPointF):
        """Move *item* by *delta* (scene units) — Shift-to-move modifier."""
        if isinstance(item, CropOverlayItem):
            # Crop frame is defined purely by its rect (pos stays at 0,0)
            item.prepareGeometryChange()
            item.setRect(item.rect().translated(delta))
        else:
            item.setPos(item.pos() + delta)
        item.update()

    def mousePressEvent(self, event):
        self.setFocus() # Ensure canvas has focus for keyboard events
        scene_pos = self.mapToScene(event.position().toPoint())
        self._last_move_scene = scene_pos

        if event.button() == Qt.MouseButton.MiddleButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._pan_start = event.position().toPoint()
            return

        if self.current_tool == "Eraser":
            if not self._erase_stroke_active:
                self.push_undo()
                self._erase_stroke_active = True
            self.erase_at(scene_pos)
            return

        if self.current_tool == "Crop":
            # W trybie Crop pozwalamy TYLKO na zmianę rozmiaru ramki kadrującej
            self.resizing_item, self.resize_handle = self.get_handle_at(scene_pos)
            if self.resizing_item and self.resizing_item == self.crop_item:
                self.start_point = scene_pos
                return
            return # Całkowicie blokujemy przesuwanie i rysowanie w trybie Crop

        if self.current_tool == "Select":
            # Detect which handle was clicked
            self.resizing_item, self.resize_handle = self.get_handle_at(scene_pos)
            if self.resizing_item:
                self.push_undo()
                self.start_point = scene_pos
                return # Block regular selection/drawing
            # If the press lands on empty canvas (or the background), Qt's
            # RubberBandDrag will start a marquee-selection rectangle instead
            # of moving anything — track that so mouseMoveEvent doesn't try
            # to apply move-snapping to whatever happens to be selected.
            clicked_item = self.itemAt(event.position().toPoint())
            self._rubber_band_active = clicked_item is None or clicked_item is self.bg_item
            if clicked_item is not None and clicked_item is not self.bg_item:
                # About to potentially drag-move this item — snapshot first.
                # (If the click turns out to be a no-op, e.g. just a select
                # with no drag, this leaves a harmless undo step that
                # restores an identical state.)
                self.push_undo()
            super().mousePressEvent(event)
            return

        # Drawing Mode
        self.is_dirty = True
        self.start_point = scene_pos
        self.push_undo()
        if self.current_tool == "Rectangle": 
            self.current_item = ResizableRectItem()
            self.current_item.corner_radius = float(self.prefs.get('rect', 'radius'))
        elif self.current_tool == "Circle": 
            self.current_item = ResizableEllipseItem()
        elif self.current_tool == "Line":
            self.current_item = LineItem(QLineF(self.start_point, self.start_point), self)
        elif self.current_tool == "Freehand":
            self._freehand_path = QPainterPath()
            self._freehand_path.moveTo(self.start_point)
            self.current_item = FreehandItem(self._freehand_path)
        elif self.current_tool == "Arrow":
            self.current_item = ArrowItem(
                QLineF(self.start_point, self.start_point), self)
            self.current_item.head_style = self.prefs.get('arrow', 'head')
        elif self.current_tool == "Highlight":
            self._hl_item = HighlightRectItem()
            self._hl_item.setRect(QRectF(self.start_point, self.start_point))
            self._hl_item._color = self.prefs.get('highlight', 'color')
            self._hl_item.setFlags(
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
            self.scene.addItem(self._hl_item)
            self.current_item = self._hl_item
            # keep drawing — mouseRelease will finalize
            self.start_point = scene_pos
            return
        elif self.current_tool == "Smart Eraser":
            fx = SmartEraserItem()
            fx.setRect(QRectF(self.start_point, self.start_point))
            fx.fill_color = _smart_eraser_sample(self.scene, self.start_point)
            fx.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                        QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                        QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
            self.scene.addItem(fx)
            self._fx_item = fx
            self.current_item = fx
        elif self.current_tool == "Spotlight":
            fx = SpotlightItem()
            fx.setRect(QRectF(self.start_point, self.start_point))
            fx.blur_radius = int(self.prefs.get('spotlight', 'blur'))
            fx.outline_enabled = bool(self.prefs.get('spotlight', 'ol_on'))
            fx.outline_width = int(self.prefs.get('spotlight', 'ol_w'))
            fx.outline_color = QColor(self.prefs.get('spotlight', 'ol'))
            fx.defer_draw = bool(self.prefs.get('spotlight', 'defer'))
            fx.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                        QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                        QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
            self.scene.addItem(fx)
            self._fx_item = fx
            self.current_item = fx
        elif self.current_tool == "Pixelate/Blur":
            fx = PixelateBlurItem()
            fx.setRect(QRectF(self.start_point, self.start_point))
            fx.mode = self.prefs.get('pixelate_blur', 'mode')
            fx.pixel_size = int(self.prefs.get('pixelate_blur', 'pixel'))
            fx.blur_radius = int(self.prefs.get('pixelate_blur', 'blur'))
            fx.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                        QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                        QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
            self.scene.addItem(fx)
            self._fx_item = fx
            self.current_item = fx
            self.start_point = scene_pos
            return   # keep dragging — mouseRelease finalizes
        elif self.current_tool == "Bubble":
            # Bubble is completed on mouseRelease — nothing to create yet
            pass
        elif self.current_tool == "Marker":
            # Number after the highest existing marker so switching tools
            # and coming back continues the sequence correctly.
            existing = [it for it in self.scene.items() if isinstance(it, _MarkerItem)]
            number = max((it.number for it in existing), default=0) + 1
            # Inherit scale from the most recently placed marker (highest number),
            # falling back to 1.0 if no markers remain on the canvas.
            last_scale = next(
                (it._scale for it in sorted(existing, key=lambda m: m.number, reverse=True)),
                1.0)
            marker = _MarkerItem(self.start_point, number)
            marker._scale = float(self.prefs.get('marker', 'scale'))
            marker._bg_color = self.prefs.get('marker', 'bg')
            marker._text_color = self.prefs.get('marker', 'fg')
            marker.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
            self.scene.addItem(marker)
            self.is_dirty = True
            self.start_point = None
            return  # marker is complete on press — no drag needed

        # Właściwości i dodanie do sceny wykonujemy tylko raz dla wszystkich narzędzi
        if self.current_item:
            self.apply_props(self.current_item)
            self.scene.addItem(self.current_item)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.MiddleButton and getattr(self, '_pan_start', None) is not None:
            delta = event.position().toPoint() - self._pan_start
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_start = event.position().toPoint()
            return

        scene_pos = self.mapToScene(event.position().toPoint())
        
        if self.current_tool in ["Select", "Crop"] and not self.resizing_item:
            _, handle = self.get_handle_at(scene_pos)
            self.update_cursor_by_handle(handle)

        if self.resizing_item and (event.buttons() & Qt.MouseButton.LeftButton):
            # Shift held: move the item/crop frame instead of resizing/rotating it
            last = getattr(self, '_last_move_scene', None)
            self._last_move_scene = scene_pos
            if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and last is not None:
                self._translate_item(self.resizing_item, scene_pos - last)
            else:
                self.handle_resize_logic(scene_pos, event.modifiers() & Qt.KeyboardModifier.ControlModifier,
                                         bool(event.modifiers() & Qt.KeyboardModifier.AltModifier))
            self.is_dirty = True
            return

        if self.current_tool == "Eraser" and event.buttons() & Qt.MouseButton.LeftButton:
            self.erase_at(scene_pos)
            return

        if not self.current_item:
            super().mouseMoveEvent(event)
            if (self.current_tool == "Select" and not self.resizing_item
                    and not self._rubber_band_active
                    and event.buttons() & Qt.MouseButton.LeftButton):
                self._apply_move_snap()
            return

        # Shift held: drag the whole shape (start point follows the mouse)
        last = getattr(self, '_last_move_scene', None)
        self._last_move_scene = scene_pos
        if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier and last is not None
                and self.start_point is not None
                and self.current_tool in ("Rectangle", "Circle", "Highlight", "Pixelate/Blur", "Spotlight", "Smart Eraser",
                                          "Line", "Arrow")):
            self.start_point = self.start_point + (scene_pos - last)

        # Regular Drawing
        rect = QRectF(self.start_point, scene_pos).normalized()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Proportional drawing (Square/Circle)
            side = max(rect.width(), rect.height())
            rect = QRectF(self.start_point.x(), self.start_point.y(), 
                          side if scene_pos.x() > self.start_point.x() else -side,
                          side if scene_pos.y() > self.start_point.y() else -side).normalized()

        # Alt held: the start point is the CENTER of the shape (works with Ctrl too)
        if (event.modifiers() & Qt.KeyboardModifier.AltModifier
                and self.current_tool in ("Rectangle", "Circle", "Highlight", "Pixelate/Blur", "Spotlight", "Smart Eraser")):
            c = self.start_point
            rect = QRectF(c.x() - rect.width(), c.y() - rect.height(),
                          rect.width() * 2, rect.height() * 2)

        if self.current_tool in ["Rectangle", "Circle"]: self.current_item.setRect(rect)
        elif self.current_tool == "Highlight":
            if getattr(self, '_hl_item', None):
                self._hl_item.setRect(rect)
        elif self.current_tool in ("Pixelate/Blur", "Spotlight", "Smart Eraser"):
            if getattr(self, '_fx_item', None):
                self._fx_item.setRect(rect)
        elif self.current_tool == "Line":
            end_pos = scene_pos
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                dx = end_pos.x() - self.start_point.x()
                dy = end_pos.y() - self.start_point.y()
                dist = math.hypot(dx, dy)
                snapped_angle = round(math.degrees(math.atan2(dy, dx)) / 45) * 45
                end_pos = QPointF(
                    self.start_point.x() + dist * math.cos(math.radians(snapped_angle)),
                    self.start_point.y() + dist * math.sin(math.radians(snapped_angle)))
            self.current_item.setLine(QLineF(self._alt_line_start(end_pos, event), end_pos))
        elif self.current_tool == "Freehand":
            if self.current_item and getattr(self, '_freehand_path', None) is not None:
                new_pos = scene_pos
                if self._freehand_path.currentPosition() != new_pos:
                    self._freehand_path.lineTo(new_pos)
                    self.current_item.setPath(self._freehand_path)
                    if hasattr(self.current_item, 'update_base_path'):
                        self.current_item.update_base_path()
        elif self.current_tool == "Arrow":
            if self.current_item:
                end_pos = scene_pos
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    dx = end_pos.x() - self.start_point.x()
                    dy = end_pos.y() - self.start_point.y()
                    dist = math.hypot(dx, dy)
                    snapped_angle = round(math.degrees(math.atan2(dy, dx)) / 45) * 45
                    end_pos = QPointF(
                        self.start_point.x() + dist * math.cos(math.radians(snapped_angle)),
                        self.start_point.y() + dist * math.sin(math.radians(snapped_angle)))
                self.current_item.setLine(QLineF(self._alt_line_start(end_pos, event), end_pos))

    def mouseReleaseEvent(self, event):
        self._rubber_band_active = False
        self._erase_stroke_active = False
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update_cursor_by_handle(None)
            self._pan_start = None
            return
        # Reset WIDTH / RADIUS drag state on release
        if self.resizing_item and hasattr(self.resizing_item, '_width_drag_active'):
            self.resizing_item._width_drag_active = False
        if self.resizing_item and hasattr(self.resizing_item, '_radius_drag_off'):
            self.resizing_item._radius_drag_off = None
        
        if self.current_tool == "Text" and self.start_point:
            _pos = QPointF(self.start_point)
            _tp = self.prefs.all('text')

            def _do_text_dialog():
                dlg = _TextInputDialog(_tp['color'], self.window(), state=_tp)
                _set_dialog_on_top(dlg)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    _store_dialog_in_prefs('text', dlg, self.prefs)
                    if hasattr(self.window(), '_refresh_tool_ui'):
                        self.window()._refresh_tool_ui()
                    txt, new_fsz, col, hl, hl_on, hl_pad, ol_on, ol_w, ol_col = dlg.result_data()
                    if txt.strip():
                        item = HighlightTextItem(txt)
                        item.setPlainText(txt)
                        item.highlight_color   = hl
                        item.highlight_enabled = hl_on
                        item.highlight_padding = hl_pad
                        item.outline_enabled   = ol_on
                        item.outline_width     = ol_w
                        item.outline_color     = ol_col
                        item.setDefaultTextColor(col)
                        item.setFont(QFont("Arial", new_fsz))
                        item.setFlags(
                            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
                        item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                        item.setPos(_pos)
                        self.scene.addItem(item)
                        self.is_dirty = True

            QTimer.singleShot(0, _do_text_dialog)
        elif self.current_tool == "Bubble" and self.start_point:
            dlg = _BubbleInputDialog(self.prefs.get('bubble', 'fg'), self,
                                     self.prefs.get('bubble', 'bg'))
            _set_dialog_on_top(dlg)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                txt, fg_col, bg_col = dlg.result_data()
                self.prefs.update('bubble', fg=fg_col, bg=bg_col)
                if hasattr(self.window(), '_refresh_tool_ui'):
                    self.window()._refresh_tool_ui()
                if txt.strip():
                    item = TextBubbleItem(txt, fg_col, bg_col)
                    item.setPos(self.start_point)
                    item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                                  QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                                  QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
                    self.scene.addItem(item)
                    self.is_dirty = True
        # Highlight: finalize — remove if too small (prevents zero-rect crash)
        elif self.current_tool == "Highlight":
            hl = getattr(self, '_hl_item', None)
            if hl:
                r = hl.rect()
                if r.width() < 3 or r.height() < 3:
                    self.scene.removeItem(hl)
                else:
                    self.is_dirty = True
            self._hl_item = None
        # Pixelate/Blur: finalize — drop accidental click-sized rectangles
        elif self.current_tool in ("Pixelate/Blur", "Spotlight", "Smart Eraser"):
            fx = getattr(self, '_fx_item', None)
            if fx:
                r = fx.rect()
                if r.width() < 3 or r.height() < 3:
                    self.scene.removeItem(fx)
                else:
                    self.is_dirty = True
            self._fx_item = None
        # Arrow: apply pen props after drawing
        elif self.current_tool == "Arrow" and self.current_item:
            pen = QPen(self.prefs.get('arrow', 'color'), self.prefs.get('arrow', 'width'))
            self.current_item.setPen(pen)
            self.is_dirty = True
        # Marker is fully handled in mousePressEvent — nothing to do on release
        elif self.current_tool == "Marker":
            self.start_point = None
            return

        self.current_item = None
        self.resizing_item = None
        self._freehand_path = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        scene_pos = self.mapToScene(event.position().toPoint())
        item = self.scene.itemAt(scene_pos, self.transform())
        win = self.window()
        key = _prefs_key_for_item(item) if item is not None else None
        # Same options window as the toolbar ⚙ button. Shapes/lines/arrows only
        # open it with the Select tool (so double-clicking while drawing is harmless).
        if (key is not None and hasattr(win, '_edit_item')
                and (key in ('text', 'freehand', 'highlight', 'bubble', 'marker', 'pixelate_blur', 'spotlight')
                     or self.current_tool == "Select")):
            win._edit_item(item)
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        scene_pos = self.mapToScene(event.pos())  # QContextMenuEvent uses .pos(), not .position()
        item = self.scene.itemAt(scene_pos, self.transform())
        if item in (self.bg_item, self.crop_item) or item is None:
            super().contextMenuEvent(event)
            return
        key = _prefs_key_for_item(item)
        win = self.window()
        menu = QMenu(self)
        edit_act = None
        pix_act = blur_act = None
        if isinstance(item, PixelateBlurItem) and not isinstance(item, (SpotlightItem, SmartEraserItem)):
            pix_act, blur_act = _pixblur_add_mode_actions(menu, item)
        if key is not None and hasattr(win, '_edit_item'):
            edit_act = menu.addAction({'text': "✏️ Edit Text", 'highlight': "✏️ Edit Highlight",
                                       'bubble': "✏️ Edit Bubble", 'marker': "✏️ Edit Marker",
                                       'arrow': "✏️ Edit Arrow"}.get(key, "✏️ Edit"))
        straighten_act = None
        if isinstance(item, ArrowItem):
            straighten_act = menu.addAction("📏 Straighten Arrow")
            straighten_act.setEnabled(getattr(item, '_bend', QPointF(0.0, 0.0)) != QPointF(0.0, 0.0))
        dup_act = None if isinstance(item, _MarkerItem) else menu.addAction("⧉ Duplicate")
        del_act = (menu.addAction("🗑️ Delete")
                   if isinstance(item, (HighlightRectItem, TextBubbleItem, _MarkerItem,
                                        FreehandItem, ArrowItem, PixelateBlurItem)) else None)
        action = menu.exec(event.globalPos())
        if action is None:
            return
        if pix_act is not None and action in (pix_act, blur_act):
            self.push_undo()
            _pixblur_set_mode(item, PixelateBlurItem.MODE_PIXELATE if action == pix_act
                              else PixelateBlurItem.MODE_BLUR, self.prefs)
            self.is_dirty = True
            if hasattr(win, '_refresh_tool_ui'):
                win._refresh_tool_ui()
            return
        if action == edit_act:
            win._edit_item(item)
        elif action == straighten_act:
            self.push_undo()
            # Reset the bend/break point so the arrow is drawn as a
            # perfectly straight line from p1 to p2 again.
            item.prepareGeometryChange()
            item._bend = QPointF(0.0, 0.0)
            item.update()
            self.is_dirty = True
        elif action == dup_act:
            self.push_undo()
            dup = self._clone_item(item)
            if dup:
                dup.setPos(item.pos() + QPointF(20, 20))
                if dup.scene() is None:
                    self.scene.addItem(dup)
                self.is_dirty = True
        elif action == del_act:
            self.push_undo()
            self.scene.removeItem(item)
            self.is_dirty = True

    def get_handle_at(self, pos):
        """Returns (item, handle_name) if mouse is over a resize handle of a selected item."""
        selected = self.scene.selectedItems()

        # With more than one item selected (e.g. several separate Freehand
        # strokes making up a drawing, picked with the rubber-band or
        # Ctrl+click), each one still draws its own full set of resize
        # handles — those overlap into a cluster covering most of the
        # group, so a plain click meant to select/move the group would
        # often land on some other item's resize handle by accident and
        # start resizing it instead. With a multi-selection, dragging
        # should just move the whole group, so don't offer any resize
        # handles at all until only one item is selected.
        if len(selected) > 1:
            return None, None

        for item in selected:

            # TextBubbleItem has its own BR resize handle and cone handle
            if isinstance(item, TextBubbleItem):
                local_pos = item.mapFromScene(pos)
                # Cone (spike) handle
                if item._over_cone_handle(local_pos):
                    return item, 'CONE'
                # Bottom-right resize handle
                if item._over_resize_handle_br(local_pos):
                    return item, 'BR'
                continue

            if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, CropOverlayItem, ResizablePixmapItem, FreehandItem)):
                # Używamy lokalnych współrzędnych, żeby obrót nie psuł wykrywania krawędzi
                local_pos = item.mapFromScene(pos)
                rect = item.rect()
                m = 10 / self.transform().m11() # Scale-aware margin

                # Corner-radius handle (purple dot, top-left corner) — Rectangle only
                if isinstance(item, ResizableRectItem):
                    rp = item._radius_handle_pos()
                    r_hit = 12 / self.transform().m11()
                    if math.hypot(local_pos.x() - rp.x(), local_pos.y() - rp.y()) <= r_hit:
                        return item, 'RADIUS'

                # Width handle (yellow dot below bottom edge) for Rect and Ellipse — check FIRST
                if isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
                    wp = item._width_handle_pos()
                    hit_radius = 14 / self.transform().m11()
                    if math.hypot(local_pos.x() - wp.x(), local_pos.y() - wp.y()) <= hit_radius:
                        return item, 'WIDTH'
                
                # Rotation handle — disabled for markers (they scale uniformly instead)
                if not isinstance(item, (CropOverlayItem, _MarkerItem, PixelateBlurItem)):
                    rot_pt = QPointF(rect.center().x(), rect.top() - 30)
                    if abs(local_pos.x() - rot_pt.x()) < m * 1.5 and abs(local_pos.y() - rot_pt.y()) < m * 1.5:
                        return item, 'ROTATE'

                L, R = abs(local_pos.x() - rect.left()) < m, abs(local_pos.x() - rect.right()) < m
                T, B = abs(local_pos.y() - rect.top()) < m, abs(local_pos.y() - rect.bottom()) < m
                
                if L and T: return item, 'TL'
                if R and T: return item, 'TR'
                if L and B: return item, 'BL'
                if R and B: return item, 'BR'
                if L: return item, 'L'
                if R: return item, 'R'
                if T: return item, 'T'
                if B: return item, 'B'
        return None, None

    def update_cursor_by_handle(self, handle):
        if not handle: self.setCursor(Qt.CursorShape.ArrowCursor)
        elif handle == 'ROTATE': self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif handle == 'WIDTH': self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif handle == 'RADIUS': self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif handle in ['TL', 'BR']: self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif handle in ['TR', 'BL']: self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif handle in ['L', 'R']: self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif handle in ['T', 'B']: self.setCursor(Qt.CursorShape.SizeVerCursor)

    def handle_resize_logic(self, pos, proportional, from_center=False):
        import math
        item = self.resizing_item

        # --- TextBubbleItem: cone drag or BR resize ---
        if isinstance(item, TextBubbleItem):
            local_pos = item.mapFromScene(pos)
            if self.resize_handle == 'CONE':
                item.prepareGeometryChange()
                item._cone_rel = QPointF(local_pos.x() - item._w / 2,
                                         local_pos.y() - item._h / 2)
                item.update()
            elif self.resize_handle == 'BR':
                new_w = max(item.MIN_SIZE, local_pos.x())
                new_h = max(item.MIN_SIZE, local_pos.y())
                item.prepareGeometryChange()
                item._w = new_w
                item._h = new_h
                item._auto_grow()
                item.update()
            return

        # --- _MarkerItem: uniform scale only (always equal W/H) ---
        if isinstance(item, _MarkerItem):
            local_pos = item.mapFromScene(pos)
            dist = math.hypot(local_pos.x(), local_pos.y())
            dist = max(dist, item.RADIUS * 0.2)  # minimum size guard
            item.prepareGeometryChange()
            item._scale = dist / item.RADIUS
            item.update()
            self.scene.update()
            return

        # --- 0. Corner-radius handle (purple dot): right = rounder, left = sharper ---
        if self.resize_handle == 'RADIUS' and isinstance(item, ResizableRectItem):
            local_pos = item.mapFromScene(pos)
            r = item.rect()
            if item._radius_drag_off is None:
                item._radius_drag_off = local_pos.x() - (r.left() + item._effective_radius())
            new_r = max(0.0, min(local_pos.x() - item._radius_drag_off - r.left(),
                                 item._max_radius()))
            item.prepareGeometryChange()
            item.corner_radius = new_r
            item.update()
            self.prefs.set('rect', 'radius', new_r)    # new rectangles inherit the last value
            self.is_dirty = True
            return

        # --- 1. OBSŁUGA OBROTU ---
        if self.resize_handle == 'ROTATE':
            import math
            # Odśwież widok przed zmianą kąta
            item.update() 
            
            center_scene = item.mapToScene(item.rect().center())
            diff = pos - center_scene
            
            angle = math.degrees(math.atan2(diff.y(), diff.x())) + 90
            
            if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
                angle = round(angle / 45) * 45
            
            item.setTransformOriginPoint(item.rect().center())
            item.setRotation(angle)
            
            # Odśwież widok po zmianie kąta oraz wymuś odświeżenie całej sceny w tym rejonie
            item.update()
            self.scene.update()
            return

        # --- 2. OBSŁUGA SKALOWANIA Z ZACHOWANIEM OBROTU ---
        old_rect = item.rect()
        if self.resize_handle != 'WIDTH':
            pos = self._snap_resize_point(item, pos)
        local_pos = item.mapFromScene(pos)
        
        # Określamy punkt stały (przeciwległy do łapanego uchwytu), żeby figura nie "odfrunęła"
        fixed_local = QPointF()
        if 'L' in self.resize_handle: fixed_local.setX(old_rect.right())
        elif 'R' in self.resize_handle: fixed_local.setX(old_rect.left())
        else: fixed_local.setX(old_rect.center().x())

        if 'T' in self.resize_handle: fixed_local.setY(old_rect.bottom())
        elif 'B' in self.resize_handle: fixed_local.setY(old_rect.top())
        else: fixed_local.setY(old_rect.center().y())
        
        if from_center:
            fixed_local = old_rect.center()
        old_scene_fixed = item.mapToScene(fixed_local)

        # Obliczamy nowe wymiary (rect)
        left, top, right, bottom = old_rect.left(), old_rect.top(), old_rect.right(), old_rect.bottom()
        
        if 'L' in self.resize_handle: left = local_pos.x()
        if 'R' in self.resize_handle: right = local_pos.x()
        if 'T' in self.resize_handle: top = local_pos.y()
        if 'B' in self.resize_handle: bottom = local_pos.y()
        
        new_rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
        
        if proportional:
            ratio = _pixmap_item_ratio(item)
            if ratio:
                # Images: Ctrl keeps the image's own proportions (not a square)
                new_rect = _aspect_locked_rect(new_rect, self.resize_handle, ratio)
            else:
                side = max(new_rect.width(), new_rect.height())
                if 'L' in self.resize_handle: left = right - side
                else: right = left + side
                
                if 'T' in self.resize_handle: top = bottom - side
                else: bottom = top + side
                
                new_rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()

        if from_center:
            # Alt: grow/shrink symmetrically around the rect's center
            c = old_rect.center()
            hw = abs(local_pos.x() - c.x()) if ('L' in self.resize_handle or 'R' in self.resize_handle) else old_rect.width() / 2
            hh = abs(local_pos.y() - c.y()) if ('T' in self.resize_handle or 'B' in self.resize_handle) else old_rect.height() / 2
            if proportional:
                ratio = _pixmap_item_ratio(item)
                if ratio:
                    h_ax = 'L' in self.resize_handle or 'R' in self.resize_handle
                    v_ax = 'T' in self.resize_handle or 'B' in self.resize_handle
                    if h_ax and v_ax:
                        hw = max(hw, hh * ratio)
                    elif v_ax:
                        hw = hh * ratio
                    hw = max(hw, 0.5)
                    hh = hw / ratio
                else:
                    hw = hh = max(hw, hh)
            new_rect = QRectF(c.x() - hw, c.y() - hh, hw * 2, hh * 2)

        # Usunięto ograniczenie intersected(), aby pozwolić na przeciąganie 
        # narzędzia Crop poza krawędzie w celu powiększenia płótna
        # if isinstance(item, CropOverlayItem):
        #     new_rect = new_rect.intersected(self.scene.sceneRect())
            
        # Width handle drag — change pen width vertically
        if self.resize_handle == 'WIDTH' and isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
            local_pos = item.mapFromScene(pos)
            if not getattr(item, '_width_drag_active', False):
                item._width_drag_active = True
                item._width_drag_start_pos = local_pos
                item._width_drag_start_w = item.pen().width()
            dy = local_pos.y() - item._width_drag_start_pos.y()
            new_w = max(1, int(item._width_drag_start_w + dy * 0.3))
            pen = item.pen()
            pen.setWidth(new_w)
            item.setPen(pen)
            item.prepareGeometryChange()
            item.update()
            # Live-update the Size spinner in the toolbar
            win = self.window()
            if hasattr(win, 'spin'):
                win.spin.blockSignals(True)
                win.spin.setValue(new_w)
                win.spin.blockSignals(False)
            return

        # Zastosowanie wymiarów i NAPRAWA przesunięcia po obrocie
        item.setRect(new_rect)
        item.setTransformOriginPoint(new_rect.center())
        
        new_scene_fixed = item.mapToScene(fixed_local)
        delta = old_scene_fixed - new_scene_fixed
        item.setPos(item.pos() + delta)

    # ── Snapping (magnet) ──────────────────────────────────────────────────
    def _snap_threshold(self):
        """Snap threshold in screen pixels, converted to scene units."""
        return 10 / max(self.transform().m11(), 0.0001)

    @staticmethod
    def _visual_scene_rect(item):
        """Scene-space rect of an item's actual visible content.

        Several annotation items (ResizablePixmapItem, ResizableRectItem,
        ResizableEllipseItem, HighlightRectItem...) override boundingRect()
        with extra padding — up to -50px on top — so their selection
        handles and the rotate-handle icon above the shape don't get
        clipped. That padded rect is correct for painting/hit-testing, but
        using sceneBoundingRect() for snapping meant every snap comparison
        was silently offset by that same padding: top-edge/corner snapping
        was off by ~50 scene px (very noticeable), left/right/bottom by a
        few px. Snapping must use the item's real geometry (rect()) instead.
        """
        if hasattr(item, 'rect'):
            try:
                return item.mapRectToScene(item.rect())
            except Exception:
                pass
        return item.sceneBoundingRect()

    def _collect_snap_targets(self, exclude_item=None):
        """Returns (x_list, y_list) of scene coordinates to snap to,
        based on the currently checked snap options."""
        vt, ht = [], []
        if not self.snap_enabled:
            return vt, ht
        bg = self.bg_item.sceneBoundingRect()
        if self.snap_options.get('half'):
            vt.append(bg.center().x())
            ht.append(bg.center().y())
        if self.snap_options.get('corners'):
            vt += [bg.left(), bg.right()]
            ht += [bg.top(), bg.bottom()]
        if self.snap_options.get('images'):
            for it in self.scene.items():
                if isinstance(it, ResizablePixmapItem) and it is not exclude_item:
                    r = self._visual_scene_rect(it)
                    vt += [r.left(), r.center().x(), r.right()]
                    ht += [r.top(), r.center().y(), r.bottom()]
        return vt, ht

    def _snap_move_delta(self, item):
        """Computes a (dx, dy) correction so the moved item's edge/center
        snaps to the nearest snap target."""
        if not self.snap_enabled:
            return 0.0, 0.0
        vt, ht = self._collect_snap_targets(exclude_item=item)
        threshold = self._snap_threshold()
        r = self._visual_scene_rect(item)

        best_dx, best_dx_abs = 0.0, threshold
        for cand in (r.left(), r.center().x(), r.right()):
            for t in vt:
                d = t - cand
                if abs(d) < best_dx_abs:
                    best_dx_abs, best_dx = abs(d), d

        best_dy, best_dy_abs = 0.0, threshold
        for cand in (r.top(), r.center().y(), r.bottom()):
            for t in ht:
                d = t - cand
                if abs(d) < best_dy_abs:
                    best_dy_abs, best_dy = abs(d), d

        return best_dx, best_dy

    def _apply_move_snap(self):
        """Called while dragging selected items with the Select tool."""
        if not self.snap_enabled:
            return
        for it in self.scene.selectedItems():
            if it in (self.bg_item, self.crop_item):
                continue
            # Snapping only applies to unrotated items — for rotated ones
            # the edges aren't axis-aligned in scene space.
            if getattr(it, 'rotation', lambda: 0)() != 0:
                continue
            dx, dy = self._snap_move_delta(it)
            if dx or dy:
                it.setPos(it.pos() + QPointF(dx, dy))

    def _snap_resize_point(self, item, pos):
        """Snaps the mouse point (in scene coordinates) used for resizing
        to the nearest snap targets, based on the active handle."""
        if not (self.snap_enabled and self.snap_options.get('scaling')):
            return pos
        if getattr(item, 'rotation', lambda: 0)() != 0:
            return pos
        handle = self.resize_handle or ''
        vt, ht = self._collect_snap_targets(exclude_item=item)
        threshold = self._snap_threshold()
        x, y = pos.x(), pos.y()

        if ('L' in handle or 'R' in handle) and vt:
            best = min(vt, key=lambda t: abs(t - x))
            if abs(best - x) < threshold:
                x = best
        if ('T' in handle or 'B' in handle) and ht:
            best = min(ht, key=lambda t: abs(t - y))
            if abs(best - y) < threshold:
                y = best

        return QPointF(x, y)

    def erase_at(self, pos):
        for item in self.scene.items(pos):
            if item != self.bg_item:
                self.scene.removeItem(item)
                self.is_dirty = True

    @staticmethod
    def _make_checker_tile(tile: int, dark: QColor, light: QColor) -> QPixmap:
        """Build a 2×2-tile QPixmap used as a brush — created once, never changed."""
        pm = QPixmap(tile * 2, tile * 2)
        pm.fill(dark)
        p = QPainter(pm)
        p.fillRect(0,    0,    tile, tile, light)
        p.fillRect(tile, tile, tile, tile, light)
        p.end()
        return pm

    def _get_checker_cache(self, w: int, h: int) -> QPixmap:
        """Return a full-image-size checkerboard pixmap, rebuilt only when size changes."""
        if (w, h) == self._checker_cache_size and self._checker_cache is not None:
            return self._checker_cache
        pm = QPixmap(w, h)
        p = QPainter(pm)
        p.drawTiledPixmap(0, 0, w, h, self._checker_tile)
        p.end()
        self._checker_cache = pm
        self._checker_cache_size = (w, h)
        return pm

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        # Base visual boundaries on the image, not the massive sceneRect
        bg_rect = self.bg_item.sceneBoundingRect() if hasattr(self, 'bg_item') and self.bg_item else self.scene.sceneRect()

        # ── Checkerboard (transparent background indicator) ───────────────────
        # drawTiledPixmap is a single GPU blit — zero per-tile Python overhead
        w, h = int(bg_rect.width()), int(bg_rect.height())
        if w > 0 and h > 0:
            checker = self._get_checker_cache(w, h)
            painter.drawPixmap(bg_rect.topLeft(), checker)
        # ─────────────────────────────────────────────────────────────────────
        # NOTE: the "outside canvas" dark mask + border used to be drawn here,
        # but drawBackground() always paints BEFORE scene items, so any
        # floating image/annotation extending past the canvas edge was drawn
        # on top of it and stayed fully visible — this is what made Crop look
        # like it "left a piece uncropped" whenever several images were on
        # the canvas (the exported file was actually fine; only the on-canvas
        # preview was misleading). That mask now lives in drawForeground()
        # below, where it is guaranteed to paint on top of every item.

    def paint_effect_clear(self, painter, rect):
        """Called by Pixelate/Blur/Spotlight items before they draw their effect on screen:
        re-paint the canvas background (checkerboard) so the effect replaces the original
        content instead of being laid over it. `painter` is in scene coords, clipped."""
        bg_rect = self.bg_item.sceneBoundingRect()
        w, h = int(bg_rect.width()), int(bg_rect.height())
        if w > 0 and h > 0:
            painter.drawPixmap(bg_rect.topLeft(), self._get_checker_cache(w, h))

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        # Włączamy wysoką jakość rysowania, aby uniknąć rozmazania
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # ── Mask out everything outside the canvas bounds ──────────────────
        # Drawn here (after all items) so floating images/annotations that
        # extend past the current canvas edge — e.g. right after a crop, or
        # while a pasted image hasn't been trimmed yet — are actually hidden
        # from view instead of appearing as an "uncropped leftover".
        #
        # While the Crop tool overlay is active it already draws its own
        # dark mask + border around the (possibly larger, expand-canvas)
        # crop rectangle, so we skip this generic one to avoid re-darkening
        # the region the user is previewing.
        if not (getattr(self, 'crop_item', None) and self.crop_item.isVisible()):
            bg_rect = self.bg_item.sceneBoundingRect() if hasattr(self, 'bg_item') and self.bg_item else self.scene.sceneRect()

            # Dark overlay outside the image
            mask_path = QPainterPath()
            mask_path.setFillRule(Qt.FillRule.OddEvenFill)
            mask_path.addRect(QRectF(-500000, -500000, 1000000, 1000000))
            mask_path.addRect(bg_rect)
            painter.fillPath(mask_path, QColor(0, 0, 0, 120))

            # Blue dashed border around the image
            pen = QPen(QColor(137, 180, 250), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(bg_rect)
        # ─────────────────────────────────────────────────────────────────────

        for item in self.scene.selectedItems():
            # Pixelate/Blur, Spotlight and Smart Eraser are axis-aligned only: they have no rotation
            # handle (see get_handle_at). Drawing one here put a guide line + green dot 30px above
            # the item — outside its boundingRect, so Qt never repainted it and every move/resize
            # left stale dotted trails on the canvas.
            if (isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, ResizablePixmapItem, FreehandItem))
                    and not isinstance(item, (CropOverlayItem, PixelateBlurItem))):
                r = item.rect()
                # Punkty w układzie lokalnym
                top_center_local = QPointF(r.center().x(), r.top())
                handle_local = QPointF(r.center().x(), r.top() - 30)
                
                # Mapujemy na scenę, aby znać pozycję po obrocie
                p1 = item.mapToScene(top_center_local)
                p2 = item.mapToScene(handle_local)
                
                # Rysowanie linii pomocniczej
                painter.setPen(QPen(QColor(255, 255, 255, 200), 1.5, Qt.PenStyle.DashLine))
                painter.drawLine(p1, p2)
                
                # Rysowanie ikony rotate handle.png
                pix = QPixmap("rotate handle.png")
                if not pix.isNull():
                    # Obliczamy rozmiar ikony zależny od zoomu, by zawsze była czytelna
                    s = 22 / self.transform().m11()
                    target_rect = QRectF(p2.x() - s/2, p2.y() - s/2, s, s)
                    painter.drawPixmap(target_rect, pix, QRectF(pix.rect()))
                else:
                    # Fallback (kółko) jeśli plik nie istnieje
                    painter.setBrush(QColor(0, 255, 0))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(p2, 5/self.transform().m11(), 5/self.transform().m11())

    def apply_props(self, item):
        """Give a freshly drawn item the settings of ITS tool (never another's)."""
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        key = _prefs_key_for_item(item)
        if key in ('rect', 'circle', 'line', 'arrow', 'freehand'):
            c = self.prefs.get(key, 'color')
            if hasattr(item, "setPen"):
                item.setPen(QPen(c, self.prefs.get(key, 'width')))
            if hasattr(item, "setBrush"):
                filled = key in ('rect', 'circle') and self.prefs.get(key, 'fill')
                if filled:
                    item._fill_color = _prefs_fill_color(self.prefs, key)
                    item.setBrush(QBrush(item._fill_color))
                else:
                    item.setBrush(QBrush(Qt.GlobalColor.transparent))
        elif isinstance(item, QGraphicsTextItem):
            item.setDefaultTextColor(self.prefs.get('text', 'color'))
            item.setFont(QFont("Arial", int(self.prefs.get('text', 'size'))))

_EDITOR_TOOL_KEYS = {
    "Rectangle": 'rect', "Circle": 'circle', "Line": 'line', "Arrow": 'arrow',
    "Highlight": 'highlight', "Pixelate/Blur": 'pixelate_blur', "Spotlight": 'spotlight',
    "Freehand": 'freehand', "Bubble": 'bubble',
    "Text": 'text', "Marker": 'marker',
}

class ImageEditorWindow(QMainWindow):
    def __init__(self, pixmap, save_callback, default_dir="", parent=None,
                 annotation_mode=False):
        super().__init__(parent)
        self.setWindowTitle("PyshareX – Annotation Editor" if annotation_mode
                            else "PyshareX Image Editor")
        self.setWindowIcon(load_app_icon())
        self.save_callback = save_callback
        self.default_dir = default_dir
        self.saved = False
        self._annotation_mode = annotation_mode   # True = opened from "Capture Region"
        self._full_pixmap = pixmap                # keep for region-capture compositing
        self._last_draw_key = 'rect'              # last tool that has options (⚙ with Select active)

        central = QWidget(); self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Canvas jest tworzona tutaj (przed toolbarem), żeby przyciski toolbara
        # (np. magnes) mogły od razu odwoływać się do self.canvas.
        self.canvas = EditorCanvas(pixmap)

        # Row 1: Tools
        tbar = QHBoxLayout()
        self.btns = {}

        # ── Capture Region button (annotation mode only) ──────────────────────
        if annotation_mode:
            self.btn_capture_region = QPushButton("📷 Capture Region")
            self.btn_capture_region.setToolTip(
                "Draw a selection rectangle on the canvas, then click this button "
                "to save that region with all annotations")
            self.btn_capture_region.setStyleSheet(
                "background:#e74c3c; color:white; font-weight:bold; padding:5px 12px;")
            self.btn_capture_region.clicked.connect(self._capture_annotated_region)
            tbar.addWidget(self.btn_capture_region)
            tbar.addSpacing(8)

        for n, i in [("Select", None), ("Crop", None), ("Rectangle", None), ("Circle", "⭕"),
                     ("Line", "📏"), ("Arrow", None), ("Highlight", "🟨"), ("Pixelate/Blur", None), ("Spotlight", None), ("Smart Eraser", None),
                     ("Freehand", "✏️"),
                     ("Bubble", "💬"), ("Text", "T"), ("Marker", "📍"), ("Eraser", None)]:
            b = QPushButton(); b.setCheckable(True)
            b.setFixedSize(40, 40)
            b.setStyleSheet("""
                QPushButton {
                    font-size: 20px;
                    padding: 0px;
                    margin: 0px;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                }
                QPushButton:checked {
                    background-color: #3498db;
                    color: white;
                }
            """)
            if n == "Select":
                b.setIcon(_svg_icon(_SVG_SELECT, 32))
                b.setIconSize(QSize(32, 32))
            elif n == "Crop":
                b.setIcon(_svg_icon(_SVG_CROP, 32))
                b.setIconSize(QSize(32, 32))
                b.setToolTip("Crop")
            elif n == "Rectangle":
                b.setIcon(_svg_icon(_SVG_RECT_TOOL, 32))
                b.setIconSize(QSize(32, 32))
                b.setToolTip("Rectangle (Ctrl = square, Alt = from center, Shift = move)")
            elif n == "Circle":
                b.setIcon(_svg_icon(_SVG_CIRCLE_TOOL, 32))
                b.setIconSize(QSize(32, 32))
                b.setToolTip("Circle (Ctrl = circle, Alt = from center, Shift = move)")
            elif n == "Line":
                b.setIcon(_svg_icon(_SVG_LINE_TOOL, 32))
                b.setIconSize(QSize(32, 32))
                b.setToolTip("Line (Ctrl = snap 45°, Alt = from center, Shift = move)")
            elif n == "Arrow":
                b.setIcon(_svg_icon(_SVG_ARROW_TOOL, 32))
                b.setIconSize(QSize(32, 32))
                b.setToolTip("Arrow (Ctrl = snap 45°, Alt = from center, Shift = move)")
            elif n == "Pixelate/Blur":
                b.setIcon(_svg_icon(_SVG_PIXELATE_TOOL, 32))
                b.setIconSize(QSize(32, 32))
                b.setToolTip("Pixelate / Blur an area (mode + intensity: ⚙ options or right-click) (Ctrl = square, Alt = from center, Shift = move)")
            elif n == "Smart Eraser":
                b.setIcon(_svg_icon(_SVG_SMART_ERASER_TOOL, 32))
                b.setIconSize(QSize(32, 32))
                b.setToolTip("Smart Eraser — click picks the colour under the cursor, drag fills a rectangle with exactly that colour (Ctrl = square, Alt = from center, Shift = move)")
            elif n == "Spotlight":
                b.setIcon(_svg_icon(_SVG_SPOTLIGHT_TOOL, 32))
                b.setIconSize(QSize(32, 32))
                b.setToolTip("Spotlight — blur everything OUTSIDE the selected area (intensity: ⚙ options) (Ctrl = square, Alt = from center, Shift = move)")
            elif n == "Eraser":
                b.setIcon(_svg_icon(_SVG_ERASER, 32))
                b.setIconSize(QSize(32, 32))
                b.setToolTip("Eraser")
            else:
                b.setText(i)
            b.clicked.connect(lambda ch, name=n: self.select_tool(name))
            tbar.addWidget(b); self.btns[n] = b

        tbar.addSpacing(8)
        self._build_magnet_button(tbar)

        tbar.addSpacing(8)
        self.btn_undo = QPushButton("↩️")
        self.btn_undo.setFixedSize(40, 40)
        self.btn_undo.setToolTip("Undo (Ctrl+Z)")
        self.btn_undo.setStyleSheet("font-size: 18px; padding: 0px;")
        self.btn_undo.clicked.connect(self.canvas_undo)
        tbar.addWidget(self.btn_undo)

        self.btn_redo = QPushButton("↪️")
        self.btn_redo.setFixedSize(40, 40)
        self.btn_redo.setToolTip("Redo (Ctrl+Y)")
        self.btn_redo.setStyleSheet("font-size: 18px; padding: 0px;")
        self.btn_redo.clicked.connect(self.canvas_redo)
        tbar.addWidget(self.btn_redo)

        tbar.addStretch()
        
        # Dedykowany przycisk zatwierdzenia przycięcia (widoczny tylko w trybie Crop)
        self.btn_apply_crop = QPushButton("✂️ Apply Crop")
        self.btn_apply_crop.setStyleSheet("background: #e74c3c; color: white; font-weight: bold; padding: 5px 10px; font-size: 14px;")
        self.btn_apply_crop.clicked.connect(self.apply_crop_action)
        self.btn_apply_crop.hide()
        tbar.addWidget(self.btn_apply_crop)

        # Save Buttons
        # Dodatkowe narzędzia (Import)
        self.btn_import = QPushButton("🖼️ Import")
        self.btn_import.setStyleSheet("background: #8e44ad; color: white; font-weight: bold; padding: 5px 10px;")
        self.btn_import.clicked.connect(self.import_image)
        tbar.addWidget(self.btn_import)

        # Import obrazka bezpośrednio ze schowka systemowego na canvas
        self.btn_import_clipboard = QPushButton("🖼️ Import from📋")
        self.btn_import_clipboard.setToolTip("Import image from clipboard onto the canvas")
        self.btn_import_clipboard.setStyleSheet("background: #8e44ad; color: white; font-weight: bold; padding: 5px 10px;")
        self.btn_import_clipboard.clicked.connect(self.import_from_clipboard)
        tbar.addWidget(self.btn_import_clipboard)

        # Save Buttons
        btn_save = QPushButton("💾 Save")
        btn_save.clicked.connect(self.save_default)
        btn_save_as = QPushButton("💾 Save As..."); 
        btn_save_as.clicked.connect(self.save_as)
        
        self.btn_copy = QPushButton("🖼️ to clipboard")
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        tbar.addWidget(self.btn_copy)
        
        btn_save.setStyleSheet("background: #27ae60; color: white; font-weight: bold; padding: 5px 10px;")
        btn_save_as.setStyleSheet("background: #2980b9; color: white; font-weight: bold; padding: 5px 10px;")
        tbar.addWidget(btn_save)
        tbar.addWidget(btn_save_as)
        layout.addLayout(tbar)
        
        # Row 2: Props
        pbar = QHBoxLayout()
        # Tool options button — same palette + wrench icon as the Capture Region
        # toolbar; opens the options window of the selected object / active tool.
        self.btn_c = QPushButton()
        self.btn_c.setObjectName("colorPickerBtn")
        self.btn_c.setIcon(_svg_icon(_SVG_COLOR_TOOLS, 104, 48))
        self.btn_c.setIconSize(QSize(46, 22))
        self.btn_c.setFixedSize(64, 36)
        self.btn_c.setToolTip("Tool options — colours, width, … "
                              "(edits the selected object, or the active tool's defaults)")
        self.btn_c.clicked.connect(self.pick_color)
        pbar.addWidget(self.btn_c)

        # Swatch mirroring the colour(s) that window edits
        self.color_swatch = _ColorSwatch()
        pbar.addWidget(self.color_swatch)
        
        self.btn_hc = QPushButton("Text Highlight")
        self.btn_hc.clicked.connect(self.pick_highlight_color)
        self.btn_hc.hide()
        pbar.addWidget(self.btn_hc)
        
        pbar.addWidget(QLabel("Size:"))

        self.spin = QSpinBox(); self.spin.setRange(1, 200); self.spin.setValue(5)
        self.spin.setToolTip("Font size for text tools / Stroke width for shape tools")
        self.spin.valueChanged.connect(self._on_size_spin_changed)
        pbar.addWidget(self.spin)

        self.fill = QCheckBox("Fill Shape"); self.fill.toggled.connect(self.update_live_props)
        pbar.addWidget(self.fill)
        pbar.addStretch()
        layout.addLayout(pbar)

        layout.addWidget(self.canvas)
        self.canvas.scene.selectionChanged.connect(self._sync_spin_from_selection)
        self.showMaximized()

        # Fit image to fill the canvas view on startup (after window is fully rendered)
        def _fit_on_open():
            bg = self.canvas.bg_item.sceneBoundingRect()
            self.canvas.fitInView(bg, Qt.AspectRatioMode.KeepAspectRatio)
            self.canvas.centerOn(self.canvas.bg_item)

        QTimer.singleShot(0, _fit_on_open)
        
        self.select_tool("Select")

        # Register keyboard shortcuts explicitly for PySide6 compatibility.
        # In PySide6 QShortcut the third constructor argument is NOT a callback —
        # use .activated.connect() instead.
        def _sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(fn)
            return s
        _sc("Ctrl+Z",       self.canvas_undo)
        _sc("Ctrl+Y",       self.canvas_redo)
        _sc("Ctrl+Shift+Z", self.canvas_redo)
        _sc("Ctrl+S",       self.save_default)
        _sc("Delete",       self._delete_selected)
        _sc("Ctrl+D",       self._duplicate_selected)
        _sc("Ctrl+A",       self._select_all)

        self._update_undo_redo_buttons()

    def _build_magnet_button(self, tbar):
        """Magnet button + a checklist popup that appears on HOVER.

        The popup has a master checkbox ("Magnet enabled") and one checkbox
        per snap option. Clicking the button still toggles the magnet too;
        the button and the master checkbox always stay in sync. Snapping is
        enabled by default."""
        class _HoverButton(QPushButton):
            hovered = Signal()
            def enterEvent(self, e):
                self.hovered.emit()
                super().enterEvent(e)

        self.btn_magnet = _HoverButton("🧲")
        self.btn_magnet.setCheckable(True)
        self.btn_magnet.setChecked(self.canvas.snap_enabled)
        self.btn_magnet.setFixedSize(40, 40)
        self.btn_magnet.setToolTip("Snapping - hover for options, click to toggle on/off")
        self.btn_magnet.setStyleSheet("""
            QPushButton {
                font-size: 20px;
                padding: 0px;
                margin: 0px;
                border: 1px solid #ccc;
                border-radius: 4px;
            }
            QPushButton:checked {
                background-color: #3498db;
                color: white;
            }
        """)
        tbar.addWidget(self.btn_magnet)

        # ── Hover popup (plain tool window: no mouse grab, so enter/leave
        #    tracking works; closed by a cursor-position poll below) ──────────
        self.snap_popup = QFrame(self, Qt.WindowType.Tool |
                                       Qt.WindowType.FramelessWindowHint |
                                       Qt.WindowType.WindowStaysOnTopHint)
        self.snap_popup.setObjectName("snapPopup")
        self.snap_popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.snap_popup.setStyleSheet(
            "#snapPopup { background: palette(window); border: 1px solid palette(mid);"
            " border-radius: 6px; }")
        vbox = QVBoxLayout(self.snap_popup)
        vbox.setContentsMargins(10, 8, 10, 8)
        vbox.setSpacing(5)

        self._snap_master = QCheckBox("Magnet enabled")
        f = self._snap_master.font(); f.setBold(True); self._snap_master.setFont(f)
        self._snap_master.setChecked(self.canvas.snap_enabled)
        self._snap_master.toggled.connect(self._set_snap_enabled)
        vbox.addWidget(self._snap_master)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        vbox.addWidget(sep)

        self._snap_checks = {}
        options = [
            ('half',    "Snap to canvas half (horizontal & vertical)"),
            ('images',  "Snap to other images"),
            ('corners', "Snap to corners"),
            ('scaling', "Snap while resizing objects"),
        ]
        for key, label in options:
            cb = QCheckBox(label)
            cb.setChecked(self.canvas.snap_options.get(key, True))
            cb.setEnabled(self.canvas.snap_enabled)
            cb.toggled.connect(
                lambda state, k=key: self.canvas.snap_options.__setitem__(k, bool(state)))
            vbox.addWidget(cb)
            self._snap_checks[key] = cb

        self._snap_leave_ticks = 0
        self._snap_poll = QTimer(self)
        self._snap_poll.setInterval(60)
        self._snap_poll.timeout.connect(self._snap_hover_check)

        self.btn_magnet.hovered.connect(self._show_snap_popup)
        self.btn_magnet.clicked.connect(self._set_snap_enabled)

    def _set_snap_enabled(self, on):
        """Single place that switches the magnet; keeps button, master
        checkbox, option checkboxes and the canvas in sync."""
        on = bool(on)
        self.canvas.snap_enabled = on
        if self.btn_magnet.isChecked() != on:
            self.btn_magnet.setChecked(on)
        if self._snap_master.isChecked() != on:
            self._snap_master.blockSignals(True)
            self._snap_master.setChecked(on)
            self._snap_master.blockSignals(False)
        for cb in self._snap_checks.values():
            cb.setEnabled(on)

    def _show_snap_popup(self):
        if self.snap_popup.isVisible():
            return
        self.snap_popup.adjustSize()
        pos = self.btn_magnet.mapToGlobal(QPoint(0, self.btn_magnet.height() - 1))
        scr = self.btn_magnet.screen()
        if scr is not None:
            sg = scr.availableGeometry()
            pos.setX(max(sg.left(), min(pos.x(), sg.right() - self.snap_popup.width())))
            pos.setY(min(pos.y(), sg.bottom() - self.snap_popup.height()))
        self.snap_popup.move(pos)
        self.snap_popup.show()
        self.snap_popup.raise_()
        self._snap_leave_ticks = 0
        self._snap_poll.start()

    def _snap_hover_check(self):
        """Hide the popup once the cursor has left both the button and the
        popup (small grace area + 2 ticks, so crossing the gap doesn't close it)."""
        if not self.snap_popup.isVisible():
            self._snap_poll.stop()
            return
        cur = QCursor.pos()
        btn_rect = QRect(self.btn_magnet.mapToGlobal(QPoint(0, 0)), self.btn_magnet.size())
        # Sprawdzamy OSOBNO przycisk i okienko (+ 2 px luzu). Wcześniejsze
        # united() tworzyło prostokąt obejmujący oba naraz, więc obszar obok
        # przycisku (reszta paska narzędzi nad okienkiem) też liczył się jako
        # "wewnątrz" i okienko nie znikało.
        inside = (btn_rect.adjusted(-2, -2, 2, 2).contains(cur) or
                  self.snap_popup.geometry().adjusted(-2, -2, 2, 2).contains(cur))
        if inside:
            self._snap_leave_ticks = 0
            return
        self._snap_leave_ticks += 1
        if self._snap_leave_ticks >= 2:      # ~120 ms zwłoki
            self.snap_popup.hide()
            self._snap_poll.stop()

    def select_tool(self, name):
        self.canvas.current_tool = name
        for n, b in self.btns.items(): b.setChecked(n == name)

        # Rubber-band marquee selection only makes sense for the Select tool —
        # everything else (drawing, crop, eraser...) handles its own dragging.
        self.canvas.setDragMode(
            QGraphicsView.DragMode.RubberBandDrag if name == "Select"
            else QGraphicsView.DragMode.NoDrag)

        # Text Highlight button removed — highlight is managed via the unified text dialog
        self.btn_hc.hide()

        # Size spinner, Fill checkbox and colour swatch all follow the newly
        # selected tool's OWN settings (nothing leaks over from the previous tool).
        _k = _EDITOR_TOOL_KEYS.get(name)
        if _k:
            self._last_draw_key = _k
        self._refresh_tool_ui()

        # Zarządzanie widocznością i cyklem życia nakładki Crop
        if name == "Crop":
            self.btn_apply_crop.show()
            self.canvas.scene.clearSelection()

            bg_rect = QRectF(self.canvas.bg_pixmap.rect())

            # NOTE: sceneRect used to be hard-locked to bg_rect here ("Ustaw
            # rozmiar sceny na twardo, aby odpowiadał wymiarom tła"). That
            # shrinks the scrollable area down to exactly the image size, so
            # QGraphicsView has nothing left to pan to — this is what made
            # middle-mouse-button panning stop working while the Crop tool
            # was active. The visual canvas-bounds mask is drawn
            # independently in drawBackground/drawForeground and doesn't
            # depend on sceneRect at all, so keep the same large free-pan
            # rect used by every other tool instead.
            self.canvas.scene.setSceneRect(
                bg_rect.center().x() - 50000, bg_rect.center().y() - 50000, 100000, 100000)

            # Utwórz ramkę kadrującą na pełnym obszarze obrazu
            if not self.canvas.crop_item:
                self.canvas.crop_item = CropOverlayItem(bg_rect)
                self.canvas.scene.addItem(self.canvas.crop_item)
            self.canvas.crop_item.setSelected(True)
            self.canvas.crop_item.show()
        else:
            self.btn_apply_crop.hide()
            if self.canvas.crop_item:
                self.canvas.scene.removeItem(self.canvas.crop_item)
                self.canvas.crop_item = None
            
            # Restore massive sceneRect for free panning
            bg_rect = QRectF(self.canvas.bg_pixmap.rect())
            self.canvas.scene.setSceneRect(bg_rect.center().x() - 50000, bg_rect.center().y() - 50000, 100000, 100000)

    def apply_crop_action(self):
        if not self.canvas.crop_item:
            return

        crop_rect = self.canvas.crop_item.rect()

        # Snap to whole pixels. The overlay rect comes from free-form mouse
        # dragging and can end up with fractional (sub-pixel) coordinates.
        # Rounding left/top/right/bottom independently (instead of e.g.
        # rounding width separately from position) keeps both edges pixel-
        # aligned with the source pixmap and with bg_item's future position,
        # so this can't drift into a 1px uncropped sliver on later crops.
        _l = round(crop_rect.left())
        _t = round(crop_rect.top())
        _r = round(crop_rect.right())
        _b = round(crop_rect.bottom())
        crop_rect = QRectF(_l, _t, _r - _l, _b - _t)

        # ── Annotation mode: save the crop region with annotations, don't crop the canvas ──
        if self._annotation_mode:
            self.canvas.scene.removeItem(self.canvas.crop_item)
            self.canvas.crop_item = None
            self.btn_apply_crop.hide()
            self._finish_annotated_capture(crop_rect)
            return

        # ── Normal editor mode: the dashed selection IS the new image
        # boundary — always, exactly. Whether it's smaller than the current
        # image on a side (crop), larger (expand), or smaller on one side
        # and larger on another *at the same time*, the result must match
        # the drawn rectangle precisely — no union/approximation.
        #
        # This replaces the old two-branch logic, which detected "expand"
        # via bg_rect.contains(crop_rect) and then took
        # bg_rect.united(crop_rect) as the new bounds. united() keeps
        # whichever of the two rects is bigger on each side — so a
        # selection that shrank the left edge while growing the right edge
        # never actually cropped the left side at all (union keeps the
        # old, larger extent), producing exactly the "still not properly
        # cropped" behaviour that was reported.
        #
        # Instead: build a new canvas that is exactly crop_rect's size, and
        # paint the old image into it at the correct offset. QPainter clips
        # anything drawn outside the target pixmap automatically (= crop),
        # and any area not covered by the old image is left transparent
        # (= expand) — one code path, exact result, both directions at once.
        bg_rect = self.canvas.bg_item.sceneBoundingRect()
        new_rect = crop_rect

        new_w = int(round(new_rect.width()))
        new_h = int(round(new_rect.height()))
        if new_w < 1 or new_h < 1:
            return  # degenerate selection — nothing to apply

        self.canvas.push_undo()

        # Offset: where the old image's top-left lands inside the new canvas
        dx = bg_rect.x() - new_rect.x()
        dy = bg_rect.y() - new_rect.y()

        new_pixmap = QPixmap(new_w, new_h)
        new_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(new_pixmap)
        painter.drawPixmap(round(dx), round(dy), self.canvas.bg_pixmap)
        painter.end()

        self.canvas.bg_pixmap = new_pixmap
        self.canvas.bg_item.setPixmap(new_pixmap)
        self.canvas.bg_item.setPos(new_rect.topLeft())

        # Shift all annotations / floating layers to match the new origin
        for item in self.canvas.scene.items():
            if item != self.canvas.bg_item and item != self.canvas.crop_item:
                item.setPos(item.pos().x() + dx, item.pos().y() + dy)

        # Remove the crop overlay
        self.canvas.scene.removeItem(self.canvas.crop_item)
        self.canvas.crop_item = None
        self.btn_apply_crop.hide()

        # Mark as dirty and return to Select tool
        self.canvas.is_dirty = True
        self.select_tool("Select")

    # ── Tool options ─────────────────────────────────────────────────────────
    # One options window for every tool (colour tabs + embedded colour panel).
    # Opened by the ⚙ button, by double-click and by the context menu; always
    # edits either the selected object or — with nothing selected — the
    # remembered defaults of the active tool.
    _STROKE_KEYS = ('rect', 'circle', 'line', 'arrow', 'freehand')

    def _tool_key(self):
        return _EDITOR_TOOL_KEYS.get(self.canvas.current_tool)

    def _active_key(self):
        """Tool whose options apply when nothing is selected."""
        return self._tool_key() or self._last_draw_key

    def _selected_editable_item(self):
        for it in self.canvas.scene.selectedItems():
            if _prefs_key_for_item(it):
                return it
        return None

    def _refresh_tool_ui(self):
        """Single place that keeps the Size spinner, Fill checkbox and colour
        swatch in sync with what is being edited: the selected object if
        there is one, otherwise the active tool's own remembered settings."""
        cv = self.canvas
        item = self._selected_editable_item()
        key = _prefs_key_for_item(item) if item is not None else self._active_key()

        colors = (_item_swatch_colors(key, item) if item is not None
                  else _prefs_swatch_colors(key, cv.prefs))
        self.color_swatch.set_colors(colors or [QColor(128, 128, 128)])

        # Size: font size for Text, line width for stroke tools, else n/a
        value = None
        if key == 'text':
            value = item.font().pointSize() if item is not None else cv.prefs.get('text', 'size')
        elif key in self._STROKE_KEYS:
            value = item.pen().width() if item is not None else cv.prefs.get(key, 'width')
        self.spin.blockSignals(True)
        if value is not None and value > 0:
            self.spin.setValue(int(value))
        self.spin.setEnabled(value is not None)
        self.spin.blockSignals(False)

        # Fill: only for rectangles / ellipses (selected, or their own tool active)
        show_fill = key in ('rect', 'circle') and (
            item is not None or self._tool_key() in ('rect', 'circle'))
        if show_fill:
            filled = _item_is_filled(item) if item is not None else bool(cv.prefs.get(key, 'fill'))
            self.fill.blockSignals(True)
            self.fill.setChecked(filled)
            self.fill.blockSignals(False)
            self.fill.show()
        else:
            self.fill.hide()

    def pick_color(self):
        """⚙ button."""
        item = self._selected_editable_item()
        if item is not None:
            self._edit_item(item)
        else:
            self._open_defaults_dialog(self._active_key())

    @staticmethod
    def _run_options_dialog(dlg):
        dlg.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        return dlg.exec()

    def _open_defaults_dialog(self, key):
        dlg = _build_options_dialog(key, self, self.canvas.prefs, allow_fill=True)
        if dlg is None:
            return
        if self._run_options_dialog(dlg) == QDialog.DialogCode.Accepted:
            _store_dialog_in_prefs(key, dlg, self.canvas.prefs, allow_fill=True)
        self._refresh_tool_ui()

    def _edit_item(self, item) -> bool:
        """Options window for *item* with live preview. On OK the values are
        applied (one undo step) and remembered as that tool's defaults.
        Returns True if a dialog was shown."""
        key = _prefs_key_for_item(item)
        if key is None:
            return False
        cv = self.canvas
        dlg = _build_options_dialog(key, self, cv.prefs, item=item, allow_fill=True)
        if dlg is None:
            return False
        if self._run_options_dialog(dlg) == QDialog.DialogCode.Accepted:
            dlg.restore_original()          # undo snapshot = state before the edit
            if key in ('bubble', 'text') and not dlg.edit.toPlainText().strip():
                if key == 'text':           # emptied text annotation = delete it
                    cv.push_undo()
                    cv.scene.removeItem(item)
                    cv.is_dirty = True
            else:
                cv.push_undo()
                _commit_item_dialog(key, dlg, item, allow_fill=True)
                _store_dialog_in_prefs(key, dlg, cv.prefs, allow_fill=True)
                cv.is_dirty = True
        self._refresh_tool_ui()
        return True

    def _edit_text_item(self, item):
        """Kept for callers that still use the old name."""
        self._edit_item(item)

    def update_live_props(self, *_):
        """'Fill Shape' checkbox: fills/unfills selected rectangles & ellipses
        and remembers the choice for the tool(s) concerned."""
        cv = self.canvas
        on = self.fill.isChecked()
        touched = False
        for it in cv.scene.selectedItems():
            k = _prefs_key_for_item(it)
            if k in ('rect', 'circle'):
                fc = _item_fill_color(it)            # remember before changing
                it._fill_color = fc
                it.setBrush(QBrush(fc) if on else QBrush(Qt.GlobalColor.transparent))
                it.update()
                cv.prefs.set(k, 'fill', on)
                cv.is_dirty = True
                touched = True
        if not touched:
            k = self._tool_key()
            if k in ('rect', 'circle'):
                cv.prefs.set(k, 'fill', on)

    def _on_size_spin_changed(self, value):
        """Size spinner: live-updates the selected objects (width / font size)
        and remembers the value for each of those tools — or, with nothing
        selected, for the active tool."""
        cv = self.canvas
        value = max(1, int(value))
        items = [i for i in cv.scene.selectedItems() if _prefs_key_for_item(i)]
        for it in items:
            k = _prefs_key_for_item(it)
            if k == 'text':
                f = it.font(); f.setPointSize(value); it.setFont(f)
                cv.prefs.set('text', 'size', value)
            elif k in self._STROKE_KEYS:
                pen = it.pen(); pen.setWidth(value); it.setPen(pen)
                if hasattr(it, 'prepareGeometryChange'):
                    it.prepareGeometryChange()
                it.update()
                cv.prefs.set(k, 'width', value)
            else:
                continue
            cv.is_dirty = True
        if not items:
            k = self._active_key()
            if k == 'text':
                cv.prefs.set('text', 'size', value)
            elif k in self._STROKE_KEYS:
                cv.prefs.set(k, 'width', value)

    def _sync_spin_from_selection(self):
        """Selection changed → Size / Fill / swatch show the selected object."""
        self._refresh_tool_ui()

    def pick_highlight_color(self):
        """Legacy (button is hidden): highlight colour of the Text tool."""
        init_col = self.canvas.prefs.get('text', 'hl')
        if init_col.alpha() == 0:
            init_col.setAlpha(180)
        c = _show_color_dialog(init_col, self, alpha=True)
        if c is not None and c.isValid():
            self.canvas.prefs.set('text', 'hl', c)
            for item in self.canvas.scene.selectedItems():
                if isinstance(item, HighlightTextItem):
                    item.highlight_color = c
                    item.update()
            self.canvas.is_dirty = True
            self._refresh_tool_ui()

    def import_image(self):
        from PySide6.QtWidgets import QFileDialog
        from PySide6.QtGui import QPixmap
        
        # Używa wbudowanej zmiennej z folderem Screenshotów jako ścieżki startowej
        path, _ = QFileDialog.getOpenFileName(self, "Import Image", self.default_dir, "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                # Używamy nowej, skalowalnej klasy opartej o QGraphicsRectItem
                item = ResizablePixmapItem(pixmap)
                
                # Wyśrodkowanie na środku aktualnego widoku ekranu
                view_center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
                item.setPos(view_center - item.boundingRect().center())
                
                self.canvas.push_undo()
                self.canvas.scene.addItem(item)
                self.canvas.is_dirty = True
                self.select_tool("Select")

    def import_from_clipboard(self):
        """Import an image currently held in the system clipboard onto the canvas
        as a new floating, resizable layer (mirrors import_image, but reads
        from QApplication.clipboard() instead of a file dialog)."""
        cb = QApplication.clipboard()

        # QClipboard exposes images two ways depending on platform/source
        # (screenshot tools usually populate .image(), some apps only set
        # .pixmap()) — try both before giving up.
        img = cb.image()
        if not img.isNull():
            pixmap = QPixmap.fromImage(img)
        else:
            pixmap = cb.pixmap()

        if pixmap is None or pixmap.isNull():
            QMessageBox.warning(self, "Clipboard Empty", "No image found in clipboard!")
            return

        item = ResizablePixmapItem(pixmap)

        # Wyśrodkowanie na środku aktualnego widoku ekranu (jak w import_image)
        view_center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        item.setPos(view_center - item.boundingRect().center())

        self.canvas.push_undo()
        self.canvas.scene.addItem(item)
        self.canvas.is_dirty = True
        self.select_tool("Select")

    def _delete_selected(self):
        """Delete selected items (keyboard shortcut helper)."""
        to_remove = [item for item in self.canvas.scene.selectedItems()
                     if item not in (self.canvas.bg_item, getattr(self.canvas, 'crop_item', None))]
        if to_remove:
            self.canvas.push_undo()
        for item in to_remove:
            self.canvas.scene.removeItem(item)
            self.canvas.is_dirty = True

    def _duplicate_selected(self):
        """Duplicate selected items (keyboard shortcut helper)."""
        if hasattr(self.canvas, '_duplicate_selected_at_cursor'):
            self.canvas._duplicate_selected_at_cursor()

    def _select_all(self):
        """Select all non-background items (keyboard shortcut helper)."""
        for item in self.canvas.scene.items():
            if item not in (self.canvas.bg_item, getattr(self.canvas, 'crop_item', None)):
                item.setSelected(True)

    def canvas_undo(self):
        """Undo last action (keyboard shortcut helper)."""
        if hasattr(self.canvas, 'undo'):
            self.canvas.undo()

    def canvas_redo(self):
        """Redo last undone action (keyboard shortcut helper)."""
        if hasattr(self.canvas, 'redo'):
            self.canvas.redo()

    def _update_undo_redo_buttons(self):
        """Enable/disable the toolbar Undo/Redo buttons to match the
        canvas's history. Called by EditorCanvas whenever its undo/redo
        stacks change (push, undo, redo)."""
        if hasattr(self, 'btn_undo'):
            self.btn_undo.setEnabled(self.canvas.can_undo())
        if hasattr(self, 'btn_redo'):
            self.btn_redo.setEnabled(self.canvas.can_redo())

    def save_default(self):
        img = self.render_scene()
        self.save_callback(img)
        self.saved = True
        self.close()
    
    def copy_to_clipboard(self):
        try:
            # Re-use your existing render_scene() logic which safely bounds
            # the rendering to the actual image, ignoring the infinite canvas.
            img = self.render_scene()
            pixmap = QPixmap.fromImage(img)
            QApplication.clipboard().setPixmap(pixmap)
            QMessageBox.information(self, "Success", "Image copied to clipboard!")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to copy image: {e}")


    
    def save_as(self):
        import os
        from datetime import datetime
        default_path = os.path.join(self.default_dir, f"edited_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save Image As", default_path, "PNG (*.png);;JPG (*.jpg)")
        if path:
            img = self.render_scene()
            img.save(path)
            self.saved = True
            self.close()

    def _capture_annotated_region(self):
        """
        In annotation mode: let the user draw a Crop rectangle, then
        composite the visible annotations over that region of the original
        full-screen grab and save it.
        """
        # Switch to Crop tool so the user can draw the selection rectangle
        self.select_tool("Crop")
        QMessageBox.information(
            self, "Capture Region",
            "Draw a crop rectangle on the canvas to mark the region you want to capture.\n"
            "Then click  ✂️ Apply Crop  — the region will be saved with annotations.")

    def _finish_annotated_capture(self, crop_rect_scene: QRectF):
        """
        Called after the user confirms the crop rectangle.
        Renders the annotated area and saves it via save_callback.
        """
        self.canvas.scene.clearSelection()
        img = QImage(crop_rect_scene.size().toSize(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.canvas.scene.render(p,
                                 target=QRectF(img.rect()),
                                 source=crop_rect_scene,
                                 aspectRatioMode=Qt.AspectRatioMode.IgnoreAspectRatio)
        p.end()
        self.save_callback(img)
        self.saved = True
        self.close()

    def render_scene(self):
        """Render the scene cropped exactly to the background image bounds."""
        self.canvas.scene.clearSelection()
        bg_rect = self.canvas.bg_item.sceneBoundingRect()
        img = QImage(bg_rect.size().toSize(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.canvas.scene.render(p,
                                 target=QRectF(img.rect()),
                                 source=bg_rect,
                                 aspectRatioMode=Qt.AspectRatioMode.IgnoreAspectRatio)
        p.end()
        return img

    def closeEvent(self, event):
        if self.canvas.is_dirty and not self.saved:
            res = QMessageBox.warning(self, "Unsaved Changes", "Quit without saving?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if res == QMessageBox.StandardButton.Yes: event.accept()
            else: event.ignore()
        else: event.accept()



# ─────────────────────────────────────────────
#  OCR / QR TOOLBOX DIALOG
# ─────────────────────────────────────────────

class OcrQrToolboxDialog(QDialog):
    """Combined OCR text recognition + QR code generator/scanner toolbox."""

    _ocr_result_sig = Signal(str)
    _qr_result_sig  = Signal(str)

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._qr_pixmap  = None
        self._sel        = None
        self._toolbox_langs = list(main_window.config.get("ocr_languages", ["pl"])) or ["pl"]
        self.setWindowTitle("OCR / QR Toolbox")
        self.setMinimumSize(720, 480)
        self.setWindowFlags(Qt.WindowType.Window)
        self._ocr_result_sig.connect(self._on_ocr_result)
        self._qr_result_sig.connect(self._on_qr_result)
        self._build()

    def showEvent(self, event):
        """Every time this window becomes visible (first open, or coming
        back after region selection / being reopened), re-sync the language
        selection with the config — it may have changed elsewhere (e.g. the
        Settings page) while this dialog existed but wasn't visible."""
        super().showEvent(event)
        self._toolbox_langs = list(self.main_window.config.get("ocr_languages", ["pl"])) or ["pl"]
        self._update_lang_btn_text()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Top button bar ──────────────────────────────────────────────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        btn_scan_text = QPushButton("🔍  Scan region to recognize text")
        btn_scan_text.setMinimumHeight(36)
        btn_scan_text.clicked.connect(self._scan_text)

        btn_scan_qr = QPushButton("📷  Scan region for QR")
        btn_scan_qr.setMinimumHeight(36)
        btn_scan_qr.clicked.connect(self._scan_qr)

        self._lang_btn = QPushButton()
        self._lang_btn.setMinimumHeight(36)
        self._lang_btn.setToolTip("Choose which language(s) OCR should recognize")
        self._lang_btn.clicked.connect(self._pick_languages)
        self._update_lang_btn_text()

        top_bar.addWidget(btn_scan_text)
        top_bar.addWidget(btn_scan_qr)
        top_bar.addWidget(self._lang_btn)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # ── Main content (left text | right QR) ────────────────────────────
        content = QHBoxLayout()
        content.setSpacing(12)

        # Left: editable text
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "Recognized / scanned text will appear here.\n"
            "You can also type or edit text — the QR code updates in real time.")
        self.text_edit.textChanged.connect(self._on_text_changed)
        content.addWidget(self.text_edit, 1)

        # Right: QR panel
        right_panel = QVBoxLayout()
        right_panel.setSpacing(6)

        self.show_qr_check = QCheckBox("Show QR code")
        self.show_qr_check.setChecked(False)
        self.show_qr_check.toggled.connect(self._toggle_qr_visibility)
        right_panel.addWidget(self.show_qr_check)

        self.qr_label = QLabel()
        self.qr_label.setFixedSize(240, 240)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setStyleSheet(
            "border: 1px solid #444; background: #1e1e2e; color: #888; font-size: 11px;")
        self.qr_label.setWordWrap(True)
        self.qr_label.hide()
        right_panel.addWidget(self.qr_label)

        btn_copy_img = QPushButton("📋  Copy image to clipboard")
        btn_copy_img.clicked.connect(self._copy_image)
        btn_save_img = QPushButton("💾  Save image")
        btn_save_img.clicked.connect(self._save_image)
        btn_save_as  = QPushButton("📁  Save as…")
        btn_save_as.clicked.connect(self._save_as)

        right_panel.addWidget(btn_copy_img)
        right_panel.addWidget(btn_save_img)
        right_panel.addWidget(btn_save_as)
        right_panel.addStretch()

        content.addLayout(right_panel)
        layout.addLayout(content, 1)

    # ── Text / QR logic ─────────────────────────────────────────────────────

    def _on_text_changed(self):
        text = self.text_edit.toPlainText().strip()
        if text:
            self._generate_qr(text)
        else:
            self._qr_pixmap = None
            if self.show_qr_check.isChecked():
                self.qr_label.clear()
                self.qr_label.setText("Enter text to generate a QR code.")

    def _generate_qr(self, text: str):
        if not QRCODE_AVAILABLE:
            self._qr_pixmap = None
            if self.show_qr_check.isChecked():
                self.qr_label.setText(
                    "qrcode library not installed.\n\npip install qrcode[pil]")
            return
        try:
            qr = _qrcode.QRCode(
                version=None,
                error_correction=_qrcode.constants.ERROR_CORRECT_M,
                box_size=6, border=2)
            qr.add_data(text)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            qpix = QPixmap()
            qpix.loadFromData(buf.getvalue())
            self._qr_pixmap = qpix
            if self.show_qr_check.isChecked():
                self.qr_label.setPixmap(
                    qpix.scaled(240, 240,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation))
        except Exception as e:
            self._qr_pixmap = None
            if self.show_qr_check.isChecked():
                self.qr_label.setText(f"QR generation error:\n{e}")

    def _toggle_qr_visibility(self, checked: bool):
        if checked:
            self.qr_label.show()
            if self._qr_pixmap:
                self.qr_label.setPixmap(
                    self._qr_pixmap.scaled(240, 240,
                                           Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
            else:
                text = self.text_edit.toPlainText().strip()
                if text:
                    self._generate_qr(text)
                else:
                    self.qr_label.setText("Enter text to generate a QR code.")
        else:
            self.qr_label.hide()

    # ── OCR language picker ─────────────────────────────────────────────────

    def _update_lang_btn_text(self):
        names = [OCR_LANGUAGE_NAMES.get(c, c) for c in self._toolbox_langs] or ["Polski"]
        label = ", ".join(names)
        if len(label) > 24:
            label = f"{len(self._toolbox_langs)} languages"
        self._lang_btn.setText(f"🌐  {label}")

    def _pick_languages(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("OCR recognition language(s)")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Select one or more languages to recognize:"))
        grid, boxes = _make_ocr_lang_grid(self._toolbox_langs)
        lay.addLayout(grid)
        hint = QLabel(
            "Tesseract/EasyOCR recognize the selected languages together in one pass "
            "(EasyOCR requires compatible character sets). PaddleOCR checks each "
            "language separately and keeps the most confident result."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size:11px; color:#a6adc8;")
        lay.addWidget(hint)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected = [code for code, cb in boxes.items() if cb.isChecked()]
            self._toolbox_langs = selected or ["pl"]
            self._update_lang_btn_text()
            # Keep in sync with Settings so both places show the same choice.
            self.main_window.config.data["ocr_languages"] = self._toolbox_langs
            self.main_window.config.save()
            # Push the change to the Settings page immediately, in case it's
            # the currently visible tab — otherwise its checkboxes would
            # stay stale until the user switches tabs and back.
            if hasattr(self.main_window, "_refresh_ocr_settings_ui"):
                self.main_window._refresh_ocr_settings_ui()

    # ── Scan region → OCR ───────────────────────────────────────────────────

    def _scan_text(self):
        self.hide()
        QTimer.singleShot(200, self._do_scan_text)

    def _do_scan_text(self):
        self._sel = RegionSelector()
        self._sel.region_selected.connect(self._run_ocr_region)
        self._sel.cancelled.connect(self.show)

    def _run_ocr_region(self, x, y, w, h):
        if w < 5 or h < 5:
            self.show(); return

        mw = self.main_window
        mw._prog_show_sig.emit("Capturing region…")

        def go():
            time.sleep(0.05)
            path = mw.engine.capture_region(x, y, w, h)
            if not path:
                mw._prog_hide_sig.emit()
                self._ocr_result_sig.emit("Failed to capture screen region.")
                return
            engine_name = mw.config.get("ocr_engine", "paddleocr")
            lang_codes = self._toolbox_langs
            mw._prog_msg_sig.emit(f"Running {engine_name.upper()}…")
            if engine_name == "paddleocr":
                txt = mw.engine._ocr_paddleocr(path, lang_codes)
            elif engine_name == "easyocr":
                txt = mw.engine._ocr_easyocr(path, lang_codes)
            else:
                txt = mw.engine._ocr_tesseract(path, lang_codes)
            mw._prog_hide_sig.emit()
            self._ocr_result_sig.emit(txt)

        threading.Thread(target=go, daemon=True).start()

    def _on_ocr_result(self, text: str):
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(text)
        self.text_edit.blockSignals(False)
        self._on_text_changed()
        self.show(); self.raise_(); self.activateWindow()

    # ── Scan region → QR decode ─────────────────────────────────────────────

    def _scan_qr(self):
        self.hide()
        QTimer.singleShot(200, self._do_scan_qr)

    def _do_scan_qr(self):
        self._sel = RegionSelector()
        self._sel.region_selected.connect(self._run_qr_region)
        self._sel.cancelled.connect(self.show)

    def _run_qr_region(self, x, y, w, h):
        if w < 5 or h < 5:
            self.show(); return

        def go():
            time.sleep(0.05)
            path = self.main_window.engine.capture_region(x, y, w, h)
            if not path:
                self._qr_result_sig.emit("__err__Failed to capture screen region.")
                return
            if not CV2_AVAILABLE:
                self._qr_result_sig.emit("__err__OpenCV not installed.\npip install opencv-python")
                return
            try:
                import cv2 as _cv2
                import numpy as np
                img = _cv2.imdecode(np.fromfile(path, dtype=np.uint8), _cv2.IMREAD_COLOR)
                detector = _cv2.QRCodeDetector()
                data, bbox, _ = detector.detectAndDecode(img)
                if data:
                    self._qr_result_sig.emit(data)
                else:
                    self._qr_result_sig.emit("__err__No QR code detected in the selected region.")
            except Exception as e:
                self._qr_result_sig.emit(f"__err__Error decoding QR: {e}")

        threading.Thread(target=go, daemon=True).start()

    def _on_qr_result(self, result: str):
        if result.startswith("__err__"):
            QMessageBox.warning(self, "QR Scan", result[7:])
            self.show(); self.raise_(); self.activateWindow()
            return
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(result)
        self.text_edit.blockSignals(False)
        self._on_text_changed()
        # Auto-enable QR display after a successful scan
        self.show_qr_check.setChecked(True)
        self.show(); self.raise_(); self.activateWindow()

    # ── Image buttons ────────────────────────────────────────────────────────

    def _copy_image(self):
        if not self._qr_pixmap:
            QMessageBox.information(self, "OCR / QR Toolbox", "No QR code generated yet.")
            return
        QApplication.clipboard().setPixmap(self._qr_pixmap)

    def _save_image(self):
        if not self._qr_pixmap:
            QMessageBox.information(self, "OCR / QR Toolbox", "No QR code generated yet.")
            return
        save_dir = Path(self.main_window.config.get(
            "save_folder",
            str(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.PicturesLocation))))
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = f"qrcode_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png"
        path = save_dir / filename
        self._qr_pixmap.save(str(path), "PNG")
        QMessageBox.information(self, "Saved", f"QR code image saved to:\n{path}")

    def _save_as(self):
        if not self._qr_pixmap:
            QMessageBox.information(self, "OCR / QR Toolbox", "No QR code generated yet.")
            return
        save_dir = self.main_window.config.get(
            "save_folder",
            str(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.PicturesLocation)))
        default_path = str(
            Path(save_dir) / f"qrcode_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save QR Code As", default_path,
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)")
        if path:
            self._qr_pixmap.save(path)


# ─────────────────────────────────────────────
#  MAIN WINDOW  (settings embedded)
# ─────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════
#  SKRÓTY SYSTEMOWE CINNAMON (Wayland)
#  Pod Waylandem pynput nie widzi klawiszy poza oknami XWayland, więc
#  rejestrujemy skróty w Cinnamonie (gsettings) - każdy wywołuje
#  `pysharex.py --action <akcja>`, które trafia do działającej instancji (IPC).
# ══════════════════════════════════════════════════════════════════════════
_CIN_SCHEMA = "org.cinnamon.desktop.keybindings"
_CIN_ITEM_SCHEMA = "org.cinnamon.desktop.keybindings.custom-keybinding"
_CIN_PATH = "/org/cinnamon/desktop/keybindings/custom-keybindings/"
_CIN_PREFIX = "pysharex-"
_cin_lock = threading.Lock()
_cin_last_sig = None


def _cinnamon_available() -> bool:
    if IS_WINDOWS or not _shutil.which("gsettings"):
        return False
    if "cinnamon" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
        return False
    try:
        r = subprocess.run(["gsettings", "get", _CIN_SCHEMA, "custom-list"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _shortcut_to_gtk_accel(sc: str) -> str:
    """'Ctrl+Alt+P' -> '<Control><Alt>p', 'Alt+Print Screen' -> '<Alt>Print'."""
    sc = sc.strip().replace("++", "+Plus")
    if not sc:
        return ""
    keymap = {
        "Print Screen": "Print", "PgUp": "Page_Up", "PgDown": "Page_Down",
        "Return": "Return", "Backspace": "BackSpace", "Escape": "Escape",
        "Space": "space", "Tab": "Tab", "Num Lock": "Num_Lock",
        "Scroll Lock": "Scroll_Lock", "Caps Lock": "Caps_Lock",
        "Plus": "plus", "Insert": "Insert", "Delete": "Delete", "Home": "Home",
        "End": "End", "Up": "Up", "Down": "Down", "Left": "Left",
        "Right": "Right", "Pause": "Pause",
    }
    modmap = {"Ctrl": "<Control>", "Alt": "<Alt>", "Shift": "<Shift>", "Meta": "<Super>"}
    mods, key = "", ""
    for part in [p.strip() for p in sc.split("+") if p.strip()]:
        if part in modmap:
            mods += modmap[part]
        else:
            key = keymap.get(part, part.lower() if len(part) == 1 else part)
    return (mods + key) if key else ""


def _cinnamon_sync_shortcuts(shortcuts) -> None:
    """Idempotentnie zapisuje skróty PyshareX jako własne skróty Cinnamona.
    Rusza wyłącznie wpisy o id zaczynającym się od 'pysharex-'."""
    import ast, shlex
    global _cin_last_sig
    sig = json.dumps(shortcuts or [], sort_keys=True, default=str)
    with _cin_lock:
        if sig == _cin_last_sig:
            return
        _cin_last_sig = sig
        _cinnamon_sync_locked(shortcuts)


def _cinnamon_sync_locked(shortcuts) -> None:
    import ast, shlex

    def gs(*args):
        return subprocess.run(["gsettings", *args], capture_output=True,
                              text=True, timeout=5)
    try:
        r = gs("get", _CIN_SCHEMA, "custom-list")
        txt = r.stdout.strip()
        if txt.startswith("@as"):
            txt = txt[3:].strip()
        cur = ast.literal_eval(txt) if txt else []
        keep = [x for x in cur if not str(x).startswith(_CIN_PREFIX)]
        old_ours = [x for x in cur if str(x).startswith(_CIN_PREFIX)]

        base = f"{shlex.quote(sys.executable)} {shlex.quote(os.path.abspath(__file__))}"
        ours, used = [], set()
        for i, sc in enumerate(shortcuts or []):
            if not sc.get("enabled", True):
                continue
            action = sc.get("action", "")
            accel = _shortcut_to_gtk_accel(sc.get("shortcut", ""))
            if not action or not accel or accel in used:
                continue
            used.add(accel)
            sid = f"{_CIN_PREFIX}{i}-{action}"
            path = f"{_CIN_ITEM_SCHEMA}:{_CIN_PATH}{sid}/"
            gs("set", path, "name", f"PyshareX: {sc.get('name') or action}")
            gs("set", path, "command", f"{base} --action {action}")
            gs("set", path, "binding", str([accel]))
            ours.append(sid)
            print(f"[Hotkeys] Cinnamon: {sc.get('shortcut')} -> {accel} -> --action {action}", flush=True)

        for sid in old_ours:
            if sid not in ours:
                path = f"{_CIN_ITEM_SCHEMA}:{_CIN_PATH}{sid}/"
                for k in ("name", "command", "binding"):
                    gs("reset", path, k)
        new_list = keep + ours
        gs("set", _CIN_SCHEMA, "custom-list", str(new_list))
        print(f"[Hotkeys] Cinnamon: zarejestrowano {len(ours)} skrótów systemowych", flush=True)
    except Exception as e:
        print(f"[Hotkeys] Cinnamon: błąd rejestracji skrótów: {e}", flush=True)


class _TrayMenu(QMenu):
    """Menu zasobnika odporne na Wayland/XWayland (Cinnamon).

    Problemy, które naprawia:
    - Qt dostaje od panelu współrzędne kliknięcia, które pod XWayland są
      błędne -> menu pojawiało się w losowym miejscu. Na Linuksie ustawiamy
      je przy prawej krawędzi ekranu, nad panelem.
    - XWayland nie daje popupowi 'grab', więc kliknięcie poza menu go nie
      zamyka, a kolejne PPM na ikonce nie robiło nic (menu już 'jest').
      Zamykamy je sami: po opuszczeniu kursora i po timeoucie bezczynności."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._idle = QTimer(self); self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._idle_check)
        self._life = QTimer(self); self._life.setSingleShot(True)
        self._life.timeout.connect(self._force_close)

    def _submenu_open(self):
        return any(m.isVisible() for m in self.findChildren(QMenu))

    def _force_close(self):
        for m in self.findChildren(QMenu):
            m.hide()
        self.hide()

    def _idle_check(self):
        if not self.isVisible():
            return
        if self.underMouse() or self._submenu_open():
            self._idle.start(1200)
        else:
            self._force_close()

    def _place(self):
        if IS_WINDOWS or not self.isVisible():
            return
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        g, av = scr.geometry(), scr.availableGeometry()
        bottom = av.bottom() if av.height() < g.height() else g.bottom() - 60
        x = g.right() - self.width() - 8
        y = bottom - self.height() - 4
        self.move(max(g.left(), x), max(g.top(), y))

    def showEvent(self, e):
        super().showEvent(e)
        if not IS_WINDOWS:
            QTimer.singleShot(0, self._place)
            self._idle.start(2500)    # zamknij, jeśli kursor nie wejdzie w menu
            self._life.start(15000)   # twardy limit życia menu

    def enterEvent(self, e):
        self._idle.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not IS_WINDOWS:
            self._idle.start(1200)
        super().leaveEvent(e)

    def hideEvent(self, e):
        self._idle.stop(); self._life.stop()
        super().hideEvent(e)


class MainWindow(QMainWindow):
    status_sig    = Signal(str)
    _notify_sig   = Signal(str, object)  # filepath — must run on main thread
    _ocr_done_sig = Signal(str, str)     # (tekst, tytul)
    _hotkey_sig   = Signal(str)          # method_name — dispatched from pynput thread
    _prog_show_sig = Signal(str)         # show progress window with message
    _prog_msg_sig  = Signal(str)         # update progress message
    _prog_hide_sig = Signal()            # hide/close progress window

    def __init__(self, config: Config):
        super().__init__()
        self.config   = config
        self.engine   = CaptureEngine(config)
        self.rec_th      = None
        self.gif_th      = None
        self.is_rec      = False
        self._border     = None
        self._bar        = None
        self._gif_bar    = None
        self._sel               = None   # keep RegionSelector alive
        self._pre_capture_cursor = None   # cursor pos before overlay
        self._abort      = False
        self._gif_aborted = False
        self._hkl        = None
        self._ocr_done_sig.connect(self._show_ocr)
        self._hotkey_sig.connect(self._dispatch_hotkey)
        self._ocr_prog = None
        self._prog_show_sig.connect(self._prog_show)
        self._prog_msg_sig.connect(self._prog_msg)
        self._prog_hide_sig.connect(self._prog_hide)
        self.last_rec_pixmap = None  # Tu będziemy trzymać miniaturkę
        self.setWindowTitle("PyshareX")
        self.setMinimumSize(780, 540)
        # read the menu bar's real (platform) font BEFORE the stylesheet touches it
        self.setStyleSheet(_css_with_menubar_font(CSS, self.menuBar().font()))
        self._app_icon = load_app_icon()
        self.setWindowIcon(self._app_icon)
        self._build()
        self._build_tray()
        self._hotkeys_start()
        self._ipc_start()
        self.status_sig.connect(self._status)
        self._notify_sig.connect(self._on_notify)

    # ════════════════════════════════════════
    #  BUILD
    # ════════════════════════════════════════

    def _build(self):
        cw = QWidget(); self.setCentralWidget(cw)
        root = QVBoxLayout(cw); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # Header
        hdr = QFrame(); hdr.setFixedHeight(52)
        hdr.setStyleSheet("background:#181825;border-bottom:1px solid #313244;")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(16, 0, 16, 0)
        ico = QLabel("📸"); ico.setStyleSheet("font-size:22px;")
        ttl = QLabel("PyshareX")
        ttl.setStyleSheet("color:#89b4fa;font-size:18px;font-weight:bold;letter-spacing:1px;")
        #self._cap_btn = QPushButton("Capture Region")
        #self._cap_btn.setObjectName("cap_btn"); self._cap_btn.clicked.connect(self.act_region)
        #self._rec_btn = QPushButton("Record")
        #self._rec_btn.setObjectName("rec_btn"); self._rec_btn.clicked.connect(self.act_toggle_rec)
        self._save_settings_btn = QPushButton("💾 Save Settings")
        self._save_settings_btn.setFixedHeight(32)
        self._save_settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_settings_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #89b4fa;"
            "  color: #1e1e2e;"
            "  border: none;"
            "  border-radius: 6px;"
            "  font-weight: bold;"
            "  padding: 0px 16px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #b4befe;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #74a1f5;"
            "}"
        )
        self._save_settings_btn.clicked.connect(self._save_settings)
        self._save_settings_btn.hide()  # only shown while the Settings tab is active
        quit_btn = QPushButton("⏻")
        quit_btn.setToolTip("Quit PyshareX")
        quit_btn.setFixedSize(32, 32)
        quit_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #2a2a3e;"
            "  color: #f38ba8;"
            "  border: 1px solid #f38ba8;"
            "  border-radius: 5px;"
            "  font-size: 14px;"
            "  padding: 0px;"
            "  min-height: 0px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #f38ba8;"
            "  color: #1e1e2e;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #c97090;"
            "  color: #1e1e2e;"
            "}"
        )
        quit_btn.clicked.connect(self._quit)
        hl.addWidget(ico); hl.addWidget(ttl); hl.addStretch()
        hl.addWidget(self._save_settings_btn)
        hl.addWidget(quit_btn)
        root.addWidget(hdr)

        # Body
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)

        # Sidebar
        sb = QFrame(); sb.setFixedWidth(186)
        sb.setStyleSheet("background:#181825;border-right:1px solid #313244;")
        sl = QVBoxLayout(sb); sl.setContentsMargins(8, 12, 8, 12); sl.setSpacing(2)
        SB_CSS = ("QPushButton{background:transparent;color:#7f849c;text-align:left;"
                  "border:none;padding:8px 12px;border-radius:6px;}"
                  "QPushButton:hover{background:#252535;color:#cdd6f4;}"
                  "QPushButton:checked{background:#313244;color:#89b4fa;font-weight:bold;}")
        self._sb_btns = []
        for lbl, fn in [("⌨️  Shortcuts",  self._show_capture),
                         ("🔧  Tools",    self._show_tools),
                         ("⚙️  Settings", self._show_settings),
                         ("📋  History",  self._show_history)]:
            b = QPushButton(lbl); b.setStyleSheet(SB_CSS); b.setCheckable(True)
            b.clicked.connect(fn); sl.addWidget(b); self._sb_btns.append(b)
        sl.addStretch()
        body.addWidget(sb)

        # Stack
        self.stack = QStackedWidget()
        self.stack.addWidget(self._mk_capture())   # 0
        self.stack.addWidget(self._mk_tools())     # 1
        self.stack.addWidget(self._mk_settings())  # 2
        self.stack.addWidget(self._mk_history())   # 3
        body.addWidget(self.stack, 1)
        root.addLayout(body)

        # Status bar
        sf = QFrame(); sf.setFixedHeight(28)
        sf.setStyleSheet("background:#181825;border-top:1px solid #313244;")
        sl2 = QHBoxLayout(sf); sl2.setContentsMargins(12, 0, 12, 0)
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color:#a6e3a1;font-size:12px;padding:4px 8px;")
        self.rec_ind = QLabel("🔴 RECORDING")
        self.rec_ind.setStyleSheet("color:#f38ba8;font-weight:bold;font-size:11px;")
        self.rec_ind.hide()
        sl2.addWidget(self.status_lbl); sl2.addStretch(); sl2.addWidget(self.rec_ind)
        root.addWidget(sf)

        self._build_menu()
        self._refresh_table()
        self._sel_sb(0)

    # ── Panels ───────────────────────────────

    def _mk_capture(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(8)
        tb = QHBoxLayout()
        for txt, fn in [("➕ Add",    self._sc_add),
                        ("✏️ Edit",   self._sc_edit),
                        ("🗑 Remove", self._sc_del),
                        ("⬆",         self._sc_up),
                        ("⬇",         self._sc_dn),
                        ("↺ Reset",   self._sc_reset)]:
            b = QPushButton(txt); b.setFixedHeight(30); b.clicked.connect(fn); tb.addWidget(b)
        tb.addStretch(); lay.addLayout(tb)
        self.tbl = QTableWidget()
        self.tbl.setColumnCount(3)
        self.tbl.setHorizontalHeaderLabels(["Action", "Shortcut", ""])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.tbl.setColumnWidth(2, 40)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.doubleClicked.connect(self._sc_edit)
        lay.addWidget(self.tbl)
        return w

    def _mk_tools(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(10)
        lbl = QLabel("🔧 Tools")
        lbl.setStyleSheet("color:#89b4fa;font-size:15px;font-weight:bold;")
        lay.addWidget(lbl)
        items = [("🧰  OCR/QR Toolbox",  self.act_ocr_qr_toolbox),
                 ("🖥  Full screen",     self.act_fullscreen),
                 ("🪟  Active window",   self.act_window),
                 ("🖥  Active monitor",  self.act_monitor),
                 ("🎞  Record GIF",      self.act_gif),
                 ("🎬  Video Converter", self.act_video_converter)]
        row = QHBoxLayout()
        for i, (lbl2, fn) in enumerate(items):
            b = QPushButton(lbl2); b.setMinimumHeight(60); b.clicked.connect(fn)
            row.addWidget(b)
            if (i + 1) % 3 == 0:
                lay.addLayout(row); row = QHBoxLayout()
        if row.count(): row.addStretch(); lay.addLayout(row)
        lay.addStretch()
        return w

    def _mk_settings(self):
        sa = QScrollArea(); sa.setWidgetResizable(True)
        sa.setStyleSheet("QScrollArea{border:none;}")
        inner = QWidget(); sa.setWidget(inner)
        lay = QVBoxLayout(inner); lay.setContentsMargins(20, 20, 20, 20); lay.setSpacing(14)
        lbl = QLabel("⚙️ Settings")
        lbl.setStyleSheet("color:#89b4fa;font-size:15px;font-weight:bold;")
        lay.addWidget(lbl)

        # Folder
        fg = QGroupBox("Screenshots folder"); fgl = QVBoxLayout(fg)
        fl = QHBoxLayout()
        self._fld = QLineEdit(self.config.get("save_folder", ""))
        br = QPushButton("Browse…"); br.clicked.connect(self._browse)
        fl.addWidget(self._fld); fl.addWidget(br); fgl.addLayout(fl)
        fg_hint = QLabel("A subfolder is created automatically for each month (e.g. 2026-08) and captures are saved there.")
        fg_hint.setStyleSheet("color:#7f849c;font-size:11px;"); fg_hint.setWordWrap(True)
        fgl.addWidget(fg_hint)
        lay.addWidget(fg)

        # Format
        fmg = QGroupBox("Image format"); fml = QHBoxLayout(fmg)
        self._fmt = QComboBox(); self._fmt.addItems(["png", "jpg", "bmp", "webp"])
        self._fmt.setCurrentText(self.config.get("image_format", "png"))
        self._jpg = QSpinBox(); self._jpg.setRange(1, 100)
        self._jpg.setValue(self.config.get("jpeg_quality", 90)); self._jpg.setSuffix("%")
        fml.addWidget(QLabel("Format:")); fml.addWidget(self._fmt)
        fml.addWidget(QLabel("JPEG quality:")); fml.addWidget(self._jpg); fml.addStretch()
        lay.addWidget(fmg)

        # Capture options
        cog = QGroupBox("Capture options"); col = QVBoxLayout(cog)
        dr = QHBoxLayout()
        self._dly = QSpinBox(); self._dly.setRange(0, 10)
        self._dly.setValue(self.config.get("delay", 0)); self._dly.setSuffix(" s")
        dr.addWidget(QLabel("Delay before capture:")); dr.addWidget(self._dly); dr.addStretch()
        col.addLayout(dr)
        self._cur = QCheckBox("Show cursor in screenshot")
        self._cur.setChecked(self.config.get("show_cursor", False)); col.addWidget(self._cur)
        lay.addWidget(cog)

        # Capture Region method
        from PySide6.QtWidgets import QButtonGroup
        crg = QGroupBox("Capture Region method"); crl = QVBoxLayout(crg)
        self._region_method_grp = QButtonGroup(crg)
        self._rm_ffmpeg_rb = QRadioButton(
            "FFmpeg method  (gdigrab / x11grab) — requires ffmpeg on PATH")
        self._rm_standard_rb = QRadioButton(
            "Standard method  (no ffmpeg needed) — DPI-aware across monitors with different scaling")
        self._region_method_grp.addButton(self._rm_ffmpeg_rb, 0)
        self._region_method_grp.addButton(self._rm_standard_rb, 1)
        crl.addWidget(self._rm_ffmpeg_rb)
        crl.addWidget(self._rm_standard_rb)
        region_method = self.config.get("region_capture_method", "standard")
        if region_method == "standard":
            self._rm_standard_rb.setChecked(True)
        else:
            self._rm_ffmpeg_rb.setChecked(True)
        rm_hint = QLabel(
            "Applies to \"Capture region\". The standard method works without ffmpeg "
            "installed and composes the screenshot correctly even when your monitors "
            "use different scaling (e.g. 100% + 150%).")
        rm_hint.setWordWrap(True)
        rm_hint.setStyleSheet("font-size:11px; color:#a6adc8; padding-left:4px;")
        crl.addWidget(rm_hint)

        self._detect_windows_cb = QCheckBox(
            "Automatically detect windows / UI elements while selecting  (click to capture instantly)")
        self._detect_windows_cb.setChecked(self.config.get("detect_windows_on_capture", True))
        crl.addWidget(self._detect_windows_cb)
        detect_hint = QLabel(
            "When enabled, moving the mouse in \"Capture region\" outlines the window or "
            "control under the cursor with a dashed rectangle — click it to capture that area "
            "instantly. Holding the left button and dragging still uses the normal manual "
            "rectangle selection, exactly as before.")
        detect_hint.setWordWrap(True)
        detect_hint.setStyleSheet("font-size:11px; color:#a6adc8; padding-left:4px;")
        crl.addWidget(detect_hint)

        lay.addWidget(crg)

        # Selected monitor
        mg = QGroupBox("Selected monitor  (for 'Capture selected monitor')")
        ml = QHBoxLayout(mg)
        self._mcb = QComboBox(); self._fill_monitors()
        rfb = QPushButton("Refresh")
        rfb.setFixedHeight(28)
        rfb.setToolTip("Refresh monitor list")
        rfb.clicked.connect(self._fill_monitors)
        ml.addWidget(QLabel("Monitor:")); ml.addWidget(self._mcb, 1)
        ml.addWidget(rfb, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(mg)

        # Recording
        reg = QGroupBox("Recording"); rel = QVBoxLayout(reg)
        self._aud = QCheckBox("Record audio  (requires PulseAudio / dshow)")
        self._aud.setChecked(self.config.get("record_audio", False)); rel.addWidget(self._aud)
        gr = QHBoxLayout()
        self._gfps = QSpinBox(); self._gfps.setRange(1, 30)
        self._gfps.setValue(self.config.get("gif_fps", 10))
        gr.addWidget(QLabel("GIF FPS:")); gr.addWidget(self._gfps); gr.addStretch()
        rel.addLayout(gr); lay.addWidget(reg)

        # OCR engine
        ocr_g = QGroupBox("OCR Engine")
        ocr_l = QVBoxLayout(ocr_g)
        self._ocr_grp = QButtonGroup(ocr_g)
        self._ocr_paddleocr_rb = QRadioButton(
            "PaddleOCR  (pip install paddlepaddle paddleocr)  — recommended, best accuracy")
        self._ocr_easyocr_rb = QRadioButton(
            "EasyOCR  (pip install easyocr)  — no external dependencies")
        self._ocr_tesseract_rb = QRadioButton(
            "Tesseract  (sudo apt install tesseract-ocr / Windows installer)")
        # Add buttons to group FIRST, then set checked state.
        # In PySide6 an exclusive QButtonGroup may silently override setChecked
        # calls made before the button is part of the group.
        self._ocr_grp.addButton(self._ocr_paddleocr_rb, 0)
        self._ocr_grp.addButton(self._ocr_easyocr_rb, 1)
        self._ocr_grp.addButton(self._ocr_tesseract_rb, 2)
        ocr_l.addWidget(self._ocr_paddleocr_rb)
        ocr_l.addWidget(self._ocr_easyocr_rb)
        ocr_l.addWidget(self._ocr_tesseract_rb)
        # Apply saved value AFTER buttons are in the group
        engine = self.config.get("ocr_engine", "paddleocr")
        if engine == "easyocr":
            self._ocr_easyocr_rb.setChecked(True)
        elif engine == "tesseract":
            self._ocr_tesseract_rb.setChecked(True)
        else:
            self._ocr_paddleocr_rb.setChecked(True)
        # Status indicators
        paddle_status = "✅ PaddleOCR installed" if PADDLEOCR_AVAILABLE else "⚠️  PaddleOCR not installed — run: pip install paddlepaddle paddleocr"
        paddle_status_lbl = QLabel(paddle_status)
        paddle_status_lbl.setStyleSheet("font-size:11px; color:#a6adc8; padding-left:4px;")
        ocr_l.addWidget(paddle_status_lbl)
        easyocr_status = "✅ EasyOCR installed" if EASYOCR_AVAILABLE else "⚠️  EasyOCR not installed — run: pip install easyocr"
        ocr_status_lbl = QLabel(easyocr_status)
        ocr_status_lbl.setStyleSheet("font-size:11px; color:#a6adc8; padding-left:4px;")
        ocr_l.addWidget(ocr_status_lbl)

        # OCR language(s) — multi-select, since Tesseract/EasyOCR can
        # recognize several languages in one pass, and PaddleOCR tries each
        # selected language separately and keeps the best-confidence result.
        ocr_l.addWidget(QLabel("Recognition language(s):"))
        cur_langs = self.config.get("ocr_languages", ["pl"])
        lang_grid, self._ocr_lang_cbs = _make_ocr_lang_grid(cur_langs)
        ocr_l.addLayout(lang_grid)
        lang_hint = QLabel(
            "Choose one or more languages to recognize. Tesseract and EasyOCR recognize "
            "the selected languages in a single pass (EasyOCR requires them to be mutually "
            "compatible, e.g. it won't combine Cyrillic with the Latin alphabet). PaddleOCR "
            "checks each selected language separately and returns the highest-confidence "
            "result — more languages means slower scanning. Tesseract requires the matching "
            "language pack to be installed (e.g. tesseract-ocr-pol)."
        )
        lang_hint.setWordWrap(True)
        lang_hint.setStyleSheet("font-size:11px; color:#a6adc8; padding-left:4px;")
        ocr_l.addWidget(lang_hint)

        lay.addWidget(ocr_g)

        # After capture
        acg = QGroupBox("After capture tasks"); acl = QVBoxLayout(acg)
        ac = self.config.get("after_capture", {})
        self._ac = {}
        for k, lbl2 in [("copy_to_clipboard","📋 Copy to clipboard"),
                         ("save_to_file",     "💾 Save to file"),
                         ("show_in_explorer", "📁 Show in explorer"),
                         ("scan_qr",          "🔳 Scan QR code"),
                         ("ocr_recognize",    "🔤 Recognize text (OCR)"),
                         ("open_in_editor",   "🎨 Open in Image Editor")]:
            cb = QCheckBox(lbl2); cb.setChecked(ac.get(k, False))
            self._ac[k] = cb; acl.addWidget(cb)
        lay.addWidget(acg)

        # Notifications
        ng = QGroupBox("Notifications"); nl = QVBoxLayout(ng)
        notif = self.config.get("notifications", {})
        self._nc = {}
        for k, lbl2, default in [
                ("enabled",           "🔔 Show notification after capture",  True),
                ("sound",             "🔊 Play sound",                       True),
                ("thumbnail",         "🖼  Show thumbnail (larger preview)",  True),
                ("show_path",         "📂 Show file path",                   True),
                ("click_open_file",   "🖱  Click → open file",               True),
                ("click_open_folder", "📁 Click → open containing folder",   False),
        ]:
            cb = QCheckBox(lbl2); cb.setChecked(notif.get(k, default))
            self._nc[k] = cb; nl.addWidget(cb)
        lay.addWidget(ng)

        lay.addStretch()
        return sa

    def _mk_history(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(8)
        top = QHBoxLayout()
        lbl = QLabel("📋 Screenshot History")
        lbl.setStyleSheet("color:#89b4fa;font-size:15px;font-weight:bold;")
        top.addWidget(lbl); top.addStretch()
        ref = QPushButton(); ref.setIcon(_svg_icon(_SVG_REFRESH, 20)); ref.setIconSize(QSize(20, 20))
        ref.setFixedWidth(34); ref.setToolTip("Refresh")
        ref.clicked.connect(self._refresh_hist); top.addWidget(ref)
        del_btn = QPushButton("🗑 Delete selected"); del_btn.setToolTip("Delete selected file from disk")
        del_btn.clicked.connect(self._hist_delete_selected); top.addWidget(del_btn)
        clr_btn = QPushButton("✕ Clear all"); clr_btn.setToolTip("Delete ALL files in the screenshots folder (across all month subfolders)")
        clr_btn.clicked.connect(self._hist_clear_all); top.addWidget(clr_btn)
        lay.addLayout(top)
        hint = QLabel("Files are stored in monthly subfolders (e.g. 2026-08) — items from a month other than the current one are shown as \"YYYY-MM/filename\".")
        hint.setStyleSheet("color:#7f849c;font-size:11px;"); hint.setWordWrap(True)
        lay.addWidget(hint)
        self.hist_list = QListWidget()
        self.hist_list.doubleClicked.connect(self._open_hist)
        lay.addWidget(self.hist_list)
        return w

    def _hist_delete_selected(self):
        item = self.hist_list.currentItem()
        if not item: return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path: return
        reply = QMessageBox.question(self, "Delete file",
            f"Delete {Path(path).name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try: Path(path).unlink()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not delete: {e}"); return
            self.hist_list.takeItem(self.hist_list.row(item))

    def _hist_clear_all(self):
        folder = Path(self.config.get("save_folder", ""))
        if not folder.exists(): return
        # Files live in per-month subfolders (2026-07, 2026-08, …) — scan all of them.
        files = [f for f in folder.rglob("*.*") if f.is_file()]
        if not files: return
        reply = QMessageBox.question(self, "Clear history",
            f"Delete ALL {len(files)} files in {folder} (all months)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            errors = []
            for f in files:
                try: f.unlink()
                except Exception as e: errors.append(str(e))
            self.hist_list.clear()
            if errors:
                QMessageBox.warning(self, "Errors", "\n".join(errors[:5]))

    # ════════════════════════════════════════
    #  MENU + TRAY
    # ════════════════════════════════════════

    def _build_menu(self):
        mb = self.menuBar()

        def _sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.ShortcutContext.ApplicationShortcut)
            s.activated.connect(fn)

        cm = mb.addMenu("Capture")
        for name, sc, fn in [
            ("Capture region",         "Ctrl+Alt+Print Screen", self.act_region),
            ("Capture active monitor", "Alt+Print Screen",      self.act_monitor),
            ("Capture active window",  "Ctrl+Print Screen",     self.act_window),
            ("Capture full screen",    "",                       self.act_fullscreen),
            ("Scrolling capture",      "Shift+Print Screen",    self.act_scrolling),
        ]:
            a = QAction(name, self)
            a.triggered.connect(fn); cm.addAction(a)
            if sc:
                _sc(sc, fn)
        cm.addSeparator()
        a = QAction("Recognize text", self)
        a.triggered.connect(self.act_ocr_text); cm.addAction(a)
        a = QAction("Recognize QR code", self)
        a.triggered.connect(self.act_ocr_code); cm.addAction(a)
        cm.addSeparator()
        for m in get_monitors():
            i = m["index"]
            cm.addAction(f"Monitor {i+1} – {m['name']}").triggered.connect(
                lambda _, x=i: self._cap_mon(x))

        rm = mb.addMenu("Recording")
        rm.addAction("Start/Stop screen recording").triggered.connect(self.act_toggle_rec)
        rm.addAction("Record GIF").triggered.connect(self.act_gif)

        tm = mb.addMenu("Tools")
        a = QAction("OCR/QR Toolbox", self)
        a.triggered.connect(self.act_ocr_qr_toolbox); tm.addAction(a)
        _sc("Ctrl+Alt+Q", self.act_ocr_qr_toolbox)
        tm.addSeparator()
        tm.addAction("Video Converter").triggered.connect(self.act_video_converter)
        tm.addAction("Image Editor").triggered.connect(self.open_image_editor)

        am = mb.addMenu("Application")
        am.addAction("Screenshots folder").triggered.connect(self._open_folder)
        am.addSeparator()
        am.addAction("History").triggered.connect(self._show_history)
        am.addSeparator()
        am.addAction("Quit").triggered.connect(self._quit)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self._app_icon, self)
        self.tray.setToolTip("PyshareX")
        self._tray_menu = None
        # Cinnamon pod Waylandem: panel sam rysuje menu z DBus (dbusmenu) i robi
        # to źle (zła pozycja, drugi PPM nic nie robi). Tam rysujemy menu sami.
        # PYSHAREX_TRAY_NATIVE=1 wymusza menu rysowane przez panel.
        self._tray_custom = (not IS_WINDOWS and _is_wayland_session()
                             and "cinnamon" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
                             and os.environ.get("PYSHAREX_TRAY_NATIVE") != "1")
        self._install_tray_menu()
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _refresh_tray_menu(self):
        """Po każdym użyciu menu budujemy je od nowa: pod XWayland stare
        QMenu potrafi zostać w stanie 'otwarte' i drugi PPM nic nie robi."""
        old = self._tray_menu
        if old is not None and old.isVisible():
            return
        self._install_tray_menu()
        if old is not None:
            old.deleteLater()

    def _install_tray_menu(self):
        menu = _TrayMenu(); menu.setStyleSheet(_css_with_menubar_font(CSS))
        menu.aboutToHide.connect(lambda: QTimer.singleShot(400, self._refresh_tray_menu))
        self._tray_menu = menu
        if self._tray_custom:
            menu.addAction("Show / hide window").triggered.connect(self.toggle_win)
            menu.addSeparator()

        csub = menu.addMenu("Capture")
        csub.addAction("Region").triggered.connect(self.act_region)
        csub.addAction("Active monitor").triggered.connect(self.act_monitor)
        csub.addAction("Active window").triggered.connect(self.act_window)
        csub.addAction("Full screen").triggered.connect(self.act_fullscreen)
        csub.addAction("Scrolling capture").triggered.connect(self.act_scrolling)
        csub.addSeparator()
        csub.addAction("Recognize text").triggered.connect(self.act_ocr_text)
        csub.addAction("Recognize QR code").triggered.connect(self.act_ocr_code)
        csub.addSeparator()
        for m in get_monitors():
            i = m["index"]
            csub.addAction(f"Monitor {i+1} – {m['name']}").triggered.connect(
                lambda _, x=i: self._cap_mon(x))

        rsub = menu.addMenu("Recording")
        rsub.addAction("Start/Stop screen recording").triggered.connect(self.act_toggle_rec)
        rsub.addAction("Record GIF").triggered.connect(self.act_gif)

        tsub = menu.addMenu("Tools")
        tsub.addAction("OCR/QR Toolbox").triggered.connect(self.act_ocr_qr_toolbox)
        tsub.addAction("Video Converter").triggered.connect(self.act_video_converter)
        tsub.addAction("Image Editor").triggered.connect(self.open_image_editor)
        
        menu.addSeparator()
        menu.addAction("Screenshots folder").triggered.connect(self._open_folder)
        menu.addSeparator()
        menu.addAction("Configuration").triggered.connect(self.show_win)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self._quit)

        if not self._tray_custom:
            self.tray.setContextMenu(menu)

    # ════════════════════════════════════════
    #  SIDEBAR NAV
    # ════════════════════════════════════════

    def _sel_sb(self, idx):
        for i, b in enumerate(self._sb_btns): b.setChecked(i == idx)
        self.stack.setCurrentIndex(idx)
        self._save_settings_btn.setVisible(idx == 2)  # Settings tab only

    def _show_capture(self):  self._sel_sb(0)
    def _show_tools(self):    self._sel_sb(1)

    def _refresh_ocr_settings_ui(self):
        """Re-sync the OCR engine/language widgets in Settings with the
        current config, in case they were changed elsewhere (e.g. the
        OCR/QR Toolbox's own language picker)."""
        cur_langs = set(self.config.get("ocr_languages", ["pl"]))
        for code, cb in self._ocr_lang_cbs.items():
            cb.setChecked(code in cur_langs)
        engine = self.config.get("ocr_engine", "paddleocr")
        if engine == "easyocr":
            self._ocr_easyocr_rb.setChecked(True)
        elif engine == "tesseract":
            self._ocr_tesseract_rb.setChecked(True)
        else:
            self._ocr_paddleocr_rb.setChecked(True)

    def _show_settings(self):
        self._refresh_ocr_settings_ui()
        self._sel_sb(2)
    def _show_history(self):
        self._sel_sb(3); self._refresh_hist()

    # ════════════════════════════════════════
    #  SHORTCUT TABLE
    # ════════════════════════════════════════

    def _refresh_table(self):
        scs = self.config.get("shortcuts", [])
        self.tbl.setRowCount(len(scs))
        for row, sc in enumerate(scs):
            en = sc.get("enabled", True)
            ni = QTableWidgetItem(sc["name"])
            ki = QTableWidgetItem(sc.get("shortcut", ""))
            if not en:
                ni.setForeground(QColor("#585b70")); ki.setForeground(QColor("#585b70"))
            self.tbl.setItem(row, 0, ni); self.tbl.setItem(row, 1, ki)
            dot = QLabel("●"); dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dot.setStyleSheet(f"color:{'#a6e3a1' if en else '#585b70'};font-size:16px;")
            self.tbl.setCellWidget(row, 2, dot); self.tbl.setRowHeight(row, 34)

    def _sc_add(self):
        self._hotkeys_stop()   # prevent hotkey loops while editing
        dlg = ShortcutEditDialog({"name":"","action":"custom","shortcut":"","enabled":True}, self)
        result = dlg.exec()
        if result == QDialog.DialogCode.Accepted:
            sc = self.config.get("shortcuts", []); sc.append(dlg.get_data())
            self.config.set("shortcuts", sc); self._refresh_table()
        self._hotkeys_start()  # always restart after dialog closes

    def _sc_edit(self):
        row = self.tbl.currentRow(); scs = self.config.get("shortcuts", [])
        if row < 0 or row >= len(scs): return
        self._hotkeys_stop()   # prevent hotkey loops while editing
        dlg = ShortcutEditDialog(scs[row], self)
        result = dlg.exec()
        if result == QDialog.DialogCode.Accepted:
            scs[row] = dlg.get_data(); self.config.set("shortcuts", scs)
            self._refresh_table()
        self._hotkeys_start()  # always restart after dialog closes

    def _sc_del(self):
        row = self.tbl.currentRow(); scs = self.config.get("shortcuts", [])
        if row < 0 or row >= len(scs): return
        if QMessageBox.question(self, "Remove", f"Remove '{scs[row]['name']}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
            scs.pop(row); self.config.set("shortcuts", scs)
            self._refresh_table(); self._hotkeys_restart()

    def _sc_up(self):
        row = self.tbl.currentRow(); scs = self.config.get("shortcuts", [])
        if row > 0:
            scs[row-1], scs[row] = scs[row], scs[row-1]
            self.config.set("shortcuts", scs); self._refresh_table(); self.tbl.selectRow(row-1)

    def _sc_dn(self):
        row = self.tbl.currentRow(); scs = self.config.get("shortcuts", [])
        if row < len(scs)-1:
            scs[row+1], scs[row] = scs[row], scs[row+1]
            self.config.set("shortcuts", scs); self._refresh_table(); self.tbl.selectRow(row+1)

    def _sc_reset(self):
        if QMessageBox.question(self, "Reset", "Restore default shortcuts?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
            self.config.set("shortcuts", Config.DEFAULT_SHORTCUTS.copy())
            self._refresh_table(); self._hotkeys_restart()

    # ════════════════════════════════════════
    #  SETTINGS HELPERS
    # ════════════════════════════════════════

    def _fill_monitors(self):
        self._mcb.clear()
        sel = self.config.get("selected_monitor", 0)
        for m in get_monitors():
            self._mcb.addItem(
                f"Monitor {m['index']+1} – {m['name']}  ({m['width']}×{m['height']})",
                m["index"])
        idx = self._mcb.findData(sel)
        if idx >= 0: self._mcb.setCurrentIndex(idx)

    def _browse(self):
        f = QFileDialog.getExistingDirectory(self, "Select folder")
        if f: self._fld.setText(f)

    def _save_settings(self):
        # Update settings in memory directly to avoid locking the JSON file
        # by performing multiple disk writes in a single millisecond.
        self.config.data["save_folder"]      = self._fld.text()
        self.config.data["image_format"]     = self._fmt.currentText()
        self.config.data["jpeg_quality"]     = self._jpg.value()
        self.config.data["delay"]            = self._dly.value()
        self.config.data["show_cursor"]      = self._cur.isChecked()
        self.config.data["gif_fps"]          = self._gfps.value()
        self.config.data["record_audio"]     = self._aud.isChecked()
        self.config.data["selected_monitor"] = self._mcb.currentData() or 0
        self.config.data["region_capture_method"] = (
            "standard" if self._region_method_grp.checkedId() == 1 else "ffmpeg")
        self.config.data["detect_windows_on_capture"] = self._detect_windows_cb.isChecked()
        
        # Safely determine the active OCR engine using QButtonGroup ID
        engine_id = self._ocr_grp.checkedId()
        if engine_id == 1:
            ocr_engine = "easyocr"
        elif engine_id == 2:
            ocr_engine = "tesseract"
        else:
            ocr_engine = "paddleocr"
            
        self.config.data["ocr_engine"]       = ocr_engine
        selected_langs = [code for code, cb in self._ocr_lang_cbs.items() if cb.isChecked()]
        self.config.data["ocr_languages"]    = selected_langs or ["pl"]
        self.config.data["after_capture"]    = {k: cb.isChecked() for k, cb in self._ac.items()}
        self.config.data["notifications"]    = {k: cb.isChecked() for k, cb in self._nc.items()}
        
        # Perform a single, safe disk write operation
        self.config.save()
        self._status("✅ Settings saved")

        # If the OCR/QR Toolbox is open, push the new language selection to
        # it immediately instead of waiting for it to be reopened.
        if getattr(self, "_toolbox_dlg", None) is not None:
            self._toolbox_dlg._toolbox_langs = list(self.config.get("ocr_languages", ["pl"])) or ["pl"]
            self._toolbox_dlg._update_lang_btn_text()

    # ════════════════════════════════════════
    #  HISTORY
    # ════════════════════════════════════════

    def _hist_label(self, f: Path, base: Path) -> str:
        """Format a history entry, prefixing the month subfolder (e.g. '2026-08/')
        whenever it isn't the current month, so older captures stay easy to spot."""
        try:
            rel = f.relative_to(base)
        except ValueError:
            rel = f.name
        rel = str(rel).replace(os.sep, "/")
        cur_month = datetime.now().strftime("%Y-%m")
        if rel.startswith(cur_month + "/"):
            rel = rel[len(cur_month) + 1:]
        sz = f.stat().st_size
        size_txt = f"{sz//1024 if sz>=1024 else sz}{'KB' if sz>=1024 else 'B'}"
        return f"📄 {rel}   {size_txt}"

    def _refresh_hist(self):
        self.hist_list.clear()
        base = Path(self.config.get("save_folder", ""))
        if not base.exists(): return
        # Screenshots/recordings now live in per-month subfolders (2026-07, 2026-08, …)
        # so gather them recursively across all months, most recent first.
        files = [f for f in base.rglob("*.*") if f.is_file()]
        for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)[:200]:
            item = QListWidgetItem(self._hist_label(f, base))
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            self.hist_list.addItem(item)

    def _open_hist(self, idx):
        item = self.hist_list.item(idx.row())
        if item:
            p = item.data(Qt.ItemDataRole.UserRole)
            if IS_WINDOWS: os.startfile(p)
            else: _popen(["xdg-open", p])

    def _add_hist(self, path):
        p = Path(path)
        if not p.exists(): return
        base = Path(self.config.get("save_folder", ""))
        item = QListWidgetItem(self._hist_label(p, base))
        item.setData(Qt.ItemDataRole.UserRole, path)
        self.hist_list.insertItem(0, item)

    # ════════════════════════════════════════
    #  STATUS / WINDOW
    # ════════════════════════════════════════

    def _status(self, msg):
        if msg == "__show_win__":
            self.show_win()
            return
        self.status_lbl.setText(msg)
        QTimer.singleShot(6000, lambda: self.status_lbl.setText("Ready"))

    def _show_tray_menu(self):
        m = self._tray_menu
        if m is None:
            return
        if m.isVisible():
            m.hide()
            return
        m.popup(QCursor.pos())      # _TrayMenu sam ustawia pozycję przy ikonie

    def _on_tray_activated(self, reason):
        R = QSystemTrayIcon.ActivationReason
        if getattr(self, "_tray_custom", False):
            print(f"[Tray] activated: {reason}", flush=True)
            if reason in (R.Trigger, R.Context):
                self._show_tray_menu()
            elif reason in (R.MiddleClick, R.DoubleClick):
                self.toggle_win()
            return
        # Linux (Cinnamon/XApp): left click = Trigger, DoubleClick often not emitted.
        # Windows: keep double-click.
        trigger = R.DoubleClick if IS_WINDOWS else R.Trigger
        if reason == trigger:
            self.toggle_win()

    def toggle_win(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_win()

    def show_win(self):
        self.setWindowState(
            (self.windowState() & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        e.ignore(); self.hide()   # no tray notification

    def _open_folder(self):
        f = _month_folder(self.config.get("save_folder", ""))
        if IS_WINDOWS: os.startfile(str(f))
        else: _popen(["xdg-open", str(f)])

    def _quit(self):
        self._hotkeys_stop()
        for t in (self.rec_th, self.gif_th):
            if t:
                try: t.stop(); t.wait(4000)
                except Exception: pass
        if self._border: self._border.stop()
        if self._bar:    self._bar.stop_display()
        QApplication.quit()

    # ════════════════════════════════════════
    #  HOTKEYS
    # ════════════════════════════════════════

    _ACTION_MAP = {
        "capture_region":           "act_region",
        "capture_active_monitor":   "act_monitor",
        "capture_active_window":    "act_window",
        "capture_selected_monitor": "act_sel_monitor",
        "capture_scrolling":        "act_scrolling",
        "toggle_recording":         "act_toggle_rec",
        "record_gif":               "act_gif",
        "ocr_text":                 "act_ocr_text",
        "ocr_code":                 "act_ocr_code",
        "ocr_qr_toolbox":           "act_ocr_qr_toolbox",
        "capture_fullscreen":       "act_fullscreen",
        "video_converter":          "act_video_converter",
    }

    def _hotkeys_start(self):
        if _is_wayland_session() and not getattr(self, "_wl_hotkey_warned", False):
            self._wl_hotkey_warned = True
            print("[Hotkeys] Wayland: pynput nie widzi klawiszy poza oknami XWayland. "
                  "Ustaw skróty w systemie (Cinnamon: Ustawienia > Klawiatura > Skróty > "
                  "Własne) na polecenie:\n"
                  f"          python3 {os.path.abspath(__file__)} --action capture_region\n"
                  "          (lista akcji: --list-actions)")
        if (_is_wayland_session() and os.environ.get("PYSHAREX_NO_AUTOKEYS") != "1"
                and _cinnamon_available()):
            # Skróty obsługuje Cinnamon; pynput pod Waylandem i tak nie działa
            # poza oknami XWayland (a podwójne wyzwalanie byłoby gorsze).
            import threading
            threading.Thread(target=_cinnamon_sync_shortcuts,
                             args=(list(self.config.get("shortcuts", [])),),
                             daemon=True).start()
            return
        if not PYNPUT_AVAILABLE:
            print("[Hotkeys] pynput not available — global hotkeys disabled")
            return
        combos = {}
        for sc in self.config.get("shortcuts", []):
            if not sc.get("enabled", True):
                continue
            mn = self._ACTION_MAP.get(sc.get("action", ""))
            pk = self._qt2pk(sc.get("shortcut", ""))
            if mn and pk:
                def make(m):
                    def h():
                        # pynput fires callbacks from its own thread.
                        # In PySide6 (unlike PyQt6) QTimer.singleShot is not
                        # thread-safe from non-Qt threads — use a queued signal
                        # via _hotkey_sig which is connected in __init__.
                        self._hotkey_sig.emit(m)
                    return h
                combos[pk] = make(mn)
            else:
                print(f"[Hotkeys] skipped: action={sc.get('action')!r} -> mn={mn!r}, shortcut={sc.get('shortcut')!r} -> pk={pk!r}")
        if not combos:
            print("[Hotkeys] no valid combos found — check shortcuts config")
            return
        print(f"[Hotkeys] registering {len(combos)} hotkeys: {list(combos.keys())}")
        try:
            from pynput import keyboard as kb
            self._hkl = kb.GlobalHotKeys(combos)
            self._hkl.start()
            print("[Hotkeys] GlobalHotKeys started OK")
        except Exception as e:
            print(f"[Hotkeys] error starting GlobalHotKeys: {e}")

    def _dispatch_hotkey(self, method_name: str):
        """Called on the main Qt thread via queued signal — safe to touch Qt objects."""
        fn = getattr(self, method_name, None)
        if fn:
            fn()

    # ════════════════════════════════════════
    #  IPC: `pysharex.py --action <nazwa>` steruje działającą instancją
    #  (skróty systemowe zamiast pynput - działa też na Waylandzie)
    # ════════════════════════════════════════
    def _ipc_start(self):
        try:
            from PySide6.QtNetwork import QLocalServer
        except Exception as e:
            print(f"[IPC] brak QtNetwork: {e}"); return
        srv = QLocalServer(self)
        if not srv.listen(_IPC_NAME):
            if _ipc_send(None, probe_only=True):
                print("[IPC] inna instancja PyshareX już nasłuchuje - pomijam IPC")
                return
            QLocalServer.removeServer(_IPC_NAME)       # martwe gniazdo po awarii
            if not srv.listen(_IPC_NAME):
                print(f"[IPC] nie mogę nasłuchiwać: {srv.errorString()}"); return
        srv.newConnection.connect(self._ipc_on_connection)
        self._ipc_server = srv
        try:
            cdir = os.path.expanduser("~/.cache/pysharex")
            os.makedirs(cdir, exist_ok=True)
            with open(os.path.join(cdir, "ipc-path"), "w", encoding="utf-8") as f:
                f.write(srv.fullServerName())
            print(f"[IPC] nasłuchuję: {srv.fullServerName()}", flush=True)
        except Exception as e:
            print(f"[IPC] nie zapisano ścieżki gniazda: {e}")

    def _ipc_on_connection(self):
        sock = self._ipc_server.nextPendingConnection()
        if sock is None:
            return
        def _read():
            data = bytes(sock.readAll()).decode("utf-8", "ignore")
            for line in data.splitlines():
                self._ipc_handle(line.strip())
            sock.disconnectFromServer()
        sock.readyRead.connect(_read)
        if sock.bytesAvailable():
            _read()

    def _ipc_handle(self, name):
        print(f"[IPC] odebrano akcję: {name!r}", flush=True)
        method = self._ACTION_MAP.get(name)
        if method:
            self._hotkey_sig.emit(method)
        elif name:
            print(f"[IPC] nieznana akcja: {name!r} (dostępne: {', '.join(self._ACTION_MAP)})")

    def _hotkeys_stop(self):
        if self._hkl:
            try: self._hkl.stop()
            except Exception: pass
            self._hkl = None

    def _hotkeys_restart(self):
        self._hotkeys_stop(); self._hotkeys_start()

    def _qt2pk(self, qs: str) -> str:
        # Single-token map (after multi-word substitution below)
        M = {
            "Ctrl":         "<ctrl>",
            "Alt":          "<alt>",
            "Shift":        "<shift>",
            "Meta":         "<cmd>",
            "PrintScreen":  "<print_screen>",
            "Return":       "<enter>",
            "Enter":        "<enter>",
            "Delete":       "<delete>",
            "Backspace":    "<backspace>",
            "Insert":       "<insert>",
            "Home":         "<home>",
            "End":          "<end>",
            "PgUp":         "<page_up>",
            "PgDown":       "<page_down>",
            "Up":           "<up>",
            "Down":         "<down>",
            "Left":         "<left>",
            "Right":        "<right>",
            "Space":        "<space>",
            "Tab":          "<tab>",
            "Escape":       "<esc>",
            "Esc":          "<esc>",
            "Plus":         "+",
        }
        # Replace multi-word key names BEFORE splitting on "+"
        qs = qs.replace("Print Screen", "PrintScreen")
        qs = qs.replace("Page Up",      "PgUp")
        qs = qs.replace("Page Down",    "PgDown")
        qs = qs.replace("Num Lock",     "num_lock")
        qs = qs.replace("Caps Lock",    "caps_lock")
        qs = qs.replace("Scroll Lock",  "scroll_lock")

        parts = qs.replace("++", "+Plus").split("+")
        res = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if p in M:
                res.append(M[p])
            elif len(p) == 1:
                res.append(p.lower())
            elif p.startswith("F") and p[1:].isdigit():
                res.append(f"<f{p[1:]}>")
            else:
                res.append(p.lower())
        return "+".join(res)
    
    
    
    # ════════════════════════════════════════
    #  CAPTURE ACTIONS
    # ════════════════════════════════════════

    def _on_notify(self, path, pixmap=None):
        self._add_hist(path)
        notify(self.config, path, pixmap=pixmap)
        
        after = self.config.get("after_capture", {})

        # 0a. Copy to clipboard
        if after.get("copy_to_clipboard"):
            try:
                pix = QPixmap(path)
                if not pix.isNull():
                    QApplication.clipboard().setPixmap(pix)
            except Exception as e:
                print(f"[after_capture] copy_to_clipboard error: {e}")

        # 0b. Show in explorer
        if after.get("show_in_explorer"):
            try:
                if IS_WINDOWS:
                    _popen(["explorer", "/select,", path.replace("/", "\\")])
                else:
                    _popen(["xdg-open", str(Path(path).parent)])
            except Exception as e:
                print(f"[after_capture] show_in_explorer error: {e}")

        # 0c. Save to file — file is already saved by the engine,
        #     but if the flag is OFF we optionally skip saving (future use).
        #     Currently we just show a status note when the flag is enabled.
        # (no additional action needed — engine always saves)

        # 1. Automatyczne rozpoznawanie tekstu (OCR)
        if after.get("ocr_recognize"):
            def do_auto_ocr():
                engine = self.config.get("ocr_engine", "paddleocr")
                lang_codes = self.config.get("ocr_languages", ["pl"])
                if engine == "paddleocr":
                    txt = self.engine._ocr_paddleocr(path, lang_codes)
                elif engine == "easyocr":
                    txt = self.engine._ocr_easyocr(path, lang_codes)
                else:
                    txt = self.engine._ocr_tesseract(path, lang_codes)
                self._ocr_done_sig.emit(txt, "OCR Result")
            threading.Thread(target=do_auto_ocr, daemon=True).start()

        # 2. Automatyczne skanowanie kodu QR
        if after.get("scan_qr"):
            def do_auto_qr():
                if not CV2_AVAILABLE:
                    self._ocr_done_sig.emit("Brak biblioteki OpenCV.\n(pip install opencv-python)", "QR Code Result")
                    return
                try:
                    import cv2
                    import numpy as np
                    # Używamy cv2.imdecode z numpy, aby uniknąć problemów z polskimi znakami w ścieżkach
                    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                    detector = cv2.QRCodeDetector()
                    data, bbox, _ = detector.detectAndDecode(img)
                    if data:
                        self._ocr_done_sig.emit(data, "QR Code Result")
                    else:
                        self._ocr_done_sig.emit("No QR code detected in the captured screenshot.", "QR Code Result")
                except Exception as e:
                    self._ocr_done_sig.emit(f"An error occurred while decoding: {e}", "QR Code Result")
            threading.Thread(target=do_auto_qr, daemon=True).start()

        # 3. Sprawdzenie czy użytkownik chce otworzyć edytor
        if after.get("open_in_editor"):
            # Jeśli nie mamy pixmapy w pamięci, ładujemy z pliku
            if not pixmap and os.path.exists(path):
                pixmap = QPixmap(path)
            
            if pixmap:
                # Otwieramy okno edytora
                self.editor = ImageEditorWindow(pixmap, self.save_edited_image, str(_month_folder(self.config.get("save_folder", ""))), self)
                self.editor.show()

    def _done(self, path, label="Screenshot"):
        if path:
            self.status_sig.emit(f"✅ {label}: {Path(path).name}")
            # Schedule on main thread (this may be called from background thread)
            self._notify_sig.emit(path, None)

    def act_region(self):
        d = self.config.get("delay", 0)
        if d: self._status(f"Capture in {d}s…"); QTimer.singleShot(d*1000, self._open_region)
        else: self._open_region()

    def _open_region(self):
        # Remember whether the main window was visible before hiding it.
        # After capture / cancel we restore only if it was visible.
        self._win_was_visible = self.isVisible()
        self.hide()
        # Longer delay when triggered from global hotkey to ensure
        # the hotkey key-up event is fully processed before overlay grabs focus
        QTimer.singleShot(200, self._do_region)

    def _do_region(self):
        self._sel = EnhancedRegionSelector()
        # same start folder as the Image Editor's Import dialog
        try:
            self._sel.import_dir = str(_month_folder(self.config.get(
                "save_folder", str(QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.PicturesLocation)))))
        except Exception:
            self._sel.import_dir = ""
        self._sel.region_selected.connect(self._on_region)
        self._sel.cancelled.connect(self._on_region_cancelled)
        # Force focus after the selector is fully constructed
        QTimer.singleShot(80, self._focus_selector)

    def _on_region_cancelled(self):
        """Restore window only if it was visible before the selector opened."""
        if getattr(self, '_win_was_visible', True):
            self.show_win()

    def _focus_selector(self):
        if self._sel and self._sel.isVisible():
            self._sel.raise_()
            self._sel.activateWindow()
            self._sel.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _on_region(self, x, y, w, h):
        # Fallback path only — normal path is handled by _grab_composite_and_close.
        if w < 5 or h < 5:
            self._on_region_cancelled()
            return
        def do():
            time.sleep(0.05)
            p = self.engine.capture_region(x, y, w, h)
            if p:
                self.status_sig.emit(f"✅ Region: {Path(p).name}")
                self._notify_sig.emit(p, None)
            # Restore window only if it was visible before capture started
            if getattr(self, '_win_was_visible', True):
                self.status_sig.emit("__show_win__")
        threading.Thread(target=do, daemon=True).start()

    def act_monitor(self):
        threading.Thread(target=lambda: self._done(
            self.engine.capture_active_monitor(), "Monitor"), daemon=True).start()

    def act_sel_monitor(self):
        idx = self.config.get("selected_monitor", 0)
        threading.Thread(target=lambda: self._done(
            self.engine.capture_specific_monitor(idx), f"Monitor {idx+1}"), daemon=True).start()

    def _cap_mon(self, idx: int):
        threading.Thread(target=lambda: self._done(
            self.engine.capture_specific_monitor(idx), f"Monitor {idx+1}"), daemon=True).start()

    def act_window(self):
        threading.Thread(target=lambda: self._done(
            self.engine.capture_active_window(), "Window"), daemon=True).start()

    def act_fullscreen(self):
        threading.Thread(target=lambda: self._done(
            self.engine.capture_fullscreen(), "Full screen"), daemon=True).start()

    def act_scrolling(self):
        self._status("Select area for scrolling capture…")
        self.hide()
        QTimer.singleShot(160, self._do_scroll_region)

    def _do_scroll_region(self):
        # Uruchamiamy sprawdzony selektor obszaru
        self._sel = RegionSelector()
        self._sel.region_selected.connect(self._on_scroll_region)
        self._sel.cancelled.connect(self.show_win)

    def _on_scroll_region(self, x, y, w, h):
        if w < 5 or h < 5: 
            self.show_win()
            return

        self._status("Scrolling capture — scrolling…")

        def do():
            time.sleep(0.05)
            # Przekazujemy wycięty obszar do silnika
            p = self.engine.capture_scrolling(region=(x, y, w, h))
            self._done(p, "Scroll")
            QTimer.singleShot(0, self.show_win)

        threading.Thread(target=do, daemon=True).start()

    # ════════════════════════════════════════
    #  RECORDING
    # ════════════════════════════════════════

    def act_toggle_rec(self):
        if self.is_rec: self._stop_rec(abort=False)
        else:           self._ask_rec_region()

    def _ask_rec_region(self):
        """Ask user to select region, then start recording."""
        self._status("Select region for recording (or press Esc for active monitor)…")
        # Capture cursor position NOW — this is the monitor the user is working on
        self._pre_capture_cursor = QCursor.pos()
        self.hide()
        QTimer.singleShot(160, self._do_rec_region)

    def _do_rec_region(self):
        self._sel = RegionSelector()
        self._sel.region_selected.connect(self._start_rec_with_region)
        self._sel.cancelled.connect(self._start_rec_fullscreen)

    def _start_rec_with_region(self, x, y, w, h):
        if w < 5 or h < 5:
            self._start_rec_fullscreen(); return
        self._start_rec(region=(x, y, w, h))

    def _start_rec_fullscreen(self):
        # Use cursor position captured before overlay opened
        region = self._monitor_region_at_cursor(self._pre_capture_cursor)
        self._start_rec(region=region)

    def _monitor_region_at_cursor(self, cursor_pos=None) -> tuple:
        """Return (x,y,w,h) of the monitor containing cursor_pos (or current cursor)."""
        if cursor_pos is None:
            cursor_pos = QCursor.pos()
        cx, cy = cursor_pos.x(), cursor_pos.y()
        # Convert logical → physical for mss
        try:
            with mss.MSS() as sct:
                for m in sct.monitors[1:]:
                    if (m["left"] <= cx < m["left"] + m["width"] and
                            m["top"]  <= cy < m["top"]  + m["height"]):
                        return m["left"], m["top"], m["width"], m["height"]
                m = sct.monitors[1]
                return m["left"], m["top"], m["width"], m["height"]
        except Exception:
            return 0, 0, 1920, 1080

    def _start_rec(self, region=None):
        if self.rec_th:
            try: self.rec_th.stop(); self.rec_th.wait(3000)
            except Exception: pass
            self.rec_th = None

        folder = _month_folder(self.config.get("save_folder", "."))
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        fp = str(folder / f"video_{ts}.mp4")

        self._abort = False
        self.rec_th = RecordingThread(region, fp, 30, self.config.get("record_audio", False))
        self.rec_th.region_ready.connect(self._on_rec_region)
        self.rec_th.finished.connect(self._on_rec_done)
        self.rec_th.error.connect(self._on_rec_err)
        self.rec_th.start()

        self.is_rec = True; self.rec_ind.show()
        if hasattr(self, "_rec_btn"):
            self._rec_btn.setText("⏹ Stop"); self._rec_btn.setProperty("rec", "1")
            self._rec_btn.setStyle(self._rec_btn.style())
        self._status("🔴 Recording…")

    def _on_rec_region(self, x, y, w, h):
        if self._border: self._border.stop()
        self._border = RecordingBorder(x, y, w, h)
        if self._bar:   self._bar.stop_display()
        self._bar = RecordingBar()
        # Robimy "zdjęcie" obszaru pod miniaturkę, zanim ruszy nagrywanie
        try:
            with mss.MSS() as sct:
                # x, y, w, h to obszar fizyczny
                mon = {"top": y, "left": x, "width": w, "height": h}
                sct_img = sct.grab(mon)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                
                # Konwersja PIL -> QPixmap
                raw_data = img.tobytes()
                # Force deep copy to prevent Segmentation Fault
                qim = QImage(raw_data, img.size[0], img.size[1], QImage.Format.Format_RGB888).copy()
                self.last_rec_pixmap = QPixmap.fromImage(qim)
        except:
            self.last_rec_pixmap = None
        self._bar.stop_clicked.connect(lambda: self._stop_rec(False))
        self._bar.abort_clicked.connect(lambda: self._stop_rec(True))
        #self._bar.start_display(region=(x, y, w, h))
        self._bar.start_display() # Wywołaj bez regionu, żeby odpalić tylko timer
        
        border_rect = RecordingBorder._phys_to_logical(x, y, w, h)
        bx = border_rect.x() + border_rect.width() // 2 - self._bar.width() // 2
        by = border_rect.y() + border_rect.height() + 6
        self._bar.move(bx, by)
        self._bar.show()

    def _stop_rec(self, abort=False):
        self._abort = abort; self.is_rec = False; self.rec_ind.hide()
        if hasattr(self, "_rec_btn"):
            self._rec_btn.setText("Record"); self._rec_btn.setProperty("rec", "0")
            self._rec_btn.setStyle(self._rec_btn.style())
        if self._border: self._border.stop(); self._border = None
        if self._bar:    self._bar.stop_display(); self._bar = None
        self._status("⏹ Stopping…")
        if self.rec_th: self.rec_th.stop()

    def _on_rec_done(self, path):
        self.is_rec = False; self.rec_ind.hide()
        if hasattr(self, "_rec_btn"):
            self._rec_btn.setText("Record"); self._rec_btn.setProperty("rec", "0")
            self._rec_btn.setStyle(self._rec_btn.style())
        self.rec_th = None
        if self._border: self._border.stop(); self._border = None
        if self._bar:    self._bar.stop_display(); self._bar = None
        if self._abort:
            try: Path(path).unlink()
            except Exception: pass
            self._status("Recording aborted"); return
        self._status(f"✅ Video: {Path(path).name}")
        self._notify_sig.emit(path, self.last_rec_pixmap)

    def _on_rec_err(self, msg):
        self.is_rec = False; self.rec_ind.hide()
        if hasattr(self, "_rec_btn"):
            self._rec_btn.setText("Record"); self._rec_btn.setProperty("rec", "0")
            self._rec_btn.setStyle(self._rec_btn.style())
        self.rec_th = None
        if self._border: self._border.stop(); self._border = None
        if self._bar:    self._bar.stop_display(); self._bar = None
        self._status("❌ Recording error")
        dlg = QDialog(self); dlg.setWindowTitle("Recording error")
        dlg.setMinimumSize(520, 260); dlg.setStyleSheet(self.styleSheet())
        lay = QVBoxLayout(dlg); lay.addWidget(QLabel("Recording ended with an error:"))
        te = QTextEdit(); te.setReadOnly(True); te.setPlainText(msg); lay.addWidget(te)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept); lay.addWidget(bb); dlg.exec()

    # ── GIF ──────────────────────────────────

    def act_gif(self):
        """Ask user to select region, then start GIF recording."""
        self._status("Select region for GIF (or press Esc for active monitor)…")
        self._pre_capture_cursor = QCursor.pos()
        self.hide()
        QTimer.singleShot(160, self._do_gif_region)

    def _do_gif_region(self):
        self._sel = RegionSelector()
        self._sel.region_selected.connect(self._start_gif_with_region)
        self._sel.cancelled.connect(self._start_gif_fullscreen)

    def _start_gif_with_region(self, x, y, w, h):
        if w < 5 or h < 5:
            self._start_gif_fullscreen(); return
        self._launch_gif(region=(x, y, w, h))

    def _start_gif_fullscreen(self):
        region = self._monitor_region_at_cursor(self._pre_capture_cursor)
        self._launch_gif(region=region)

    def _launch_gif(self, region):
        if self.gif_th and self.gif_th.isRunning():
            return  # already recording
        folder = _month_folder(self.config.get("save_folder", "."))
        ts  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        fp  = str(folder / f"gif_{ts}.gif")
        fps = self.config.get("gif_fps", 10)
        self.gif_th = RecordingThread(region, fp, gif_mode=True, gif_fps=fps, gif_duration=99999)
        self.gif_th.region_ready.connect(self._on_gif_region)
        self.gif_th.finished.connect(self._on_gif_done)
        self.gif_th.error.connect(lambda e: (self._status(f"❌ GIF: {e}"), self._stop_gif_border()))
        self.gif_th.start()
        self._status("🎞 Recording GIF… (Stop via border or tray)")

    def _on_gif_region(self, x, y, w, h):
        if self._border: self._border.stop()
        self._border = RecordingBorder(x, y, w, h)
        if self._gif_bar: self._gif_bar.stop_display()
        self._gif_bar = RecordingBar()
        self._gif_bar.stop_clicked.connect(self._stop_gif)
        self._gif_bar.abort_clicked.connect(self._abort_gif)
        try:
            with mss.MSS() as sct:
                mon = {"top": y, "left": x, "width": w, "height": h}
                sct_img = sct.grab(mon)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                raw_data = img.tobytes()
                # Force deep copy to prevent Segmentation Fault
                qim = QImage(raw_data, img.size[0], img.size[1], QImage.Format.Format_RGB888).copy()
                self.last_rec_pixmap = QPixmap.fromImage(qim)
        except:
            self.last_rec_pixmap = None
        
        # Poprawka paska sterowania (by nie uciekał):
        self._gif_bar.start_display() 
        l_rect = physical_to_logical_rect(x, y, w, h)
        # Przesunięcie panelu bliżej ramki (zmniejszono z +10 na +2)
        self._gif_bar.move(l_rect.x(), l_rect.bottom() + 2)
        self._gif_bar.show()

    def _stop_gif(self):
        if self.gif_th: self.gif_th.stop()
        self._stop_gif_border()

    def _abort_gif(self):
        self._gif_aborted = True
        if self.gif_th: self.gif_th.stop()
        self._stop_gif_border()

    def _stop_gif_border(self):
        if self._border: self._border.stop(); self._border = None
        if self._gif_bar: self._gif_bar.stop_display(); self._gif_bar = None

    def _on_gif_done(self, path):
        self._stop_gif_border()
        self.gif_th = None
        if getattr(self, "_gif_aborted", False):
            self._gif_aborted = False
            try: Path(path).unlink()
            except Exception: pass
            self._status("GIF recording aborted"); return
        self._status(f"✅ GIF: {Path(path).name}")
        self._notify_sig.emit(path, self.last_rec_pixmap)

    # ── OCR ──────────────────────────────────

    def act_ocr_text(self):
        self._status("Select area for OCR…"); self.hide()
        self._ocr_mode = "text"
        QTimer.singleShot(200, self._do_ocr)

    def act_ocr_code(self):
        self._status("Select area for QR/code OCR…"); self.hide()
        self._ocr_mode = "qr"
        QTimer.singleShot(200, self._do_ocr)

    def _do_ocr(self):
        self._sel = RegionSelector()
        if getattr(self, "_ocr_mode", "text") == "qr":
            self._sel.region_selected.connect(self._run_qr)
        else:
            self._sel.region_selected.connect(self._run_ocr)
        self._sel.cancelled.connect(self.show_win)

    def _run_ocr(self, x, y, w, h):
        if w < 5 or h < 5:
            self.show_win()
            return

        self._status("⏳ Running OCR…")
        self._prog_show_sig.emit("Capturing region…")

        def go():
            time.sleep(0.05)
            path = self.engine.capture_region(x, y, w, h)
            if not path:
                self._prog_hide_sig.emit()
                self._ocr_done_sig.emit("Failed to capture screen region.", "OCR Result")
                return
            engine_name = self.config.get("ocr_engine", "paddleocr")
            lang_codes = self.config.get("ocr_languages", ["pl"])
            self._prog_msg_sig.emit(f"Running {engine_name.upper()}…")
            if engine_name == "paddleocr":
                txt = self.engine._ocr_paddleocr(path, lang_codes)
            elif engine_name == "easyocr":
                txt = self.engine._ocr_easyocr(path, lang_codes)
            else:
                txt = self.engine._ocr_tesseract(path, lang_codes)
            self._prog_hide_sig.emit()
            self._ocr_done_sig.emit(txt, "OCR Result")

        threading.Thread(target=go, daemon=True).start()

    def _run_qr(self, x, y, w, h):
        if w < 5 or h < 5:
            self.show_win()
            return

        self._status("⏳ Scanning QR…")
        self._prog_show_sig.emit("Capturing region…")

        def go():
            time.sleep(0.05)
            path = self.engine.capture_region(x, y, w, h)
            if not path:
                self._prog_hide_sig.emit()
                self._ocr_done_sig.emit("Failed to capture screen region.", "QR Code Result")
                return
            if not CV2_AVAILABLE:
                self._prog_hide_sig.emit()
                self._ocr_done_sig.emit("OpenCV not installed.\npip install opencv-python", "QR Code Result")
                return
            self._prog_msg_sig.emit("Scanning QR code…")
            try:
                import cv2
                import numpy as np
                img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                detector = cv2.QRCodeDetector()
                data, bbox, _ = detector.detectAndDecode(img)
                result = data if data else "No QR code detected in the selected region."
                self._prog_hide_sig.emit()
                self._ocr_done_sig.emit(result, "QR Code Result")
            except Exception as e:
                self._prog_hide_sig.emit()
                self._ocr_done_sig.emit(f"QR decode error: {e}", "QR Code Result")

        threading.Thread(target=go, daemon=True).start()
    
    def act_ocr_qr_toolbox(self):
        if not hasattr(self, "_toolbox_dlg") or self._toolbox_dlg is None:
            self._toolbox_dlg = OcrQrToolboxDialog(self, parent=None)
            self._toolbox_dlg.setStyleSheet(self.styleSheet())
            self._toolbox_dlg.setWindowIcon(self._app_icon)
            self._toolbox_dlg.finished.connect(lambda: setattr(self, "_toolbox_dlg", None))
        self._toolbox_dlg.show()
        self._toolbox_dlg.raise_()
        self._toolbox_dlg.activateWindow()

    def act_video_converter(self):
        # Otwieramy okno konwertera
        dlg = VideoConverterDialog(self)
        dlg.setStyleSheet(self.styleSheet()) # Opcjonalne: dopasowanie stylu do ciemnego motywu apki
        dlg.exec()
    
    def open_image_editor(self):
        """Launches the Image Editor workflow."""
        # Pobranie folderu zapisu z konfiguracji
        screenshot_dir = str(_month_folder(self.config.get("save_folder", str(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)))))
        
        start_dlg = ImageEditorStartDialog(self, default_dir=screenshot_dir)
        if start_dlg.exec() == QDialog.DialogCode.Accepted:
            pixmap = start_dlg.result_image
            if pixmap:
                # Otwarcie właściwego okna edytora (z przekazaniem screenshot_dir)
                self.editor_win = ImageEditorWindow(pixmap, self.save_edited_image, screenshot_dir, self)
                self.editor_win.show()
    
    def copy_to_clipboard(self):
        self.scene.clearSelection()
        rect = self.scene.sceneRect()
        pixmap = QPixmap(int(rect.width()), int(rect.height()))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.scene.render(painter)
        painter.end()
        QApplication.clipboard().setPixmap(pixmap)
    
    
    def save_edited_image(self, qimage):
        """Saves the output from the editor to the current month's screenshots folder."""
        save_dir = _month_folder(self.config.get("save_folder", "screenshots"))
        
        filename = f"edited_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png"
        path = save_dir / filename
        qimage.save(str(path), "PNG")
        
        # Powiadomienie (używa Twojej istniejącej metody powiadomień)
        self._on_notify(str(path), None)
    
    def _prog_show(self, message: str):
        """Create and show the progress widget on the main thread."""
        if self._ocr_prog is not None:
            try: self._ocr_prog.close()
            except Exception: pass
        self._ocr_prog = OcrProgressDialog(message)
        self._ocr_prog.setStyleSheet(self.styleSheet())
        self._ocr_prog.show()

    def _prog_msg(self, message: str):
        """Update progress message on the main thread."""
        if self._ocr_prog is not None:
            self._ocr_prog.set_message(message)

    def _prog_hide(self):
        """Close the progress widget on the main thread."""
        if self._ocr_prog is not None:
            try: self._ocr_prog.close()
            except Exception: pass
            self._ocr_prog = None

    def _show_ocr(self, txt, title="Result"):
        dlg = OcrResultDialog(txt, parent=None)
        dlg.setWindowTitle(title)
        dlg.setStyleSheet(self.styleSheet())
        dlg.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        dlg.show(); dlg.raise_(); dlg.activateWindow()
        QTimer.singleShot(100, self.show_win)
        dlg.exec()


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

def _script_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _svg_icon(svg_str: str, size: int = 32, height: int | None = None) -> QIcon:
    """Render an SVG string to a QIcon of the given pixel size.
    `height` defaults to `size` (square icon); pass it for wide icons."""
    renderer = QSvgRenderer(svg_str.encode())
    pm = QPixmap(size, height or size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)

def _svg_emoji_icon(svg_str: str, emoji: str, btn_w: int, btn_h: int) -> QIcon:
    """Render SVG on the left and emoji on the right into a single QIcon
    sized to fill the button (btn_w x btn_h).  Both glyphs share the same
    height so they look visually balanced."""
    pm = QPixmap(btn_w, btn_h)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # SVG occupies the left square
    svg_size = btn_h - 4          # 2 px padding top/bottom
    renderer = QSvgRenderer(svg_str.encode())
    from PySide6.QtCore import QRectF
    renderer.render(painter, QRectF(2, 2, svg_size, svg_size))
    # Emoji occupies the right portion
    emoji_px = max(10, btn_h - 8)
    font = QFont()
    font.setPixelSize(emoji_px)
    painter.setFont(font)
    painter.setPen(Qt.GlobalColor.white)
    emoji_x = 2 + svg_size + 2
    emoji_rect = QRectF(emoji_x, 0, btn_w - emoji_x - 1, btn_h)
    painter.drawText(emoji_rect,
                     Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                     emoji)
    painter.end()
    return QIcon(pm)

# SVG source for the History "Refresh" button - a clean circular-arrow glyph,
# replacing the previous "refresh" emoji which rendered as a broken/tofu glyph
# in the app's custom font (see the History screenshot) instead of a real icon.
_SVG_REFRESH = """<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
 <path d="M20.5 11.5A8.5 8.5 0 1 1 18.2 6" stroke="#89b4fa" stroke-width="2.3" stroke-linecap="round" fill="none"/>
 <path d="M20.7 4.8v6.2h-6.2" stroke="#89b4fa" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
</svg>"""

# SVG source for the Select cursor tool
_SVG_SELECT = """<svg width="77.068" height="77.068" version="1.1" viewBox="0 0 18.496 18.496" xmlns="http://www.w3.org/2000/svg">
 <path d="m3.8616 1.6312v15.048l3.9126-3.9126 3.0097 4.665 2.3325-1.5048-2.7242-4.3081 4.7557-0.35688z" fill="#fff" stroke="#000" stroke-width="1.5048"/>
</svg>"""

# SVG source for the Rectangle annotation tool — a plain square frame in
# the same red as the "⭕" circle-tool emoji next to it, replacing the
# "⬜" (white square) emoji which read as a filled white swatch rather
# than a rectangle-drawing tool.
_SVG_RECT_TOOL = """<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
 <rect x="2.6" y="2.6" width="18.8" height="18.8" stroke="#ea3323" stroke-width="2.6"/>
</svg>"""

# SVG source for the Circle (ellipse) annotation tool — a red unfilled ring,
# same colour and stroke weight as _SVG_RECT_TOOL, replacing the "⭕" emoji.
_SVG_CIRCLE_TOOL = """<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
 <circle cx="12" cy="12" r="9.4" stroke="#ea3323" stroke-width="2.6"/>
</svg>"""

# SVG source for the straight Line annotation tool — a red diagonal line
# (bottom-left to top-right), same colour and stroke weight as the other
# red shape icons, replacing the "📏" emoji.
_SVG_LINE_TOOL = """<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
 <line x1="3.5" y1="20.5" x2="20.5" y2="3.5" stroke="#ea3323" stroke-width="2.6" stroke-linecap="round"/>
</svg>"""

# SVG source for the Arrow annotation tool — a solid white arrow glyph
# with a thin black outline (same white-fill/black-stroke style as
# _SVG_SELECT so it reads clearly on both the dark Capture Region toolbar
# and the light Image Editor toolbar), replacing the "➡️" emoji.
_SVG_ARROW_TOOL = """<svg width="32" height="32" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
 <path d="M2 9 H14 V4 L22 12 L14 20 V15 H2 Z" fill="#fff" stroke="#000" stroke-width="1" stroke-linejoin="round"/>
</svg>"""

# SVG source for the Crop tool — classic two-corner "crop marks" glyph
# (matches the universal crop-tool icon used by most photo editors),
# replacing the previous generic "📐" ruler emoji which didn't read as
# a crop action.
# SVG source for the Pixelate / Blur tool — a small mosaic of grey cells inside the same red
# frame as the other shape tools.
_SVG_PIXELATE_TOOL = """<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
 <rect x="3" y="3" width="6" height="6" fill="#4a4a4a"/><rect x="9" y="3" width="6" height="6" fill="#9a9a9a"/><rect x="15" y="3" width="6" height="6" fill="#d0d0d0"/>
 <rect x="3" y="9" width="6" height="6" fill="#9a9a9a"/><rect x="9" y="9" width="6" height="6" fill="#d0d0d0"/><rect x="15" y="9" width="6" height="6" fill="#4a4a4a"/>
 <rect x="3" y="15" width="6" height="6" fill="#d0d0d0"/><rect x="9" y="15" width="6" height="6" fill="#4a4a4a"/><rect x="15" y="15" width="6" height="6" fill="#9a9a9a"/>
 <rect x="1.8" y="1.8" width="20.4" height="20.4" stroke="#ea3323" stroke-width="1.6"/>
</svg>"""

# SVG source for the Spotlight tool — a grey disc with three italic "A" letters
# (drawn as paths, so no font is needed) inside a dashed yellow ring.
_SVG_SPOTLIGHT_TOOL = """<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
 <circle cx="12" cy="12" r="8.6" fill="#b3b3b3"/>
 <g stroke="#333333" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round">
  <path d="M5.8 15.4 L9.2 8.6 L10.2 15.4 M7.3 12.9 H9.9"/>
  <path d="M10.2 15.4 L13.6 8.6 L14.6 15.4 M11.7 12.9 H14.3"/>
  <path d="M14.6 15.4 L18 8.6 L19 15.4 M16.1 12.9 H18.7"/>
 </g>
 <circle cx="12" cy="12" r="9.3" stroke="#ffff00" stroke-width="1.7" stroke-dasharray="3.6 2.3"/>
</svg>"""

# SVG source for the Smart Eraser tool — a grey band with dark letters, partly
# covered by a flat grey rectangle with a dashed green outline.
_SVG_SMART_ERASER_TOOL = """<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
 <rect x="0.5" y="4" width="23" height="16" fill="#666666"/>
 <g stroke="#333333" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
  <path d="M3 18.5 L6.2 8.5 L7.6 18.5 M4.2 15.5 H7.2"/>
  <path d="M8.5 18.5 L11.7 8.5 L13.1 18.5 M9.7 15.5 H12.7"/>
  <ellipse cx="16.4" cy="15.2" rx="1.9" ry="2.6"/>
  <ellipse cx="21" cy="15.2" rx="1.9" ry="2.6"/>
 </g>
 <rect x="2.2" y="5.2" width="12.6" height="10.6" fill="#666666" stroke="#2fd10b" stroke-width="1.6" stroke-dasharray="3.2 2"/>
</svg>"""

_SVG_CROP = """<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
 <path d="M6 1v15a2 2 0 0 0 2 2h15" stroke="#000" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
 <path d="M18 23V8a2 2 0 0 0-2-2H1" stroke="#000" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

# SVG source for the "Empty Canvas" option in the Image Editor's
# "Select Source" dialog — a blank white painter's canvas on an easel,
# replacing the generic "⬜" white-square emoji.
_SVG_EMPTY_CANVAS = """<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
 <path d="M7 21.5 9.6 5.5M17 21.5 14.4 5.5M3.5 21.5h17" stroke="#8a8a8a" stroke-width="1.3" stroke-linecap="round"/>
 <rect x="4.3" y="2.5" width="15.4" height="12" rx="0.6" fill="#ffffff" stroke="#5b5b5b" stroke-width="1.4"/>
</svg>"""

# SVG source for the Eraser tool — a classic angled eraser block split
# evenly in half (pink top, white bottom) with a short white smudge/stroke
# mark trailing below it, replacing the "🧹" broom emoji which read as a
# cleaning/sweep icon rather than an eraser.
_SVG_ERASER = """<svg width="32" height="32" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
 <defs
     id="defs1">
    <linearGradient
       id="linearGradient4">
      <stop
         style="stop-color:#ffffff;stop-opacity:1;"
         offset="0.5"
         id="stop4" />
      <stop
         style="stop-color:#ef5da8;stop-opacity:1;"
         offset="0.5"
         id="stop5" />
    </linearGradient>
    <clipPath
       id="eraserBodyClip">
      <rect
         x="4"
         y="6"
         width="16"
         height="11"
         rx="2.4000001"
         id="rect1" />
    </clipPath>
    <linearGradient
       xlink:href="#linearGradient4"
       id="linearGradient5"
       x1="3.3000021"
       y1="11.499998"
       x2="20.700001"
       y2="11.499998"
       gradientUnits="userSpaceOnUse" />
  </defs>
  <path
     d="M 3,21.5 H 9"
     stroke="#ffffff"
     stroke-width="1.8"
     stroke-linecap="round"
     id="path1" />
  <g
     transform="rotate(-35,12,12)"
     id="g4">
    <rect
       x="4"
       y="6"
       width="16"
       height="11"
       rx="2.4000001"
       fill="#ffffff"
       id="rect2"
       style="display:none" />
    <rect
       x="4"
       y="6"
       width="16"
       height="5.5"
       fill="#ef5da8"
       clip-path="url(#eraserBodyClip)"
       id="rect3"
       style="display:none" />
    <path
       id="rect4"
       style="display:inline;fill:url(#linearGradient5);stroke:#1a1a1a;stroke-width:1.4"
       d="M 6.4000001,6 H 17.6 C 18.9296,6 20,7.0704 20,8.4000001 V 14.6 C 20,15.9296 18.9296,17 17.6,17 H 6.4000001 C 5.0704,17 4,15.9296 4,14.6 V 8.4000001 C 4,7.0704 5.0704,6 6.4000001,6 Z" />
  </g>
</svg>"""


# SVG source for the "Screen clip" tool — a monitor with a dashed selection
# rectangle on its screen (cut a piece of the screen -> image on the canvas).
_SVG_SCREEN_CLIP = """<svg width="32" height="32" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
 <rect x="1.8" y="3" width="20.4" height="14" rx="1.8" fill="#1e1e2e" stroke="#cdd6f4" stroke-width="1.7"/>
 <path d="M12 17v3.6M7.6 21h8.8" stroke="#cdd6f4" stroke-width="1.7" stroke-linecap="round"/>
 <rect x="5.6" y="6.2" width="12.8" height="7.6" stroke="#00c8ff" stroke-width="1.5" stroke-dasharray="2.4 1.8" fill="none"/>
</svg>"""

# SVG source for the "Change annotation color / width" tool — colour palette,
# a slash, and a wrench ("Color / Settings"). Wide (52x24) icon; every shape has
# a black outline so it reads on both the dark Capture Region toolbar and the
# light Image Editor toolbar. Render with _svg_icon(_SVG_COLOR_TOOLS, 104, 48).
_SVG_COLOR_TOOLS = """<svg width="52" height="24" viewBox="0 0 52 24" xmlns="http://www.w3.org/2000/svg">
 <path d="M12 2.2C6.2 2.2 2 6.4 2 11.6c0 5.4 4.4 9.6 9.6 9.6 1.5 0 2.3-.9 2.3-2 0-.6-.3-1.1-.6-1.5-.4-.5-.6-.9-.6-1.5 0-1.1.9-2 2-2h2.6c3 0 5.2-2.2 5.2-5.1C22.5 5.5 18 2.2 12 2.2z" fill="#ffffff" stroke="#000" stroke-width="1.3" stroke-linejoin="round"/>
 <circle cx="6.6" cy="11" r="1.9" fill="#ea3323" stroke="#000" stroke-width="0.5"/>
 <circle cx="9.4" cy="6.6" r="1.9" fill="#f5c518" stroke="#000" stroke-width="0.5"/>
 <circle cx="14.6" cy="5.8" r="1.9" fill="#2ecc40" stroke="#000" stroke-width="0.5"/>
 <circle cx="18.4" cy="9.6" r="1.9" fill="#3b82f6" stroke="#000" stroke-width="0.5"/>
 <line x1="23.4" y1="22" x2="29" y2="2" stroke="#000" stroke-width="3.6" stroke-linecap="round"/>
 <line x1="23.4" y1="22" x2="29" y2="2" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round"/>
 <g transform="translate(29.5 0)">
  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" fill="#cfd4dc" stroke="#000" stroke-width="1.3" stroke-linejoin="round"/>
 </g>
</svg>"""


def load_app_icon() -> QIcon:
    """Search for PyShareX.ico in ./icons/ subfolder, then script dir, then fallback.
    Tries all known capitalisation variants so it works on case-sensitive filesystems."""
    base = _script_dir()
    for candidate in [
        base / "icons" / "PyShareX.ico",
        base / "icons" / "PyshareX.ico",
        base / "icons" / "pysharex.ico",
        base / "icons" / "PySharex.ico",
        base / "PyShareX.ico",
        base / "PyshareX.ico",
        base / "pysharex.ico",
    ]:
        if candidate.exists():
            return QIcon(str(candidate))
    return _tray_icon()


def _set_windows_dpi_awareness():
    """Deklaruje proces jako Per-Monitor-V2 DPI aware, zanim cokolwiek (Qt,
    win32gui, mss) zdąży odpytać Windows o rozmiary/pozycje okien.

    Bez tego GetWindowRect/DwmGetWindowAttribute (fizyczne piksele) i
    zrzuty ekranu robione przez mss (też fizyczne piksele, ale liczone
    względem "zwirtualizowanego" widoku procesu, jeśli ten nie jest DPI
    aware) rozjeżdżają się przy skalowaniu >100% - stąd czarny margines
    na "Capture active window" (wyliczony region jest nieco za duży albo
    przesunięty względem realnej siatki pikseli).

    Musi być wywołane jak najwcześniej w main(), przed QApplication().
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        # PROCESS_PER_MONITOR_DPI_AWARE = 2 (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes
            # Fallback dla starszych Windows (Vista/7): system-wide DPI aware
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _dump_screen_layout():
    """Diagnostyka: wypisuje układ monitorów widziany przez Qt i przez mss.
    Jeśli wykrywanie okien znów się rozjedzie, wklej ten wydruk z konsoli."""
    try:
        print("[screens] Qt:")
        for s in QApplication.screens():
            g = s.geometry()
            print(f"   {s.name():<14} geo=({g.x()},{g.y()} {g.width()}x{g.height()})"
                  f" dpr={s.devicePixelRatio():.3f}"
                  f"{'  <primary>' if s is QApplication.primaryScreen() else ''}")
        with mss.MSS() as sct:
            print("[screens] mss (fizyczne px):")
            for m in sct.monitors[1:]:
                print(f"   ({m['left']},{m['top']} {m['width']}x{m['height']})")
    except Exception as e:
        print(f"[screens] dump failed: {e}")


_IPC_NAME = "pysharex-" + (os.environ.get("USER") or os.environ.get("USERNAME") or "user")


def _ipc_send(action, probe_only=False, timeout_ms=800):
    """Wysyła nazwę akcji do działającej instancji. True = dostarczono."""
    try:
        from PySide6.QtNetwork import QLocalSocket
    except Exception:
        return False
    s = QLocalSocket()
    s.connectToServer(_IPC_NAME)
    if not s.waitForConnected(timeout_ms):
        return False
    if not probe_only and action:
        s.write((action + "\n").encode("utf-8"))
        s.flush()
        s.waitForBytesWritten(timeout_ms)
    s.disconnectFromServer()
    return True


def _cli_action():
    """Wartość --action <nazwa> z linii poleceń (albo None)."""
    for i, a in enumerate(sys.argv):
        if a == "--action" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--action="):
            return a.split("=", 1)[1]
    return None


def _install_hang_diagnostics():
    """Linux: `kill -USR1 <pid>` wypisuje w terminalu, gdzie dokładnie stoi
    program (stos każdego wątku) - do diagnozy zawieszeń."""
    if not IS_LINUX:
        return
    try:
        import faulthandler, signal
        faulthandler.register(signal.SIGUSR1, all_threads=True)
        print(f"[PyshareX] diagnostyka zawieszeń: w razie zacięcia uruchom  kill -USR1 {os.getpid()}", flush=True)
    except Exception:
        pass


def main():
    _install_hang_diagnostics()
    if "--list-actions" in sys.argv:
        print("\n".join(MainWindow._ACTION_MAP))
        return
    _set_windows_dpi_awareness()

    if IS_WINDOWS and os.environ.get("PYSHAREX_HIGHDPI") != "1":
        # Mieszane DPI + jedno okno-overlay rozpięte na kilku monitorach:
        # Qt liczy pozycje dzieci (pasek narzędzi, ramki okien) w logicznych
        # pikselach, a Windows nakłada je na okno skalą JEDNEGO monitoru.
        # Błąd rośnie z odległością od (0,0) - stąd "dobrze tylko na
        # środkowym". Wyłączamy skalowanie Qt: współrzędne Qt == fizyczne px
        # == współrzędne Win32/mss, więc cała konwersja staje się tożsamością.
        # (PYSHAREX_HIGHDPI=1 przywraca stare zachowanie.)
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ.pop("QT_SCALE_FACTOR", None)

    if IS_LINUX:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    app = QApplication(sys.argv)
    cli_action = _cli_action()
    if cli_action:
        if cli_action not in MainWindow._ACTION_MAP:
            print(f"Nieznana akcja {cli_action!r}. Dostępne:\n  " + "\n  ".join(MainWindow._ACTION_MAP))
            sys.exit(2)
        if _ipc_send(cli_action):
            sys.exit(0)          # przekazano do działającej instancji
        print("[IPC] brak działającej instancji - uruchamiam program i wykonam akcję")
    _dump_screen_layout()
    app.setApplicationName("PyshareX")
    app.setQuitOnLastWindowClosed(False)
    # Set taskbar icon as early as possible
    app.setWindowIcon(load_app_icon())

    config = Config()
    win = MainWindow(config)
    win.show()
    if cli_action:
        QTimer.singleShot(1500, lambda: win._ipc_handle(cli_action))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
