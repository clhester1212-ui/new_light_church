"""Keep the PTZ tracker running in the background, with no window.

Started at Windows login by the "New Light PTZ Tracker" scheduled task (see install_autostart.ps1),
or by the desktop button if the tracker isn't running. Run it with pythonw so no black window appears.

- Only one copy runs: a second copy sees the first and quits.
- If the tracker stops or crashes, it starts it again after 10 seconds.
- The tracker's printed output goes to data/logs/tracker.log.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
LOG_DIR = APP / "data" / "logs"
LOG = LOG_DIR / "tracker.log"
URL = "http://127.0.0.1:8890/api/cameras"
LOCK_PORT = 48890          # held while this keeper runs, so only one keeper exists
RESTART_WAIT = 10


def tracker_answering(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def note(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [keeper] {msg}\n")


def trim_log() -> None:
    """Keep the log from growing forever: over 5 MB, keep the last 1 MB."""
    try:
        if LOG.exists() and LOG.stat().st_size > 5_000_000:
            data = LOG.read_bytes()[-1_000_000:]
            LOG.write_bytes(data)
    except OSError:
        pass


def main() -> int:
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        return 0  # another keeper is already running
    lock.listen(1)

    python = Path(sys.executable).with_name("python.exe")
    if not python.exists():
        python = Path(sys.executable)
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    note("keeper started")
    while True:
        if tracker_answering():
            # Someone started it another way (e.g. the old .bat). Leave it alone and check again later.
            time.sleep(30)
            continue
        trim_log()
        note("starting tracker")
        with LOG.open("a", encoding="utf-8") as out:
            proc = subprocess.Popen([str(python), "-u", "run.py"], cwd=APP, stdout=out,
                                    stderr=subprocess.STDOUT, creationflags=no_window)
            code = proc.wait()
        note(f"tracker stopped (exit code {code}); restarting in {RESTART_WAIT} s")
        time.sleep(RESTART_WAIT)


if __name__ == "__main__":
    sys.exit(main())
