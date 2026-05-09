#!/usr/bin/env python3
"""
PyshareX - Cross-platform screen capture and recording tool
Inspired by ShareX, built with Python and PyQt6
"""

import sys
import os
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

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QSystemTrayIcon, QMenu, QFileDialog, QDialog, QLineEdit,
    QComboBox, QCheckBox, QGroupBox, QScrollArea, QFrame,
    QMessageBox, QListWidget, QListWidgetItem,
    QKeySequenceEdit, QDialogButtonBox, QSpinBox, QTabWidget,
    QTextEdit, QSizePolicy, QStackedWidget
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QSize, QRect, QPoint,
    QStandardPaths, QElapsedTimer
)
from PyQt6.QtGui import (
    QIcon, QKeySequence, QAction, QPixmap, QPainter, QColor,
    QFont, QPen, QBrush, QCursor, QPainterPath, QImage
)

try:
    from PIL import Image
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

_easyocr_reader = None   # lazy singleton — first use initialises it

def _get_easyocr_reader():
    global _easyocr_reader
    if _easyocr_reader is None and EASYOCR_AVAILABLE:
        try:
            _easyocr_reader = _easyocr.Reader(["en", "pl"], gpu=False, verbose=False)
        except Exception as e:
            print(f"EasyOCR init error: {e}")
    return _easyocr_reader

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"

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
        {"name": "OCR – Recognize text",     "action": "ocr_text",                "shortcut": "Ctrl+Alt+O",             "enabled": True},
        {"name": "OCR – Recognize code",     "action": "ocr_code",                "shortcut": "Ctrl+Alt+K",             "enabled": True},
    ]
    DEFAULT_AFTER = {"copy_to_clipboard": True,  "save_to_file": True,
                     "show_in_explorer": False, "scan_qr": False, "ocr_recognize": False}
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
            "ocr_engine": "easyocr",
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
                    # Persist the fix immediately so next launch is clean too
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
        self._on_click_open = on_click_open

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
        if self._filepath and Path(self._filepath).exists():
            if platform.system() == "Windows": os.startfile(self._filepath)
            self.close()

def notify(config: Config, filepath: str, pixmap=None):
    notif = config.get("notifications", {})
    if not notif.get("enabled", True): return
    
    if notif.get("sound", True):
        threading.Thread(target=_play_beep, daemon=True).start()

    p = Path(filepath)
    title = "Zadanie ukończone"
    msg = f"Obraz zapisany w:\n{p.name}" # Formatowanie jak w ShareX
    
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
            qi = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
            pixmap = QPixmap.fromImage(qi)
        except: pass

    QTimer.singleShot(0, lambda: _show_toast(title, msg, pixmap, filepath))

_toasts = []
def _show_toast(title, msg, pixmap, filepath):
    global _toasts
    # Czyścimy stare, niewidoczne powiadomienia
    _toasts = [t for t in _toasts if t.isVisible()]
    t = NotificationToast(title, msg, pixmap, filepath)
    _toasts.append(t)


# ─────────────────────────────────────────────
#  RECORDING BORDER OVERLAY
# ─────────────────────────────────────────────

