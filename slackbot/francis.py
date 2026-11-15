"""Francis: a Slack bot that relays your messages to Claude Code on this computer.

DM the bot (or @mention it) from your phone. Each Slack thread is one Claude
conversation, so replies in the same thread keep context. Only the Slack users
in secrets/slack.json "allowed_user_ids" are listened to; anyone else just gets
told their Slack user ID.

Run:  slackbot\\francis.cmd   (also starts automatically at login)

Started that way, this script is a small watchdog: it runs the real bot as a child
process ("--bot"), sends anything the bot prints (including crash tracebacks) to
work/francis.log, and starts it again a minute after it stops.
"""
import glob
import json
import msvcrt
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(ROOT, "secrets", "slack.json")
WORK = os.path.join(ROOT, "work")
SESSIONS = os.path.join(WORK, "slack_sessions.json")
LOG = os.path.join(WORK, "francis.log")
LOCK = os.path.join(WORK, "francis.lock")
RESTART_DELAY = 60  # seconds to wait before restarting a bot that stopped

SYSTEM_PROMPT = """You are Francis, New Light Church's media assistant, running on the church computer.
The message comes from Slack on the user's phone. Nobody is at the computer, so nobody can
approve a permission prompt. Do the work, then reply with a short, plain-language summary
suitable for reading on a phone (no long code dumps). Before anything hard to undo or that
reaches other people (publishing to YouTube/social, emailing anyone but the requester,
deleting files, git push), stop and ask in your reply; they will answer in the same thread."""


def log(msg):
    os.makedirs(WORK, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def find_claude():
    """Prefer a `claude` on PATH; otherwise the newest copy bundled with the desktop app."""
    exe = shutil.which("claude")
    if exe:
        return exe
    # The Store version of the desktop app keeps its files under LocalCache, which only
    # the app itself sees as %APPDATA%\Claude; look in both places.
    patterns = [
        os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code", "*", "*", "claude.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "Claude_*", "LocalCache",
                     "Roaming", "Claude", "claude-code", "*", "*", "claude.exe"),
        # Newer desktop app versions (since about Nov 2026) keep it here instead.
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-Data", "claude_*",
                     "claude-code", "*", "*", "claude.exe"),
    ]
    found = sorted({p for pat in patterns for p in glob.glob(pat)}, key=os.path.getmtime)
    if not found:
        raise RuntimeError("Can't find claude.exe. Is the Claude desktop app installed?")
    return found[-1]


def run_claude(prompt, session_id, permission_mode):
    cmd = [find_claude(), "-p", "--output-format", "json",
           "--permission-mode", permission_mode,
           "--append-system-prompt", SYSTEM_PROMPT]
    if session_id:
        cmd += ["--resume", session_id]
    proc = subprocess.run(cmd, input=prompt, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        err = (proc.stderr or proc.stdout or "no output").strip()[-1500:]
        return f"Claude didn't finish cleanly (exit {proc.returncode}):\n```{err}```", session_id
    text = out.get("result") or "(Done. No text reply.)"
    if out.get("is_error"):
        text = "Something went wrong: " + text
    return text, out.get("session_id") or session_id


def chunks(text, size=3500):
    while text:
        yield text[:size]
        text = text[size:]


def main():
    cfg = load_json(SECRETS, {})
    allowed = set(cfg.get("allowed_user_ids", []))
    if cfg.get("allowed_user_id", "").strip():
        allowed.add(cfg["allowed_user_id"].strip())
    mode = cfg.get("permission_mode", "auto")
    app = App(token=cfg["bot_token"])
    bot_user = app.client.auth_test()["user_id"]
    jobs = queue.Queue()
    sessions = load_json(SESSIONS, {})

    def worker():
        while True:
            channel, thread_ts, ts, text = jobs.get()
            key = f"{channel}:{thread_ts}"
            try:
                app.client.reactions_add(channel=channel, timestamp=ts, name="eyes")
                log(f"start {key}: {text[:200]!r}")
                reply, sid = run_claude(text, sessions.get(key), mode)
                sessions[key] = sid
                save_json(SESSIONS, sessions)
                for part in chunks(reply):
                    app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=part)
                app.client.reactions_add(channel=channel, timestamp=ts, name="white_check_mark")
                log(f"done  {key}")
            except Exception:
                log(traceback.format_exc())
                try:
                    app.client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                                text="Francis hit an error. Details are in work/francis.log.")
                except Exception:
                    pass
            finally:
                jobs.task_done()

    threading.Thread(target=worker, daemon=True).start()

    def handle(event, say):
        if event.get("bot_id") or event.get("subtype"):
            return
        user = event.get("user", "")
        if user not in allowed:
            log(f"ignored message from {user}")
            say(f"Francis doesn't take requests from you yet. Your Slack user ID is `{user}`; "
                f"ask the church office to add it to \"allowed_user_ids\" in secrets/slack.json.")
            return
        text = event.get("text", "").replace(f"<@{bot_user}>", "").strip()
        if not text:
            return
        thread_ts = event.get("thread_ts") or event["ts"]
        waiting = jobs.unfinished_tasks
        if waiting:
            say(text=f"Queued behind {waiting} other task(s).", thread_ts=thread_ts)
        jobs.put((event["channel"], thread_ts, event["ts"], text))

    @app.event("message")
    def on_dm(event, say):
        if event.get("channel_type") == "im":
            handle(event, say)

    @app.event("app_mention")
    def on_mention(event, say):
        if event.get("channel_type") != "im":
            handle(event, say)

    log("Francis starting")
    SocketModeHandler(app, cfg["app_token"]).start()


def watchdog():
    """Keep one copy of the bot running; restart it whenever it exits."""
    os.makedirs(WORK, exist_ok=True)
    lock = open(LOCK, "w")
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return  # another watchdog already has Francis running; two would answer twice
    while True:
        log("watchdog: starting bot")
        with open(LOG, "a", encoding="utf-8") as out:
            proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--bot"],
                                    cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            code = proc.wait()
        log(f"watchdog: bot stopped (exit {code}); restarting in {RESTART_DELAY}s")
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    if "--bot" in sys.argv:
        try:
            main()
        except BaseException:
            log("bot crashed:\n" + traceback.format_exc())
            raise
    else:
        watchdog()
