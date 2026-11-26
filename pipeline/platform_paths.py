"""The few things that differ between the booth PC (Windows) and the Mac Mini.

config.json keeps its Windows paths, so the PC keeps working exactly as before. On a Mac,
localize() rewrites them when the config is loaded:

    G:\\Shared drives\\...                 ->  ~/Library/CloudStorage/GoogleDrive-<account>/Shared drives/...
    G:\\My Drive\\...                      ->  ~/Library/CloudStorage/GoogleDrive-<account>/My Drive/...
    C:\\Users\\NewLight\\Dropbox\\...        ->  ~/Library/CloudStorage/Dropbox/...  (or ~/Dropbox/...)

If the Mac's folders are somewhere else, set them in config.json:
    "mac_paths": {"google_drive": "/Users/booth/Library/CloudStorage/GoogleDrive-x@y",
                  "dropbox": "/Users/booth/Dropbox"}
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"
HOME = Path.home()

WIN_GOOGLE_DRIVE = "G:\\"
WIN_DROPBOX = "C:\\Users\\NewLight\\Dropbox"


def _first_existing(candidates):
    for c in candidates:
        for hit in sorted(glob.glob(str(c))):
            if Path(hit).exists():
                return Path(hit)
    return None


def google_drive_root(config: dict | None = None) -> Path:
    """The folder that holds "My Drive" and "Shared drives" (G:\\ on the PC)."""
    if not IS_MAC:
        return Path(WIN_GOOGLE_DRIVE)
    custom = ((config or {}).get("mac_paths") or {}).get("google_drive")
    if custom:
        return Path(os.path.expanduser(custom))
    found = _first_existing([HOME / "Library" / "CloudStorage" / "GoogleDrive-*",
                             Path("/Volumes/GoogleDrive"), HOME / "Google Drive"])
    return found or HOME / "Library" / "CloudStorage" / "GoogleDrive"


def dropbox_root(config: dict | None = None) -> Path:
    if not IS_MAC:
        return Path(WIN_DROPBOX)
    custom = ((config or {}).get("mac_paths") or {}).get("dropbox")
    if custom:
        return Path(os.path.expanduser(custom))
    found = _first_existing([HOME / "Library" / "CloudStorage" / "Dropbox*", HOME / "Dropbox"])
    return found or HOME / "Dropbox"


def shared_drives_root(config: dict | None = None) -> Path:
    return google_drive_root(config) / "Shared drives"


def localize_path(text: str, config: dict | None = None) -> str:
    """One Windows path -> the same place on this computer. Unchanged on Windows."""
    if not IS_MAC or not isinstance(text, str):
        return text
    low = text.lower()
    for win, mac in ((WIN_DROPBOX, dropbox_root(config)), (WIN_GOOGLE_DRIVE, google_drive_root(config))):
        if low.startswith(win.lower()):
            rest = text[len(win):].lstrip("\\/").replace("\\", "/")
            return str(mac / rest) if rest else str(mac)
    return text


def localize(obj, config: dict | None = None):
    """Every Windows path inside a loaded config -> this computer's paths (Mac only)."""
    if not IS_MAC:
        return obj
    cfg = config if config is not None else (obj if isinstance(obj, dict) else None)
    if isinstance(obj, dict):
        return {k: localize(v, cfg) for k, v in obj.items()}
    if isinstance(obj, list):
        return [localize(v, cfg) for v in obj]
    return localize_path(obj, cfg)


def recording_drives(labels) -> list[Path]:
    """Mounted drives whose name is one of `labels` (the Crucial X6)."""
    roots: list[Path] = []
    if IS_MAC:
        for label in labels:
            # Two drives with the same name mount as "Crucial X6" and "Crucial X6 1".
            roots += [Path(p) for p in sorted(glob.glob(f"/Volumes/{glob.escape(label)}*"))
                      if Path(p).name == label or Path(p).name.startswith(label + " ")]
        return roots
    import ctypes
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        drive = f"{letter}:\\"
        if not os.path.exists(drive):
            continue
        try:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.kernel32.GetVolumeInformationW(drive, buf, 256, None, None, None, None, 0)
            label = buf.value
        except Exception:
            label = ""
        if label in labels:
            roots.append(Path(drive))
    return roots


def drivefs_metadata_dbs() -> list[str]:
    """Google Drive for desktop's local database(s), used to look up folder IDs."""
    if IS_MAC:
        return glob.glob(str(HOME / "Library" / "Application Support" / "Google" / "DriveFS" / "*" / "metadata_sqlite_db"))
    return glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Google\DriveFS\*\metadata_sqlite_db"))


def move_to_trash(path) -> None:
    """Delete with undo: Recycle Bin on Windows, Trash on the Mac. On Google Drive the
    deletion syncs to Drive's Trash (kept 30 days) on both."""
    path = str(path)
    if IS_MAC:
        script = 'on run argv\ntell application "Finder" to delete (POSIX file (item 1 of argv))\nend run'
        r = subprocess.run(["osascript", "-e", script, path], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(f"could not move {path} to the Trash: {r.stderr.strip()}")
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, path + "\0\0", None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc or op.fAnyOperationsAborted:
        raise OSError(f"could not delete {path} (code {rc})")


def companion_modules_dir() -> Path:
    """Where Companion keeps its installed modules (the ATEM library lives here)."""
    if IS_MAC:
        return HOME / "Library" / "Application Support" / "companion" / "modules"
    return Path(os.environ.get("APPDATA", "")) / "companion" / "modules"


def companion_node() -> str | None:
    """Companion's bundled Node (runs the ATEM helper scripts). Falls back to any `node`."""
    import shutil
    if IS_MAC:
        hit = _first_existing(["/Applications/Companion.app/Contents/Resources/node-runtimes/node*/bin/node",
                               "/Applications/Companion.app/Contents/Resources/node-runtimes/node*/node",
                               "/Applications/Companion.app/Contents/Resources/node-runtime*/bin/node"])
    else:
        hit = _first_existing([r"C:\Program Files\Companion\resources\node-runtimes\node*\node.exe"])
    return str(hit) if hit else shutil.which("node")


def ffmpeg_fallbacks() -> list[str]:
    """Places to look for ffmpeg when it isn't on PATH (launch agents get a short PATH)."""
    if IS_MAC:
        return ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    return [os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\ffmpeg.exe")]


def no_window_flags() -> int:
    """subprocess creationflags that hide the console window on Windows (0 elsewhere)."""
    import subprocess as sp
    return getattr(sp, "CREATE_NO_WINDOW", 0)