def physical_to_logical_rect(phys_x, phys_y, phys_w, phys_h) -> QRect:
    import mss
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QRect
    
    # Sortujemy ekrany tak samo jak w RegionSelector, by indeksy się zgadzały
    qt_screens = sorted(QApplication.screens(), key=lambda s: (s.geometry().x(), s.geometry().y()))
    
    with mss.mss() as sct:
        # Sortujemy monitory fizyczne
        mss_mons = sorted(sct.monitors[1:], key=lambda m: (m["left"], m["top"]))
        
        target_screen = None
        target_mon = None
        
        # Znajdujemy, na którym monitorze fizycznym znajduje się punkt startowy
        for q_scr, m_mon in zip(qt_screens, mss_mons):
            if (m_mon["left"] <= phys_x < m_mon["left"] + m_mon["width"] and
                m_mon["top"] <= phys_y < m_mon["top"] + m_mon["height"]):
                target_screen = q_scr
                target_mon = m_mon
                break
        
        if not target_screen:
            target_screen = QApplication.primaryScreen()
            target_mon = mss_mons[0] if mss_mons else {"left": 0, "top": 0}

        ratio = target_screen.devicePixelRatio()
        logical_geom = target_screen.geometry()
        
        # Obliczamy pozycję wewnątrz monitora (fizyczną) i zamieniamy na logiczną
        local_phys_x = phys_x - target_mon["left"]
        local_phys_y = phys_y - target_mon["top"]
        
        local_log_x = local_phys_x / ratio
        local_log_y = local_phys_y / ratio
        log_w = phys_w / ratio
        log_h = phys_h / ratio
        
        # Dodajemy offset monitora w układzie Qt
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
        
        # PRZELICZENIE TUTAJ:
        rect = physical_to_logical_rect(phys_x, phys_y, phys_w, phys_h)
        
        B = 5 # Zwiększamy margines bezpieczeństwa
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # ... reszta atrybutów ...
        
        # Przesuwamy ramkę o 2-3 piksele na zewnątrz (odejmując od X/Y i dodając do W/H)
        self.setGeometry(rect.x() - B, rect.y() - B, rect.width() + B * 2, rect.height() + B * 2)
        
        self._dash = 0
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(80)
        self._t = t
        self.show()

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
    stop_clicked  = pyqtSignal()
    abort_clicked = pyqtSignal()

    def __init__(self):
        super().__init__(None,
                         Qt.WindowType.Tool |
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.BypassWindowManagerHint)
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
            rx, ry, rw, rh = region
            # Position bar at bottom-center of the recorded region
            bx = rx + rw // 2 - self.width() // 2
            by = ry + rh - self.height() - 8
            # Clamp to visible area
            sg = QApplication.primaryScreen().availableVirtualGeometry()
            bx = max(sg.left(), min(bx, sg.right() - self.width()))
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
    region_selected = pyqtSignal(int, int, int, int)
    cancelled       = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.start_pos = self.end_pos = None
        self.drawing = False

        # Cover EVERY screen — compute the bounding rect of all screens
        # in LOGICAL coordinates (Qt). Do NOT use showFullScreen() as that
        # snaps to the primary screen only on Windows.
        self._geo = QRect()
        for s in QApplication.screens():
            self._geo = self._geo.united(s.geometry())
        self.setGeometry(self._geo)
        self.show()
        self.raise_()
        self.activateWindow()

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
        """Convert widget-local point to absolute screen coordinates."""
        # Use mapToGlobal which correctly handles multi-monitor + DPI
        return self.mapToGlobal(local_pt)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.start_pos = self.end_pos = e.pos()
            # Pobieramy absolutną pozycję globalną prosto ze zdarzenia (odporną na błędy DWM/DPI w wielkich oknach)
            self.global_start = self.global_end = e.globalPosition().toPoint()
            self.drawing = True

    def mouseMoveEvent(self, e):
        if self.drawing:
            self.end_pos = e.pos()
            self.global_end = e.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.drawing:
            self.end_pos = e.pos()
            self.global_end = e.globalPosition().toPoint()
            self.drawing = False
            self.close()

            # 1. Tworzymy zaznaczenie z nieprzekłamanych, globalnych koordynatów pulpitu wirtualnego
            r = QRect(self.global_start, self.global_end).normalized()

            if r.width() > 0 and r.height() > 0:
                # 2. Wykrywamy ekran na podstawie ŚRODKA zaznaczenia (znacznie bezpieczniejsze niż topLeft)
                screen = QApplication.screenAt(r.center())
                if not screen:
                    screen = QApplication.primaryScreen()
                
                ratio = screen.devicePixelRatio()
                logical_geom = screen.geometry()
                
                # Zaznaczenie RELATYWNIE do lewego górnego rogu TEGO konkretnego ekranu
                local_x = r.x() - logical_geom.x()
                local_y = r.y() - logical_geom.y()
                
                # Zaznaczenie w pikselach FIZYCZNYCH (skalowane przez DPI)
                phys_w = int(r.width() * ratio)
                phys_h = int(r.height() * ratio)
                phys_local_x = int(local_x * ratio)
                phys_local_y = int(local_y * ratio)
                
                # 3. Zastosowanie poprawnego offsetu dla wielu monitorów (fizyczny punkt X/Y)
                final_x = int(r.x() * ratio) 
                final_y = int(r.y() * ratio)
                
                try:
                    import mss
                    with mss.mss() as sct:
                        # KLUCZOWA ZMIANA: Sortujemy monitory po osi X ORAZ Y. 
                        # Gwarantuje to identyczne sparowanie ekranów Qt i mss niezależnie od ułożenia.
                        qt_screens = sorted(QApplication.screens(), key=lambda s: (s.geometry().x(), s.geometry().y()))
                        mss_mons = sorted(sct.monitors[1:], key=lambda m: (m["left"], m["top"]))
                        
                        if screen in qt_screens:
                            screen_idx = qt_screens.index(screen)
                            if screen_idx < len(mss_mons):
                                target_mon = mss_mons[screen_idx]
                                # Dodajemy przeskalowany offset do twardego, fizycznego narożnika monitora
                                final_x = target_mon["left"] + phys_local_x
                                final_y = target_mon["top"]  + phys_local_y
                except Exception as ex:
                    print(f"[PyshareX] Błąd przy parowaniu monitorów: {ex}")

                # Emitujemy twarde, fizyczne koordynaty dla mss oraz ffmpeg
                self.region_selected.emit(final_x, final_y, phys_w, phys_h)


