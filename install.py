#!/usr/bin/env python3
"""
PysharX - dependency installer.

Run it with the SAME Python you will use to start the program:

    python3 install.py              # full install (pip packages + system tools)
    python3 install.py --no-system  # pip packages only (no sudo / apt / winget / brew)
    python3 install.py --no-ocr     # skip the heavy OCR packages (easyocr, paddleocr, paddlepaddle)

What it does:
  1. installs requirements.txt through  <this python> -m pip  (not through a bare `pip`,
     which may belong to a different Python version),
  2. picks the pip flags that fit the system (venv / PEP 668 "externally managed" / user account),
  3. CHECKS that every package can really be imported - if not (e.g. a package installed by apt
     for a DIFFERENT Python version), it is reinstalled into this interpreter,
  4. installs system tools: ffmpeg and (Linux/Wayland) a screenshot tool.

The OCR packages are optional: if they cannot be installed for your Python version, everything
else is still installed and the program simply runs without OCR.
"""
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
PY = sys.executable
SYSTEM = platform.system()                                   # Linux / Windows / Darwin
IN_VENV = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
SKIP_SYSTEM = "--no-system" in sys.argv
SKIP_OCR = "--no-ocr" in sys.argv
MIN_PYTHON = (3, 9)                                          # PySide6 does not support older versions

# pip package name (lower case) -> module name used in `import`
IMPORT_NAMES = {
    "pillow": "PIL",
    "pyside6": "PySide6",
    "opencv-python": "cv2",
    "opencv-python-headless": "cv2",
    "paddlepaddle": "paddle",
    "python-xlib": "Xlib",
    "pyyaml": "yaml",
    "pygobject": "gi",
    "pywin32": "win32api",
    "scikit-image": "skimage",
    "beautifulsoup4": "bs4",
}

# Heavy / often unavailable for new Python versions. The program loads them in try/except,
# so a failure here must not stop the installation of everything else.
OPTIONAL = {"easyocr", "paddleocr", "paddlepaddle"}


def say(msg=""):
    print(msg, flush=True)


def run(cmd, quiet=False):
    """Runs a command given as a LIST (no shell, so '>' '<' '[' in version specs are safe)."""
    if not quiet:
        say("  $ " + " ".join(str(c) for c in cmd))
    try:
        if quiet:
            return subprocess.run(cmd, capture_output=True, text=True)
        return subprocess.run(cmd)
    except FileNotFoundError as e:
        say(f"  ! command not found: {e.filename}")
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


# ───────────────────────────────────────── pip ──────────────────────────────────────────

def ensure_pip() -> bool:
    if run([PY, "-m", "pip", "--version"], quiet=True).returncode == 0:
        return True
    say("pip is not available in this Python - trying ensurepip...")
    run([PY, "-m", "ensurepip", "--upgrade"])
    if run([PY, "-m", "pip", "--version"], quiet=True).returncode == 0:
        return True
    say("\nERROR: this Python has no pip.")
    if SYSTEM == "Linux":
        say("  Debian/Ubuntu/Mint:  sudo apt install python3-pip   (or use a venv: python3 -m venv .venv)")
        say("  Fedora:              sudo dnf install python3-pip")
        say("  Arch:                sudo pacman -S python-pip")
    return False


def pip_supports(flag: str) -> bool:
    r = run([PY, "-m", "pip", "install", "--help"], quiet=True)
    return flag in (r.stdout or "")


def pip_flags() -> list:
    """Flags shared by every install into THIS interpreter."""
    flags = ["--no-cache-dir"]
    if IN_VENV:
        return flags                                         # nothing more needed in a venv (and --user is not allowed)
    # PEP 668: Debian 12+/Ubuntu 23.04+/Fedora/Arch refuse pip without this flag.
    # Older pips do not know the flag (but do not enforce the block either), so it is added
    # only when the marker file exists AND this pip understands the flag.
    marker = Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED"
    if marker.exists() and pip_supports("--break-system-packages"):
        flags.append("--break-system-packages")
    return flags


def can_use_user_site() -> bool:
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    return not IN_VENV and not is_root


# ────────────────────────────────── requirements.txt ────────────────────────────────────

