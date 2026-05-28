#!/usr/bin/env python3
"""
PyshareX - Cross-platform screen capture and recording tool
Inspired by ShareX, built with Python and PySide6
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

import urllib.request # Potrzebne do otwierania z sieci
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QSystemTrayIcon, QMenu, QFileDialog, QDialog, QLineEdit,
    QComboBox, QCheckBox, QGroupBox, QScrollArea, QFrame,
    QMessageBox, QListWidget, QListWidgetItem,
    QDialogButtonBox, QSpinBox, QTabWidget, QRadioButton,
    QTextEdit, QSizePolicy, QStackedWidget, QColorDialog, QInputDialog,
    QGraphicsScene, QGraphicsView, QGraphicsItem, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsTextItem
)
from PySide6.QtCore import (
    QPointF, Qt, QThread, Signal, QTimer, QSize, QRect, QPoint,
    QStandardPaths, QElapsedTimer, QLineF, QRectF, QSizeF
)
from PySide6.QtGui import (
    QIcon, QKeySequence, QAction, QMouseEvent, QPixmap, QPainter, QColor,
    QFont, QPen, QBrush, QCursor, QPainterPath, QImage, QPainterPathStroker,
    QFontMetricsF
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QShortcut

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
_paddleocr_reader = None   # lazy singleton — first use initialises it





def _get_easyocr_reader():
    global _easyocr_reader
    if _easyocr_reader is None and EASYOCR_AVAILABLE:
        try:
            _easyocr_reader = _easyocr.Reader(["en", "pl"], gpu=False, verbose=False)
        except Exception as e:
            print(f"EasyOCR init error: {e}")
    return _easyocr_reader


# Stores both the reader instance and which API generation it uses
_paddleocr_api_version = None   # "v3" | "v2" | None
_paddleocr_init_error  = None   # last init error message, shown to user

def _get_paddleocr_reader():
    global _paddleocr_reader, _paddleocr_api_version, _paddleocr_init_error
    if _paddleocr_reader is None and PADDLEOCR_AVAILABLE:
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

        last_err = None
        for ver, kwargs in [
            ("v3", {"lang": "en", "show_log": False}),
            ("v2", {"use_angle_cls": True, "lang": "en", "show_log": False}),
        ]:
            try:
                inst = _try_init_paddle(kwargs)
                _paddleocr_reader = inst
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

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"

def _set_dialog_on_top(dlg):
    """Set dialog window flags so it appears above fullscreen overlays on all platforms.
    On Linux, X11BypassWindowManagerHint is required to float above fullscreen windows."""
    flags = Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
    if IS_LINUX:
        flags |= Qt.WindowType.X11BypassWindowManagerHint
    dlg.setWindowFlags(flags)
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
                # Auto-migrate: force paddleocr as default if not explicitly set to a known engine
                valid_engines = {"paddleocr", "easyocr", "tesseract"}
                if d.get("ocr_engine") not in valid_engines:
                    d["ocr_engine"] = "paddleocr"
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
    import mss
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
            # Match Qt screen whose physical origin equals target_mon's origin
            for q_scr in QApplication.screens():
                lg  = q_scr.geometry()
                dpr = q_scr.devicePixelRatio()
                qpx = round(lg.x() * dpr)
                qpy = round(lg.y() * dpr)
                if abs(qpx - target_mon["left"]) <= 2 and abs(qpy - target_mon["top"]) <= 2:
                    target_screen = q_scr
                    break

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
    stop_clicked  = Signal()
    abort_clicked = Signal()

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
    region_selected = Signal(int, int, int, int)
    cancelled       = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.BypassWindowManagerHint
        )
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
                    import mss
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
    def add_rect(self, rect: QRectF, color: QColor, width: int):
        item = ResizableRectItem()
        item.setRect(rect)
        self._apply_props(item, color, width)
        item.setAcceptHoverEvents(True)
        self._scene.addItem(item)
        return item

    def add_ellipse(self, rect: QRectF, color: QColor, width: int):
        item = ResizableEllipseItem()
        item.setRect(rect)
        self._apply_props(item, color, width)
        item.setAcceptHoverEvents(True)
        self._scene.addItem(item)
        return item

    def add_line(self, line: QLineF, color: QColor, width: int):
        item = LineItem(line, self)
        self._apply_props(item, color, width)
        self._scene.addItem(item)
        return item

    def add_highlight(self, rect: QRectF):
        item = HighlightRectItem()
        item.setRect(rect)
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self._scene.addItem(item)
        return item

    def add_arrow(self, line: QLineF, color: QColor, width: int):
        item = ArrowItem(line, self)
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

    def add_marker(self, pos: QPointF, color: QColor):
        # Always number after the highest existing marker so re-selecting
        # the tool and adding more markers continues the sequence correctly.
        existing = [it for it in self._scene.items() if isinstance(it, _MarkerItem)]
        self._marker_count = max((it.number for it in existing), default=0) + 1

        # Inherit scale, bg_color and text_color from the most recently placed marker.
        # Falls back to defaults if no markers remain on the canvas.
        last_marker = next(
            (it for it in sorted(existing, key=lambda m: m.number, reverse=True)),
            None)
        last_scale      = last_marker._scale      if last_marker else 1.0
        last_bg_color   = QColor(last_marker._bg_color)   if last_marker else color
        last_text_color = QColor(last_marker._text_color) if last_marker else QColor(Qt.GlobalColor.white)

        item = _MarkerItem(pos, self._marker_count)
        item._scale      = last_scale
        item._bg_color   = last_bg_color
        item._text_color = last_text_color
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

            # Rotation handle — above top edge (all rect/ellipse/pixmap/freehand items)
            rot_pt = QPointF(rect.center().x(), rect.top() - 30)
            if (abs(local_pos.x() - rot_pt.x()) < m * 1.5 and
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

    def handle_resize(self, item, handle, scene_pos: QPointF, proportional=False):
        """Resize/rotate item — full unified logic."""
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
            
            old_scene_fixed = item.mapToScene(fixed_local)

            left, top, right, bottom = old_rect.left(), old_rect.top(), old_rect.right(), old_rect.bottom()
            
            if 'L' in handle: left = local_pos.x()
            if 'R' in handle: right = local_pos.x()
            if 'T' in handle: top = local_pos.y()
            if 'B' in handle: bottom = local_pos.y()
            
            new_rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
            
            if proportional:
                side = max(new_rect.width(), new_rect.height())
                if 'L' in handle: left = right - side
                else: right = left + side
                if 'T' in handle: top = bottom - side
                else: bottom = top + side
                new_rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()

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


# ─────────────────────────────────────────────────────────────────────────────
#  NEW ANNOTATION ITEMS
# ─────────────────────────────────────────────────────────────────────────────

class HighlightEditDialog(QDialog):
    """Dialog for editing Highlight color and opacity."""
    def __init__(self, current_color: QColor, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Edit Highlight")
        self.color = QColor(current_color)
        lay = QVBoxLayout(self)

        # Color picker button
        row = QHBoxLayout()
        row.addWidget(QLabel("Background color:"))
        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(80, 28)
        self.btn_color.clicked.connect(self._pick_color)
        row.addWidget(self.btn_color)
        lay.addLayout(row)

        # Opacity slider
        lay.addWidget(QLabel("Opacity:"))
        slider_row = QHBoxLayout()
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(self.color.alpha())
        self.opacity_label = QLabel(f"{int(self.color.alpha() / 255 * 100)}%")
        self.opacity_label.setFixedWidth(38)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        slider_row.addWidget(self.opacity_slider)
        slider_row.addWidget(self.opacity_label)
        lay.addLayout(slider_row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update_btn()

    def _pick_color(self):
        c = _show_color_dialog(self.color, self, alpha=False)
        if c is not None:
            c.setAlpha(self.color.alpha())
            self.color = c
            self._update_btn()

    def _on_opacity_changed(self, val):
        self.color.setAlpha(val)
        self.opacity_label.setText(f"{int(val / 255 * 100)}%")
        self._update_btn()

    def _update_btn(self):
        c = self.color
        self.btn_color.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"color: {'black' if c.lightness() > 128 else 'white'}; border: 1px solid #888;")

    def result_color(self) -> QColor:
        return self.color


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
        dlg = HighlightEditDialog(self._color)
        _set_dialog_on_top(dlg)
        # Show near the item
        if event:
            dlg.move(event.screenPos())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_color = dlg.result_color()
            self._color = new_color
            self.setPen(Qt.PenStyle.NoPen)
            self.setBrush(QBrush(self._color))
            self.update()


class ArrowItem(QGraphicsLineItem):
    """Line with an arrowhead at p2."""
    ARROW_BASE_SIZE = 14  # base arrow size at pen width=1

    def __init__(self, line, canvas=None):
        super().__init__(line)
        self.canvas = canvas
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.active_handle = None

    def boundingRect(self):
        extra = (self.pen().width() + self._arrow_size() + 30) / (self.canvas.transform().m11() if self.canvas else 1)
        return super().boundingRect().adjusted(-extra, -extra, extra, extra)

    def _arrow_size(self):
        """Arrow head size scales with pen width."""
        w = max(1, self.pen().width())
        return self.ARROW_BASE_SIZE + (w - 1) * 3

    def _arrow_head_points(self):
        line = self.line()
        if line.length() < 1:
            return []
        angle = math.atan2(-line.dy(), line.dx())
        sz = self._arrow_size()
        # Tip is exactly at p2
        tip = line.p2()
        p_left  = QPointF(tip.x() + sz * math.cos(angle + math.pi * 0.75),
                          tip.y() - sz * math.sin(angle + math.pi * 0.75))
        p_right = QPointF(tip.x() + sz * math.cos(angle - math.pi * 0.75),
                          tip.y() - sz * math.sin(angle - math.pi * 0.75))
        # Shorten the line so it ends at the base of the arrow, not the tip
        arrow_len = sz * math.cos(math.pi * 0.25)
        line_end = QPointF(tip.x() + arrow_len * math.cos(angle + math.pi),
                           tip.y() - arrow_len * math.sin(angle + math.pi))
        return [tip, p_left, p_right, line_end]

    def paint(self, painter, option, widget=None):
        pts = self._arrow_head_points()
        # Draw line only up to the arrow base (not overlapping the head)
        if pts:
            shortened = QLineF(self.line().p1(), pts[3])
            painter.setPen(self.pen())
            painter.drawLine(shortened)
        else:
            painter.setPen(self.pen())
            painter.drawLine(self.line())
        pts = self._arrow_head_points()
        if len(pts) >= 3:
            path = QPainterPath()
            path.moveTo(pts[0])
            path.lineTo(pts[1])
            path.lineTo(pts[2])
            path.closeSubpath()
            painter.setBrush(QBrush(self.pen().color()))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)
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
            self.prepareGeometryChange()
            line = self.line()
            new_pos = event.pos()

            # 45-degree angle snapping constraint
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                anchor = line.p2() if self.active_handle == 'p1' else line.p1()
                dx, dy = new_pos.x() - anchor.x(), new_pos.y() - anchor.y()
                snapped_angle = round(math.degrees(math.atan2(dy, dx)) / 45) * 45
                d = math.hypot(dx, dy)
                new_pos = QPointF(anchor.x() + d * math.cos(math.radians(snapped_angle)),
                                  anchor.y() + d * math.sin(math.radians(snapped_angle)))

            if self.active_handle == 'p1':
                line.setP1(new_pos)
            else:
                line.setP2(new_pos)

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

class _BubbleInputDialog(QDialog):
    def __init__(self, fg_color: QColor = None, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Text Bubble")
        self.fg_color = fg_color or QColor(Qt.GlobalColor.black)
        self.bg_color = QColor(230, 230, 230, 240)
        lay = QVBoxLayout(self)
        self.edit = QTextEdit()
        self.edit.setFixedHeight(80)
        lay.addWidget(QLabel("Text:"))
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        self.btn_fg = QPushButton("Text color")
        self.btn_fg.clicked.connect(self._pick_fg)
        self.btn_bg = QPushButton("Background color")
        self.btn_bg.clicked.connect(self._pick_bg)
        row.addWidget(self.btn_fg)
        row.addWidget(self.btn_bg)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update_btn_styles()

    def _pick_fg(self):
        c = _show_color_dialog(self.fg_color, self, force_opaque=True)
        if c is not None:
            self.fg_color = c
            self._update_btn_styles()

    def _pick_bg(self):
        c = _show_color_dialog(self.bg_color, self, force_opaque=True)
        if c is not None:
            self.bg_color = c
            self._update_btn_styles()

    def _update_btn_styles(self):
        c = self.fg_color
        self.btn_fg.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"color: {'black' if c.lightness() > 128 else 'white'};")
        c = self.bg_color
        self.btn_bg.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"color: {'black' if c.lightness() > 128 else 'white'};")

    def result_data(self):
        return self.edit.toPlainText(), self.fg_color, self.bg_color


class _BubbleEditDialog(QDialog):
    """Edit dialog for an existing TextBubbleItem (text + fg/bg colours)."""
    def __init__(self, fg_color: QColor = None, bg_color: QColor = None,
                 text: str = "", parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Edit Text Bubble")
        self.fg_color = QColor(fg_color) if fg_color else QColor(Qt.GlobalColor.black)
        self.bg_color = QColor(bg_color) if bg_color else QColor(230, 230, 230, 240)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("Text:"))
        self.edit = QTextEdit()
        self.edit.setPlainText(text)
        self.edit.setFixedHeight(80)
        lay.addWidget(self.edit)

        row = QHBoxLayout()
        self.btn_fg = QPushButton("Text color")
        self.btn_fg.clicked.connect(self._pick_fg)
        self.btn_bg = QPushButton("Background color")
        self.btn_bg.clicked.connect(self._pick_bg)
        row.addWidget(self.btn_fg)
        row.addWidget(self.btn_bg)
        lay.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update_btn_styles()

    def _pick_fg(self):
        c = _show_color_dialog(self.fg_color, self, force_opaque=True)
        if c is not None:
            self.fg_color = c
            self._update_btn_styles()

    def _pick_bg(self):
        c = _show_color_dialog(self.bg_color, self, force_opaque=True)
        if c is not None:
            self.bg_color = c
            self._update_btn_styles()

    def _update_btn_styles(self):
        c = self.fg_color
        self.btn_fg.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"color: {'black' if c.lightness() > 128 else 'white'};")
        c = self.bg_color
        self.btn_bg.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"color: {'black' if c.lightness() > 128 else 'white'};")

    def result_data(self):
        return self.edit.toPlainText(), self.fg_color, self.bg_color


class _MarkerEditDialog(QDialog):
    def __init__(self, bg_color: QColor = None, text_color: QColor = None, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Edit Marker")
        self.bg_color   = bg_color   or QColor(220, 50, 50)
        self.text_color = text_color or QColor(Qt.GlobalColor.white)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.btn_bg   = QPushButton("Background color")
        self.btn_txt  = QPushButton("Text color")
        self.btn_bg.clicked.connect(self._pick_bg)
        self.btn_txt.clicked.connect(self._pick_txt)
        row.addWidget(self.btn_bg)
        row.addWidget(self.btn_txt)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update_styles()

    def _pick_bg(self):
        c = _show_color_dialog(self.bg_color, self, force_opaque=True)
        if c is not None:
            self.bg_color = c
            self._update_styles()

    def _pick_txt(self):
        c = _show_color_dialog(self.text_color, self, force_opaque=True)
        if c is not None:
            self.text_color = c
            self._update_styles()

    def _update_styles(self):
        c = self.bg_color
        self.btn_bg.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"color: {'black' if c.lightness() > 128 else 'white'};")
        c = self.text_color
        self.btn_txt.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"color: {'black' if c.lightness() > 128 else 'white'};")

    def result_data(self):
        return self.bg_color, self.text_color


class _MarkerItem(QGraphicsItem):
    """Numbered circular marker with:
    - a draggable spike handle (visible when selected, drag to reposition tip)
    - a scale handle on the left edge (visible when selected, drag to resize)
    Scaling is always uniform (equal W/H).
    """
    RADIUS = 14
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

class _TextInputDialog(QDialog):
    def __init__(self, color, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Add Text")
        self.color = color
        lay = QVBoxLayout(self)
        self.edit = QTextEdit(); self.edit.setFixedHeight(80)
        lay.addWidget(QLabel("Text:")); lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.addWidget(QLabel("Font size:"))
        self.sz = QSpinBox(); self.sz.setRange(6, 120); self.sz.setValue(18)
        row.addWidget(self.sz)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.btn_col = QPushButton("Text color"); self.btn_col.clicked.connect(self._pick_col)
        self.btn_hl  = QPushButton("Highlight"); self.btn_hl.clicked.connect(self._pick_hl)
        row2.addWidget(self.btn_col); row2.addWidget(self.btn_hl)
        lay.addLayout(row2)

        # ── Highlight options ────────────────────────────────────────────────
        hl_row = QHBoxLayout()
        self.chk_hl = QCheckBox("Enable highlight")
        self.chk_hl.setChecked(True)
        hl_row.addWidget(self.chk_hl)
        hl_row.addWidget(QLabel("Padding:"))
        self.hl_pad = QSpinBox(); self.hl_pad.setRange(0, 40); self.hl_pad.setValue(0)
        self.hl_pad.setSuffix(" px")
        hl_row.addWidget(self.hl_pad)
        hl_row.addStretch()
        lay.addLayout(hl_row)

        # ── Outline options ──────────────────────────────────────────────────
        ol_row = QHBoxLayout()
        self.chk_ol = QCheckBox("Enable outline")
        self.chk_ol.setChecked(False)
        ol_row.addWidget(self.chk_ol)
        ol_row.addWidget(QLabel("Width:"))
        self.ol_width = QSpinBox(); self.ol_width.setRange(1, 20); self.ol_width.setValue(2)
        self.ol_width.setSuffix(" px")
        ol_row.addWidget(self.ol_width)
        self.btn_ol_color = QPushButton("Outline color")
        self.btn_ol_color.clicked.connect(self._pick_ol_color)
        ol_row.addWidget(self.btn_ol_color)
        ol_row.addStretch()
        lay.addLayout(ol_row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.highlight = QColor(255, 255, 0, 255)  # fully opaque yellow highlight by default
        self.outline_color = QColor(0, 0, 0)       # default outline color: black
        self._update_btn_color()

    def _pick_col(self):
        c = _show_color_dialog(self.color, self, force_opaque=True)
        if c is not None:
            self.color = c
            self._update_btn_color()

    def _pick_hl(self):
        # Highlight color keeps alpha (intentionally semi-transparent)
        c = _show_color_dialog(self.highlight, self)
        if c is not None:
            self.highlight = c
            self._update_btn_color()

    def _pick_ol_color(self):
        c = _show_color_dialog(self.outline_color, self, force_opaque=True)
        if c is not None:
            self.outline_color = c
            self._update_btn_color()

    def _update_btn_color(self):
        c = self.color
        self.btn_col.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); "
            f"color: {'black' if c.lightness() > 128 else 'white'}; border: 1px solid #888;")
        hl = self.highlight
        self.btn_hl.setStyleSheet(
            f"background: rgba({hl.red()},{hl.green()},{hl.blue()},{hl.alphaF():.2f}); "
            f"color: {'black' if hl.lightness() > 128 else 'white'}; border: 1px solid #888;")
        if hasattr(self, 'btn_ol_color'):
            ol = self.outline_color
            self.btn_ol_color.setStyleSheet(
                f"background: rgba({ol.red()},{ol.green()},{ol.blue()},{ol.alphaF():.2f}); "
                f"color: {'black' if ol.lightness() > 128 else 'white'}; border: 1px solid #888;")

    def result_data(self):
        return (self.edit.toPlainText(), self.sz.value(), self.color, self.highlight,
                self.chk_hl.isChecked(), self.hl_pad.value(),
                self.chk_ol.isChecked(), self.ol_width.value(), self.outline_color)

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

    TOOL_DETECT    = "detect"
    TOOL_SELECT    = "select"
    TOOL_RECT      = "rect"
    TOOL_CIRCLE    = "circle"
    TOOL_FREEHAND  = "freehand"
    TOOL_LINE      = "line"
    TOOL_ARROW     = "arrow"
    TOOL_HIGHLIGHT = "highlight"
    TOOL_BUBBLE    = "bubble"
    TOOL_MARKER    = "marker"
    TOOL_TEXT      = "text"
    TOOL_IMAGE     = "image"
    TOOL_COLOR     = "color"

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
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMouseTracking(True)

        # Cover all screens
        self._geo = QRect()
        for s in QApplication.screens():
            self._geo = self._geo.united(s.geometry())
        self.setGeometry(self._geo)

        # ── State ──────────────────────────────────────────────────────────────
        self._current_tool      = self.TOOL_RECT
        self._detected_rect     = QRect()   # LOCAL widget coords
        self._drag_start        = None      # QPoint local
        self._drag_end          = None
        self._dragging          = False
        self._draw_color        = QColor(255, 0, 0)
        self._draw_width        = 3
        self._hide_bg           = False
        # Freehand tool keeps its own independent color and width
        self._freehand_color    = QColor(255, 0, 0)
        self._freehand_width    = 3
        # Marker and Bubble tools each keep their own independent color
        self._marker_color      = QColor(255, 0, 0)
        self._bubble_color      = QColor(255, 0, 0)

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
        self._canvas.raise_()
        # Sync color swatch and freehand defaults when selection changes
        self._canvas._scene.selectionChanged.connect(self._on_selection_changed)

        # ── Window-rect cache — disabled (detection mode removed) ──────────────
        self._win_rects: list[QRect] = []
        self._cache_timer = QTimer(self)  # kept as reference but not started

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
        QTimer.singleShot(150, self._activate_and_grab)
        QTimer.singleShot(80, self._update_detection_at_cursor)

    def _activate_and_grab(self):
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
        Collect visible window rects (Win32 physical px → logical → LOCAL coords).
        On Linux falls back to per-screen full-rect entries.
        """
        rects = []
        if IS_WINDOWS:
            try:
                import win32gui
                screens = QApplication.screens()

                def _cb(hwnd, _):
                    if not (win32gui.IsWindowVisible(hwnd) and
                            win32gui.GetWindowText(hwnd)):
                        return
                    try:
                        r = win32gui.GetWindowRect(hwnd)
                    except Exception:
                        return
                    pl, pt, pr, pb = r
                    pw, ph = pr - pl, pb - pt
                    if pw < 20 or ph < 20:
                        return

                    # Find screen by physical origin
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
                    loc_x = int(lx) - self._geo.x()
                    loc_y = int(ly) - self._geo.y()
                    rects.append(QRect(loc_x, loc_y, int(lw), int(lh)))

                win32gui.EnumWindows(_cb, None)
            except Exception:
                pass
        else:
            for s in QApplication.screens():
                lg = s.geometry()
                rects.append(QRect(
                    lg.x() - self._geo.x(), lg.y() - self._geo.y(),
                    lg.width(), lg.height()))
        self._win_rects = rects
        self._update_detection_at_cursor()

    def _update_detection_at_cursor(self):
        if self._current_tool != self.TOOL_DETECT or self._dragging:
            return
        gpos  = QCursor.pos()
        found = self._best_rect_at_global(gpos)
        if found != self._detected_rect:
            self._detected_rect = found
            self.update()

    def _best_rect_at_global(self, gpos: QPoint) -> QRect:
        """Smallest LOCAL-coord rect containing global logical pos gpos."""
        local = gpos - self._geo.topLeft()
        best, best_area = QRect(), 10**9
        for r in self._win_rects:
            if r.contains(local) and r.width() * r.height() < best_area:
                best, best_area = r, r.width() * r.height()
        return best

    # ══════════════════════════════════════════════════════════════════════════
    #  Toolbar
    # ══════════════════════════════════════════════════════════════════════════

    def _on_selection_changed(self):
        """Called when scene selection changes.
        If a FreehandItem is selected, update the toolbar color swatch and
        sync _freehand_width/_freehand_color so the Edit dialog pre-fills correctly."""
        selected = self._canvas._scene.selectedItems()
        for item in selected:
            if isinstance(item, FreehandItem):
                w = item.pen().width()
                c = item.pen().color()
                self._freehand_width = w if w > 0 else self._freehand_width
                self._freehand_color = QColor(c)
                self._color_preview.setStyleSheet(
                    f"background:{c.name()}; border:1px solid white; border-radius:3px;")
                return
        # No freehand item in selection — restore swatch to the active tool color
        if self._current_tool == self.TOOL_FREEHAND:
            swatch = self._freehand_color
        elif self._current_tool == self.TOOL_MARKER:
            swatch = self._marker_color
        elif self._current_tool == self.TOOL_BUBBLE:
            swatch = self._bubble_color
        else:
            swatch = self._draw_color
        self._color_preview.setStyleSheet(
            f"background:rgba({swatch.red()},{swatch.green()},{swatch.blue()},{swatch.alphaF():.2f}); border:1px solid white; border-radius:3px;")

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
                min-width: 80px; max-width: 80px; font-size: 13px;
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
            (self.TOOL_RECT,      "⬜",  "Draw rectangle annotation"),
            (self.TOOL_CIRCLE,    "⭕",  "Draw ellipse annotation"),
            (self.TOOL_HIGHLIGHT, "🟨",  "Draw highlight (semi-transparent yellow rectangle)"),
            (self.TOOL_FREEHAND,  "✏️",  "Freehand drawing"),
            (self.TOOL_LINE,      "📏",  "Draw straight line"),
            (self.TOOL_ARROW,     "➡️",  "Draw arrow"),
            (self.TOOL_BUBBLE,    "💬",  "Add text bubble"),
            (self.TOOL_MARKER,    "📍",  "Add numbered marker"),
            (self.TOOL_TEXT,      "T",   "Add text annotation"),
            (self.TOOL_IMAGE,     "🖼️",  "Import image onto canvas"),
            (self.TOOL_COLOR,     None,  "Change annotation color / width"),
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
            elif tid == self.TOOL_COLOR:
                btn.setObjectName("colorPickerBtn")
                btn.setText("🎨🔧")
            else:
                btn.setText(icon)
            lay.addWidget(btn)
            self._tool_btns[tid] = btn
        self._tool_btns[self.TOOL_RECT].setChecked(True)

        # Color swatch
        self._color_preview = QLabel()
        self._color_preview.setFixedSize(18, 18)
        self._color_preview.setStyleSheet(
            f"background:{self._draw_color.name()}; border:1px solid white; border-radius:3px;")
        lay.addWidget(self._color_preview)

        lay.addSpacing(6)

        # ── 📷 Capture button (shown only in annotation / draw modes) ─────────
        self._capture_btn = QPushButton("📷 Capture")
        self._capture_btn.setObjectName("captureBtn")
        self._capture_btn.setToolTip(
            "Capture the last detected / dragged region together with annotations")
        self._capture_btn.clicked.connect(self._capture_with_annotations)
        self._capture_btn.show()   # always visible — detection mode is disabled
        lay.addWidget(self._capture_btn)

        bar.adjustSize()
        return bar

    # ── helpers ───────────────────────────────────────────────────────────────
    def _is_draw_tool(self, tid=None):
        t = tid if tid is not None else self._current_tool
        return t in (self.TOOL_RECT, self.TOOL_CIRCLE, self.TOOL_FREEHAND,
                     self.TOOL_LINE, self.TOOL_ARROW, self.TOOL_HIGHLIGHT,
                     self.TOOL_BUBBLE, self.TOOL_MARKER, self.TOOL_TEXT,
                     self.TOOL_IMAGE, self.TOOL_SELECT)

    def _select_tool(self, tool_id):
        if tool_id == self.TOOL_IMAGE:
            self._import_image(); return
        if tool_id == self.TOOL_COLOR:
            # In Freehand mode: always show the Freehand edit dialog (width + color)
            if self._current_tool == self.TOOL_FREEHAND:
                selected = self._canvas._scene.selectedItems()
                freehand_items = [i for i in selected if isinstance(i, FreehandItem)]
                if freehand_items:
                    # Edit the selected freehand item's properties
                    item = freehand_items[0]
                    dlg = FreehandEditDialog(item.pen().width(), item.pen().color(), self)
                    if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
                        new_width, new_color = dlg.result_data()
                        pen = item.pen()
                        pen.setWidth(new_width)
                        pen.setColor(new_color)
                        item.setPen(pen)
                        item.update()
                        # Save into freehand-specific slots
                        self._freehand_width = new_width
                        self._freehand_color = QColor(new_color)
                        self._color_preview.setStyleSheet(
                            f"background:{new_color.name()}; border:1px solid white; border-radius:3px;")
                        if hasattr(self, 'spin'):
                            self.spin.blockSignals(True)
                            self.spin.setValue(new_width)
                            self.spin.blockSignals(False)
                else:
                    # No freehand item selected — edit default stroke width/color for Freehand tool
                    dlg = FreehandEditDialog(self._freehand_width, self._freehand_color, self)
                    if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
                        new_width, new_color = dlg.result_data()
                        self._freehand_width = new_width
                        self._freehand_color = QColor(new_color)
                        self._color_preview.setStyleSheet(
                            f"background:{new_color.name()}; border:1px solid white; border-radius:3px;")
                        if hasattr(self, 'spin'):
                            self.spin.blockSignals(True)
                            self.spin.setValue(new_width)
                            self.spin.blockSignals(False)
                return
            self._pick_color(); return

        # Reset drawing state when switching tools to avoid stale start position
        self._draw_start_scene = None
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
        # Update color swatch: each independent tool has its own color
        if tool_id == self.TOOL_FREEHAND:
            swatch_color = self._freehand_color
        elif tool_id == self.TOOL_MARKER:
            swatch_color = self._marker_color
        elif tool_id == self.TOOL_BUBBLE:
            swatch_color = self._bubble_color
        else:
            swatch_color = self._draw_color
        self._color_preview.setStyleSheet(
            f"background:rgba({swatch_color.red()},{swatch_color.green()},{swatch_color.blue()},{swatch_color.alphaF():.2f}); border:1px solid white; border-radius:3px;")

        # Show/hide capture button
        if self._is_draw_tool(tool_id):
            self._capture_btn.show()
        else:
            self._capture_btn.hide()
        # Canvas is ALWAYS transparent — mouse routing is done manually

        cross = (self.TOOL_DETECT, self.TOOL_RECT, self.TOOL_CIRCLE,
                 self.TOOL_LINE, self.TOOL_ARROW, self.TOOL_HIGHLIGHT,
                 self.TOOL_FREEHAND)
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
                     self.TOOL_LINE, self.TOOL_ARROW, self.TOOL_HIGHLIGHT,
                     self.TOOL_FREEHAND)
            cur = Qt.CursorShape.CrossCursor if prev_tool in cross else Qt.CursorShape.ArrowCursor
            self.setCursor(QCursor(cur))
        else:
            self._current_tool = self.TOOL_SELECT
        self._toolbar.show()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.grabKeyboard()
        return result

    

    def _pick_color(self):
        """Open a context-aware edit dialog for the selected item, or a plain
        colour picker if no item is selected / the item has no dedicated dialog."""
        self._toolbar.hide()
        self.releaseKeyboard()

        selected = self._canvas._scene.selectedItems()

        if selected:
            item = selected[0]
            handled = self._open_item_edit_dialog(item)
            if not handled:
                # Fallback: plain colour picker applied to selected item
                self._open_generic_color_picker(apply_to_item=item)
        else:
            # No selection — just pick the drawing colour
            self._open_generic_color_picker(apply_to_item=None)

        self._toolbar.show()
        self.activateWindow(); self.setFocus()
        self.grabKeyboard()

    def _open_item_edit_dialog(self, item) -> bool:
        """Open the dedicated edit dialog for *item*.
        Returns True if a dedicated dialog was shown, False otherwise."""
        if isinstance(item, HighlightRectItem):
            dlg = HighlightEditDialog(item._color, self)
            if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
                new_color = dlg.result_color()
                item._color = new_color
                border = QColor(new_color.red(), new_color.green(), new_color.blue(), 160)
                item.setPen(QPen(border, 1.5))
                item.setBrush(QBrush(new_color))
                item.update()
            return True

        if isinstance(item, TextBubbleItem):
            dlg = _BubbleEditDialog(item._fg_color, item._bg_color, item._text, self)
            if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
                text, fg, bg = dlg.result_data()
                item._text     = text
                item._fg_color = fg
                item._bg_color = bg
                item.update()
            return True

        if isinstance(item, _MarkerItem):
            dlg = _MarkerEditDialog(
                QColor(item._bg_color),
                QColor(getattr(item, '_text_color', QColor(Qt.GlobalColor.white))),
                self)
            if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
                item._bg_color   = dlg.bg_color
                item._text_color = dlg.text_color
                item.update()
            return True

        if isinstance(item, FreehandItem):
            dlg = FreehandEditDialog(item.pen().width(), item.pen().color(), self)
            if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
                new_width, new_color = dlg.result_data()
                pen = item.pen()
                pen.setWidth(new_width)
                pen.setColor(new_color)
                item.setPen(pen)
                item.update()
                # Save into freehand-specific slots (independent from other tools)
                self._freehand_width = new_width
                self._freehand_color = QColor(new_color)
                self._color_preview.setStyleSheet(
                    f"background:{new_color.name()}; border:1px solid white; border-radius:3px;")
                if hasattr(self, 'spin'):
                    self.spin.blockSignals(True)
                    self.spin.setValue(new_width)
                    self.spin.blockSignals(False)
            return True

        return False  # no dedicated dialog for this type

    def _open_generic_color_picker(self, apply_to_item=None):
        """Show a plain QColorDialog and apply result to the drawing colour
        and optionally to *apply_to_item*.
        Highlight keeps its own alpha; all other tools preserve user-chosen alpha."""
        is_highlight = isinstance(apply_to_item, HighlightRectItem)

        # Pick the right source color for the current tool so the dialog opens
        # with whatever the user last chose (including their alpha value).
        if self._current_tool == self.TOOL_FREEHAND:
            init_color = QColor(self._freehand_color)
        elif self._current_tool == self.TOOL_MARKER:
            init_color = QColor(self._marker_color)
        elif self._current_tool == self.TOOL_BUBBLE:
            init_color = QColor(self._bubble_color)
        else:
            init_color = QColor(self._draw_color)

        # For non-highlight tools: if alpha is 0 (uninitialised), default to 255.
        # Otherwise keep whatever the user previously set.
        if not is_highlight and init_color.alpha() == 0:
            init_color.setAlpha(255)

        # Use _show_color_dialog so the Linux alpha=0 bug fix is applied.
        c = _show_color_dialog(init_color, self, alpha=True)
        if c is not None and c.isValid():
            # Save color into the correct per-tool slot
            if self._current_tool == self.TOOL_FREEHAND:
                self._freehand_color = c
            elif self._current_tool == self.TOOL_MARKER:
                self._marker_color = c
            elif self._current_tool == self.TOOL_BUBBLE:
                self._bubble_color = c
            else:
                self._draw_color = c
                self._color_preview.setStyleSheet(
                    f"background:rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.2f}); border:1px solid white; border-radius:3px;")
                if apply_to_item is not None:
                    # Preserve the item's current pen width instead of resetting to default
                    current_width = (apply_to_item.pen().width()
                                     if hasattr(apply_to_item, 'pen')
                                     else self._draw_width)
                    self._canvas._apply_props(apply_to_item, c, current_width)
                    if hasattr(apply_to_item, 'setDefaultTextColor'):
                        apply_to_item.setDefaultTextColor(c)
                    apply_to_item.update()

    def _import_image(self):
        self._toolbar.hide()
        self.releaseKeyboard()
        # Make the entire overlay pass-through so the file dialog gets mouse events
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Briefly lower the overlay so the dialog can paint on top
        self.lower()
        QApplication.processEvents()

        # Parent MUST be 'self' so it inherits the overlay's z-index and bypasses modality block
        dlg = QFileDialog(self, "Import Image", "",
                          "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        flags = (Qt.WindowType.Window |
                 Qt.WindowType.WindowStaysOnTopHint |
                 Qt.WindowType.Dialog)
        if IS_LINUX:
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        dlg.setWindowFlags(flags)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        QApplication.processEvents()

        # Restore mouse events only AFTER dialog closes (exec is blocking)
        result = dlg.exec()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        # Canvas MUST remain transparent so the parent overlay can route mouse events properly
        self._canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Raise the overlay back after the dialog closes
        self.raise_()
        self._canvas.raise_()

        if result == QFileDialog.DialogCode.Accepted:
            files = dlg.selectedFiles()
            if files:
                pix = QPixmap(files[0])
                if not pix.isNull():
                    center = QPointF(self._geo.width() / 2 - pix.width()  / 2,
                                     self._geo.height()/ 2 - pix.height() / 2)
                    self._canvas.add_pixmap(center, pix)

        # Always switch to SELECT after image import so user can move/resize it
        self._select_tool(self.TOOL_SELECT)
        self._toolbar.show()
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

    def paintEvent(self, e):
        if self._hide_bg:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))

        # ── Draw inline capture selection rectangle ────────────────────────────
        if (self._current_tool == "_capture_select" and
                self._inline_start and self._inline_end):
            r = QRect(self._inline_start, self._inline_end).normalized()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(r, QColor(0, 0, 0, 1))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(0, 200, 255), 2))
            p.drawRect(r)
            p.setPen(QColor(255, 255, 255))
            p.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
            p.drawText(r.x() + 4, r.y() - 6 if r.y() > 20 else r.bottom() + 14,
                       f"{r.width()} × {r.height()} px")

        if self._dragging and self._drag_start and self._drag_end:
            r = QRect(self._drag_start, self._drag_end).normalized()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(r, QColor(0, 0, 0, 1))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(0, 174, 255), 2))
            p.drawRect(r)
            p.setPen(QColor(255, 255, 255))
            p.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
            label = f"{r.width()} × {r.height()} px"
            ty = r.y() - 6 if r.y() > 20 else r.bottom() + 14
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
            self._inline_start = lpos
            self._inline_end   = lpos
            return

        # ── DETECT mode ───────────────────────────────────────────────────────
        if self._current_tool == self.TOOL_DETECT:
            return

        # ── SELECT mode — route into QGraphicsScene for item picking ──────────
        if self._current_tool == self.TOOL_SELECT:
            scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))
            item, handle = self._canvas.get_handle_at(scene_pos)
            if item and handle:
                self._resizing_item = item
                self._resize_handle = handle
                # Send a synthetic press at the item's position so the scene
                # registers it as the current mouse grabber — this ensures the
                # paired synthetic release in mouseReleaseEvent will correctly
                # clear the grabber rather than leaving it in a stale state.
                self._canvas.send_mouse_to_scene(e)
                return
            self._canvas.send_mouse_to_scene(e)
            return

        # ── Drawing tools ─────────────────────────────────────────────────────
        scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))
        self._draw_start_scene = scene_pos
        self._preview_item = None

        if self._current_tool == self.TOOL_FREEHAND:
            self._canvas.begin_freehand(scene_pos, self._freehand_color, self._freehand_width)

        elif self._current_tool == self.TOOL_MARKER:
            self._canvas.add_marker(scene_pos, self._marker_color)

        elif self._current_tool == self.TOOL_TEXT:
            dlg = _TextInputDialog(QColor(self._draw_color), self)
            if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
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
            dlg = _BubbleInputDialog(QColor(self._bubble_color), self)
            if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
                txt, fg_col, bg_col = dlg.result_data()
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
                self._inline_end = lpos
                self.update()
            return

        # ── DETECT mode disabled
        if self._current_tool == self.TOOL_DETECT:
            return

        # ── SELECT mode — route into QGraphicsScene ───────────────────────────
        if self._current_tool == self.TOOL_SELECT:
            scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))

            # Handle Active Resizing
            if getattr(self, '_resizing_item', None) and getattr(self, '_resize_handle', None):
                proportional = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self._canvas.handle_resize(self._resizing_item, self._resize_handle, scene_pos, proportional)
                return

            # Handle Cursor Hover Updates for Handles
            _, handle = self._canvas.get_handle_at(scene_pos)
            if handle:
                if handle == 'ROTATE': self.setCursor(Qt.CursorShape.PointingHandCursor)
                elif handle == 'WIDTH': self.setCursor(Qt.CursorShape.SizeVerCursor)
                elif handle in ['TL', 'BR']: self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif handle in ['TR', 'BL']: self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                elif handle in ['L', 'R']: self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif handle in ['T', 'B']: self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

            self._canvas.send_mouse_to_scene(e)
            return

        # ── Drawing tools (LMB held) ──────────────────────────────────────────
        if not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        scene_pos = self._canvas.mapToScene(self._canvas.mapFrom(self, lpos))

        if self._current_tool == self.TOOL_FREEHAND:
            self._canvas.extend_freehand(scene_pos)

        elif self._current_tool in (self.TOOL_RECT, self.TOOL_CIRCLE, self.TOOL_LINE, self.TOOL_HIGHLIGHT, self.TOOL_ARROW):
            if self._draw_start_scene is None:
                return
            end_scene = scene_pos
            # Ctrl held: constrain rect/circle to equal width and height (perfect square/circle)
            if (self._current_tool in (self.TOOL_RECT, self.TOOL_CIRCLE) and
                    e.modifiers() & Qt.KeyboardModifier.ControlModifier):
                dx = end_scene.x() - self._draw_start_scene.x()
                dy = end_scene.y() - self._draw_start_scene.y()
                side = min(abs(dx), abs(dy))
                end_scene = QPointF(
                    self._draw_start_scene.x() + math.copysign(side, dx),
                    self._draw_start_scene.y() + math.copysign(side, dy))
            r = QRectF(self._draw_start_scene, end_scene).normalized()
            if self._preview_item is not None:
                self._canvas._scene.removeItem(self._preview_item)
                self._preview_item = None
            if self._current_tool == self.TOOL_RECT:
                self._preview_item = self._canvas.add_rect(
                    r, self._draw_color, self._draw_width)
            elif self._current_tool == self.TOOL_HIGHLIGHT:
                self._preview_item = self._canvas.add_highlight(r)
            elif self._current_tool == self.TOOL_CIRCLE:
                self._preview_item = self._canvas.add_ellipse(
                    r, self._draw_color, self._draw_width)
            elif self._current_tool == self.TOOL_LINE:
                end_pos = scene_pos
                if e.modifiers() & (Qt.KeyboardModifier.ShiftModifier |
                                    Qt.KeyboardModifier.ControlModifier):
                    dx = end_pos.x() - self._draw_start_scene.x()
                    dy = end_pos.y() - self._draw_start_scene.y()
                    angle = math.atan2(dy, dx)
                    snapped = round(math.degrees(angle) / 45) * 45
                    dist = math.hypot(dx, dy)
                    end_pos = QPointF(
                        self._draw_start_scene.x() + dist * math.cos(math.radians(snapped)),
                        self._draw_start_scene.y() + dist * math.sin(math.radians(snapped)))
                self._preview_item = self._canvas.add_line(
                    QLineF(self._draw_start_scene, end_pos),
                    self._draw_color, self._draw_width)
            elif self._current_tool == self.TOOL_ARROW:
                end_pos = scene_pos
                if e.modifiers() & (Qt.KeyboardModifier.ShiftModifier |
                                    Qt.KeyboardModifier.ControlModifier):
                    dx = end_pos.x() - self._draw_start_scene.x()
                    dy = end_pos.y() - self._draw_start_scene.y()
                    angle = math.atan2(dy, dx)
                    snapped = round(math.degrees(angle) / 45) * 45
                    dist = math.hypot(dx, dy)
                    end_pos = QPointF(
                        self._draw_start_scene.x() + dist * math.cos(math.radians(snapped)),
                        self._draw_start_scene.y() + dist * math.sin(math.radians(snapped)))
                self._preview_item = self._canvas.add_arrow(
                    QLineF(self._draw_start_scene, end_pos),
                    self._draw_color, self._draw_width)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        # Reset WIDTH drag state on any selected ResizableRect/Ellipse
        for item in self._canvas._scene.selectedItems():
            if hasattr(item, '_width_drag_active'):
                item._width_drag_active = False
        lpos = e.position().toPoint()

        # ── Inline capture-selection mode — finalize rect ─────────────────────
        if self._current_tool == "_capture_select":
            if self._inline_start and self._inline_end:
                local_rect = QRect(self._inline_start, self._inline_end).normalized()
                if local_rect.width() > 4 and local_rect.height() > 4:
                    global_rect = local_rect.translated(self._geo.topLeft())
                    self._current_tool = self._prev_tool
                    self._inline_selecting = False
                    self._inline_start = None
                    self._inline_end = None
                    self._do_capture_global_rect(global_rect)
                    return
            # Selection too small — cancel and restore toolbar
            self._current_tool = self._prev_tool
            self._inline_selecting = False
            self._inline_start = None
            self._inline_end = None
            self._toolbar.show()
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

        # ── Draw tools — finalise ─────────────────────────────────────────────
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

            elif isinstance(item, TextBubbleItem):
                self._edit_bubble(item)

            elif isinstance(item, _MarkerItem):
                self._edit_marker(item)

            elif isinstance(item, FreehandItem):
                self._edit_freehand(item)

    def _edit_freehand(self, item):
        """Open FreehandEditDialog for a selected FreehandItem — same UI/logic as Image Editor."""
        dlg = FreehandEditDialog(item.pen().width(), item.pen().color(), self)
        if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
            new_width, new_color = dlg.result_data()
            pen = item.pen()
            pen.setWidth(new_width)
            pen.setColor(new_color)
            item.setPen(pen)
            item.update()
            # Keep freehand-specific defaults in sync for future strokes
            self._freehand_width = new_width
            self._freehand_color = QColor(new_color)
            # Update color swatch on toolbar
            self._color_preview.setStyleSheet(
                f"background:{new_color.name()}; border:1px solid white; border-radius:3px;")

    def _edit_text(self, item):
        # Pre-fill dialog with existing text item data
        dlg = _TextInputDialog(item.defaultTextColor(), self)
        dlg.edit.setPlainText(item.toPlainText())
        dlg.sz.setValue(item.font().pointSize())
        if hasattr(item, 'highlight_color'):
            dlg.highlight = item.highlight_color
        dlg.chk_hl.setChecked(getattr(item, 'highlight_enabled', True))
        dlg.hl_pad.setValue(getattr(item, 'highlight_padding', 0))
        dlg.chk_ol.setChecked(getattr(item, 'outline_enabled', False))
        dlg.ol_width.setValue(getattr(item, 'outline_width', 2))
        dlg.outline_color = QColor(getattr(item, 'outline_color', QColor(0, 0, 0)))
        dlg._update_btn_color()

        if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
            txt, fsz, col, hl, hl_on, hl_pad, ol_on, ol_w, ol_col = dlg.result_data()
            if txt.strip():
                item.setPlainText(txt)
                item.setFont(QFont("Arial", fsz))
                item.setDefaultTextColor(col)
                item.highlight_color   = hl
                item.highlight_enabled = hl_on
                item.highlight_padding = hl_pad
                item.outline_enabled   = ol_on
                item.outline_width     = ol_w
                item.outline_color     = ol_col
                item.update()
            else:
                self._canvas._scene.removeItem(item)

    def _edit_bubble(self, item):
        dlg = _BubbleInputDialog(item._fg_color, self)
        dlg.edit.setPlainText(item._text)
        dlg.bg_color = item._bg_color
        dlg._update_btn_styles()
        if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
            txt, fg_col, bg_col = dlg.result_data()
            if txt.strip():
                item._text = txt
                item._fg_color = fg_col
                item._bg_color = bg_col
                item.prepareGeometryChange()
                item.update()

    def _edit_marker(self, item):
        dlg = _MarkerEditDialog(item._bg_color,
                                getattr(item, '_text_color', QColor(Qt.GlobalColor.white)),
                                self)
        if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
            bg, fg = dlg.result_data()
            item._bg_color = bg
            item._text_color = fg
            item.update()

    def _edit_highlight(self, item):
        dlg = HighlightEditDialog(item._color, self)
        if self._exec_dialog(dlg) == QDialog.DialogCode.Accepted:
            new_color = dlg.result_color()
            item._color = new_color
            border = QColor(new_color.red(), new_color.green(), new_color.blue(), 160)
            item.setPen(QPen(border, 1.5))
            item.setBrush(QBrush(new_color))
            item.update()

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
                    self._canvas._scene.removeItem(item)
            elif isinstance(item, _MarkerItem):
                edit_act = menu.addAction("✏️ Edit")
                del_act  = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action == edit_act:
                    self._edit_marker(item)
                elif action == del_act:
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
                    self._canvas._scene.removeItem(item)
            else:
                dup_act = menu.addAction("⧉ Duplicate")
                del_act = menu.addAction("🗑️ Delete")
                action = menu.exec(e.globalPos())
                if action == dup_act:
                    self._duplicate_item_beside(item)
                elif action == del_act:
                    self._canvas._scene.removeItem(item)

    def _duplicate_item_beside(self, item):
        """Duplicate a single item and place it slightly offset beside the original."""
        dup = self._clone_item(item)
        if dup is None:
            return
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
        if (e.key() == Qt.Key.Key_D and
                e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._duplicate_selected_at_cursor()
            e.accept()
            return
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            selected = self._canvas._scene.selectedItems()
            if selected:
                for item in selected:
                    self._canvas._scene.removeItem(item)
                e.accept()
                return
        super().keyPressEvent(e)

    def _duplicate_selected_at_cursor(self):
        """Duplicate selected annotation items centred exactly at the cursor position."""
        selected = self._canvas._scene.selectedItems()
        if not selected:
            return
        cursor_global = QCursor.pos()
        # Convert global → local widget coords → scene coords
        local = self.mapFromGlobal(cursor_global)
        cursor_scene = self._canvas.mapToScene(self._canvas.mapFrom(self, local))

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
        if isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
            cls = type(item)
            dup = cls()
            dup.setRect(QRectF(item.rect()))
            dup.setPen(QPen(item.pen()))
            dup.setBrush(QBrush(item.brush()))
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
        Hide overlay completely, then after a short delay grab + composite + save.
        The widget hides (not closes) first so MSS sees the real screen.
        """
        self._last_detected_global = global_rect
        self._toolbar.hide()
        self._hide_bg = True
        self.update()
        self.hide()                        # hide so MSS sees real screen
        QApplication.processEvents()
        QTimer.singleShot(200, lambda: self._grab_composite_and_close(global_rect))

    def _grab_composite_and_close(self, global_rect: QRect):
        """
        Grab screenshot, composite annotations, save, notify — then close.
        Widget is already hidden (not closed) so MSS sees the real screen.
        All self.* access happens before close() to avoid use-after-free.
        """
        # ── 1. Collect everything from self while widget is alive ─────────────
        captured_geo    = QRect(self._geo)
        has_annotations = self._canvas.has_items()

        screen = QApplication.screenAt(global_rect.topLeft())
        if not screen:
            screen = QApplication.screenAt(global_rect.center())
        if not screen:
            screen = QApplication.primaryScreen()

        ratio        = screen.devicePixelRatio()
        logical_geom = screen.geometry()
        phys_x       = int((global_rect.x() - logical_geom.x()) * ratio)
        phys_y       = int((global_rect.y() - logical_geom.y()) * ratio)
        phys_w       = max(1, int(global_rect.width()  * ratio))
        phys_h       = max(1, int(global_rect.height() * ratio))
        final_x      = phys_x
        final_y      = phys_y

        try:
            with mss.MSS() as sct:
                qt_screens = sorted(QApplication.screens(),
                                    key=lambda s: (s.geometry().x(), s.geometry().y()))
                mss_mons   = sorted(sct.monitors[1:],
                                    key=lambda m: (m["left"], m["top"]))
                if screen in qt_screens:
                    idx = qt_screens.index(screen)
                    if idx < len(mss_mons):
                        mon     = mss_mons[idx]
                        final_x = mon["left"] + phys_x
                        final_y = mon["top"]  + phys_y
        except Exception as ex:
            print(f"[PyshareX] monitor mapping error: {ex}")

        # ── 2. Render annotation layer while canvas is still alive ────────────
        ann_pixmap = None
        if has_annotations:
            logical_rect_f = QRectF(global_rect.translated(-captured_geo.topLeft()))
            pw = max(1, int(logical_rect_f.width()))
            ph = max(1, int(logical_rect_f.height()))
            ann_pixmap = QPixmap(pw, ph)
            ann_pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(ann_pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._canvas._scene.render(painter,
                                       target=QRectF(ann_pixmap.rect()),
                                       source=logical_rect_f)
            painter.end()

        # ── 3. Grab screenshot (widget already hidden — real screen visible) ───
        base_img = None
        try:
            with mss.MSS() as sct:
                shot     = sct.grab({"left": final_x, "top": final_y,
                                     "width": phys_w, "height": phys_h})
                base_img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception as ex:
            print(f"[PyshareX] mss grab error: {ex}")

        # ── 4. Composite annotations onto screenshot ──────────────────────────
        composited = base_img
        if base_img and ann_pixmap and has_annotations:
            try:
                from PySide6.QtCore import QBuffer, QIODevice
                qbuf = QBuffer()
                qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
                ann_pixmap.save(qbuf, "PNG")
                qbuf.close()
                buf = io.BytesIO(qbuf.data().data())
                buf.seek(0)
            except Exception as ex:
                print(f"[PyshareX] pixmap-to-bytes error: {ex}")
                buf = None
            try:
                if buf is None:
                    raise Exception("pixmap buffer empty")
                ann_pil   = Image.open(buf).convert("RGBA")
                ann_pil   = ann_pil.resize((phys_w, phys_h), Image.LANCZOS)
                base_rgba = base_img.convert("RGBA")
                base_rgba.paste(ann_pil, (0, 0), ann_pil)
                composited = base_rgba.convert("RGB")
            except Exception as ex:
                print(f"[PyshareX] composite error: {ex}")

        # ── 5. Save file and notify main window ───────────────────────────────
        saved_path = None
        if composited:
            try:
                engine = None
                for w in QApplication.topLevelWidgets():
                    if hasattr(w, "engine") and hasattr(w.engine, "_save"):
                        engine = w.engine
                        break
                if engine:
                    saved_path = engine._save(composited, "region")
            except Exception as ex:
                print(f"[PyshareX] save error: {ex}")

        # ── 6. Close widget LAST — after all self.* access is done ────────────
        self.close()

        # ── 7. Notify main window (after close — no self.* needed) ────────────
        if saved_path:
            try:
                for w in QApplication.topLevelWidgets():
                    if hasattr(w, "_notify_sig"):
                        if composited:
                            # Store bytes in a local variable to prevent Garbage Collection 
                            # before QPixmap reads the memory buffer
                            raw_data = composited.tobytes()
                            # Force deep copy to prevent Segmentation Fault
                            qim   = QImage(raw_data,
                                           composited.width, composited.height,
                                           composited.width * 3,
                                           QImage.Format.Format_RGB888).copy()
                            thumb = QPixmap.fromImage(qim)
                        else:
                            thumb = None
                        w._notify_sig.emit(saved_path, thumb)
                        # Restore main window only if it was visible before capture
                        if getattr(w, '_win_was_visible', True):
                            QTimer.singleShot(300, w.show_win)
                        break
            except Exception as ex:
                print(f"[PyshareX] notify error: {ex}")
        else:
            # Fallback: tell main window to re-grab the region normally
            try:
                self.region_selected.emit(final_x, final_y, phys_w, phys_h)
            except Exception:
                pass

    def _emit_and_close(self, global_rect: QRect):
        """Kept for compatibility — delegates to _grab_composite_and_close."""
        self._grab_composite_and_close(global_rect)


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
        if engine == "paddleocr":
            return self._ocr_paddleocr(path)
        elif engine == "easyocr":
            return self._ocr_easyocr(path)
        else:
            return self._ocr_tesseract(path)

    def _ocr_paddleocr(self, image_path: str) -> str:
        if not PADDLEOCR_AVAILABLE:
            return ("PaddleOCR is not installed.\n"
                    "Install with:  pip install paddlepaddle paddleocr\n"
                    "Falling back — try EasyOCR or Tesseract in Settings.")
        reader = _get_paddleocr_reader()
        if reader is None:
            detail = f"\n\nError: {_paddleocr_init_error}" if _paddleocr_init_error else ""
            return (
                f"PaddleOCR failed to initialize.{detail}\n\n"
                "Possible fixes:\n"
                "  • Upgrade: pip install --upgrade paddlepaddle paddleocr\n"
                "  • Or switch to EasyOCR in Settings → OCR engine."
            )
        try:
            # On Linux CPUs without AVX (e.g. VirtualBox), PaddlePaddle may
            # raise SIGILL (Illegal instruction). Catch broad Exception — the
            # signal itself can't be caught in Python, but init-time errors can.
            import signal as _signal
            if IS_LINUX:
                # Set a 15-second alarm so a hanging paddle call doesn't freeze the app
                _signal.alarm(15)
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

            # PaddleOCR 3.x uses .predict(); 2.x uses .ocr()
            if _paddleocr_api_version == "v3":
                result_obj = reader.predict(img_input)
                lines = []
                for item in (result_obj or []):
                    # PaddleX OCRResult behaves like a dict — access rec_texts directly
                    try:
                        texts = item["rec_texts"]
                        if isinstance(texts, (list, tuple)):
                            lines.extend([str(t) for t in texts if t])
                        elif isinstance(texts, str) and texts:
                            lines.append(texts)
                        continue
                    except (KeyError, TypeError):
                        pass
                    # Fallback: attribute access
                    texts = getattr(item, "rec_texts", None)
                    if isinstance(texts, (list, tuple)):
                        lines.extend([str(t) for t in texts if t])
                    elif isinstance(texts, str) and texts:
                        lines.append(texts)
                    else:
                        # Last resort: legacy list of (box, (text, score))
                        try:
                            for line in item:
                                if line and len(line) >= 2:
                                    text_info = line[1]
                                    if isinstance(text_info, (list, tuple)) and text_info:
                                        lines.append(str(text_info[0]))
                                    elif isinstance(text_info, str):
                                        lines.append(text_info)
                        except Exception:
                            pass
                return "\n".join(lines)
            else:
                # PaddleOCR 2.x
                results = reader.ocr(img_input, cls=True)
                lines = []
                for block in (results or []):
                    if block is None:
                        continue
                    for line in block:
                        if line and len(line) >= 2:
                            text_info = line[1]
                            if isinstance(text_info, (list, tuple)) and text_info:
                                lines.append(str(text_info[0]))
                            elif isinstance(text_info, str):
                                lines.append(text_info)
                return "\n".join(lines)
        except Exception as e:
            return f"PaddleOCR error: {e}"
        finally:
            try:
                import signal as _signal
                if IS_LINUX:
                    _signal.alarm(0)  # cancel alarm
            except Exception:
                pass

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
    finished     = Signal(str)
    error        = Signal(str)
    region_ready = Signal(int, int, int, int)

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

    def get_data(self): return self.data


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
    _MOD_NAMES = {"ctrl_l", "ctrl_r", "alt_l", "alt_r", "alt_gr",
                  "shift_l", "shift_r", "cmd", "cmd_l", "cmd_r"}

    def __init__(self, initial: str = "", parent=None):
        super().__init__(parent)
        self._shortcut = initial
        self._listener = None
        self._held_mods: set = set()
        self._combo_ready.connect(self._accept_combo)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self._display = QLineEdit(initial)
        self._display.setReadOnly(True)
        self._display.setPlaceholderText("Press Capture, then press your key combination")
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
        if any(m in self._held_mods for m in ("ctrl_l", "ctrl_r")):
            parts.append("Ctrl")
        if any(m in self._held_mods for m in ("alt_l", "alt_r", "alt_gr")):
            parts.append("Alt")
        if any(m in self._held_mods for m in ("shift_l", "shift_r")):
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
        self._rb_custom.setChecked(True)
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
        btn_empty     = QPushButton("⬜ Empty Canvas")

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

class FreehandEditDialog(QDialog):
    """Edit dialog for Freehand strokes — line width and color."""
    def __init__(self, current_width: int, current_color: QColor, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Edit Freehand")
        self._width = current_width
        self._color = QColor(current_color)
        lay = QVBoxLayout(self)

        # Line width
        width_row = QHBoxLayout()
        width_row.addWidget(QLabel("Line width:"))
        self.spin_width = QSpinBox()
        self.spin_width.setRange(1, 100)
        self.spin_width.setValue(current_width)
        self.spin_width.setSuffix(" px")
        width_row.addWidget(self.spin_width)
        lay.addLayout(width_row)

        # Line color
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Line color:"))
        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(80, 28)
        self.btn_color.clicked.connect(self._pick_color)
        color_row.addWidget(self.btn_color)
        lay.addLayout(color_row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update_btn()

    def _pick_color(self):
        c = _show_color_dialog(self._color, self, force_opaque=True)
        if c is not None:
            self._color = c
            self._update_btn()

    def _update_btn(self):
        c = self._color
        self.btn_color.setStyleSheet(
            f"background: rgba({c.red()},{c.green()},{c.blue()},1.0); "
            f"color: {'black' if c.lightness() > 128 else 'white'}; border: 1px solid #888;")

    def result_data(self):
        return self.spin_width.value(), QColor(self._color)


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
        """Open FreehandEditDialog above the canvas via the overlay's _exec_dialog."""
        # Locate the CaptureRegionWindow (overlay) that owns this item
        overlay = None
        if self.scene() and self.scene().views():
            for view in self.scene().views():
                win = view.window()
                if win and hasattr(win, '_exec_dialog'):
                    overlay = win
                    break

        dlg = FreehandEditDialog(self.pen().width(), self.pen().color(),
                                 parent=overlay)

        if overlay is not None:
            result = overlay._exec_dialog(dlg)
        else:
            # Fallback when no overlay is found (e.g. standalone ImageEditorWindow)
            _set_dialog_on_top(dlg)
            if screen_pos:
                dlg.move(screen_pos)
            result = dlg.exec()

        if result == QDialog.DialogCode.Accepted:
            new_width, new_color = dlg.result_data()
            pen = self.pen()
            pen.setWidth(new_width)
            pen.setColor(new_color)
            self.setPen(pen)
            self.update()
            # Sync the Size spinner in the parent window
            if self.scene() and self.scene().views():
                win = self.scene().views()[0].window()
                if win and hasattr(win, 'spin'):
                    win.spin.blockSignals(True)
                    win.spin.setValue(new_width)
                    win.spin.blockSignals(False)
            # Update _draw_width on the overlay so new strokes use the new width
            if overlay is not None and hasattr(overlay, '_draw_width'):
                overlay._draw_width = new_width

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
            self.prepareGeometryChange()
            line = self.line()
            new_pos = event.pos()

            # 45-degree angle snapping constraint
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                anchor = line.p2() if self.active_handle == 'p1' else line.p1()
                dx, dy = new_pos.x() - anchor.x(), new_pos.y() - anchor.y()
                snapped_angle = round(math.degrees(math.atan2(dy, dx)) / 45) * 45
                d = math.hypot(dx, dy)
                new_pos = QPointF(anchor.x() + d * math.cos(math.radians(snapped_angle)),
                                  anchor.y() + d * math.sin(math.radians(snapped_angle)))

            if self.active_handle == 'p1':
                line.setP1(new_pos)
            else:
                line.setP2(new_pos)

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
        
        # Drawing Props
        self.stroke_color = QColor(255, 0, 0, 255)
        self.fill_color = QColor(255, 0, 0, 0)
        self.stroke_width = 3
        self.font_size = 40
        self.is_filled = False
        self.text_highlight_color = QColor(255, 255, 0, 255)
        self.highlight_tool_color = QColor(255, 255, 0, 90)
        # Marker and Bubble tools each keep their own independent color
        self.marker_color = QColor(255, 0, 0, 255)
        self.bubble_color = QColor(255, 0, 0, 255)
        self._pan_start = None
        
        # Massive sceneRect allows infinite panning regardless of zoom
        bg_rect = QRectF(pixmap.rect())
        self.scene.setSceneRect(bg_rect.center().x() - 50000, bg_rect.center().y() - 50000, 100000, 100000)

        # Pre-bake checkerboard tile into a QPixmap once — reused every paint call
        self._checker_tile = self._make_checker_tile(10, QColor(65, 65, 65), QColor(96, 96, 96))
        self._checker_cache: QPixmap | None = None   # full-size cache, rebuilt on image resize
        self._checker_cache_size: tuple[int, int] = (-1, -1)

    def keyPressEvent(self, event):
        """Handle Delete key to remove selected items, Ctrl+D to duplicate."""
        if event.key() == Qt.Key.Key_Delete:
            for item in self.scene.selectedItems():
                if item != self.bg_item and item != self.crop_item:
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
        if isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
            cls = type(item)
            dup = cls()
            dup.setRect(QRectF(item.rect()))
            dup.setPen(QPen(item.pen()))
            dup.setBrush(QBrush(item.brush()))
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
        return None

    def wheelEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if event.angleDelta().y() > 0 else 0.8
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        self.setFocus() # Ensure canvas has focus for keyboard events
        scene_pos = self.mapToScene(event.position().toPoint())

        if event.button() == Qt.MouseButton.MiddleButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._pan_start = event.position().toPoint()
            return

        if self.current_tool == "Eraser":
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
                self.start_point = scene_pos
                return # Block regular selection/drawing
            super().mousePressEvent(event)
            return

        # Drawing Mode
        self.is_dirty = True
        self.start_point = scene_pos
        if self.current_tool == "Rectangle": 
            self.current_item = ResizableRectItem()
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
        elif self.current_tool == "Highlight":
            self._hl_item = HighlightRectItem()
            self._hl_item.setRect(QRectF(self.start_point, self.start_point))
            self._hl_item._color = QColor(self.highlight_tool_color)
            self._hl_item.setFlags(
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
            self.scene.addItem(self._hl_item)
            self.current_item = self._hl_item
            # keep drawing — mouseRelease will finalize
            self.start_point = scene_pos
            return
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
            marker._scale = last_scale
            marker._bg_color = QColor(self.marker_color)
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
            self.handle_resize_logic(scene_pos, event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.is_dirty = True
            return

        if self.current_tool == "Eraser" and event.buttons() & Qt.MouseButton.LeftButton:
            self.erase_at(scene_pos)
            return

        if not self.current_item:
            super().mouseMoveEvent(event)
            return

        # Regular Drawing
        rect = QRectF(self.start_point, scene_pos).normalized()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Proportional drawing (Square/Circle)
            side = max(rect.width(), rect.height())
            rect = QRectF(self.start_point.x(), self.start_point.y(), 
                          side if scene_pos.x() > self.start_point.x() else -side,
                          side if scene_pos.y() > self.start_point.y() else -side).normalized()

        if self.current_tool in ["Rectangle", "Circle"]: self.current_item.setRect(rect)
        elif self.current_tool == "Highlight":
            if getattr(self, '_hl_item', None):
                self._hl_item.setRect(rect)
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
            self.current_item.setLine(QLineF(self.start_point, end_pos))
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
                self.current_item.setLine(QLineF(self.start_point, end_pos))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update_cursor_by_handle(None)
            self._pan_start = None
            return
        # Reset WIDTH drag state on release
        if self.resizing_item and hasattr(self.resizing_item, '_width_drag_active'):
            self.resizing_item._width_drag_active = False
        
        if self.current_tool == "Text" and self.start_point:
            _pos = QPointF(self.start_point)
            _col = QColor(self.stroke_color)
            _fsz = max(1, int(self.font_size))

            def _do_text_dialog():
                dlg = _TextInputDialog(_col, self.window())
                dlg.sz.setValue(_fsz)
                _set_dialog_on_top(dlg)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    txt, new_fsz, col, hl, hl_on, hl_pad, ol_on, ol_w, ol_col = dlg.result_data()
                    if txt.strip():
                        self.font_size = new_fsz
                        if hasattr(self.window(), 'spin'):
                            self.window().spin.blockSignals(True)
                            self.window().spin.setValue(new_fsz)
                            self.window().spin.blockSignals(False)
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
            dlg = _BubbleInputDialog(self.bubble_color, self)
            _set_dialog_on_top(dlg)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                txt, fg_col, bg_col = dlg.result_data()
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
        # Arrow: apply pen props after drawing
        elif self.current_tool == "Arrow" and self.current_item:
            pen = QPen(self.stroke_color, self.stroke_width)
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
        if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
            win = self.window()
            if hasattr(win, '_edit_text_item'):
                win._edit_text_item(item)
                return
        elif isinstance(item, FreehandItem):
            item._open_edit_dialog(event.globalPosition().toPoint())
            return
        elif isinstance(item, HighlightRectItem):
            dlg = HighlightEditDialog(item._color, self)
            _set_dialog_on_top(dlg)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_color = dlg.result_color()
                item._color = new_color
                item.setPen(QPen(Qt.PenStyle.NoPen))
                item.setBrush(QBrush(new_color))
                item.update()
                self.is_dirty = True
        elif isinstance(item, TextBubbleItem):
            dlg = _BubbleEditDialog(item._fg_color, item._bg_color, item._text, self)
            _set_dialog_on_top(dlg)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                txt, fg_col, bg_col = dlg.result_data()
                if txt.strip():
                    item._text = txt
                    item._fg_color = fg_col
                    item._bg_color = bg_col
                    item._auto_grow()
                    item.prepareGeometryChange()
                    item.update()
                    self.is_dirty = True
        elif isinstance(item, _MarkerItem):
            dlg = _MarkerEditDialog(
                QColor(item._bg_color),
                QColor(getattr(item, '_text_color', QColor(Qt.GlobalColor.white))),
                self)
            _set_dialog_on_top(dlg)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                item._bg_color   = dlg.bg_color
                item._text_color = dlg.text_color
                item.update()
                self.is_dirty = True
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        scene_pos = self.mapToScene(event.pos())  # QContextMenuEvent uses .pos(), not .position()
        item = self.scene.itemAt(scene_pos, self.transform())
        if item in (self.bg_item, self.crop_item) or item is None:
            super().contextMenuEvent(event)
            return
        if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
            menu = QMenu(self)
            edit_action = menu.addAction("✏️ Edit Text")
            dup_action  = menu.addAction("⧉ Duplicate")
            action = menu.exec(event.globalPos())
            if action == dup_action:
                dup = self._clone_item(item)
                if dup:
                    dup.setPos(item.pos() + QPointF(20, 20))
                    self.scene.addItem(dup)
                    self.is_dirty = True
                return
            if action == edit_action:
                win = self.window()
                if hasattr(win, '_edit_text_item'):
                    win._edit_text_item(item)
        elif isinstance(item, HighlightRectItem):
            menu = QMenu(self)
            edit_act = menu.addAction("✏️ Edit Highlight")
            dup_act  = menu.addAction("⧉ Duplicate")
            del_act  = menu.addAction("🗑️ Delete")
            action = menu.exec(event.globalPos())
            if action == dup_act:
                dup = self._clone_item(item)
                if dup:
                    dup.setPos(item.pos() + QPointF(20, 20))
                    self.scene.addItem(dup)
                    self.is_dirty = True
                return
            if action == edit_act:
                dlg = HighlightEditDialog(item._color, self)
                _set_dialog_on_top(dlg)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    new_color = dlg.result_color()
                    item._color = new_color
                    item.setPen(QPen(Qt.PenStyle.NoPen))
                    item.setBrush(QBrush(new_color))
                    item.update()
                    self.is_dirty = True
            elif action == del_act:
                self.scene.removeItem(item)
                self.is_dirty = True
            return
        elif isinstance(item, TextBubbleItem):
            menu = QMenu(self)
            edit_act = menu.addAction("✏️ Edit Bubble")
            dup_act  = menu.addAction("⧉ Duplicate")
            del_act  = menu.addAction("🗑️ Delete")
            action = menu.exec(event.globalPos())
            if action == dup_act:
                dup = self._clone_item(item)
                if dup:
                    dup.setPos(item.pos() + QPointF(20, 20))
                    self.scene.addItem(dup)
                    self.is_dirty = True
                return
            if action == edit_act:
                dlg = _BubbleEditDialog(item._fg_color, item._bg_color, item._text, self)
                _set_dialog_on_top(dlg)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    txt, fg_col, bg_col = dlg.result_data()
                    if txt.strip():
                        item._text = txt
                        item._fg_color = fg_col
                        item._bg_color = bg_col
                        item.prepareGeometryChange()
                        item.update()
                        self.is_dirty = True
            elif action == del_act:
                self.scene.removeItem(item)
                self.is_dirty = True
            return
        elif isinstance(item, _MarkerItem):
            menu = QMenu(self)
            edit_act = menu.addAction("✏️ Edit Marker")
            del_act  = menu.addAction("🗑️ Delete")
            action = menu.exec(event.globalPos())
            if action == edit_act:
                dlg = _MarkerEditDialog(
                    QColor(item._bg_color),
                    QColor(getattr(item, '_text_color', QColor(Qt.GlobalColor.white))),
                    self)
                _set_dialog_on_top(dlg)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    item._bg_color   = dlg.bg_color
                    item._text_color = dlg.text_color
                    item.update()
                    self.is_dirty = True
            elif action == del_act:
                self.scene.removeItem(item)
                self.is_dirty = True
        elif isinstance(item, FreehandItem):
            menu = QMenu(self)
            edit_act = menu.addAction("✏️ Edit")
            dup_act  = menu.addAction("⧉ Duplicate")
            del_act  = menu.addAction("🗑️ Delete")
            action = menu.exec(event.globalPos())
            if action == edit_act:
                item._open_edit_dialog(event.globalPos())
                self.is_dirty = True
            elif action == dup_act:
                dup = self._clone_item(item)
                if dup:
                    dup.setPos(item.pos() + QPointF(20, 20))
                    self.scene.addItem(dup)
                    self.is_dirty = True
            elif action == del_act:
                self.scene.removeItem(item)
                self.is_dirty = True
        else:
            menu = QMenu(self)
            dup_act = menu.addAction("⧉ Duplicate")
            action = menu.exec(event.globalPos())
            if action == dup_act:
                dup = self._clone_item(item)
                if dup:
                    dup.setPos(item.pos() + QPointF(20, 20))
                    self.scene.addItem(dup)
                    self.is_dirty = True

    def get_handle_at(self, pos):
        """Returns (item, handle_name) if mouse is over a resize handle of a selected item."""
        for item in self.scene.selectedItems():

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

                # Width handle (yellow dot below bottom edge) for Rect and Ellipse — check FIRST
                if isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
                    wp = item._width_handle_pos()
                    hit_radius = 14 / self.transform().m11()
                    if math.hypot(local_pos.x() - wp.x(), local_pos.y() - wp.y()) <= hit_radius:
                        return item, 'WIDTH'
                
                # Rotation handle — disabled for markers (they scale uniformly instead)
                if not isinstance(item, (CropOverlayItem, _MarkerItem)):
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
        elif handle in ['TL', 'BR']: self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif handle in ['TR', 'BL']: self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif handle in ['L', 'R']: self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif handle in ['T', 'B']: self.setCursor(Qt.CursorShape.SizeVerCursor)

    def handle_resize_logic(self, pos, proportional):
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
        local_pos = item.mapFromScene(pos)
        
        # Określamy punkt stały (przeciwległy do łapanego uchwytu), żeby figura nie "odfrunęła"
        fixed_local = QPointF()
        if 'L' in self.resize_handle: fixed_local.setX(old_rect.right())
        elif 'R' in self.resize_handle: fixed_local.setX(old_rect.left())
        else: fixed_local.setX(old_rect.center().x())

        if 'T' in self.resize_handle: fixed_local.setY(old_rect.bottom())
        elif 'B' in self.resize_handle: fixed_local.setY(old_rect.top())
        else: fixed_local.setY(old_rect.center().y())
        
        old_scene_fixed = item.mapToScene(fixed_local)

        # Obliczamy nowe wymiary (rect)
        left, top, right, bottom = old_rect.left(), old_rect.top(), old_rect.right(), old_rect.bottom()
        
        if 'L' in self.resize_handle: left = local_pos.x()
        if 'R' in self.resize_handle: right = local_pos.x()
        if 'T' in self.resize_handle: top = local_pos.y()
        if 'B' in self.resize_handle: bottom = local_pos.y()
        
        new_rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
        
        if proportional:
            side = max(new_rect.width(), new_rect.height())
            if 'L' in self.resize_handle: left = right - side
            else: right = left + side
            
            if 'T' in self.resize_handle: top = bottom - side
            else: bottom = top + side
            
            new_rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
            
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

        # Dark overlay outside the image
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(-500000, -500000, 1000000, 1000000))
        path.addRect(bg_rect)
        painter.fillPath(path, QColor(0, 0, 0, 120))

        # Blue dashed border around the image
        pen = QPen(QColor(137, 180, 250), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.GlobalColor.transparent)
        painter.drawRect(bg_rect)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        # Włączamy wysoką jakość rysowania, aby uniknąć rozmazania
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        for item in self.scene.selectedItems():
            if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, ResizablePixmapItem, FreehandItem)) and not isinstance(item, CropOverlayItem):
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
        item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | 
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        pen = QPen(self.stroke_color, self.stroke_width)
        brush = QBrush(self.fill_color if self.is_filled else Qt.GlobalColor.transparent)
        if isinstance(item, QGraphicsTextItem):
            item.setDefaultTextColor(self.stroke_color)
            item.setFont(QFont("Arial", int(self.font_size)))
        else:
            if hasattr(item, "setPen"): item.setPen(pen)
            if hasattr(item, "setBrush"): item.setBrush(brush)

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

        central = QWidget(); self.setCentralWidget(central)
        layout = QVBoxLayout(central)

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

        for n, i in [("Select", None), ("Crop", "📐"), ("Rectangle", "⬜"), ("Circle", "⭕"),
                     ("Line", "📏"), ("Arrow", "➡️"), ("Highlight", "🟨"), ("Freehand", "✏️"),
                     ("Bubble", "💬"), ("Text", "T"), ("Marker", "📍"), ("Eraser", "🧹")]:
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
            else:
                b.setText(i)
            b.clicked.connect(lambda ch, name=n: self.select_tool(name))
            tbar.addWidget(b); self.btns[n] = b
            
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
        self.btn_c = QPushButton("🎨🔧 Color / Set")
        self.btn_c.setFixedHeight(36)
        self.btn_c.setMinimumWidth(140)
        self.btn_c.clicked.connect(self.pick_color)
        pbar.addWidget(self.btn_c)
        
        self.btn_hc = QPushButton("Text Highlight")
        self.btn_hc.clicked.connect(self.pick_highlight_color)
        self.btn_hc.hide()
        pbar.addWidget(self.btn_hc)
        
        pbar.addWidget(QLabel("Size:"))

        self.spin = QSpinBox(); self.spin.setRange(1, 200); self.spin.setValue(3)
        self.spin.setToolTip("Font size for text tools / Stroke width for shape tools")
        self.spin.valueChanged.connect(self._on_size_spin_changed)
        pbar.addWidget(self.spin)

        # Hidden legacy stroke spinner — kept so existing update_live_props references don't break
        self.lbl_stroke = QLabel()
        self.lbl_stroke.hide()
        self.spin_stroke = QSpinBox(); self.spin_stroke.setRange(1, 100); self.spin_stroke.setValue(3)
        self.spin_stroke.hide()
        self.spin_stroke.valueChanged.connect(self.update_live_props)

        self.fill = QCheckBox("Fill Shape"); self.fill.stateChanged.connect(self.update_live_props)
        pbar.addWidget(self.fill)
        pbar.addStretch()
        layout.addLayout(pbar)

        self.canvas = EditorCanvas(pixmap)
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

    def select_tool(self, name):
        self.canvas.current_tool = name
        for n, b in self.btns.items(): b.setChecked(n == name)

        if name in ["Rectangle", "Circle"]:
            self.fill.show()
        elif name == "Select" and any(isinstance(i, (ResizableRectItem, ResizableEllipseItem)) for i in self.canvas.scene.selectedItems()):
            self.fill.show()
        else:
            self.fill.hide()

        # Text Highlight button removed — highlight is managed via the unified text dialog
        self.btn_hc.hide()

        # Size spinner is always visible — label changes based on tool context
        # (stroke_tools use it as border width, text tools as font size)
        self.spin.blockSignals(True)
        if name == "Text":
            self.spin.setValue(self.canvas.font_size)
        else:
            self.spin.setValue(self.canvas.stroke_width)
        self.spin.blockSignals(False)

        # Zarządzanie widocznością i cyklem życia nakładki Crop
        if name == "Crop":
            self.btn_apply_crop.show()
            self.canvas.scene.clearSelection()
            
            # Ustaw rozmiar sceny na twardo, aby odpowiadał wymiarom tła
            bg_rect = QRectF(self.canvas.bg_pixmap.rect())
            self.canvas.scene.setSceneRect(bg_rect)
            
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

        # ── Annotation mode: save the crop region with annotations, don't crop the canvas ──
        if self._annotation_mode:
            self.canvas.scene.removeItem(self.canvas.crop_item)
            self.canvas.crop_item = None
            self.btn_apply_crop.hide()
            self._finish_annotated_capture(crop_rect)
            return

        # ── Normal editor mode: crop or expand the canvas ──────────────────────
        bg_rect = self.canvas.bg_item.sceneBoundingRect()

        # Detect if the crop rect extends beyond the current image (= expand canvas)
        expand = not bg_rect.contains(crop_rect)

        if expand:
            # ── Expand Canvas mode ──────────────────────────────────────────────
            # Union of current image and the crop rect gives the new canvas bounds
            new_rect = bg_rect.united(crop_rect)

            # Offset: how much the image origin shifts inside the new canvas
            dx = bg_rect.x() - new_rect.x()
            dy = bg_rect.y() - new_rect.y()

            # Create new transparent pixmap (ARGB) and paint old image into it
            new_w = int(new_rect.width())
            new_h = int(new_rect.height())
            new_pixmap = QPixmap(new_w, new_h)
            new_pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(new_pixmap)
            painter.drawPixmap(int(dx), int(dy), self.canvas.bg_pixmap)
            painter.end()

            self.canvas.bg_pixmap = new_pixmap
            self.canvas.bg_item.setPixmap(new_pixmap)
            self.canvas.bg_item.setPos(new_rect.topLeft())

            # Shift all annotations to match the new origin
            for item in self.canvas.scene.items():
                if item != self.canvas.bg_item and item != self.canvas.crop_item:
                    item.setPos(item.pos().x() + dx, item.pos().y() + dy)

        else:
            # ── Crop mode (rectangle fully inside image) ────────────────────────
            dx = crop_rect.x()
            dy = crop_rect.y()

            cropped_pixmap = self.canvas.bg_pixmap.copy(
                crop_rect.translated(-bg_rect.topLeft()).toRect()
            )
            self.canvas.bg_pixmap = cropped_pixmap
            self.canvas.bg_item.setPixmap(cropped_pixmap)
            self.canvas.bg_item.setPos(crop_rect.topLeft())

            # Shift all annotations so they stay aligned with the background
            for item in self.canvas.scene.items():
                if item != self.canvas.bg_item and item != self.canvas.crop_item:
                    item.setPos(item.pos().x() - dx, item.pos().y() - dy)

        # Remove the crop overlay
        self.canvas.scene.removeItem(self.canvas.crop_item)
        self.canvas.crop_item = None
        self.btn_apply_crop.hide()

        # Mark as dirty and return to Select tool
        self.canvas.is_dirty = True
        self.select_tool("Select")

    def pick_color(self):
        selected = self.canvas.scene.selectedItems()

        # If an item with a dedicated edit dialog is selected, open it
        if selected:
            item = selected[0]
            if isinstance(item, HighlightRectItem):
                dlg = HighlightEditDialog(item._color, self)
                dlg.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    new_color = dlg.result_color()
                    item._color = new_color
                    item.setPen(QPen(Qt.PenStyle.NoPen))
                    item.setBrush(QBrush(new_color))
                    item.update()
                    self.canvas.is_dirty = True
                return
            if isinstance(item, TextBubbleItem):
                dlg = _BubbleEditDialog(item._fg_color, item._bg_color, item._text, self)
                dlg.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    txt, fg_col, bg_col = dlg.result_data()
                    if txt.strip():
                        item._text = txt
                        item._fg_color = fg_col
                        item._bg_color = bg_col
                        item._auto_grow()
                        item.prepareGeometryChange()
                        item.update()
                        self.canvas.is_dirty = True
                return
            if isinstance(item, _MarkerItem):
                dlg = _MarkerEditDialog(
                    QColor(item._bg_color),
                    QColor(getattr(item, '_text_color', QColor(Qt.GlobalColor.white))),
                    self)
                dlg.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    item._bg_color   = dlg.bg_color
                    item._text_color = dlg.text_color
                    item.update()
                    self.canvas.is_dirty = True
                return
            if isinstance(item, FreehandItem):
                dlg = FreehandEditDialog(item.pen().width(), item.pen().color(), self)
                dlg.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    new_width, new_color = dlg.result_data()
                    pen = item.pen()
                    pen.setWidth(new_width)
                    pen.setColor(new_color)
                    item.setPen(pen)
                    item.update()
                    self.canvas.stroke_width = new_width
                    self.spin.blockSignals(True)
                    self.spin.setValue(new_width)
                    self.spin.blockSignals(False)
                    self.canvas.is_dirty = True
                return
            if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
                self._edit_text_item(item)
                return

        # No special item selected — fall back to highlight tool color or generic picker
        if not selected and self.canvas.current_tool == "Highlight":
            dlg = HighlightEditDialog(self.canvas.highlight_tool_color, self)
            dlg.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.canvas.highlight_tool_color = dlg.result_color()
                self.canvas.is_dirty = True
            return

        # Pick the right source color for the current tool so the dialog opens
        # with whatever alpha the user last chose — not a reset to 0.
        if self.canvas.current_tool == "Marker":
            init_col = QColor(self.canvas.marker_color)
        elif self.canvas.current_tool == "Bubble":
            init_col = QColor(self.canvas.bubble_color)
        else:
            init_col = QColor(self.canvas.stroke_color)

        # Only default to fully opaque when alpha was never set (0 = uninitialised).
        if init_col.alpha() == 0:
            init_col.setAlpha(255)

        # Use _show_color_dialog so the Linux alpha=0 bug fix is applied consistently.
        c = _show_color_dialog(init_col, self, alpha=True)
        if c is not None and c.isValid():
            # Save color into the correct per-tool slot
            if self.canvas.current_tool == "Marker":
                self.canvas.marker_color = c
            elif self.canvas.current_tool == "Bubble":
                self.canvas.bubble_color = c
            else:
                self.canvas.stroke_color = c
                self.canvas.fill_color = c

            # Update color preview button
            rgba = f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alphaF()})"
            self.btn_c.setStyleSheet(f"background-color: {rgba}; border: 1px solid #888;")
            self.update_live_props()

            # Live-apply color to all currently selected items
            for item in self.canvas.scene.selectedItems():
                if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
                    item.setDefaultTextColor(c)
                    self.canvas.is_dirty = True
                elif isinstance(item, HighlightRectItem):
                    pass  # Highlight shape has its own dialog
                else:
                    if hasattr(item, 'setPen') and hasattr(item, 'pen'):
                        pen = item.pen()
                        pen.setColor(c)
                        item.setPen(pen)
                    if hasattr(item, 'setBrush') and self.canvas.is_filled and isinstance(item, (ResizableRectItem, ResizableEllipseItem)):
                        item.setBrush(QBrush(c))
                    item.update()
                    self.canvas.is_dirty = True

    def update_live_props(self):
        self.canvas.stroke_width = self.spin_stroke.value()
        self.canvas.font_size = self.spin.value()
        self.canvas.is_filled = self.fill.isChecked()
        
        if self.canvas.is_filled:
            self.canvas.fill_color = QColor(self.canvas.stroke_color)
        else:
            self.canvas.fill_color = QColor(0, 0, 0, 0)
            
        for item in self.canvas.scene.selectedItems():
            if hasattr(item, 'setBrush') and not isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
                brush = QBrush(self.canvas.fill_color if self.canvas.is_filled else Qt.GlobalColor.transparent)
                item.setBrush(brush)
                item.update()
                self.canvas.is_dirty = True

    def _on_size_spin_changed(self, value):
        """Called when the user manually changes the Size spinner.
        Updates canvas properties AND live-updates any selected items."""
        selected = self.canvas.scene.selectedItems()
        text_tools = {"Text"}
        is_text_tool = self.canvas.current_tool in text_tools

        if selected:
            for item in selected:
                if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
                    # Live-update font size
                    f = item.font()
                    f.setPointSize(max(1, value))
                    item.setFont(f)
                    self.canvas.is_dirty = True
                elif isinstance(item, FreehandItem):
                    # Live-update stroke width for freehand items
                    pen = item.pen()
                    pen.setWidth(max(1, value))
                    item.setPen(pen)
                    item.update()
                    self.canvas.is_dirty = True
                elif hasattr(item, 'pen') and hasattr(item, 'setPen'):
                    # Live-update stroke width for shape/line/arrow items
                    pen = item.pen()
                    pen.setWidth(max(1, value))
                    item.setPen(pen)
                    if hasattr(item, 'prepareGeometryChange'):
                        item.prepareGeometryChange()
                    item.update()
                    self.canvas.is_dirty = True

        # Always sync canvas props for newly drawn items
        self.canvas.stroke_width = value
        self.spin_stroke.setValue(value)   # keep hidden legacy spinner in sync
        self.canvas.font_size = value
        self.canvas.is_filled = self.fill.isChecked()

    def _sync_spin_from_selection(self):
        """Read stroke width or font size from selected items and update the Size spinner."""
        selected = self.canvas.scene.selectedItems()

        # Update Fill Checkbox visibility and state
        if self.canvas.current_tool == "Select":
            if selected and isinstance(selected[0], (ResizableRectItem, ResizableEllipseItem)):
                self.fill.show()
                brush = selected[0].brush()
                is_filled = brush.style() != Qt.BrushStyle.NoBrush and brush.color().alpha() > 0
                self.fill.blockSignals(True)
                self.fill.setChecked(is_filled)
                self.fill.blockSignals(False)
                self.canvas.is_filled = is_filled
                self.canvas.fill_color = brush.color() if is_filled else QColor(0, 0, 0, 0)
            else:
                self.fill.hide()

            # Text Highlight button removed — managed via unified text dialog
            self.btn_hc.hide()

        for item in selected:
            if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
                # Text item — read font size
                size = item.font().pointSize()
                if size > 0:
                    self.spin.blockSignals(True)
                    self.spin.setValue(size)
                    self.spin.blockSignals(False)
                    self.canvas.font_size = size
                return
            elif isinstance(item, FreehandItem):
                # Freehand item — read pen width
                width = item.pen().width()
                if width > 0:
                    self.spin.blockSignals(True)
                    self.spin.setValue(width)
                    self.spin.blockSignals(False)
                    self.canvas.stroke_width = width
                    self.spin_stroke.setValue(width)
                return
            elif hasattr(item, 'pen') and callable(item.pen):
                # Shape / line / arrow — read pen width
                width = item.pen().width()
                if width > 0:
                    self.spin.blockSignals(True)
                    self.spin.setValue(width)
                    self.spin.blockSignals(False)
                    self.canvas.stroke_width = width
                    self.spin_stroke.setValue(width)
                return

        # No selection — just sync fill colour
        if not self.canvas.is_filled:
            self.canvas.fill_color = QColor(0, 0, 0, 0)
        else:
            self.canvas.fill_color = self.canvas.stroke_color
    def _edit_text_item(self, item):
        """Open the unified text edit dialog (same as Capture Region toolbar) for a HighlightTextItem."""
        dlg = _TextInputDialog(item.defaultTextColor(), self)
        dlg.edit.setPlainText(item.toPlainText())
        dlg.sz.setValue(item.font().pointSize())
        if hasattr(item, 'highlight_color'):
            dlg.highlight = QColor(item.highlight_color)
        dlg.chk_hl.setChecked(getattr(item, 'highlight_enabled', True))
        dlg.hl_pad.setValue(getattr(item, 'highlight_padding', 0))
        dlg.chk_ol.setChecked(getattr(item, 'outline_enabled', False))
        dlg.ol_width.setValue(getattr(item, 'outline_width', 2))
        dlg.outline_color = QColor(getattr(item, 'outline_color', QColor(0, 0, 0)))
        dlg._update_btn_color()
        dlg.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            txt, fsz, col, hl, hl_on, hl_pad, ol_on, ol_w, ol_col = dlg.result_data()
            if txt.strip():
                item.setPlainText(txt)
                item.setFont(QFont("Arial", fsz))
                item.setDefaultTextColor(col)
                item.highlight_color   = hl
                item.highlight_enabled = hl_on
                item.highlight_padding = hl_pad
                item.outline_enabled   = ol_on
                item.outline_width     = ol_w
                item.outline_color     = ol_col
                item.update()
                self.canvas.font_size  = fsz
                if hasattr(self, 'spin'):
                    self.spin.blockSignals(True)
                    self.spin.setValue(fsz)
                    self.spin.blockSignals(False)
                self.canvas.is_dirty = True
            else:
                self.canvas.scene.removeItem(item)
                self.canvas.is_dirty = True

    def pick_highlight_color(self):
        # Build initial color: preserve user's previously chosen alpha.
        # Only default to a visible alpha (180) when uninitialised (alpha == 0).
        init_col = QColor(self.canvas.text_highlight_color)
        if init_col.alpha() == 0:
            init_col.setAlpha(180)

        # Use _show_color_dialog so the Linux alpha=0 bug fix is applied consistently.
        c = _show_color_dialog(init_col, self, alpha=True)
        if c is not None and c.isValid():
            self.canvas.text_highlight_color = c
            rgba = f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alphaF()})"
            self.btn_hc.setStyleSheet(f"background-color: {rgba}; border: 1px solid #888;")

            # Immediately apply highlight color to all selected text items
            for item in self.canvas.scene.selectedItems():
                if isinstance(item, HighlightTextItem):
                    item.highlight_color = c
                    item.update()
            self.canvas.is_dirty = True

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
                
                self.canvas.scene.addItem(item)
                self.canvas.is_dirty = True
                self.select_tool("Select")
    
    
    def _delete_selected(self):
        """Delete selected items (keyboard shortcut helper)."""
        for item in self.canvas.scene.selectedItems():
            if item not in (self.canvas.bg_item, getattr(self.canvas, 'crop_item', None)):
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
                                 source=crop_rect_scene)
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
                                 source=bg_rect)
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
        self.setWindowTitle("OCR / QR Toolbox")
        self.setMinimumSize(720, 480)
        self.setWindowFlags(Qt.WindowType.Window)
        self._ocr_result_sig.connect(self._on_ocr_result)
        self._qr_result_sig.connect(self._on_qr_result)
        self._build()

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

        top_bar.addWidget(btn_scan_text)
        top_bar.addWidget(btn_scan_qr)
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
            mw._prog_msg_sig.emit(f"Running {engine_name.upper()}…")
            if engine_name == "paddleocr":
                txt = mw.engine._ocr_paddleocr(path)
            elif engine_name == "easyocr":
                txt = mw.engine._ocr_easyocr(path)
            else:
                txt = mw.engine._ocr_tesseract(path)
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

        sv = QPushButton("💾 Save Settings"); sv.setObjectName("cap_btn")
        sv.setFixedHeight(36); sv.clicked.connect(self._save_settings)
        lay.addWidget(sv)

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
        from PySide6.QtWidgets import QRadioButton, QButtonGroup
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
        menu = QMenu(); menu.setStyleSheet(CSS)

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
        
        # Safely determine the active OCR engine using QButtonGroup ID
        engine_id = self._ocr_grp.checkedId()
        if engine_id == 1:
            ocr_engine = "easyocr"
        elif engine_id == 2:
            ocr_engine = "tesseract"
        else:
            ocr_engine = "paddleocr"
            
        self.config.data["ocr_engine"]       = ocr_engine
        self.config.data["after_capture"]    = {k: cb.isChecked() for k, cb in self._ac.items()}
        self.config.data["notifications"]    = {k: cb.isChecked() for k, cb in self._nc.items()}
        
        # Perform a single, safe disk write operation
        self.config.save()
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
        if msg == "__show_win__":
            self.show_win()
            return
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
        "ocr_qr_toolbox":           "act_ocr_qr_toolbox",
        "capture_fullscreen":       "act_fullscreen",
        "video_converter":          "act_video_converter",
    }

    def _hotkeys_start(self):
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
                if engine == "paddleocr":
                    txt = self.engine._ocr_paddleocr(path)
                elif engine == "easyocr":
                    txt = self.engine._ocr_easyocr(path)
                else:
                    txt = self.engine._ocr_tesseract(path)
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
                        self._ocr_done_sig.emit("Nie wykryto kodu QR w zrobionym screenie.", "QR Code Result")
                except Exception as e:
                    self._ocr_done_sig.emit(f"Wystąpił błąd podczas dekodowania: {e}", "QR Code Result")
            threading.Thread(target=do_auto_qr, daemon=True).start()

        # 3. Sprawdzenie czy użytkownik chce otworzyć edytor
        if after.get("open_in_editor"):
            # Jeśli nie mamy pixmapy w pamięci, ładujemy z pliku
            if not pixmap and os.path.exists(path):
                pixmap = QPixmap(path)
            
            if pixmap:
                # Otwieramy okno edytora
                self.editor = ImageEditorWindow(pixmap, self.save_edited_image, self.config.get("save_folder", ""), self)
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
        self._status("Zaznacz obszar do przewijania (Scrolling capture)…")
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

        self._status("Scrolling capture — przewijanie…")

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
            self._prog_msg_sig.emit(f"Running {engine_name.upper()}…")
            if engine_name == "paddleocr":
                txt = self.engine._ocr_paddleocr(path)
            elif engine_name == "easyocr":
                txt = self.engine._ocr_easyocr(path)
            else:
                txt = self.engine._ocr_tesseract(path)
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
        screenshot_dir = self.config.get("save_folder", str(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)))
        
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
        """Saves the output from the editor to the screenshots folder."""
        save_dir = Path(self.config.get("save_folder", "screenshots"))
        save_dir.mkdir(parents=True, exist_ok=True)
        
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


def _svg_icon(svg_str: str, size: int = 32) -> QIcon:
    """Render an SVG string to a QIcon of the given pixel size."""
    renderer = QSvgRenderer(svg_str.encode())
    pm = QPixmap(size, size)
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

# SVG source for the Select cursor tool
_SVG_SELECT = """<svg width="77.068" height="77.068" version="1.1" viewBox="0 0 18.496 18.496" xmlns="http://www.w3.org/2000/svg">
 <path d="m3.8616 1.6312v15.048l3.9126-3.9126 3.0097 4.665 2.3325-1.5048-2.7242-4.3081 4.7557-0.35688z" fill="#fff" stroke="#000" stroke-width="1.5048"/>
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