# ─────────────────────────────────────────────
#  CAPTURE ENGINE
# ─────────────────────────────────────────────

class CaptureEngine:
    def __init__(self, config: Config):
        self.config = config

    def _folder(self):
        f = Path(self.config.get("save_folder")); f.mkdir(parents=True, exist_ok=True); return f

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
            for cmd in [["xclip", "-selection", "clipboard", "-t", "image/png", "-i", tmp.name],
                        ["wl-copy", "--type", "image/png"]]:
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

    def capture_active_window(self) -> str:
        if IS_WINDOWS:
            try:
                import win32gui
                hwnd = win32gui.GetForegroundWindow()
                x, y, x2, y2 = win32gui.GetWindowRect(hwnd)
                return self.capture_region(x, y, x2 - x, y2 - y)
            except Exception: pass
        else:
            try:
                r = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
                    capture_output=True, text=True, timeout=3)
                if r.returncode == 0:
                    info = dict(l.split("=") for l in r.stdout.strip().split("\n") if "=" in l)
                    return self.capture_region(int(info.get("X", 0)), int(info.get("Y", 0)),
                                               int(info.get("WIDTH", 800)), int(info.get("HEIGHT", 600)))
            except Exception: pass
        return self.capture_active_monitor()

    def capture_fullscreen(self) -> str:
        if not MSS_AVAILABLE: return None
        with mss.MSS() as sct:
            shot = sct.grab(sct.monitors[0])
            return self._save(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), "fullscreen")

    def capture_scrolling(self) -> str:
        """
        Scrolling capture: takes multiple screenshots while auto-scrolling,
        then detects the scroll offset between frames using pixel matching
        and stitches only the NEW content each time.
        """
        if not (MSS_AVAILABLE and PIL_AVAILABLE): return None
        try:
            import pyautogui
            import numpy as _np
        except ImportError:
            return self.capture_active_monitor()

        FRAMES    = 10
        SCROLL_PX = 500      # pixels to scroll per step
        DELAY     = 0.4      # seconds between frames

        # Use monitor under cursor
        cx, cy = QCursor.pos().x(), QCursor.pos().y()
        with mss.MSS() as sct:
            mon = sct.monitors[1]
            for m in sct.monitors[1:]:
                if (m["left"] <= cx < m["left"] + m["width"] and
                        m["top"] <= cy < m["top"] + m["height"]):
                    mon = m; break

        frames = []
        with mss.MSS() as sct:
            for i in range(FRAMES):
                shot = sct.grab(mon)
                frames.append(Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"))
                if i < FRAMES - 1:
                    pyautogui.scroll(-SCROLL_PX)
                    time.sleep(DELAY)
                    # Check if page moved at all (reached bottom)
                    shot2 = sct.grab(mon)
                    f2 = Image.frombytes("RGB", shot2.size, shot2.bgra, "raw", "BGRX")
                    a1 = _np.array(frames[-1])
                    a2 = _np.array(f2)
                    if _np.array_equal(a1, a2):
                        break   # reached end of page

        if not frames: return None
        if len(frames) == 1:
            return self._save(frames[0], "scroll")

        def detect_scroll_offset(img_a, img_b, max_search=None):
            """Find vertical offset between img_b relative to img_a using strip matching."""
            arr_a = _np.array(img_a.convert("L"))
            arr_b = _np.array(img_b.convert("L"))
            h = arr_a.shape[0]
            if max_search is None:
                max_search = h
            strip_h = min(60, h // 8)
            # Use a strip from the BOTTOM of img_a to search in img_b
            ref = arr_a[h - strip_h: h, :]
            best_score = float("inf")
            best_off = h // 2
            for off in range(strip_h, min(max_search, h - strip_h)):
                candidate = arr_b[off - strip_h: off, :]
                if candidate.shape != ref.shape:
                    continue
                score = float(_np.mean(_np.abs(ref.astype(int) - candidate.astype(int))))
                if score < best_score:
                    best_score = score
                    best_off = off
            # best_off = where the bottom strip of img_a appears in img_b
            # → new content starts at (h - best_off) from bottom of img_a
            scroll_amount = h - best_off
            return max(1, scroll_amount)

        # Stitch: first frame in full, then only the NEW rows from each subsequent frame
        strips = [frames[0]]
        offsets = []
        for i in range(1, len(frames)):
            off = detect_scroll_offset(frames[i-1], frames[i])
            offsets.append(off)
            # New content = bottom `off` rows of frames[i]
            new_h = frames[i].height - off
            if new_h < 2:
                new_h = off
            strips.append(frames[i].crop((0, frames[i].height - new_h, frames[i].width, frames[i].height)))

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
        engine = self.config.get("ocr_engine", "easyocr")
        if engine == "easyocr":
            return self._ocr_easyocr(path)
        else:
            return self._ocr_tesseract(path)

    def _ocr_easyocr(self, image_path: str) -> str:
        if not EASYOCR_AVAILABLE:
            return ("EasyOCR is not installed.\n"
                    "Install with:  pip install easyocr\n"
                    "Falling back — try Tesseract engine in Settings.")
        reader = _get_easyocr_reader()
        if reader is None:
            return "EasyOCR failed to initialize."
        try:
            results = reader.readtext(image_path, detail=0, paragraph=True)
            return "\n".join(results)
        except Exception as e:
            return f"EasyOCR error: {e}"

    def _ocr_tesseract(self, image_path: str) -> str:
        try:
            r = subprocess.run(["tesseract", image_path, "stdout",
                                 "-l", "pol+eng", "--psm", "3"],
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
            return "Brak biblioteki OpenCV. Zainstaluj ją poleceniem:\npip install opencv-python"
            
        try:
            import cv2
            img = cv2.imread(path)
            detector = cv2.QRCodeDetector()
            data, bbox, _ = detector.detectAndDecode(img)
            
            if data:
                return data
            return "Nie wykryto kodu QR w zaznaczonym obszarze."
        except Exception as e:
            return f"Wystąpił błąd podczas dekodowania: {e}"

# ─────────────────────────────────────────────
#  RECORDING THREAD
# ─────────────────────────────────────────────

class RecordingThread(QThread):
    finished     = pyqtSignal(str)
    error        = pyqtSignal(str)
    region_ready = pyqtSignal(int, int, int, int)

    def __init__(self, region, output_path, fps=30, audio=False,
                 gif_mode=False, gif_fps=10, gif_duration=5):
        super().__init__()
        self.region      = region
        self.output_path = output_path
        self.fps         = fps
        self.audio       = audio
        self.gif_mode    = gif_mode
        self.gif_fps     = gif_fps
        self.gif_duration= gif_duration
        self._stop       = threading.Event()
        self._proc       = None

    def stop(self):
        self._stop.set()
        self._kill_ffmpeg()

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
            if self.gif_mode: self._gif()
            else:             self._video()
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

    def _video(self):
        try:
            x, y, w, h = self._resolve_region()
        except Exception as e:
            self.error.emit(f"Screen error: {e}"); return

        self.region_ready.emit(x, y, w, h)

        if IS_WINDOWS:
            # gdigrab on a DPI-aware app uses LOGICAL pixel coordinates —
            # same space as Qt / mss. No scaling needed.
            gw = w if w % 2 == 0 else w - 1
            gh = h if h % 2 == 0 else h - 1
            vin = ["-f", "gdigrab",
                   "-framerate", str(self.fps),
                   "-rtbufsize", "256M",
                   "-draw_mouse", "1",
                   "-offset_x", str(x), "-offset_y", str(y),
                   "-video_size", f"{gw}x{gh}",
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

        oops = [
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

        # Shortcut
        lay.addWidget(QLabel("Keyboard shortcut:"))
        self.ke = QKeySequenceEdit(QKeySequence(data.get("shortcut", "")))
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
        self.data["shortcut"] = self.ke.keySequence().toString()
        self.data["enabled"]  = self.en.isChecked()
        self.accept()

    def get_data(self): return self.data


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

CSS = """
QWidget { background:#1e1e2e; color:#cdd6f4;
    font-family:'Segoe UI','Ubuntu',sans-serif; font-size:13px; }
QMainWindow { background:#1e1e2e; }
QMenuBar { background:#181825; color:#cdd6f4; border-bottom:1px solid #313244; padding:2px; }
QMenuBar::item:selected { background:#313244; border-radius:4px; }
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
QSpinBox { background:#181825; border:1px solid #45475a; border-radius:6px;
    padding:5px 8px; color:#cdd6f4; }
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


# ─────────────────────────────────────────────
#  MAIN WINDOW  (settings embedded)
# ─────────────────────────────────────────────

class MainWindow(QMainWindow):
    status_sig  = pyqtSignal(str)
    _notify_sig = pyqtSignal(str, object)   # filepath — must run on main thread
    _ocr_done_sig = pyqtSignal(str, str) # <--- DODANA LINIA (tekst, tytul)

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
        self.status_sig.connect(self._status)
        self._notify_sig.connect(self._on_notify)
        self._ocr_done_sig.connect(self._show_ocr) # <--- DODANA LINIA
        self.last_rec_pixmap = None  # Tu będziemy trzymać miniaturkę
        self.setWindowTitle("PyshareX")
        self.setMinimumSize(780, 540)
        self.setStyleSheet(CSS)
        self._app_icon = load_app_icon()
        self.setWindowIcon(self._app_icon)
        self._build()
        self._build_tray()
        self._hotkeys_start()
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
        self._cap_btn = QPushButton("Capture Region")
        self._cap_btn.setObjectName("cap_btn"); self._cap_btn.clicked.connect(self.act_region)
        self._rec_btn = QPushButton("Record")
        self._rec_btn.setObjectName("rec_btn"); self._rec_btn.clicked.connect(self.act_toggle_rec)
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
        hl.addWidget(self._cap_btn); hl.addWidget(self._rec_btn); hl.addWidget(quit_btn)
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
        for lbl, fn in [("🖼  Capture",  self._show_capture),
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
        items = [("🔤 OCR – text",       self.act_ocr_text),
                 ("🔳 OCR – code",       self.act_ocr_code),
                 ("🖥  Full screen",     self.act_fullscreen),
                 ("🪟  Active window",   self.act_window),
                 ("🖥  Active monitor",  self.act_monitor),
                 ("🎞  Record GIF",      self.act_gif)]
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
        fg = QGroupBox("Screenshots folder"); fl = QHBoxLayout(fg)
        self._fld = QLineEdit(self.config.get("save_folder", ""))
        br = QPushButton("Browse…"); br.clicked.connect(self._browse)
        fl.addWidget(self._fld); fl.addWidget(br); lay.addWidget(fg)

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

        # Selected monitor
        mg = QGroupBox("Selected monitor  (for 'Capture selected monitor')")
        ml = QHBoxLayout(mg)
        self._mcb = QComboBox(); self._fill_monitors()
        rfb = QPushButton("🔄"); rfb.setFixedWidth(34); rfb.clicked.connect(self._fill_monitors)
        ml.addWidget(QLabel("Monitor:")); ml.addWidget(self._mcb, 1); ml.addWidget(rfb)
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
        from PyQt6.QtWidgets import QRadioButton, QButtonGroup
        self._ocr_grp = QButtonGroup(ocr_g)
        self._ocr_easyocr_rb = QRadioButton(
            "EasyOCR  (pip install easyocr)  — no external dependencies")
        self._ocr_tesseract_rb = QRadioButton(
            "Tesseract  (sudo apt install tesseract-ocr / Windows installer)")
        engine = self.config.get("ocr_engine", "easyocr")
        self._ocr_easyocr_rb.setChecked(engine == "easyocr")
        self._ocr_tesseract_rb.setChecked(engine == "tesseract")
        self._ocr_grp.addButton(self._ocr_easyocr_rb, 0)
        self._ocr_grp.addButton(self._ocr_tesseract_rb, 1)
        ocr_l.addWidget(self._ocr_easyocr_rb)
        ocr_l.addWidget(self._ocr_tesseract_rb)
        # Status indicator
        easyocr_status = "✅ EasyOCR installed" if EASYOCR_AVAILABLE else "⚠️  EasyOCR not installed — run: pip install easyocr"
        ocr_status_lbl = QLabel(easyocr_status)
        ocr_status_lbl.setStyleSheet("font-size:11px; color:#a6adc8; padding-left:4px;")
        ocr_l.addWidget(ocr_status_lbl)
        lay.addWidget(ocr_g)

        # After capture
        acg = QGroupBox("After capture tasks"); acl = QVBoxLayout(acg)
        ac = self.config.get("after_capture", {})
        self._ac = {}
        for k, lbl2 in [("copy_to_clipboard","📋 Copy to clipboard"),
                         ("save_to_file",     "💾 Save to file"),
                         ("show_in_explorer", "📁 Show in explorer"),
                         ("scan_qr",          "🔳 Scan QR code"),
                         ("ocr_recognize",    "🔤 Recognize text (OCR)")]:
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

        sv = QPushButton("💾 Save Settings"); sv.setObjectName("cap_btn")
        sv.setFixedHeight(36); sv.clicked.connect(self._save_settings)
        lay.addWidget(sv); lay.addStretch()
        return sa

    def _mk_history(self):
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(8)
        top = QHBoxLayout()
        lbl = QLabel("📋 Screenshot History")
        lbl.setStyleSheet("color:#89b4fa;font-size:15px;font-weight:bold;")
        top.addWidget(lbl); top.addStretch()
        ref = QPushButton("🔄"); ref.setFixedWidth(34); ref.setToolTip("Refresh")
        ref.clicked.connect(self._refresh_hist); top.addWidget(ref)
        del_btn = QPushButton("🗑 Delete selected"); del_btn.setToolTip("Delete selected file from disk")
        del_btn.clicked.connect(self._hist_delete_selected); top.addWidget(del_btn)
        clr_btn = QPushButton("✕ Clear all"); clr_btn.setToolTip("Delete ALL files in screenshots folder")
        clr_btn.clicked.connect(self._hist_clear_all); top.addWidget(clr_btn)
        lay.addLayout(top)
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
        files = list(folder.glob("*.*"))
        if not files: return
        reply = QMessageBox.question(self, "Clear history",
            f"Delete ALL {len(files)} files in {folder}?",
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

        cm = mb.addMenu("Capture")
        for name, sc, fn in [
            ("Capture region",         "Ctrl+Alt+Print Screen", self.act_region),
            ("Capture active monitor", "Alt+Print Screen",      self.act_monitor),
            ("Capture active window",  "Ctrl+Print Screen",     self.act_window),
            ("Capture full screen",    "",                       self.act_fullscreen),
            ("Scrolling capture",      "Shift+Print Screen",    self.act_scrolling),
        ]:
            a = QAction(name, self)
            if sc: a.setShortcut(QKeySequence(sc))
            a.triggered.connect(fn); cm.addAction(a)
        cm.addSeparator()
        for m in get_monitors():
            i = m["index"]
            cm.addAction(f"Monitor {i+1} – {m['name']}").triggered.connect(
                lambda _, x=i: self._cap_mon(x))

        rm = mb.addMenu("Recording")
        rm.addAction("Start/Stop screen recording").triggered.connect(self.act_toggle_rec)
        rm.addAction("Record GIF").triggered.connect(self.act_gif)

        tm = mb.addMenu("Tools")
        tm.addAction("OCR – Recognize text").triggered.connect(self.act_ocr_text)
        tm.addAction("OCR – Recognize code").triggered.connect(self.act_ocr_code)

        am = mb.addMenu("Application")
        am.addAction("Screenshots folder").triggered.connect(self._open_folder)
        am.addSeparator()
        am.addAction("History").triggered.connect(self._show_history)
        am.addSeparator()
        am.addAction("Quit").triggered.connect(self._quit)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self._app_icon, self)
        self.tray.setToolTip("PyshareX")
        menu = QMenu(); menu.setStyleSheet(CSS)

        csub = menu.addMenu("Capture")
        csub.addAction("Region").triggered.connect(self.act_region)
        csub.addAction("Active monitor").triggered.connect(self.act_monitor)
        csub.addAction("Active window").triggered.connect(self.act_window)
        csub.addAction("Full screen").triggered.connect(self.act_fullscreen)
        csub.addAction("Scrolling capture").triggered.connect(self.act_scrolling)
        csub.addSeparator()
        for m in get_monitors():
            i = m["index"]
            csub.addAction(f"Monitor {i+1} – {m['name']}").triggered.connect(
                lambda _, x=i: self._cap_mon(x))

        rsub = menu.addMenu("Recording")
        rsub.addAction("Start/Stop screen recording").triggered.connect(self.act_toggle_rec)
        rsub.addAction("Record GIF").triggered.connect(self.act_gif)

        tsub = menu.addMenu("Tools")
        tsub.addAction("OCR – text").triggered.connect(self.act_ocr_text)
        tsub.addAction("OCR – code").triggered.connect(self.act_ocr_code)

        menu.addSeparator()
        menu.addAction("Screenshots folder").triggered.connect(self._open_folder)
        menu.addSeparator()
        menu.addAction("Configuration").triggered.connect(self.show_win)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self._quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.show_win()
            if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    # ════════════════════════════════════════
    #  SIDEBAR NAV
    # ════════════════════════════════════════

    def _sel_sb(self, idx):
        for i, b in enumerate(self._sb_btns): b.setChecked(i == idx)
        self.stack.setCurrentIndex(idx)

    def _show_capture(self):  self._sel_sb(0)
    def _show_tools(self):    self._sel_sb(1)
    def _show_settings(self): self._sel_sb(2)
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
        self.config.set("save_folder",      self._fld.text())
        self.config.set("image_format",     self._fmt.currentText())
        self.config.set("jpeg_quality",     self._jpg.value())
        self.config.set("delay",            self._dly.value())
        self.config.set("show_cursor",      self._cur.isChecked())
        self.config.set("gif_fps",          self._gfps.value())
        self.config.set("record_audio",     self._aud.isChecked())
        self.config.set("selected_monitor", self._mcb.currentData() or 0)
        self.config.set("ocr_engine",
                        "easyocr" if self._ocr_easyocr_rb.isChecked() else "tesseract")
        self.config.set("after_capture",    {k: cb.isChecked() for k, cb in self._ac.items()})
        self.config.set("notifications",    {k: cb.isChecked() for k, cb in self._nc.items()})
        self._status("✅ Settings saved")

    # ════════════════════════════════════════
    #  HISTORY
    # ════════════════════════════════════════

    def _refresh_hist(self):
        self.hist_list.clear()
        folder = Path(self.config.get("save_folder", ""))
        if not folder.exists(): return
        for f in sorted(folder.glob("*.*"), key=lambda x: x.stat().st_mtime, reverse=True)[:200]:
            sz = f.stat().st_size
            item = QListWidgetItem(f"📄 {f.name}   {sz//1024 if sz>=1024 else sz}{'KB' if sz>=1024 else 'B'}")
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
        sz = p.stat().st_size
        item = QListWidgetItem(f"📄 {p.name}   {sz//1024 if sz>=1024 else sz}{'KB' if sz>=1024 else 'B'}")
        item.setData(Qt.ItemDataRole.UserRole, path)
        self.hist_list.insertItem(0, item)

    # ════════════════════════════════════════
    #  STATUS / WINDOW
    # ════════════════════════════════════════

    def _status(self, msg):
        self.status_lbl.setText(msg)
        QTimer.singleShot(6000, lambda: self.status_lbl.setText("Ready"))

    def show_win(self):
        self.show(); self.raise_(); self.activateWindow()

    def closeEvent(self, e):
        e.ignore(); self.hide()   # no tray notification

    def _open_folder(self):
        f = self.config.get("save_folder", "")
        Path(f).mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS: os.startfile(f)
        else: _popen(["xdg-open", f])

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
        "capture_fullscreen":       "act_fullscreen",
    }

    def _hotkeys_start(self):
        if not PYNPUT_AVAILABLE: return
        combos = {}
        for sc in self.config.get("shortcuts", []):
            if not sc.get("enabled", True): continue
            mn = self._ACTION_MAP.get(sc.get("action", ""))
            pk = self._qt2pk(sc.get("shortcut", ""))
            if mn and pk:
                def make(m):
                    def h():
                        fn = getattr(self, m, None)
                        if fn: QTimer.singleShot(0, fn)
                    return h
                combos[pk] = make(mn)
        if not combos: return
        try:
            from pynput import keyboard as kb
            self._hkl = kb.GlobalHotKeys(combos)
            self._hkl.start()
        except Exception as e:
            print(f"Hotkey error: {e}")

    def _hotkeys_stop(self):
        if self._hkl:
            try: self._hkl.stop()
            except Exception: pass
            self._hkl = None

    def _hotkeys_restart(self):
        self._hotkeys_stop(); self._hotkeys_start()

    def _qt2pk(self, qs: str) -> str:
        M = {"Ctrl":"<ctrl>","Alt":"<alt>","Shift":"<shift>","Meta":"<cmd>",
             "Print Screen":"<print_screen>","PrintScreen":"<print_screen>",
             "Return":"<enter>","Delete":"<delete>","Insert":"<insert>",
             "Home":"<home>","End":"<end>","PgUp":"<page_up>","PgDown":"<page_down>"}
        parts = qs.replace("++", "+Plus").split("+")
        res = []
        for p in parts:
            p = p.strip()
            if p in M: res.append(M[p])
            elif len(p) == 1: res.append(p.lower())
            elif p.startswith("F") and p[1:].isdigit(): res.append(f"<f{p[1:]}>")
            elif p: res.append(p.lower())
        return "+".join(res)

    # ════════════════════════════════════════
    #  CAPTURE ACTIONS
    # ════════════════════════════════════════

    def _on_notify(self, path, pixmap=None):
        """Called on main thread — safe to create QWidgets."""
        self._add_hist(path)
        notify(self.config, path, pixmap=pixmap)

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
        self.hide(); QTimer.singleShot(160, self._do_region)

    def _do_region(self):
        self._sel = RegionSelector()
        self._sel.region_selected.connect(self._on_region)
        self._sel.cancelled.connect(self.show_win)

    def _on_region(self, x, y, w, h):
        if w < 5 or h < 5: self.show_win(); return
        def do():
            time.sleep(0.05)
            p = self.engine.capture_region(x, y, w, h)
            self._done(p, "Region")
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
        self._status("Scrolling capture — scroll now…")
        self.hide(); QTimer.singleShot(500, self._do_scroll)

    def _do_scroll(self):
        def do():
            p = self.engine.capture_scrolling()
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

        folder = Path(self.config.get("save_folder", ".")); folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        fp = str(folder / f"video_{ts}.mp4")

        self._abort = False
        self.rec_th = RecordingThread(region, fp, 30, self.config.get("record_audio", False))
        self.rec_th.region_ready.connect(self._on_rec_region)
        self.rec_th.finished.connect(self._on_rec_done)
        self.rec_th.error.connect(self._on_rec_err)
        self.rec_th.start()

        self.is_rec = True; self.rec_ind.show()
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
            with mss.mss() as sct:
                # x, y, w, h to obszar fizyczny
                mon = {"top": y, "left": x, "width": w, "height": h}
                sct_img = sct.grab(mon)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                
                # Konwersja PIL -> QPixmap
                qim = QImage(img.tobytes(), img.size[0], img.size[1], QImage.Format.Format_RGB888)
                self.last_rec_pixmap = QPixmap.fromImage(qim)
        except:
            self.last_rec_pixmap = None
        self._bar.stop_clicked.connect(lambda: self._stop_rec(False))
        self._bar.abort_clicked.connect(lambda: self._stop_rec(True))
        #self._bar.start_display(region=(x, y, w, h))
        self._bar.start_display() # Wywołaj bez regionu, żeby odpalić tylko timer
        
        logical_rect = physical_to_logical_rect(x, y, w, h)
        self._bar.move(logical_rect.x(), logical_rect.bottom() + 10)
        self._bar.show()

    def _stop_rec(self, abort=False):
        self._abort = abort; self.is_rec = False; self.rec_ind.hide()
        self._rec_btn.setText("Record"); self._rec_btn.setProperty("rec", "0")
        self._rec_btn.setStyle(self._rec_btn.style())
        if self._border: self._border.stop(); self._border = None
        if self._bar:    self._bar.stop_display(); self._bar = None
        self._status("⏹ Stopping…")
        if self.rec_th: self.rec_th.stop()

    def _on_rec_done(self, path):
        self.is_rec = False; self.rec_ind.hide()
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
        folder = Path(self.config.get("save_folder", ".")); folder.mkdir(parents=True, exist_ok=True)
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
            with mss.mss() as sct:
                mon = {"top": y, "left": x, "width": w, "height": h}
                sct_img = sct.grab(mon)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                qim = QImage(img.tobytes(), img.size[0], img.size[1], QImage.Format.Format_RGB888)
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
        # Sprawdzamy, czy obszar nie jest za mały (podobnie jak przy zwykłym screenshot'cie)
        if w < 5 or h < 5: 
            self.show_win()
            return

        self._status("⏳ Running OCR…")

        def go():
            # Używamy dokładnie takiego samego opóźnienia jak w klasycznym zrzucie (0.05s)
            time.sleep(0.05)
            
            # Bezpośrednio wywołujemy sprawdzoną i działającą metodę capture_region!
            path = self.engine.capture_region(x, y, w, h)
            if not path:
                self._ocr_done_sig.emit("Nie udało się przechwycić ekranu.", "OCR Result")
                return

            # Mając już w 100% poprawny plik na dysku, wykonujemy na nim OCR
            engine = self.config.get("ocr_engine", "easyocr")
            if engine == "easyocr":
                txt = self.engine._ocr_easyocr(path)
            else:
                txt = self.engine._ocr_tesseract(path)

            self._ocr_done_sig.emit(txt, "OCR Result")

        threading.Thread(target=go, daemon=True).start()

    def _run_qr(self, x, y, w, h):
        if w < 5 or h < 5: 
            self.show_win()
            return

        self._status("⏳ Scanning QR…")

        def go():
            time.sleep(0.05)
            
            # Ponownie, używamy poprawnej metody capture_region
            path = self.engine.capture_region(x, y, w, h)
            if not path:
                self._ocr_done_sig.emit("Nie udało się przechwycić ekranu.", "QR Code Result")
                return

            if not CV2_AVAILABLE:
                self._ocr_done_sig.emit("Brak biblioteki OpenCV. (pip install opencv-python)", "QR Code Result")
                return
                
            try:
                import cv2
                import numpy as np
                
                # Używamy cv2.imdecode z numpy - w przeciwieństwie do zwykłego cv2.imread, 
                # to podejście bezbłędnie radzi sobie z polskimi znakami w ścieżkach Windowsa!
                img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
                detector = cv2.QRCodeDetector()
                data, bbox, _ = detector.detectAndDecode(img)
                
                if data:
                    result = data
                else:
                    result = "Nie wykryto kodu QR w zaznaczonym obszarze."
                    
                self._ocr_done_sig.emit(result, "QR Code Result")
            except Exception as e:
                self._ocr_done_sig.emit(f"Wystąpił błąd podczas dekodowania: {e}", "QR Code Result")

        threading.Thread(target=go, daemon=True).start()

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


def load_app_icon() -> QIcon:
    """Search for PyshareX.ico in ./icons/ subfolder, then script dir, then fallback."""
    base = _script_dir()
    for candidate in [
        base / "icons" / "PyshareX.ico",
        base / "PyshareX.ico",
        base / "icons" / "pysharex.ico",
        base / "pysharex.ico",
    ]:
        if candidate.exists():
            return QIcon(str(candidate))
    return _tray_icon()


def main():
    if IS_LINUX:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    app = QApplication(sys.argv)
    app.setApplicationName("PyshareX")
    app.setQuitOnLastWindowClosed(False)
    # Set taskbar icon as early as possible
    app.setWindowIcon(load_app_icon())

    config = Config()
    win = MainWindow(config)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
