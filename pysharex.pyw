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

import urllib.request # Potrzebne do otwierania z sieci
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QSystemTrayIcon, QMenu, QFileDialog, QDialog, QLineEdit,
    QComboBox, QCheckBox, QGroupBox, QScrollArea, QFrame,
    QMessageBox, QListWidget, QListWidgetItem,
    QKeySequenceEdit, QDialogButtonBox, QSpinBox, QTabWidget,
    QTextEdit, QSizePolicy, QStackedWidget, QColorDialog, QInputDialog,
    QGraphicsScene, QGraphicsView, QGraphicsItem, QGraphicsRectItem, 
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsTextItem
)
from PyQt6.QtCore import (
    QPointF, Qt, QThread, pyqtSignal, QTimer, QSize, QRect, QPoint,
    QStandardPaths, QElapsedTimer, QLineF, QRectF
)
from PyQt6.QtGui import (
    QIcon, QKeySequence, QAction, QMouseEvent, QPixmap, QPainter, QColor,
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

class FFmpegConverterThread(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)

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

from PyQt6.QtWidgets import (QGridLayout, QFormLayout, QProgressBar)
from PyQt6.QtWidgets import QSlider

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
        {"name": "OCR – Recognize text",     "action": "ocr_text",                "shortcut": "Ctrl+Alt+O",             "enabled": True},
        {"name": "OCR – Recognize code",     "action": "ocr_code",                "shortcut": "Ctrl+Alt+K",             "enabled": True},
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


from PyQt6.QtWidgets import QInputDialog, QGraphicsScene, QGraphicsView, QGraphicsItem, \
    QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem, \
    QGraphicsTextItem

from PyQt6.QtWidgets import QColorDialog, QSpinBox, QCheckBox


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

class ImageEditorStartDialog(QDialog):
    def __init__(self, parent=None, default_dir=""):
        super().__init__(parent)
        self.setWindowTitle("Image Editor - Select Source")
        self.setFixedSize(320, 180)
        self.result_image = None
        self.default_dir = default_dir
        
        layout = QVBoxLayout(self)
        btn_file = QPushButton("📂 Open screenshot file")
        btn_clipboard = QPushButton("📋 Open screenshot from clipboard")
        btn_web = QPushButton("🌐 Open image from web")
        
        btn_file.clicked.connect(self.open_file)
        btn_clipboard.clicked.connect(self.open_clipboard)
        btn_web.clicked.connect(self.open_web)
        
        layout.addWidget(btn_file)
        layout.addWidget(btn_clipboard)
        layout.addWidget(btn_web)

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
                else: raise Exception("Invalid image")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed: {e}")

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
        
        from PyQt6.QtGui import QTransform
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

    def boundingRect(self):
        return self._rect.adjusted(-5, -50, 5, 5)

    def paint(self, painter, option, widget=None):
        from PyQt6.QtWidgets import QStyle
        option.state &= ~QStyle.StateFlag.State_Selected
        painter.setPen(self.pen())
        painter.drawPath(self.path())
        
        if self.isSelected():
            pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(self._rect)


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
        
        # Rysujemy uchwyty tylko jeśli zaznaczone
        if self.isSelected():
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QBrush(Qt.GlobalColor.white))
            painter.setPen(QPen(Qt.GlobalColor.blue, 1.5))
            # Rozmiar uchwytu stały niezależnie od zoomu
            s = 10 / (self.canvas.transform().m11() if self.canvas else 1)
            painter.drawEllipse(self.line().p1(), s/2, s/2)
            painter.drawEllipse(self.line().p2(), s/2, s/2)

    def mousePressEvent(self, event):
        p = event.pos()
        p1, p2 = self.line().p1(), self.line().p2()
        # Detekcja kliknięcia w uchwyt
        dist = 15 / (self.canvas.transform().m11() if self.canvas else 1)
        if (p - p1).manhattanLength() < dist: self.active_handle = 'p1'
        elif (p - p2).manhattanLength() < dist: self.active_handle = 'p2'
        else: self.active_handle = None; super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.active_handle:
            self.prepareGeometryChange() # Usuwa smużenie
            line = self.line()
            new_pos = event.pos()
            
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                anchor = line.p2() if self.active_handle == 'p1' else line.p1()
                dx, dy = new_pos.x() - anchor.x(), new_pos.y() - anchor.y()
                angle = math.atan2(dy, dx)
                # Angle snapping co 45 stopni
                snapped_angle = round(math.degrees(angle) / 45) * 45
                dist = math.hypot(dx, dy)
                new_pos = QPointF(anchor.x() + dist * math.cos(math.radians(snapped_angle)),
                                 anchor.y() + dist * math.sin(math.radians(snapped_angle)))
            
            if self.active_handle == 'p1': line.setP1(new_pos)
            else: line.setP2(new_pos)
            self.setLine(line)
        else:
            super().mouseMoveEvent(event)



