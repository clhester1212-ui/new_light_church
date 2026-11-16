"""The "PTZ Tracker" desktop button: opens the dashboard, starting the tracker first if needed.

Run with pythonw (the desktop shortcut does), so no black window appears.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from keep_running import APP, LOG, tracker_answering  # noqa: E402

DASHBOARD = "http://localhost:8890"


def main() -> None:
    if not tracker_answering():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([str(pythonw if pythonw.exists() else sys.executable),
                          str(APP / "tools" / "keep_running.py")], cwd=APP, creationflags=flags, close_fds=True)
        for _ in range(40):  # up to 20 seconds
            time.sleep(0.5)
            if tracker_answering(1.0):
                break
        else:
            ctypes.windll.user32.MessageBoxW(
                None, "The PTZ Tracker didn't start within 20 seconds.\n\n"
                      f"Details are in:\n{LOG}", "New Light PTZ Tracker", 0x30)
            return
    webbrowser.open(DASHBOARD)


if __name__ == "__main__":
    main()