def read_requirements():
    """[{spec, name, module, optional}] from requirements.txt.
    `spec` is the whole line (extras like qrcode[pil] and markers are kept for pip)."""
    out = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        m = re.match(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", line)
        if not m:
            continue
        name = m.group(0).lower()
        out.append({
            "spec": line,
            "name": name,
            "module": IMPORT_NAMES.get(name, name.replace("-", "_")),
            "optional": name in OPTIONAL,
            "has_marker": ";" in line,           # pip decides if it applies to this system
        })
    return out


def check_module(module: str, optional: bool):
    """(ok, error). Optional heavy packages (they pull in torch/paddle) are only located,
    not imported - importing them would take a long time."""
    if optional:
        r = run([PY, "-c",
                 f"import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('{module}') else 1)"],
                quiet=True)
        return (r.returncode == 0), "not installed"
    r = run([PY, "-c", f"import {module}"], quiet=True)
    if r.returncode == 0:
        return True, ""
    err = (r.stderr or "").strip().splitlines()
    return False, (err[-1] if err else "unknown error")


# ───────────────────────────────── system packages ──────────────────────────────────────

# package manager -> (install command, package names for each tool)
PACKAGE_MANAGERS = {
    "apt-get": (["apt-get", "install", "-y"],
                {"ffmpeg": "ffmpeg", "gi": ["python3-gi", "gir1.2-glib-2.0"], "spectacle": "spectacle",
                 "gnome-screenshot": "gnome-screenshot", "grim": "grim"}),
    "dnf": (["dnf", "install", "-y"],
            {"ffmpeg": "ffmpeg-free", "gi": "python3-gobject", "spectacle": "spectacle",
             "gnome-screenshot": "gnome-screenshot", "grim": "grim"}),
    "pacman": (["pacman", "-S", "--needed", "--noconfirm"],
               {"ffmpeg": "ffmpeg", "gi": "python-gobject", "spectacle": "spectacle",
                "gnome-screenshot": "gnome-screenshot", "grim": "grim"}),
    "zypper": (["zypper", "install", "-y"],
               {"ffmpeg": "ffmpeg", "gi": "python3-gobject", "spectacle": "spectacle",
                "gnome-screenshot": "gnome-screenshot", "grim": "grim"}),
}


def system_python_has_gi() -> bool:
    """The program looks for PyGObject (screenshot portal on Wayland) in sys.executable and /usr/bin/python3."""
    for py in (PY, "/usr/bin/python3", shutil.which("python3")):
        if py and run([py, "-c", "import gi"], quiet=True).returncode == 0:
            return True
    return False


def install_system_linux():
    pm_name = next((n for n in PACKAGE_MANAGERS if shutil.which(n)), None)
    if pm_name is None and shutil.which("apt"):
        pm_name = "apt-get"
    if pm_name is None:
        say("Package manager not recognised - please install manually: ffmpeg"
            " (and on Wayland: python3-gi + spectacle / gnome-screenshot / grim).")
        return
    base_cmd, names = PACKAGE_MANAGERS[pm_name]

    wanted = []
    if not shutil.which("ffmpeg"):
        wanted.append("ffmpeg")

    wayland = (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
               or bool(os.environ.get("WAYLAND_DISPLAY")))
    if wayland:
        # On Wayland screen pixels are only available through the portal or a desktop tool.
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if "kde" in desktop and not shutil.which("spectacle"):
            wanted.append("spectacle")
        elif any(d in desktop for d in ("gnome", "cinnamon", "budgie", "unity")) \
                and not shutil.which("gnome-screenshot"):
            wanted.append("gnome-screenshot")
        elif any(d in desktop for d in ("sway", "hypr", "wlroots", "river")) and not shutil.which("grim"):
            wanted.append("grim")
        if not system_python_has_gi():
            wanted.append("gi")

    pkgs = []
    for w in wanted:
        v = names[w]
        pkgs += v if isinstance(v, list) else [v]
    if not pkgs:
        say("System tools: everything is already installed.")
        return

    sudo = [] if (hasattr(os, "geteuid") and os.geteuid() == 0) else (["sudo"] if shutil.which("sudo") else None)
    if sudo is None:
        say("No sudo available - install manually as administrator: " + " ".join(base_cmd + pkgs))
        return
    rc = run(sudo + base_cmd + pkgs).returncode
    if rc != 0:
        say("  ! installing the system packages failed (the rest of the installation continues).")
        say("    Manually: " + " ".join(base_cmd + pkgs))


def install_system():
    say("\n== System tools ==")
    if SYSTEM == "Linux":
        install_system_linux()
    elif SYSTEM == "Windows":
        if shutil.which("ffmpeg"):
            say("ffmpeg is already installed.")
        elif shutil.which("winget"):
            run(["winget", "install", "--id", "Gyan.FFmpeg", "-e",
                 "--accept-package-agreements", "--accept-source-agreements"])
            say("  (restart your terminal after installing ffmpeg so that PATH is refreshed)")
        else:
            say("Install ffmpeg manually: https://www.gyan.dev/ffmpeg/builds/ and add it to PATH.")
    elif SYSTEM == "Darwin":
        if shutil.which("ffmpeg"):
            say("ffmpeg is already installed.")
        elif shutil.which("brew"):
            run(["brew", "install", "ffmpeg"])
        else:
            say("Install Homebrew (https://brew.sh) and run: brew install ffmpeg")


# ───────────────────────────────────────── main ─────────────────────────────────────────

def install_packages(reqs, flags) -> list:
    """Installs everything; returns the list of requirement dicts that FAILED."""
    wanted = [r for r in reqs if not (SKIP_OCR and r["optional"])]
    if SKIP_OCR:
        say("(skipping OCR packages: --no-ocr)")

    # 1) everything in one go (fastest, lets pip resolve versions together)
    if SKIP_OCR:
        cmd = [PY, "-m", "pip", "install", *flags, *[r["spec"] for r in wanted]]
    else:
        cmd = [PY, "-m", "pip", "install", *flags, "-r", str(REQUIREMENTS)]
    if run(cmd).returncode == 0:
        return []

    # 2) one failing package makes pip install NOTHING - so retry one by one, to keep
    #    everything that can be installed and to find out exactly what cannot.
    say("\nThe combined install failed - retrying package by package...")
    failed = []
    for r in wanted:
        say(f"\n-- {r['spec']}")
        if run([PY, "-m", "pip", "install", *flags, r["spec"]]).returncode != 0:
            failed.append(r)
    return failed


def main() -> int:
    say(f"Python {sys.version.split()[0]}  ({PY})")
    say(f"System: {SYSTEM}  |  venv: {'yes' if IN_VENV else 'no'}")

    if sys.version_info < MIN_PYTHON:
        say(f"\nERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required "
            f"(you have {sys.version_info.major}.{sys.version_info.minor}).")
        return 1
    if not REQUIREMENTS.exists():
        say(f"\nERROR: {REQUIREMENTS} not found")
        return 1
    if not ensure_pip():
        return 1

    flags = pip_flags()
    reqs = read_requirements()

    say("\n== Python packages (requirements.txt) ==")
    failed = install_packages(reqs, flags)
    failed_core = [r for r in failed if not r["optional"]]
    failed_opt = [r for r in failed if r["optional"]]

    say("\n== Checking that the packages load ==")
    broken = []
    for r in reqs:
        if r in failed or (SKIP_OCR and r["optional"]) or r["has_marker"]:
            continue                                         # not installed on purpose / pip decided it does not apply
        ok, err = check_module(r["module"], r["optional"])
        if ok:
            say(f"  OK   {r['name']}")
            continue
        say(f"  !!   {r['name']}: {err}")
        if "cannot open shared object file" in err:
            # A missing SYSTEM library (e.g. libGL for opencv-python) - reinstalling cannot fix that.
            say("       a system library is missing; on Debian/Ubuntu try: sudo apt install libgl1 libglib2.0-0")
            (failed_opt if r["optional"] else broken).append(r)
            continue
        if not err.startswith(("ImportError", "ModuleNotFoundError", "not installed")):
            # e.g. pyautogui raises other errors without a display - environment, not installation.
            say("       (not an installation problem - probably the environment, e.g. no display)")
            continue
        # Typical case: the package sits in /usr/lib/python3/dist-packages (apt) for ANOTHER Python
        # version -> pip says "already satisfied" but the import fails. Force a copy into this
        # interpreter (user account or venv), which takes precedence.
        say("       reinstalling for this interpreter...")
        extra = ["--ignore-installed"] + (["--user"] if can_use_user_site() else [])
        run([PY, "-m", "pip", "install", *flags, *extra, r["spec"]])
        ok, err = check_module(r["module"], r["optional"])
        say(f"  {'OK  ' if ok else 'FAIL'} {r['name']}" + ("" if ok else f": {err}"))
        if not ok:
            (failed_opt if r["optional"] else broken).append(r)

    if not SKIP_SYSTEM:
        install_system()
    else:
        say("\n(skipping system tools: --no-system)")

    say("\n== Summary ==")
    if failed_opt:
        say("Optional packages NOT installed: " + ", ".join(r["name"] for r in failed_opt))
        say("  The program still works, but text recognition (OCR) will be unavailable."
            f"\n  They may not be available for Python {sys.version_info.major}.{sys.version_info.minor} - "
            "Python 3.10-3.13 is the safest choice.")
    core_bad = failed_core + broken
    if core_bad:
        say("Required packages that could not be installed/loaded: " + ", ".join(r["name"] for r in core_bad))
        say("The program may not work correctly. Please attach this output to a GitHub issue."
            f"\nTip: if your Python is very new or very old, use another one, e.g.:"
            f"\n    python3.12 -m venv .venv && .venv/bin/python install.py")
        return 2
    say(f"Done. Start the program with this same Python:\n    {PY} pysharex.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\nInterrupted.")
        sys.exit(130)