class HighlightTextItem(QGraphicsTextItem):

    def paint(self, painter, option, widget=None):
        # Rysowanie tła (podświetlenia) przed narysowaniem liter
        if hasattr(self, 'highlight_color') and self.highlight_color and self.highlight_color.alpha() > 0:
            painter.save()
            painter.setBrush(QBrush(self.highlight_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(self.boundingRect())
            painter.restore()
        super().paint(painter, option, widget)

class ResizableRectItem(QGraphicsRectItem):
    def boundingRect(self):
        # Powiększamy obszar odświeżania w górę o 50 pikseli, aby pomieścić linię i uchwyt obrotu
        return super().boundingRect().adjusted(-5, -50, 5, 5)

    def paint(self, painter, option, widget=None):
        from PyQt6.QtWidgets import QStyle
        # Wyłączamy domyślną ramkę zaznaczenia rysowaną przez Qt (bo bazuje na powiększonym boundingRect)
        option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, widget)
        
        # Rysujemy własną ramkę idealnie na krawędziach figury
        if self.isSelected():
            pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(self.rect())

class ResizableEllipseItem(QGraphicsEllipseItem):
    def boundingRect(self):
        return super().boundingRect().adjusted(-5, -50, 5, 5)

    def paint(self, painter, option, widget=None):
        from PyQt6.QtWidgets import QStyle
        option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, widget)
        
        if self.isSelected():
            pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(self.rect())

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
            pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawRect(self.rect())
    def boundingRect(self):
        return super().boundingRect().adjusted(-5, -50, 5, 5)

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
        self.font_size = 14
        self.is_filled = False
        self.text_highlight_color = QColor(255, 255, 0, 0)
        self._pan_start = None
        
        # Massive sceneRect allows infinite panning regardless of zoom
        bg_rect = QRectF(pixmap.rect())
        self.scene.setSceneRect(bg_rect.center().x() - 50000, bg_rect.center().y() - 50000, 100000, 100000) # Domyślnie przeźroczysty

    def keyPressEvent(self, event):
        """Handle Delete key to remove selected items."""
        if event.key() == Qt.Key.Key_Delete:
            for item in self.scene.selectedItems():
                if item != self.bg_item and item != self.crop_item:
                    self.scene.removeItem(item)
                    self.is_dirty = True
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if event.angleDelta().y() > 0 else 0.8
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        self.setFocus() # Ensure canvas has focus for keyboard events
        scene_pos = self.mapToScene(event.pos())
        
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._pan_start = event.pos()
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
        
        # Właściwości i dodanie do sceny wykonujemy tylko raz dla wszystkich narzędzi
        if self.current_item:
            self.apply_props(self.current_item)
            self.scene.addItem(self.current_item)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.MiddleButton and getattr(self, '_pan_start', None) is not None:
            delta = event.pos() - self._pan_start
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_start = event.pos()
            return

        scene_pos = self.mapToScene(event.pos())
        
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
        elif self.current_tool == "Line": self.current_item.setLine(QLineF(self.start_point, scene_pos))
        elif self.current_tool == "Freehand":
            if self.current_item and getattr(self, '_freehand_path', None) is not None:
                new_pos = scene_pos
                if self._freehand_path.currentPosition() != new_pos:
                    self._freehand_path.lineTo(new_pos)
                    self.current_item.setPath(self._freehand_path)
                    if hasattr(self.current_item, 'update_base_path'):
                        self.current_item.update_base_path()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update_cursor_by_handle(None)
            self._pan_start = None
            return
        
        if self.current_tool == "Text" and self.start_point:
            txt, ok = QInputDialog.getMultiLineText(self, "Text", "Enter text:", "")
            if ok and txt:
                item = HighlightTextItem(txt, self.text_highlight_color)
                item.setPos(self.start_point); self.apply_props(item)
                self.scene.addItem(item); self.is_dirty = True

        self.current_item = None
        self.resizing_item = None
        self._freehand_path = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        scene_pos = self.mapToScene(event.pos())
        item = self.scene.itemAt(scene_pos, self.transform())
        if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
            txt, ok = QInputDialog.getMultiLineText(self, "Edit Text", "Update text:", item.toPlainText())
            if ok and txt:
                item.setPlainText(txt)
                self.is_dirty = True
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        scene_pos = self.mapToScene(event.pos())
        item = self.scene.itemAt(scene_pos, self.transform())
        if isinstance(item, (HighlightTextItem, QGraphicsTextItem)):
            menu = QMenu(self)
            edit_action = menu.addAction("✏️ Edit Text")
            action = menu.exec(event.globalPos())
            if action == edit_action:
                txt, ok = QInputDialog.getMultiLineText(self, "Edit Text", "Update text:", item.toPlainText())
                if ok and txt:
                    item.setPlainText(txt)
                    self.is_dirty = True
        else:
            super().contextMenuEvent(event)
        if item and item != self.bg_item:
            menu = QMenu(self)
            
            # Jeśli obiekt jest obrócony, pokaż opcję resetu
            if abs(item.rotation()) > 0.1:
                reset_rot = menu.addAction("🔄 Reset rotation")
                reset_rot.triggered.connect(lambda: item.setRotation(0))
                menu.addSeparator()

            del_act = menu.addAction("Delete")
            del_act.triggered.connect(lambda: self.scene.removeItem(item))
            
            # Pobieranie koloru dla menu (zachowanie Twojej logiki)
            curr_col = QColor(Qt.GlobalColor.white)
            if hasattr(item, 'pen'): curr_col = item.pen().color()
            
            color_act = menu.addAction("Change Color")
            color_act.triggered.connect(lambda: self._change_item_color(item, curr_col))
            
            menu.exec(event.globalPos())
        else:
            super().contextMenuEvent(event)

    def get_handle_at(self, pos):
        """Returns (item, handle_name) if mouse is over a resize handle of a selected item."""
        for item in self.scene.selectedItems():
   
             if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem, CropOverlayItem, ResizablePixmapItem, FreehandItem)):
                # Używamy lokalnych współrzędnych, żeby obrót nie psuł wykrywania krawędzi
                local_pos = item.mapFromScene(pos)
                rect = item.rect()
                m = 10 / self.transform().m11() # Scale-aware margin
                
                # Detekcja uchwytu obrotu (tylko dla normalnych obiektów, nie dla Crop)
                if not isinstance(item, CropOverlayItem):
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
        elif handle in ['TL', 'BR']: self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif handle in ['TR', 'BL']: self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif handle in ['L', 'R']: self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif handle in ['T', 'B']: self.setCursor(Qt.CursorShape.SizeVerCursor)

    def handle_resize_logic(self, pos, proportional):
        import math
        item = self.resizing_item
        
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

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        # Base visual boundaries on the image, not the massive sceneRect
        bg_rect = self.bg_item.sceneBoundingRect() if hasattr(self, 'bg_item') and self.bg_item else self.scene.sceneRect()
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(QRectF(-500000, -500000, 1000000, 1000000)) 
        path.addRect(bg_rect)                             
        painter.fillPath(path, QColor(0, 0, 0, 120)) 
        
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
    def __init__(self, pixmap, save_callback, default_dir="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("PyshareX Image Editor")
        self.setWindowIcon(load_app_icon())  # Naprawa ikonki okna
        self.save_callback = save_callback
        self.default_dir = default_dir
        self.saved = False
        
        central = QWidget(); self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Row 1: Tools
        tbar = QHBoxLayout()
        self.btns = {}
        for n, i in [("Select", "🖱️"), ("Crop", "📐"), ("Rectangle", "⬜"), ("Circle", "⭕"), 
                     ("Line", "📏"), ("Freehand", "✏️"), ("Text", "T"), ("Eraser", "🧹")]:
            b = QPushButton(i); b.setCheckable(True)
            b.setFixedSize(40, 40)  # Większy stały rozmiar
            # Styl: usunięcie marginesów i wyśrodkowanie tekstu/emoji
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
        self.btn_c = QPushButton("Color / Alpha")
        self.btn_c.clicked.connect(self.pick_color)
        pbar.addWidget(self.btn_c)
        
        self.btn_hc = QPushButton("Highlight")
        self.btn_hc.clicked.connect(self.pick_highlight_color)
        pbar.addWidget(self.btn_hc)
        
        pbar.addWidget(QLabel("Size:"))
        
        self.spin = QSpinBox(); self.spin.setRange(1,100); self.spin.setValue(3)
        self.spin.valueChanged.connect(self.update_live_props)
        pbar.addWidget(self.spin)
        self.fill = QCheckBox("Fill Shape"); self.fill.stateChanged.connect(self.update_live_props)
        pbar.addWidget(self.fill)
        pbar.addStretch()
        layout.addLayout(pbar)

        self.canvas = EditorCanvas(pixmap)
        layout.addWidget(self.canvas)
        self.showMaximized()

        # Fit image to fill the canvas view on startup (after window is fully rendered)
        def _fit_on_open():
            bg = self.canvas.bg_item.sceneBoundingRect()
            self.canvas.fitInView(bg, Qt.AspectRatioMode.KeepAspectRatio)
            self.canvas.centerOn(self.canvas.bg_item)

        QTimer.singleShot(0, _fit_on_open)
        
        self.select_tool("Select")

    def select_tool(self, name):
        self.canvas.current_tool = name
        for n, b in self.btns.items(): b.setChecked(n == name)
        
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

        # 1. Pobieramy docelowy obszar kadrowania (zaokrąglony do pełnych pikseli)
        crop_rect = self.canvas.crop_item.rect().toRect()
        
        # 2. Renderujemy aktualny stan edytora do QImage
        self.canvas.crop_item.hide()
        self.canvas.scene.clearSelection()
        
        # Render only the cropped area to avoid massive memory usage
        from PyQt6.QtCore import QRect
        cropped_img = QImage(QRect(0, 0, crop_rect.width(), crop_rect.height()).size(), QImage.Format.Format_ARGB32)
        cropped_img.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(cropped_img)
        self.canvas.scene.render(painter, target=QRectF(cropped_img.rect()), source=QRectF(crop_rect))
        painter.end()

        new_pixmap = QPixmap.fromImage(cropped_img)

        self.canvas.scene.clear()
        self.canvas.bg_pixmap = new_pixmap
        self.canvas.bg_item = self.canvas.scene.addPixmap(new_pixmap)
        self.canvas.bg_item.setZValue(-100)
        
        # Restore massive sceneRect for free panning
        bg_rect = QRectF(new_pixmap.rect())
        self.canvas.scene.setSceneRect(bg_rect.center().x() - 50000, bg_rect.center().y() - 50000, 100000, 100000)
        
        self.canvas.crop_item = None
        self.canvas.is_dirty = True
        self.select_tool("Select")

    def pick_color(self):
        # Tworzymy instancję okna zamiast metody statycznej
        dialog = QColorDialog(self.canvas.stroke_color, self)
        dialog.setWindowTitle("Pick Color & Alpha")
        
        # Kluczowa linia: ShowAlphaChannel dodaje suwak, DontUseNativeDialog wyłącza stare okno Windowsa
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        
        if dialog.exec():
            c = dialog.selectedColor()
            if c.isValid():
                self.canvas.stroke_color = c
                self.canvas.fill_color = c
                
                # Odświeżamy podgląd na przycisku (obsługa przezroczystości w podglądzie)
                rgba = f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alphaF()})"
                self.btn_c.setStyleSheet(f"background-color: {rgba}; border: 1px solid #888;")
                self.update_live_props()

    def update_live_props(self):
        self.canvas.stroke_width = self.spin.value()
        self.canvas.font_size = self.spin.value()
        self.canvas.is_filled = self.fill.isChecked()
        
        # Jeśli użytkownik nie chce wypełnienia, ustawiamy kolor wypełnienia na przezroczysty
        # ale jeśli CHCE, to bierzemy kolor wybrany w pick_color (który ma już w sobie Alpha)
        if not self.canvas.is_filled:
            self.canvas.fill_color = QColor(0, 0, 0, 0)
        else:
            self.canvas.fill_color = self.canvas.stroke_color

        for item in self.canvas.scene.selectedItems():
            self.canvas.apply_props(item)
        self.canvas.is_dirty = True
        for item in self.canvas.scene.selectedItems():
            self.canvas.apply_props(item)
            self.canvas.is_dirty = True
    def pick_highlight_color(self):
        dialog = QColorDialog(self.canvas.text_highlight_color, self)
        dialog.setWindowTitle("Pick Text Highlight Color & Alpha")
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        
        if dialog.exec():
            c = dialog.selectedColor()
            if c.isValid():
                self.canvas.text_highlight_color = c
                rgba = f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alphaF()})"
                self.btn_hc.setStyleSheet(f"background-color: {rgba}; border: 1px solid #888;")
                
                # Natychmiast aplikuj tło do zaznaczonych tekstów
                for item in self.canvas.scene.selectedItems():
                    if isinstance(item, HighlightTextItem):
                        item.highlight_color = c
                        item.update()
                self.canvas.is_dirty = True

    def import_image(self):
        from PyQt6.QtWidgets import QFileDialog
        from PyQt6.QtGui import QPixmap
        
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
    
    
    def save_default(self):
        img = self.render_scene()
        self.save_callback(img)
        self.saved = True
        self.close()
    
    def copy_to_clipboard(self):
        self.canvas.scene.clearSelection()
        rect = self.canvas.scene.sceneRect()
        pixmap = QPixmap(int(rect.width()), int(rect.height()))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.canvas.scene.render(painter)
        painter.end()
        QApplication.clipboard().setPixmap(pixmap)
        QMessageBox.information(self, "Success", "Image copied to clipboard!")


    
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

    def render_scene(self):
        self.canvas.scene.clearSelection()
        rect = self.canvas.scene.itemsBoundingRect()
        img = QImage(rect.size().toSize(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img); self.canvas.scene.render(p); p.end()
        return img

    def closeEvent(self, event):
        if self.canvas.is_dirty and not self.saved:
            res = QMessageBox.warning(self, "Unsaved Changes", "Quit without saving?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if res == QMessageBox.StandardButton.Yes: event.accept()
            else: event.ignore()
        else: event.accept()



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
        items = [("🔤 OCR – text",       self.act_ocr_text),
                 ("🔳 OCR – code",       self.act_ocr_code),
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
        "video_converter":          "act_video_converter",
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
        self._add_hist(path)
        notify(self.config, path, pixmap=pixmap)
        
        after = self.config.get("after_capture", {})
        
        # 1. Automatyczne rozpoznawanie tekstu (OCR)
        if after.get("ocr_recognize"):
            def do_auto_ocr():
                engine = self.config.get("ocr_engine", "easyocr")
                if engine == "easyocr":
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
