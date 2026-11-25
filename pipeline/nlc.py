r"""New Light Church Sunday pipeline — the mechanical half.

Claude (via the new-light-sunday skill) makes the judgment calls — where the songs and
sermon are, what's date-specific, titles/descriptions — and writes them to plan.json.
This script does everything deterministic around that:

  python pipeline/nlc.py pending                           next new recording on the X6 drive (or NONE)
  python pipeline/nlc.py find        [--date YYYY-MM-DD]   locate that Sunday's Worship recording
  python pipeline/nlc.py notes       [--date YYYY-MM-DD]   print that week's sermon notes file
  python pipeline/nlc.py transcribe  --date YYYY-MM-DD     transcribe the recording (local Whisper)
  python pipeline/nlc.py render      --date YYYY-MM-DD     cut sermon (1080p) + radio MP3 + songs (480p)
  python pipeline/nlc.py slides      --date YYYY-MM-DD     build the 5 Instagram slides
  python pipeline/nlc.py carousel    --date YYYY-MM-DD     designed carousel (pastor cover + series art) + caption
  python pipeline/nlc.py upload      --date YYYY-MM-DD     upload the sermon video to YouTube
  python pipeline/nlc.py drive-link  --path "G:\Shared drives\..."   Google Drive link for a synced folder or file
  python pipeline/nlc.py cleanup     [--date YYYY-MM-DD] [--dry-run]  recycle work files/slides/timelines older than 4 weeks

Everything for one Sunday lives in work/<date>/ (gitignored).
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
WORK = ROOT / "work"


# ---------------------------------------------------------------- helpers

def log(*a):
    print(*a, flush=True)


def die(msg):
    log(f"ERROR: {msg}")
    sys.exit(1)


def sunday_of(date_str):
    if date_str:
        return dt.date.fromisoformat(date_str)
    return dt.date.today()  # no silent fallback to an earlier Sunday


def workdir(date):
    d = WORK / date.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_plan(date):
    p = workdir(date) / "plan.json"
    if not p.exists():
        die(f"{p} not found — Claude writes this after reading the transcript.")
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def ffmpeg(*args):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y", *map(str, args)]
    log("  $ ffmpeg", " ".join(str(a) for a in args[:6]), "...")
    r = subprocess.run(cmd)
    if r.returncode:
        die(f"ffmpeg failed (exit {r.returncode})")


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip()
    return float(out)


def safe_name(s):
    return re.sub(r'[<>:"/\\|?*]', "", s).strip()


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


# ---------------------------------------------------------------- find

DATE_PATTERNS = [
    (r"(\d{4})-(\d{1,2})-(\d{1,2})", "ymd"),      # Worship_2026-08-23 01
    (r"(\d{4})(\d{2})(\d{2})", "ymd"),            # Worship_20260812 01
    (r"(\d{1,2})_(\d{1,2})_(\d{4})", "mdy"),      # Worship_9_27_2026 01
    (r"(\d{1,2})_(\d{1,2})_(\d{2})\b", "mdy2"),   # Worship_8_16_26 01
]


def date_in_name(name):
    for pat, kind in DATE_PATTERNS:
        m = re.search(pat, name)
        if not m:
            continue
        a, b, c = map(int, m.groups())
        try:
            if kind == "ymd":
                return dt.date(a, b, c)
            if kind == "mdy":
                return dt.date(c, a, b)
            return dt.date(2000 + c, a, b)
        except ValueError:
            pass
    return None


def search_roots():
    roots = []
    # Every attached drive whose volume label matches (both Crucial X6 drives share a name,
    # and either one may be plugged in on a given Sunday).
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        drive = f"{letter}:\\"
        if not os.path.exists(drive):
            continue
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.kernel32.GetVolumeInformationW(drive, buf, 256, None, None, None, None, 0)
            label = buf.value
        except Exception:
            label = ""
        if label in CONFIG["recording_drive_labels"]:
            roots.append(Path(drive))
    roots += [Path(p) for p in CONFIG["recording_folders"]]
    return [r for r in roots if r.exists()]


def started_around_10am(f):
    """True if the recording began on a Sunday morning between 9:00 and 11:30 — the start is
    the file's last-write time minus its length (creation times don't survive copying)."""
    try:
        end = dt.datetime.fromtimestamp(f.stat().st_mtime)
        start = end - dt.timedelta(seconds=duration(f))
    except Exception:
        return False
    return start.weekday() == 6 and dt.time(9, 0) <= start.time() <= dt.time(11, 30)


def is_service_recording(f):
    """A Sunday service recording: named "Worship…", or any other recording (not a practice
    file) that started around 10am on a Sunday — that's the service, whatever it's called."""
    n = f.name.lower()
    if "conflict" in n or n.startswith("._") or n.startswith("practice"):
        return False
    return n.startswith("worship") or started_around_10am(f)


def find_recording(date):
    hits = []
    for root in search_roots():
        for f in root.glob("*.mp4"):
            n = f.name
            if not is_service_recording(f):
                continue
            named = date_in_name(n)
            modified = dt.date.fromtimestamp(f.stat().st_mtime)
            if named == date or (named is None and modified == date):
                hits.append(f)
    if not hits:
        return None, []
    hits.sort(key=lambda f: f.stat().st_size, reverse=True)  # the full service, not a stub
    return hits[0], hits


def wait_until_stable(path, quiet_sec=90, timeout_min=60):
    """Dropbox / Blackmagic sync may still be writing the file — wait for its size to settle."""
    deadline = time.time() + timeout_min * 60
    last = -1
    while time.time() < deadline:
        size = path.stat().st_size
        if size == last and time.time() - path.stat().st_mtime > quiet_sec:
            return True
        last = size
        time.sleep(quiet_sec)
    return False


def cmd_find(a):
    date = sunday_of(a.date)
    f, all_hits = find_recording(date)
    if not f:
        die(f"No service recording (named 'Worship…' or started ~10am) dated {date} in: " + ", ".join(map(str, search_roots())))
    log(f"Found {len(all_hits)} candidate(s); using the largest:")
    for h in all_hits:
        log(f"  {'*' if h == f else ' '} {h}  ({h.stat().st_size / 1e9:.1f} GB)")
    if not a.no_wait:
        log("Waiting for the file to finish syncing...")
        if not wait_until_stable(f):
            die(f"{f} was still changing after 60 minutes")
    info = {"date": date.isoformat(), "source": str(f), "duration": duration(f)}
    save_json(workdir(date) / "source.json", info)
    log(json.dumps(info, indent=2))


LOCK_HOURS = 8  # a run that hasn't finished after this long is assumed to have died


def recording_date(f):
    return date_in_name(f.name) or dt.date.fromtimestamp(f.stat().st_mtime)


def cmd_pending(a):
    """The routine polls this every few minutes: is there a new, unprocessed Sunday recording
    on a plugged-in X6 drive? Prints JSON {date, source} for the oldest one, or NONE."""
    since = dt.date.fromisoformat(CONFIG["process_recordings_since"])
    found = {}
    for root in search_roots():
        for f in root.glob("*.mp4"):
            if f.stat().st_size < CONFIG["min_recording_gb"] * 1e9:  # stubs / false starts
                continue
            if not is_service_recording(f):
                continue
            d = recording_date(f)
            if d < since or d.weekday() != 6:  # Sundays only
                continue
            if d not in found or f.stat().st_size > found[d].stat().st_size:
                found[d] = f
    for d in sorted(found):
        wd = WORK / d.isoformat()
        if (wd / "done.json").exists():
            continue
        lock = wd / "running.json"
        if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_HOURS * 3600:
            continue  # another run is working on it
        if a.claim:
            workdir(d)
            save_json(lock, {"started": dt.datetime.now().isoformat(timespec="seconds"), "source": str(found[d])})
        log(json.dumps({"date": d.isoformat(), "source": str(found[d])}))
        return
    log("NONE")


# ---------------------------------------------------------------- notes

def cmd_notes(a):
    date = sunday_of(a.date)
    folder = Path(CONFIG["sermon_notes_folder"])
    lo = dt.datetime.combine(date - dt.timedelta(days=7), dt.time())
    hi = dt.datetime.combine(date, dt.time(23, 59))
    cands = [f for f in folder.glob("*") if f.is_file()
             and lo <= dt.datetime.fromtimestamp(f.stat().st_mtime) <= hi]
    if not cands:
        log(f"No sermon notes in {folder} modified the week before {date}.")
        return
    f = max(cands, key=lambda f: f.stat().st_mtime)
    raw = f.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    log(f"=== {f.name} (modified {dt.datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d %H:%M}) ===")
    log(text)


# ---------------------------------------------------------------- transcribe

def cmd_transcribe(a):
    date = sunday_of(a.date)
    wd = workdir(date)
    src = json.loads((wd / "source.json").read_text())["source"]
    wav = wd / "audio16k.wav"
    if not wav.exists():
        log("Extracting audio...")
        ffmpeg("-i", src, "-vn", "-ac", "1", "-ar", "16000", wav)

    from faster_whisper import WhisperModel
    log(f"Transcribing with Whisper '{CONFIG['whisper_model']}' (this takes a while on this laptop)...")
    model = WhisperModel(CONFIG["whisper_model"], device="cpu", compute_type="int8",
                         cpu_threads=os.cpu_count())
    # Hand Whisper raw samples instead of a path — sidesteps PyAV version mismatches.
    audio = load_wav(date)
    segments, _ = model.transcribe(audio, language="en", vad_filter=True, word_timestamps=True,
                                   condition_on_previous_text=False)
    segs, t0 = [], time.time()
    words_out = []
    for s in segments:
        segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        words_out += [f"{w.start:.2f}-{w.end:.2f} {w.word.strip()}" for w in (s.words or [])]
        if len(segs) % 100 == 0:
            log(f"  ...{hms(s.end)} transcribed ({time.time() - t0:.0f}s elapsed)")
    save_json(wd / "transcript.json", segs)
    with open(wd / "transcript.txt", "w", encoding="utf-8") as fh:
        for s in segs:
            fh.write(f"[{hms(s['start'])}] {s['text']}\n")
    (wd / "words.txt").write_text("\n".join(words_out), encoding="utf-8")
    log(f"Wrote {len(segs)} segments to {wd / 'transcript.txt'} (+ word timings in words.txt)")
    write_audio_map(date)


def load_wav(date):
    import wave
    import numpy as np
    with wave.open(str(workdir(date) / "audio16k.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def write_audio_map(date):
    """Whisper mostly skips singing, so songs show up as loud stretches with no transcript.
    Writes audio_map.txt: one line per 10 s — loudness and whether words were transcribed."""
    import numpy as np
    wd = workdir(date)
    audio = load_wav(date)
    segs = json.loads((wd / "transcript.json").read_text(encoding="utf-8"))
    n = len(audio) // 16000
    rms = np.sqrt(np.mean(audio[: n * 16000].reshape(n, 16000) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    words = np.zeros(n)
    for s in segs:
        for t in range(int(s["start"]), min(int(s["end"]) + 1, n)):
            words[t] += len(s["text"].split()) / max(s["end"] - s["start"], 1)
    lines = ["# time | loudness dB | words/sec | guess  (MUSIC? = loud with few words → likely a song)"]
    for t0 in range(0, n, 10):
        d, w = float(np.mean(db[t0:t0 + 10])), float(np.mean(words[t0:t0 + 10]))
        guess = "MUSIC?" if d > -32 and w < 0.8 else ("speech" if w >= 0.8 else "quiet")
        lines.append(f"{hms(t0)} | {d:6.1f} | {w:4.1f} | {guess}")
    (wd / "audio_map.txt").write_text("\n".join(lines), encoding="utf-8")
    log(f"Wrote {wd / 'audio_map.txt'}")

    # Re-transcribe each long music stretch with the speech filter OFF to catch sung lyrics,
    # so songs can be identified by name.
    music = (db > -32) & (words < 0.8)
    runs, start = [], None
    for t in range(n + 1):
        m = t < n and music[t]
        if m and start is None:
            start = t
        elif not m and start is not None:
            if t - start >= 45:
                runs.append((start, t))
            start = None
    merged = []
    for a, b in runs:  # bridge short gaps (talking between verses, etc.)
        if merged and a - merged[-1][1] < 20:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    from faster_whisper import WhisperModel
    model = WhisperModel(CONFIG["whisper_model"], device="cpu", compute_type="int8", cpu_threads=os.cpu_count())
    out = []
    for a, b in merged:
        out.append(f"=== music {hms(a)} – {hms(b)} ===")
        segs_m, _ = model.transcribe(audio[a * 16000:b * 16000], language="en", vad_filter=False,
                                     condition_on_previous_text=False, no_speech_threshold=0.9)
        for s in segs_m:
            out.append(f"[{hms(a + s.start)}] {s.text.strip()}")
    (wd / "lyrics.txt").write_text("\n".join(out), encoding="utf-8")
    log(f"Wrote {wd / 'lyrics.txt'} ({len(merged)} music stretch(es))")


def pauses(date):
    """Midpoints of short silences (≥0.25 s) — cut points get snapped to these."""
    import numpy as np
    audio = load_wav(date)
    hop = 160  # 10 ms
    n = len(audio) // hop
    db = 20 * np.log10(np.sqrt(np.mean(audio[: n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-9)
    thresh = np.percentile(db, 20) + 6  # relative to this recording's noise floor
    quiet = db < thresh
    out, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if i - start >= 25:
                out.append((start + i) / 2 * hop / 16000)
            start = None
    return np.array(out)


def snap(t, ps, window=0.4):  # edit points come from word timestamps; only nudge into the gap
    if len(ps) == 0:
        return t
    i = int(abs(ps - t).argmin())
    return float(ps[i]) if abs(ps[i] - t) <= window else t


def cmd_audiomap(a):
    write_audio_map(sunday_of(a.date))


# ---------------------------------------------------------------- render

def font(size, bold=True):
    from PIL import ImageFont
    names = (["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"])
    for n in names:
        p = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / n
        if p.exists():
            return ImageFont.truetype(str(p), size)
    from PIL import ImageFont as F
    return F.load_default()


def make_lower_third(name, subtitle, out, w=1920, h=1080):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f1, f2 = font(54), font(34, bold=False)
    x, y = 110, h - 250
    tw = max(d.textlength(name, font=f1), d.textlength(subtitle, font=f2)) if subtitle else d.textlength(name, font=f1)
    box_h = 140 if subtitle else 96
    d.rectangle([x - 30, y - 20, x + tw + 40, y - 20 + box_h], fill=(15, 15, 18, 200))
    d.rectangle([x - 30, y - 20, x - 22, y - 20 + box_h], fill=tuple(CONFIG["brand_accent_rgb"]) + (255,))
    d.text((x, y - 8), name, font=f1, fill=(255, 255, 255, 255))
    if subtitle:
        d.text((x, y + 60), subtitle, font=f2, fill=(220, 220, 220, 255))
    img.save(out)


def keep_ranges(start, end, cuts):
    """[start,end] minus each cut range, in source seconds."""
    ranges, cur = [], start
    for a, b in sorted(cuts):
        a, b = max(a, start), min(b, end)
        if b <= a:
            continue
        if a > cur:
            ranges.append((cur, a))
        cur = max(cur, b)
    if cur < end:
        ranges.append((cur, end))
    return ranges


def render_sermon(plan, src, wd, date):
    s = dict(plan["sermon"])
    ps = pauses(date)  # snap every edit point to a natural pause so nothing is cut mid-word
    s["start"], s["end"] = snap(s["start"], ps), snap(s["end"], ps)
    s["cuts"] = [[snap(a, ps), snap(b, ps)] for a, b in s.get("cuts", [])]
    log(f"Edit points after snapping to pauses: start {hms(s['start'])}, end {hms(s['end'])}, "
        f"cuts {[[hms(a), hms(b)] for a, b in s['cuts']]}")
    keeps = keep_ranges(s["start"], s["end"], s["cuts"])
    lt_png = wd / "lower_third.png"
    make_lower_third(plan["speaker"], plan.get("lower_third_subtitle", CONFIG["church_name"]), lt_png)

    # Input-seek to the sermon window, then trim/concat the keep ranges relative to it.
    base = s["start"]
    parts, labels = [], []
    for i, (a, b) in enumerate(keeps):
        a0, b0, ln = a - base, b - base, b - a
        parts.append(f"[0:v]trim=start={a0:.3f}:end={b0:.3f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim=start={a0:.3f}:end={b0:.3f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.04,afade=t=out:st={max(ln - 0.04, 0):.3f}:d=0.04[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(keeps)}:v=1:a=1[vc][ac]")

    # Lower thirds: fade in/out at each requested time (output-relative seconds).
    lts = plan.get("lower_thirds") or [{"at": 4, "duration": 8}]
    cur = "vc"
    for i, lt in enumerate(lts):
        t, d = lt["at"], lt.get("duration", 8)
        parts.append(f"[1:v]format=rgba,fade=t=in:st={t}:d=0.6:alpha=1,"
                     f"fade=t=out:st={t + d - 0.6}:d=0.6:alpha=1[lt{i}]")
        parts.append(f"[{cur}][lt{i}]overlay=0:0:enable='between(t,{t},{t + d})'[vo{i}]")
        cur = f"vo{i}"

    out = wd / "sermon.mp4"
    total = sum(b - a for a, b in keeps)
    ffmpeg("-ss", base, "-to", s["end"], "-i", src,
           "-loop", "1", "-t", total, "-i", lt_png,
           "-filter_complex", ";".join(parts),
           "-map", f"[{cur}]", "-map", "[ac]",
           "-c:v", "libx264", "-preset", CONFIG["sermon_x264_preset"], "-crf", CONFIG["sermon_crf"],
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", out)
    log(f"Sermon video: {out} ({hms(total)} long, {len(keeps)} piece(s), {len(s.get('cuts', []))} cut(s))")
    return out, keeps


def mmss(sec):
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def sermon_folder(plan, date, test_root=None):
    """Sermons/<year>/<date> - <file_title>/ on the Google Drive shared drive."""
    root = test_root / "Sermons" if test_root else Path(CONFIG["sermons_folder"])
    return root / str(date.year) / f"{date.isoformat()} - {safe_name(plan['file_title'])}"


def render_mp3(plan, sermon_mp4, date, test_root=None):
    folder = sermon_folder(plan, date, test_root)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{date.isoformat()} - {safe_name(plan['file_title'])}.mp3"
    ffmpeg("-i", sermon_mp4, "-vn", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
           "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k",
           "-metadata", f"title={plan['file_title']}",
           "-metadata", f"artist={plan['speaker']}",
           "-metadata", f"album={CONFIG['church_name']}",
           "-metadata", f"date={date.isoformat()}", out)
    log(f"Radio MP3: {out}")
    if not test_root:  # every sermon MP3 also collected in one folder: Media\Sermon MP3s
        mp3s = Path(CONFIG["sermon_mp3_folder"])
        mp3s.mkdir(parents=True, exist_ok=True)
        shutil.copy(out, mp3s / out.name)
        log(f"Copied to {mp3s / out.name}")
    return out


SONG_LOG_FIELDS = ["Date", "Order", "Song", "Artist", "Lead", "Length", "Service time", "File"]


def read_song_log(logf):
    import csv
    if not Path(logf).exists():
        return []
    with open(logf, newline="", encoding="utf-8-sig") as fh:
        return [r for r in csv.DictReader(fh) if r.get("Date")]


def write_song_log(logf, rows):
    import csv
    with open(logf, "w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel shows "·" right
        w = csv.DictWriter(fh, SONG_LOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows({k: r.get(k) or "" for k in SONG_LOG_FIELDS} for r in rows)


def set_song_leads(date_iso, leads):
    """Fill the Song Log's Lead column for one Sunday. leads = {song file name: lead text};
    "None" = no soloist; blank = not confirmed yet."""
    logf = Path(CONFIG["worship_songs_folder"]) / "Song Log.csv"
    rows = read_song_log(logf)
    n = 0
    for r in rows:
        if r["Date"] == date_iso and r["File"] in leads:
            r["Lead"] = leads[r["File"]]
            n += 1
    write_song_log(logf, rows)
    log(f"Song Log: set Lead on {n} song(s) for {date_iso}")
    return n



def render_songs(plan, src, date, test_root=None):
    """Matches the existing Worship Songs layout: <date>/<date> - <n> - <Song>.mp4 + Song Log.csv."""
    root = test_root / "Worship Songs" if test_root else Path(CONFIG["worship_songs_folder"])
    dest = root / date.isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    outs, rows = [], []
    for n, song in enumerate(plan.get("songs", []), 1):
        name = f"{date.isoformat()} - {n} - {safe_name(song['title'])}.mp4"
        out = dest / name
        if out.exists():  # never overwrite someone's existing (possibly full-quality) copy
            log(f"Song already exists, leaving it alone: {out}")
            outs.append(str(out))
            continue
        ffmpeg("-ss", song["start"], "-to", song["end"], "-i", src,
               "-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out)
        outs.append(str(out))
        rows.append([date.isoformat(), n, song["title"], song.get("artist", ""),
                     mmss(song["end"] - song["start"]), f"{mmss(song['start'])}-{mmss(song['end'])}", name])
        log(f"Song: {out}")

    logf = root / "Song Log.csv"
    old = {r["File"]: r.get("Lead", "") for r in read_song_log(logf) if r["Date"] == date.isoformat()}
    keep = [r for r in read_song_log(logf) if r["Date"] != date.isoformat()]  # re-runs replace that date's rows
    new = [dict(zip(["Date", "Order", "Song", "Artist", "Length", "Service time", "File"], map(str, r)),
                Lead=old.get(r[6], "")) for r in rows]
    write_song_log(logf, keep + new)
    log(f"Updated {logf}")
    return outs


def write_resolve_timeline(plan, src, keeps, wd, date):
    """FCPXML of the same edit — Resolve (free) → File → Import → Timeline, to fine-tune by hand."""
    from urllib.parse import quote
    from xml.sax.saxutils import escape
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=r_frame_rate", "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout
    rate = next(l for l in out.split() if "/" in l)  # ATEM files list the stream twice
    num, den = (int(x) for x in rate.split("/"))
    fd = f"{den}/{num}s"  # frame duration
    fr = lambda sec: f"{round(sec * num / den) * den}/{num}s"  # frame-aligned time
    total_src = duration(src)
    url = "file:///" + quote(str(src).replace("\\", "/"), safe="/:")
    title = escape(f"{date.isoformat()} - {plan['file_title']}")
    clips, offset = [], 0.0
    lts = plan.get("lower_thirds") or [{"at": 4, "duration": 8}]
    for a, b in keeps:
        marks = "".join(
            f'<marker start="{fr(a + lt["at"] - offset)}" duration="{fd}" '
            f'value="{escape("Lower third: " + plan["speaker"])}"/>'
            for lt in lts if offset <= lt["at"] < offset + (b - a))
        clips.append(f'<asset-clip ref="r2" offset="{fr(offset)}" name="{escape(Path(src).name)}" '
                     f'start="{fr(a)}" duration="{fr(b - a)}" format="r1" tcFormat="NDF">{marks}</asset-clip>')
        offset += b - a
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r1" frameDuration="{fd}" width="1920" height="1080"/>
    <asset id="r2" name="{escape(Path(src).name)}" start="0s" duration="{fr(total_src)}" hasVideo="1" hasAudio="1" format="r1" audioSources="1" audioChannels="2" audioRate="48000">
      <media-rep kind="original-media" src="{url}"/>
    </asset>
  </resources>
  <library>
    <event name="New Light Sermons">
      <project name="{title}">
        <sequence format="r1" duration="{fr(offset)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
          <spine>
            {chr(10).join("            " + c for c in clips).strip()}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
'''
    p = wd / f"{date.isoformat()} - Resolve timeline.fcpxml"
    p.write_text(xml, encoding="utf-8")
    log(f"Resolve timeline: {p}")
    return p


def cmd_render(a):
    date = sunday_of(a.date)
    wd = workdir(date)
    plan = load_plan(date)
    src = json.loads((wd / "source.json").read_text())["source"]
    test_root = wd / "TEST OUTPUT" if a.test else None
    if test_root:
        log(f"TEST MODE — songs and MP3 go to {test_root}, not Google Drive")
    results = {}
    if not a.skip_songs:
        results["songs"] = render_songs(plan, src, date, test_root)
    if a.timeline_only:  # just (re)build the Resolve timeline from the plan
        ps = pauses(date)
        s = plan["sermon"]
        keeps = keep_ranges(snap(s["start"], ps), snap(s["end"], ps),
                            [[snap(x, ps), snap(y, ps)] for x, y in s.get("cuts", [])])
        write_resolve_timeline(plan, src, keeps, wd, date)
        return
    sermon, keeps = render_sermon(plan, src, wd, date)
    results["sermon_video"] = str(sermon)
    results["sermon_kept_ranges"] = [[round(x, 2), round(y, 2)] for x, y in keeps]
    results["radio_mp3"] = str(render_mp3(plan, sermon, date, test_root))
    timeline = write_resolve_timeline(plan, src, keeps, wd, date)
    results["resolve_timeline"] = str(timeline)
    if not test_root:  # finished video -> Sermons, Resolve timeline -> Media\Editing Projects
        dest = sermon_folder(plan, date)
        dest.mkdir(parents=True, exist_ok=True)
        video = dest / f"{date.isoformat()} - {safe_name(plan['file_title'])}.mp4"
        log(f"Copying sermon video to {video} ...")
        shutil.copy(sermon, video)
        results["sermon_video_drive"] = str(video)
        proj = Path(CONFIG["media_folder"]) / "Editing Projects"
        proj.mkdir(parents=True, exist_ok=True)
        shutil.copy(timeline, proj)
        results["resolve_timeline_drive"] = str(proj / timeline.name)
    save_json(wd / "render_results.json", results)
    log(json.dumps(results, indent=2))


# ---------------------------------------------------------------- slides

def wrap(draw, text, fnt, width):
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if draw.textlength(trial, font=fnt) <= width:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def cmd_slides(a):
    from PIL import Image, ImageDraw
    date = sunday_of(a.date)
    wd = workdir(date)
    slides = load_plan(date)["slides"]  # list of 5 {"kicker":..., "text":...}
    W, H, M = 1080, 1350, 96
    accent = tuple(CONFIG["brand_accent_rgb"])
    outs = []
    for i, sl in enumerate(slides, 1):
        sl = {**sl, "text": sl["text"].replace("*", "")}  # *highlight* marks are for the carousel only
        img = Image.new("RGB", (W, H), (18, 20, 24))
        d = ImageDraw.Draw(img)
        for y in range(H):  # subtle vertical gradient
            c = int(18 + 14 * y / H)
            d.line([(0, y), (W, y)], fill=(c, c + 2, c + 6))
        d.rectangle([M, M, M + 90, M + 10], fill=accent)
        d.text((M, M + 34), sl.get("kicker", "").upper(), font=font(34), fill=accent)
        size = 92 if i == 1 else 70
        f = font(size)
        lines = wrap(d, sl["text"], f, W - 2 * M)
        while len(lines) * size * 1.18 > H - 2 * M - 300 and size > 40:
            size -= 4
            f = font(size)
            lines = wrap(d, sl["text"], f, W - 2 * M)
        last = i == len(slides)
        inv_f = font(38, bold=False)
        inv = wrap(d, CONFIG["church_invite"], inv_f, W - 2 * M) if last else []
        inv_h = (len(inv) * 50 + 70) if last else 0
        y = (H - len(lines) * size * 1.18 - inv_h) / 2
        for ln in lines:
            d.text((M, y), ln, font=f, fill=(255, 255, 255))
            y += size * 1.18
        if last:  # church invite as its own smaller block under the closing line
            y += 40
            d.rectangle([M, y, M + 60, y + 6], fill=accent)
            y += 30
            for ln in inv:
                d.text((M, y), ln, font=inv_f, fill=(225, 225, 225))
                y += 50
        d.text((M, H - M - 40), CONFIG["church_name"].upper(), font=font(30), fill=(200, 200, 200))
        pg = f"{i}/{len(slides)}"
        d.text((W - M - d.textlength(pg, font=font(30)), H - M - 40), pg, font=font(30), fill=(200, 200, 200))
        out = wd / f"slide_{i}.png"
        # 128-colour palette: looks identical on a dark text slide, ~1/3 the size (cheap to email)
        img.quantize(colors=128, dither=Image.Dither.NONE).save(out, optimize=True)
        outs.append(str(out))
    # Also into Google Drive (Media\Instagram Slides) so they're on the phone, ready to post.
    dest = Path(CONFIG["media_folder"]) / "Instagram Slides" / date.isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    for o in outs:
        shutil.copy(o, dest)
    log("\n".join(outs))
    log(f"Copied to {dest}")


def serif(size, style="b"):
    """Georgia (b = bold, i = italic, z = bold italic) for carousel headlines — pairs with engraved series art."""
    from PIL import ImageFont
    p = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / f"georgia{style}.ttf"
    return ImageFont.truetype(str(p), size) if p.exists() else font(size)


def fit_cover(im, W, H, fx=0.5):
    """Scale to cover W x H and crop, keeping fraction fx of the width centered."""
    from PIL import Image
    sc = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * sc) + 1, int(im.height * sc) + 1), Image.LANCZOS)
    x = int(min(max(0, fx * im.width - W / 2), im.width - W))
    y = (im.height - H) // 2
    return im.crop((x, y, x + W, y + H))


def fit_text(d, text, fnt_of, size, width, max_h, lead=1.2, floor=34):
    """Largest size (from `size` down) whose wrapped lines fit width x max_h."""
    while True:
        f = fnt_of(size)
        lines = wrap(d, text, f, width)
        if len(lines) * size * lead <= max_h or size <= floor:
            return f, size, lines
        size -= 3


def rich_words(text):
    """'More *cannot* give' -> [("More", False), ("cannot", True), ...]; *a few words* works too."""
    out, on = [], False
    for w in text.split():
        start, end = w.startswith("*"), w.rstrip(".,;:!?'\"").endswith("*")
        if start:
            on = True
        out.append((w.replace("*", ""), on))
        if end:
            on = False
    return out


def draw_rich(d, lines, x, y, f, size, lead, color, accent, shadow=True):
    """Draw already-wrapped lines word by word, highlighted words in the accent color."""
    for ln in lines:
        cx = x
        for w, hi in ln:
            if shadow:
                d.text((cx + 3, y + 4), w, font=f, fill=(0, 0, 0))
            d.text((cx, y), w, font=f, fill=accent if hi else color)
            cx += d.textlength(w + " ", font=f)
        y += size * lead
    return y


def fit_rich(d, text, fnt_of, size, width, max_h, lead=1.16, floor=34):
    words = rich_words(text)
    while True:
        f = fnt_of(size)
        lines, line = [], []
        for w in words:
            trial = " ".join(t for t, _ in line + [w])
            if line and d.textlength(trial, font=f) > width:
                lines.append(line)
                line = [w]
            else:
                line.append(w)
        if line:
            lines.append(line)
        if len(lines) * size * lead <= max_h or size <= floor:
            return f, size, lines
        size -= 3


def series_logo(art, info, width):
    from PIL import Image, ImageDraw
    cl, ct, cr, cb = info.get("badge_crop", [0, 0, 1, 1])
    logo = art.crop((int(cl * art.width), int(ct * art.height), int(cr * art.width), int(cb * art.height)))
    logo = logo.resize((width, int(width * logo.height / logo.width)), Image.LANCZOS)
    m = Image.new("L", logo.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, *logo.size], max(8, width // 30), fill=255)
    return logo, m


def banknote_frame(img, accent, inset=44):
    """Double hairline border, like the engraved edge of a bill."""
    from PIL import Image, ImageDraw
    W, H = img.size
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rectangle([inset, inset, W - inset, H - inset], outline=(*accent, 200), width=3)
    d.rectangle([inset + 14, inset + 14, W - inset - 14, H - inset - 14], outline=(*accent, 110), width=1)
    img.alpha_composite(ov)


def cmd_carousel(a):
    """Designed Instagram carousel (1080x1350): a cover with the pastor (the thumbnail's cut-out)
    on the series art, then plan["slides"] on one panorama of the series background that flows
    across the swipe — plus the caption from plan["instagram_caption"]. The plain slides stay as is."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    date = sunday_of(a.date)
    wd = workdir(date)
    plan = load_plan(date)
    th = plan["thumbnail"]
    name, art_path, info = series_info(plan, th)
    if not art_path:
        die(f"No series art in config for '{name}' — add it to series_art first.")
    accent = tuple(info.get("accent") or CONFIG["brand_accent_rgb"])
    art = Image.open(art_path).convert("RGB")
    bgs = [Path(p) for p in info.get("carousel_bg", []) if Path(p).exists()] or [art_path]
    W, H, M = 1080, 1350, 110
    slides = plan["slides"]
    n_total = len(slides) + 1
    sub = plan.get("lower_third_subtitle", name)
    title = plan["youtube"]["title"].split(" | ")[0]
    cream = (240, 232, 214)
    outs = []

    # ---- 1. cover: series art, the pastor, the series logo, the message title
    bg = fit_cover(Image.open(bgs[0]).convert("RGB"), W, H, float(info.get("carousel_cover_focus", 0.28)))
    img = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(1.2))).enhance(0.78).convert("RGBA")
    shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(250 * max(0, (v - 120) / 135) ** 1.3))
    img = Image.composite(Image.new("RGBA", (W, H), (8, 10, 6, 255)), img, shade)
    cut, (face_x, face_y) = pastor_cutout(wd, th)
    s = H * 0.64 / cut.height
    c = cut.resize((int(cut.width * s), int(cut.height * s)), Image.LANCZOS)
    pos = (int(W * float(info.get("carousel_pastor_at", 0.66)) - face_x * s), H - c.height - 190)
    # No glow/halo around him (the pastor asked: it read as "angelic"). Just a soft,
    # dark contact shadow so the cutout sits naturally on the background.
    soft = c.split()[3].filter(ImageFilter.GaussianBlur(10))
    shadow = Image.new("RGBA", c.size, (0, 0, 0, 0))
    shadow.putalpha(soft.point(lambda v: int(v * 0.35)))
    img.alpha_composite(shadow, pos)
    img.alpha_composite(c, pos)
    # deepen the bottom again over his sweater so the title reads
    low = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(235 * max(0, (v - 175) / 80)))
    img = Image.composite(Image.new("RGBA", (W, H), (6, 8, 5, 255)), img, low)
    logo, lm = series_logo(art, info, 760)
    d = ImageDraw.Draw(img)
    lx, ly = (W - logo.width) // 2, 70
    d.rounded_rectangle([lx - 5, ly - 5, lx + logo.width + 5, ly + logo.height + 5], 30, fill=accent)
    img.paste(logo, (lx, ly), lm)
    d = ImageDraw.Draw(img)
    tf, ts, tl = fit_text(d, title.upper(), lambda z: serif(z), 116, W - 2 * M, 260, lead=1.05)
    if len(tl) == 2:  # balance the two lines ("THE LIE / OF MORE", not "THE LIE OF / MORE")
        ws = title.upper().split()
        k = min(range(1, len(ws)), key=lambda k: max(d.textlength(" ".join(ws[:k]), font=tf),
                                                     d.textlength(" ".join(ws[k:]), font=tf)))
        tl = [" ".join(ws[:k]), " ".join(ws[k:])]
    y = H - 150 - len(tl) * ts * 1.05 - 40
    for ln in tl:
        x = (W - d.textlength(ln, font=tf)) / 2
        d.text((x + 4, y + 5), ln, font=tf, fill=(0, 0, 0))
        d.text((x, y), ln, font=tf, fill=cream)
        y += ts * 1.05
    meta = f"{plan['speaker'].upper()}   ·   {sub.upper()}"
    mf = font(30)
    d.text(((W - d.textlength(meta, font=mf)) / 2, y + 22), meta, font=mf, fill=accent)
    banknote_frame(img, accent)
    sw_f = font(26)
    d = ImageDraw.Draw(img)
    d.text((W - 76 - d.textlength("SWIPE  →", font=sw_f), H - 104), "SWIPE  →", font=sw_f, fill=(225, 220, 205))
    outs.append(img)

    # ---- 2..n: one panorama of the series background, sliced so it flows across the swipe
    pano_src = Image.open(bgs[-1]).convert("RGB")
    n = len(slides)
    sc = max(H / pano_src.height, n * W / pano_src.width)
    pano = pano_src.resize((int(pano_src.width * sc) + 1, int(pano_src.height * sc) + 1), Image.LANCZOS)
    pano = pano.crop((0, (pano.height - H) // 2, n * W, (pano.height - H) // 2 + H)).filter(ImageFilter.GaussianBlur(2.5))
    pano = ImageEnhance.Brightness(pano).enhance(0.55)
    small_logo, sm = series_logo(art, info, 250)
    roman = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V"}
    for i, sl in enumerate(slides, 1):
        img = pano.crop(((i - 1) * W, 0, i * W, H)).convert("RGBA")
        vig = Image.radial_gradient("L").resize((W, H)).point(lambda v: int(150 * (v / 255) ** 1.6))
        img = Image.composite(Image.new("RGBA", (W, H), (4, 6, 3, 255)), img, vig)
        d = ImageDraw.Draw(img)
        kicker = sl.get("kicker", "")
        num = kicker.split()[-1] if kicker.lower().startswith("point") else ""
        if i == 1:
            kicker = "The big idea"
        if num in roman:  # big engraved-style numeral, faint, top right
            nf = serif(330)
            wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(wm).text((W - M - d.textlength(roman[num], font=nf), 120), roman[num], font=nf,
                                    fill=(*accent, 60))
            img.alpha_composite(wm)
            d = ImageDraw.Draw(img)
        text = sl["text"].strip()
        m = re.search(r"([.!?]\*?)\s+", text)  # first sentence = headline, the rest = body
        head, body = (text[:m.end(1)], text[m.end():]) if m else (text, "")
        last = i == n
        hf, hs, hl = fit_rich(d, head, lambda z: serif(z), 86 if body else 92, W - 2 * M, 470, lead=1.16)
        bf, bs, bl = fit_rich(d, body, lambda z: font(z, bold=False), 46, W - 2 * M, 300, lead=1.35) if body else (None, 0, [])
        inv_f = font(34, bold=False)
        inv = wrap(d, CONFIG["church_invite"], inv_f, W - 2 * M) if last else []
        block = 70 + len(hl) * hs * 1.16 + (48 + len(bl) * bs * 1.35 if bl else 0) + (66 + len(inv) * 46 if last else 0)
        top, bottom = 230, H - 330  # between the frame top and the series logo
        y = top + max(0, (bottom - top - block) / 2)
        kf = font(30)
        d.text((M, y), "  ".join(kicker.upper()), font=kf, fill=accent)
        y += 70
        y = draw_rich(d, hl, M, y, hf, hs, 1.16, cream, accent)
        if bl:
            y += 18
            d.rectangle([M, y, M + 70, y + 5], fill=accent)
            y += 30
            y = draw_rich(d, bl, M, y, bf, bs, 1.35, (222, 216, 200), accent, shadow=False)
        if last:  # church invite under the closing line
            y += 36
            d.rectangle([M, y, M + 70, y + 5], fill=accent)
            y += 30
            for ln in inv:
                d.text((M, y), ln, font=inv_f, fill=(225, 220, 205))
                y += 46
        bx, by = (W - small_logo.width) // 2, H - 110 - small_logo.height
        d.rounded_rectangle([bx - 3, by - 3, bx + small_logo.width + 3, by + small_logo.height + 3], 12, fill=accent)
        img.paste(small_logo, (bx, by), sm)
        banknote_frame(img, accent)
        d = ImageDraw.Draw(img)
        pf = font(24)
        pg = f"{i + 1} / {n_total}"
        d.text((W - 80 - d.textlength(pg, font=pf), H - 100), pg, font=pf, fill=(190, 185, 170))
        d.text((80, H - 100), CONFIG["church_name"].upper(), font=pf, fill=(190, 185, 170))
        outs.append(img)

    files = []
    for i, im in enumerate(outs, 1):
        p = wd / f"carousel_{i}.jpg"
        im.convert("RGB").save(p, quality=94)
        files.append(p)
    cap = plan.get("instagram_caption")
    if cap:
        (wd / "instagram_caption.txt").write_text(cap.strip() + "\n", encoding="utf-8")
    else:
        log('No "instagram_caption" in plan.json — caption file not written.')
    log("\n".join(map(str, files)))
    if not a.test:
        dest = Path(CONFIG["media_folder"]) / "Instagram Slides" / date.isoformat()
        dest.mkdir(parents=True, exist_ok=True)
        for p in files:
            shutil.copy(p, dest / f"{date.isoformat()} - Carousel {p.stem.split('_')[1]}.jpg")
        if cap:
            shutil.copy(wd / "instagram_caption.txt", dest / f"{date.isoformat()} - Instagram caption.txt")
        log(f"Copied to {dest}")


# ---------------------------------------------------------------- upload

def youtube_creds():
    p = ROOT / "secrets" / "youtube.json"
    if p.exists():
        c = json.loads(p.read_text(encoding="utf-8"))
    else:
        c = {k: os.environ.get(f"YOUTUBE_{k.upper()}") for k in ("client_id", "client_secret", "refresh_token")}
    missing = [k for k, v in c.items() if not v or "PASTE" in str(v)]
    if missing:
        die(f"YouTube credentials missing: {', '.join(missing)} — fill in secrets/youtube.json")
    from google.oauth2.credentials import Credentials
    return Credentials(None, refresh_token=c["refresh_token"], client_id=c["client_id"],
                       client_secret=c["client_secret"], token_uri="https://oauth2.googleapis.com/token",
                       scopes=YOUTUBE_SCOPES)


def cmd_upload(a):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    date = sunday_of(a.date)
    wd = workdir(date)
    yt = load_plan(date)["youtube"]
    privacy = a.privacy or yt.get("privacy") or CONFIG["youtube_privacy"]
    api = build("youtube", "v3", credentials=youtube_creds(), cache_discovery=False)
    thumb = wd / "thumbnail.jpg"
    if a.thumbnail_only:  # (re)set the thumbnail on the video already uploaded
        res = json.loads((wd / "youtube_result.json").read_text())
        res["thumbnail"] = set_thumbnail(api, res["video_id"], thumb)
        save_json(wd / "youtube_result.json", res)
        log(json.dumps(res, indent=2))
        return
    body = {"snippet": {"title": yt["title"][:100], "description": yt["description"][:5000],
                        "tags": yt.get("tags", []), "categoryId": "29"},  # Nonprofits & Activism
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    req = api.videos().insert(part="snippet,status", body=body,
                              media_body=MediaFileUpload(str(wd / "sermon.mp4"), chunksize=16 << 20, resumable=True))
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log(f"  upload {status.progress() * 100:.0f}%")
    res = {"video_id": resp["id"], "url": f"https://www.youtube.com/watch?v={resp['id']}", "privacy": privacy}
    res["thumbnail"] = set_thumbnail(api, resp["id"], thumb) if thumb.exists() else "none (run thumbnail first)"
    res["playlist"] = add_to_series_playlist(api, resp["id"], load_plan(date))
    save_json(wd / "youtube_result.json", res)
    log(json.dumps(res, indent=2))


def add_to_series_playlist(api, video_id, plan):
    """Add the video to the channel's "<Series> | Sermon Series | New Light Church" playlist.
    Series comes from lower_third_subtitle ("Growth Rings · Week 1"). Returns a status string."""
    series = plan.get("lower_third_subtitle", "").split("·")[0].strip()
    if not series:
        return "skipped (no series in plan)"
    try:
        pls, page = [], None
        while True:
            r = api.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=page).execute()
            pls += r["items"]
            page = r.get("nextPageToken")
            if not page:
                break
        match = [p for p in pls if p["snippet"]["title"].split("|")[0].strip().lower() == series.lower()]
        if not match:
            return f"no playlist named '{series}' — add it by hand"
        pl = match[0]["id"]
        have, page = set(), None
        while True:
            r = api.playlistItems().list(part="snippet", playlistId=pl, maxResults=50, pageToken=page).execute()
            have |= {i["snippet"]["resourceId"]["videoId"] for i in r["items"]}
            page = r.get("nextPageToken")
            if not page:
                break
        if video_id in have:
            return f"already in '{match[0]['snippet']['title']}'"
        api.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
        return f"added to '{match[0]['snippet']['title']}'"
    except Exception as e:
        return f"failed: {e}"


LIVE_FOOTER = """New Light Church in Bladenboro, NC. Join us in person Sundays at 10am at 307 Chestnut St, Bladenboro, NC 28320, or live online right here every week.

👋 New here? What to expect: https://newlight.network/i-m-new
📝 Connect card (takes a minute): https://form.church/hBxLqQXe
🙏 Give: https://newlight.network/give
🔗 Events & everything else: https://linktr.ee/newlight

Instagram: https://www.instagram.com/new_light_church/
Facebook: https://www.facebook.com/NewLightChurch.info
Website: https://newlight.network

🔔 Subscribe so you never miss a Sunday.

#NewLightChurch #BladenboroNC #ChurchOnline"""

LIVE_TAGS = ["Bladen County", "Bladenboro", "Bladenboro NC", "New Light Church", "Sunday service",
             "church in Bladenboro", "church online", "live church service", "sermon", "worship"]


def cmd_live(a):
    """Rename that Sunday's YouTube livestream (usually "Online Worship Service") to the message
    title, give it a description, and add it to the series playlist. Optional plan.json
    "live": {"guest": "Special Guest Worship Leader Russ Lee", "note": "<first line of description>"}."""
    from googleapiclient.discovery import build
    date = sunday_of(a.date)
    plan = load_plan(date)
    yt, live = plan["youtube"], plan.get("live") or {}
    api = build("youtube", "v3", credentials=youtube_creds(), cache_discovery=False)
    found = []
    for status in ("completed", "active"):
        r = api.liveBroadcasts().list(part="snippet", broadcastStatus=status, broadcastType="all",
                                      maxResults=10).execute()
        for b in r.get("items", []):
            start = b["snippet"].get("actualStartTime") or ""
            if start and dt.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone().date() == date:
                found.append(b)
    if not found:
        die(f"No YouTube livestream found for {date}")
    vid = found[0]["id"]
    base = yt["title"]
    title = f"{base} | {live['guest']}" if live.get("guest") else f"{base} | New Light Church"
    if len(title) > 100:
        title = f"{base} | New Light Church"
    summary = yt["description"].split("\n\n")[0]
    desc = (f"🎤 {live['note']}\n\n" if live.get("note") else "") + f"{summary}\n\n{LIVE_FOOTER}"
    series = plan.get("lower_third_subtitle", "").split("·")[0].strip()
    guest_name = live.get("guest", "").split("Leader")[-1].strip() if live.get("guest") else ""
    tags = LIVE_TAGS + [t for t in (plan.get("speaker"), series, base.split("|")[0].strip(), guest_name) if t]
    api.videos().update(part="snippet", body={"id": vid, "snippet": {
        "title": title[:100], "description": desc[:5000], "tags": tags,
        "categoryId": "24", "defaultLanguage": "en"}}).execute()
    res = {"video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": title,
           "playlist": add_to_series_playlist(api, vid, plan)}
    save_json(workdir(date) / "youtube_live_result.json", res)
    log(json.dumps(res, indent=2, ensure_ascii=False))


YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube.force-ssl"]


def cmd_youtube_login(a):
    """One-time: sign in to the church's YouTube channel in the browser and save a refresh
    token to secrets/youtube.json. Needs the OAuth client file from Google Cloud Console
    saved as secrets/client_secret.json (Desktop app type)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    cs = ROOT / "secrets" / "client_secret.json"
    if not cs.exists():
        found = sorted((Path.home() / "Downloads").glob("client_secret*.json"), key=lambda p: p.stat().st_mtime)
        if not found:
            die("Download the OAuth client JSON from Google Cloud Console first "
                "(it lands in Downloads as client_secret_....json).")
        shutil.copy(found[-1], cs)
        log(f"Using {found[-1].name} from Downloads")
    flow = InstalledAppFlow.from_client_secrets_file(str(cs), YOUTUBE_SCOPES)
    log("A browser window is opening — sign in with the Google account that owns the "
        "New Light YouTube channel and click Allow.")
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    info = json.loads(cs.read_text())
    info = info.get("installed") or info.get("web")
    save_json(ROOT / "secrets" / "youtube.json", {"client_id": info["client_id"],
                                                  "client_secret": info["client_secret"],
                                                  "refresh_token": creds.refresh_token})
    from googleapiclient.discovery import build
    me = build("youtube", "v3", credentials=creds, cache_discovery=False).channels().list(
        part="snippet", mine=True).execute()
    names = [c["snippet"]["title"] for c in me.get("items", [])]
    log(f"Saved YouTube sign-in to secrets/youtube.json. Channel: {', '.join(names) or '(none found!)'}")


def cmd_opus_upload(a):
    """Upload a ~1 GB copy of the sermon to the OpusClip signed URL (from opusclip_create_upload_link)."""
    import requests
    date = sunday_of(a.date)
    wd = workdir(date)
    proxy = wd / "sermon_opus.mp4"
    if not proxy.exists():
        log("Making a smaller copy for OpusClip...")
        ffmpeg("-i", wd / "sermon.mp4", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", proxy)
    r = requests.post(a.url, headers={"x-goog-resumable": "start", "Content-Length": "0"}, timeout=60)
    r.raise_for_status()
    session = r.headers["Location"]
    size = proxy.stat().st_size
    log(f"Uploading {size / 1e9:.2f} GB to OpusClip...")
    with open(proxy, "rb") as fh:
        r = requests.put(session, data=fh, headers={"Content-Length": str(size)}, timeout=3600)
    r.raise_for_status()
    log("OpusClip upload complete.")


# ---------------------------------------------------------------- google drive

def drive_cloud_id(path):
    """Look up a G:\\ folder's Google Drive ID in Drive for Desktop's local metadata.
    Returns None until Drive has finished uploading the folder."""
    import glob
    import sqlite3
    rel = Path(path).resolve().relative_to(Path("G:/Shared drives")).parts  # (drive, sub, ...)
    for db in glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Google\DriveFS\*\metadata_sqlite_db")):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT stable_id, id FROM items WHERE local_title=? AND is_folder=1 "
                              "AND id LIKE '0A%' AND is_tombstone=0", (rel[0],)).fetchone()
            for part in rel[1:]:
                if not row:
                    break
                row = con.execute(
                    "SELECT i.stable_id, i.id FROM items i JOIN stable_parents p ON p.item_stable_id=i.stable_id "
                    "WHERE p.parent_stable_id=? AND i.local_title=? AND i.is_tombstone=0 AND i.trashed=0",
                    (row[0], part)).fetchone()
        finally:
            con.close()
        if row and not row[1].startswith("local"):
            return row[1]
    return None


def cmd_drive_link(a):
    deadline = time.time() + a.wait * 60
    while True:
        cid = drive_cloud_id(a.path)
        if cid:
            if Path(a.path).is_file():
                log(f"https://drive.google.com/file/d/{cid}/view")
            else:
                log(f"https://drive.google.com/drive/folders/{cid}")
            return
        if time.time() > deadline:
            die(f"Google Drive hasn't synced {a.path} yet (waited {a.wait} min)")
        time.sleep(20)


# ---------------------------------------------------------------- YouTube thumbnail

def cmd_thumb_frames(a):
    """Contact sheets of candidate frames from the edited sermon, to pick the thumbnail shot.
    Wide sheets every ~55 s; pass --around <sec> for a 0.5 s close look at one moment."""
    from PIL import Image, ImageDraw
    date = sunday_of(a.date)
    wd = workdir(date)
    out = wd / "thumbs"
    out.mkdir(exist_ok=True)
    if a.raw:
        return cmd_thumb_frames_raw(a, wd, out)
    dur = duration(wd / "sermon.mp4")
    if a.around is not None:
        times = [round(a.around - 3 + i * 0.5, 1) for i in range(12)]
        name, cell = f"around_{a.around:g}", (640, 360)
    else:
        times = [float(t) for t in range(30, int(dur) - 10, 55)]
        name, cell = "sheet", (480, 270)
    W, H = cell
    cols = 4 if a.around is not None else 6
    pages = [times[i:i + 24] for i in range(0, len(times), 24)]
    for n, page in enumerate(pages):
        sheet = Image.new("RGB", (W * cols, H * ((len(page) + cols - 1) // cols)))
        for i, t in enumerate(page):
            f = out / f"f_{t:g}.png"
            ffmpeg("-ss", t, "-i", wd / "sermon.mp4", "-frames:v", "1", f)
            im = Image.open(f).convert("RGB").resize(cell)
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, 120, 40], fill="black")
            d.text((6, 2), f"{t:g}", font=font(30), fill="yellow")
            sheet.paste(im, ((i % cols) * W, (i // cols) * H))
        p = out / f"{name}_{n}.jpg"
        sheet.save(p, quality=85)
        log(str(p))


def thumb_font(size):
    from PIL import ImageFont
    for n in ("seguibl.ttf", "ariblk.ttf", "impact.ttf", "arialbd.ttf"):
        p = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / n
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return font(size)


def sermon_to_raw(wd, t):
    """Second in the edited sermon -> second in the raw recording (via render's kept ranges)."""
    rr = wd / "render_results.json"
    if not rr.exists():
        return None
    for s, e in json.loads(rr.read_text())["sermon_kept_ranges"]:
        if t <= e - s:
            return s + t
        t -= e - s
    return None


def raw_source(wd):
    p = wd / "source.json"
    src = Path(json.loads(p.read_text())["source"]) if p.exists() else None
    return src if src and src.exists() else None


def sharpness(img, box):
    from PIL import ImageFilter, ImageStat
    return ImageStat.Stat(img.convert("L").crop(box).filter(ImageFilter.FIND_EDGES)).var[0]


def face_box(size, fx, fy):
    w, h = size
    return (int(max(0, fx * w - 0.07 * w)), int(max(0, fy * h - 0.13 * h)),
            int(min(w, fx * w + 0.07 * w)), int(min(h, fy * h + 0.15 * h)))


def cmd_thumb_frames_raw(a, wd, out):
    """Every frame of the RAW recording within ±0.6 s of --around, scored for sharpness on the
    face, so the thumbnail uses the crispest frame with a good expression."""
    from PIL import Image, ImageDraw
    src, rt = raw_source(wd), sermon_to_raw(wd, a.around)
    if not src or rt is None:
        die("raw recording (X6 drive) or render_results.json not available — use --around without --raw")
    fx, fy = (float(v) for v in a.face.split(",")) if a.face else (0.5, 0.35)
    tmp = out / "raw_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    ffmpeg("-ss", round(rt - 0.6, 3), "-i", src, "-t", 1.2, "-fps_mode", "passthrough", tmp / "r_%03d.png")
    files = sorted(tmp.glob("r_*.png"))
    step = 1.2 / max(1, len(files))
    rows = []
    for i, f in enumerate(files):
        t = round(a.around - 0.6 + i * step, 2)
        dest = out / f"r_{t:.2f}.png"
        shutil.move(str(f), dest)
        im = Image.open(dest)
        rows.append((t, sharpness(im, face_box(im.size, fx, fy)), dest))
    tmp.rmdir()
    top = {r[0] for r in sorted(rows, key=lambda r: -r[1])[:5]}
    cw, ch, cols = 300, 360, 8
    sheet = Image.new("RGB", (cw * cols, ch * ((len(rows) + cols - 1) // cols)), (20, 20, 20))
    for i, (t, sc, p) in enumerate(rows):
        im = Image.open(p).convert("RGB")
        w, h = im.size
        cx, cy = fx * w, fy * h
        cell = im.crop((int(cx - 0.09 * w), int(cy - 0.12 * h), int(cx + 0.09 * w), int(cy + 0.26 * h))).resize((cw, ch))
        d = ImageDraw.Draw(cell)
        if t in top:
            d.rectangle([0, 0, cw - 1, ch - 1], outline=(60, 220, 90), width=6)
        d.rectangle([0, 0, cw, 36], fill="black")
        d.text((6, 2), f"{t:.2f}  sharp {sc:.0f}", font=font(24), fill="yellow")
        sheet.paste(cell, ((i % cols) * cw, (i // cols) * ch))
    p = out / f"raw_{a.around:g}.jpg"
    sheet.save(p, quality=88)
    log(str(p))
    log("Sharpest (green): " + ", ".join(f"{t:.2f}" for t, _, _ in sorted(rows, key=lambda r: -r[1])[:5]))


def enhance_frame(im):
    """Make a video still crisper WITHOUT redrawing anything (no AI face restore): light denoise,
    2x upscale, two unsharp passes, a touch of contrast."""
    from PIL import Image, ImageEnhance, ImageFilter
    im = im.filter(ImageFilter.MedianFilter(3))
    im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
    im = im.filter(ImageFilter.UnsharpMask(radius=3, percent=110, threshold=2))
    im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=90, threshold=1))
    im = ImageEnhance.Contrast(im).enhance(1.07)
    return ImageEnhance.Color(im).enhance(1.04)


def series_info(plan, th):
    name = th.get("series") or plan.get("lower_third_subtitle", "").split(" · ")[0].strip()
    info = CONFIG.get("series_art", {}).get(name) or {}
    art = Path(info["path"]) if info.get("path") else None
    return name, (art if art and art.exists() else None), info


def pastor_cutout(wd, th):
    """The pastor cut out of the thumbnail frame (RGBA) and his face position inside the cut-out.
    Cached in work\\<date>\\pastor_cutout.png/.json so the carousel reuses the thumbnail's shot."""
    from PIL import Image
    from rembg import remove, new_session
    png, meta = wd / "pastor_cutout.png", wd / "pastor_cutout.json"
    key = {k: th.get(k) for k in ("at", "pastor_x", "pastor_y", "body_box", "fade_from", "raw")}
    if png.exists() and meta.exists() and json.loads(meta.read_text())["key"] == key:
        return Image.open(png), tuple(json.loads(meta.read_text())["face"])
    frame = wd / "thumb_frame.png"
    src, rt = raw_source(wd), sermon_to_raw(wd, th["at"])
    if src and rt is not None and th.get("raw", True):
        ffmpeg("-ss", round(rt, 3), "-i", src, "-frames:v", "1", frame)  # straight from the camera file
    else:
        log("Raw recording not available — using the edited sermon for the frame.")
        ffmpeg("-ss", th["at"], "-i", wd / "sermon.mp4", "-frames:v", "1", frame)
    img_src = enhance_frame(Image.open(frame).convert("RGB"))
    sw, sh = img_src.size
    fx, fy = th["pastor_x"] * sw, th.get("pastor_y", 0.36) * sh

    # Cut the pastor out, waist-up (default box) and fade him out before the pulpit.
    l, t, r, b = th.get("body_box") or [th["pastor_x"] - 0.16, th.get("pastor_y", 0.36) - 0.16,
                                       th["pastor_x"] + 0.19, th.get("pastor_y", 0.36) + 0.41]
    box = (int(max(0, l * sw)), int(max(0, t * sh)), int(min(sw, r * sw)), int(min(sh, b * sh)))
    cut = remove(img_src.crop(box), session=new_session("u2net_human_seg"))
    fs = int(255 * float(th.get("fade_from", 0.72)))
    fade = Image.linear_gradient("L").resize(cut.size).point(
        lambda v: 255 if v < fs else int(255 * (255 - v) / max(1, 255 - fs)))
    cut.putalpha(Image.composite(cut.split()[3], Image.new("L", cut.size, 0), fade))
    face = (fx - box[0], fy - box[1])
    cut.save(png)
    save_json(meta, {"key": key, "face": face})
    return cut, face


def cmd_thumbnail(a):
    """1280x720 YouTube thumbnail from plan["thumbnail"]: the pastor cut out of a real (sharpened,
    never retouched) frame, in front of the series artwork, with the series logo and a bold caption."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    date = sunday_of(a.date)
    wd = workdir(date)
    plan = load_plan(date)
    th = plan["thumbnail"]
    W, H = 1280, 720
    cut, (face_x, _) = pastor_cutout(wd, th)

    name, art_path, info = series_info(plan, th)
    accent = tuple(info.get("accent") or CONFIG["brand_accent_rgb"])
    side = th.get("pastor_side", "left")
    # Background: the series art (crisp, dimmed) — or, with no art on file, his own stage blurred.
    if art_path:
        art = Image.open(art_path).convert("RGB")
        sc = max(W / art.width, H / art.height)
        bg = art.resize((int(art.width * sc) + 1, int(art.height * sc) + 1), Image.LANCZOS)
        bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(1.5))).enhance(0.68)
    else:
        stage = Image.open(wd / "thumb_frame.png").convert("RGB")
        bg = ImageEnhance.Brightness(stage.resize((W, H)).filter(ImageFilter.GaussianBlur(12))).enhance(0.35)
        log(f"No series art in config for '{name}' — using a blurred stage background.")
    img = bg.convert("RGBA")
    # Darken the caption side so the words read.
    wash = Image.new("L", (W, 1))
    for x in range(W):
        u = x / W if side == "left" else 1 - x / W
        wash.putpixel((x, 0), int(185 * min(1, max(0, (u - 0.40) / 0.22))))
    img = Image.composite(Image.new("RGBA", (W, H), (12, 6, 2, 255)), img, wash.resize((W, H)))

    # Place him: height ~106% of the frame, face at pastor_at across. No glow/halo (the pastor
    # asked, Nov 2026 — it read as "angelic"); just a soft dark shadow so the cutout sits naturally.
    s = H * 1.06 / cut.height
    c = cut.resize((int(cut.width * s), int(cut.height * s)), Image.LANCZOS)
    face_in_c = face_x * s
    at = float(th.get("pastor_at", 0.2))
    px = int((at * W if side == "left" else (1 - at) * W) - face_in_c)
    pos = (px, H - c.height + 24)
    soft = c.split()[3].filter(ImageFilter.GaussianBlur(8))
    shadow = Image.new("RGBA", c.size, (0, 0, 0, 0))
    shadow.putalpha(soft.point(lambda v: int(v * 0.35)))
    img.alpha_composite(shadow, pos)
    img.alpha_composite(c, pos)

    d = ImageDraw.Draw(img)
    if art_path:  # series logo badge (from the art), accent border
        cl, ct, cr, cb = info.get("badge_crop", [0, 0, 1, 1])
        aw, ah = art.size
        logo = art.crop((int(cl * aw), int(ct * ah), int(cr * aw), int(cb * ah)))
        lw = 340
        logo = logo.resize((lw, int(lw * logo.height / logo.width)), Image.LANCZOS)
        bx = W - lw - 45 if side == "left" else 45
        by = 34
        d.rounded_rectangle([bx - 4, by - 4, bx + lw + 4, by + logo.height + 4], 22, fill=accent)
        lm = Image.new("L", logo.size, 0)
        ImageDraw.Draw(lm).rounded_rectangle([0, 0, *logo.size], 20, fill=255)
        img.paste(logo, (bx, by), lm)
        cap_top = by + logo.height + 30
    else:  # text badge
        tag = plan.get("lower_third_subtitle", name).upper()
        tf = font(30)
        tw = d.textlength(tag, font=tf)
        bx = W - 45 - tw - 36 if side == "left" else 45
        d.rounded_rectangle([bx, 40, bx + tw + 36, 90], 10, fill=(20, 12, 6), outline=accent, width=3)
        d.text((bx + 18, 46), tag, font=tf, fill=(255, 230, 200))
        cap_top = 130
    box_l, box_r = (int(W * 0.47), W - 45) if side == "left" else (45, int(W * 0.53))
    box_t, box_b = cap_top, H - 40

    # Caption: biggest size where every line fits; highlight lines 1.45x in the accent color.
    lines = [(x, False) for x in th["caption"]] + [(x, True) for x in th.get("highlight", [])]
    size = 200
    while size > 40:
        fonts = [thumb_font(int(size * (1.45 if hi else 1))) for _, hi in lines]
        widths = [d.textlength(x, font=f) for (x, _), f in zip(lines, fonts)]
        heights = [f.getbbox(x)[3] - f.getbbox(x)[1] for (x, _), f in zip(lines, fonts)]
        total = sum(heights) + 14 * (len(lines) - 1)
        if max(widths) <= box_r - box_l and total <= box_b - box_t:
            break
        size -= 4
    y = box_t + (box_b - box_t - total) // 2
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    pos_ = []
    for (x_, hi), f, w, h in zip(lines, fonts, widths, heights):
        x = box_r - w if side == "left" else box_l
        top = y - f.getbbox(x_)[1]
        pos_.append((x, top, x_, hi, f))
        sd.text((x + 6, top + 9), x_, font=f, fill=(0, 0, 0, 230))
        y += h + 14
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(9)))
    d = ImageDraw.Draw(img)
    for x, top, x_, hi, f in pos_:
        d.text((x, top), x_, font=f, fill=accent if hi else (255, 255, 255),
               stroke_width=max(3, size // 26), stroke_fill=(0, 0, 0))

    out = wd / "thumbnail.jpg"
    img.convert("RGB").save(out, quality=93)
    log(str(out))
    if not a.test:
        dest = sermon_folder(plan, date)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(out, dest / f"{date.isoformat()} - {safe_name(plan['file_title'])} - thumbnail.jpg")


def set_thumbnail(api, video_id, path):
    from googleapiclient.http import MediaFileUpload
    try:
        api.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(path), mimetype="image/jpeg")).execute()
        return "set"
    except Exception as e:  # e.g. channel not verified for custom thumbnails
        log(f"Could not set thumbnail: {e}")
        return f"failed: {e}"


# ---------------------------------------------------------------- cleanup

KEEP_WEEKS = 4
DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def to_recycle_bin(path):
    """Delete via the Windows shell with undo allowed: local files go to the Recycle Bin;
    on the Google Drive (G:) the deletion syncs to Drive's Trash (kept 30 days)."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path) + "\0\0", None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc or op.fAnyOperationsAborted:
        raise OSError(f"could not delete {path} (code {rc})")


def cmd_cleanup(a):
    """Keep the last 4 weeks; recycle anything dated earlier. Reference = --date or the most
    recent Sunday. E.g. Sunday 2026-11-15 -> everything dated before 2026-10-18 goes.
      * laptop work/<date>/: everything EXCEPT the small .json records (done.json must stay,
        or `pending` would re-process that Sunday if the recording is still on the X6)
      * Media\\Instagram Slides\\<date>  and  Media\\Editing Projects\\<date> - Resolve timeline
    Never touched: Sermon MP3s, Sermons videos, Worship song videos, Practice files,
    and undated work folders (voices, practice, backfill...)."""
    if a.date:
        ref = dt.date.fromisoformat(a.date)
    else:
        today = dt.date.today()
        ref = today - dt.timedelta(days=(today.weekday() + 1) % 7)  # most recent Sunday
    cutoff = ref - dt.timedelta(weeks=KEEP_WEEKS)
    log(f"Keeping {cutoff} and later; removing older ({'DRY RUN' if a.dry_run else 'to Recycle Bin / Drive Trash'}).")

    def old(name):
        m = DATED.match(name)
        if not m:
            return False
        try:
            return dt.date.fromisoformat(m.group(1)) < cutoff
        except ValueError:
            return False

    targets = []
    for d in sorted(WORK.iterdir()):
        if d.is_dir() and DATED.fullmatch(d.name) and old(d.name):
            targets += [p for p in sorted(d.iterdir()) if not (p.is_file() and p.suffix.lower() == ".json")]
    media = Path(CONFIG["media_folder"])
    for sub in ("Instagram Slides", "Editing Projects"):
        folder = media / sub
        if folder.exists():
            targets += [p for p in sorted(folder.iterdir()) if old(p.name)]

    freed, failed = 0, []
    for p in targets:
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
        log(f"  {'would remove' if a.dry_run else 'removing'}  {p}  ({size / 1e6:,.0f} MB)")
        if a.dry_run:
            freed += size
            continue
        try:
            to_recycle_bin(p)
            freed += size
        except OSError as e:
            failed.append(str(p))
            log(f"    FAILED: {e}")
    log(f"{'Would free' if a.dry_run else 'Freed'} {freed / 1e9:.1f} GB from {len(targets) - len(failed)} item(s)."
        + (f" {len(failed)} failed." if failed else ""))
    save_json(WORK / "cleanup_last.json", {"ref": ref.isoformat(), "cutoff": cutoff.isoformat(),
                                           "dry_run": a.dry_run, "removed": len(targets) - len(failed),
                                           "gb": round(freed / 1e9, 1), "failed": failed})


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("find", "notes", "transcribe", "audiomap", "render", "slides", "upload",
                 "youtube-login", "opus-upload", "pending", "drive-link",
                 "thumbnail", "thumb-frames", "live", "carousel", "cleanup"):
        p = sub.add_parser(name)
        if name == "cleanup":
            p.add_argument("--dry-run", action="store_true", help="list what would go, delete nothing")
        if name == "pending":
            p.add_argument("--claim", action="store_true", help="mark the recording as being worked on")
        if name == "drive-link":
            p.add_argument("--path", required=True, help=r"folder or file under G:\Shared drives")
            p.add_argument("--wait", type=int, default=10, help="minutes to wait for Drive to sync it")
        if name == "opus-upload":
            p.add_argument("--url", required=True, help="upload_url from opusclip_create_upload_link")
        p.add_argument("--date", help="the Sunday, YYYY-MM-DD (default: most recent Sunday)")
        if name == "find":
            p.add_argument("--no-wait", action="store_true", help="don't wait for sync to settle")
        if name == "render":
            p.add_argument("--skip-songs", action="store_true")
            p.add_argument("--test", action="store_true", help="write songs/MP3 under work/<date>/TEST OUTPUT")
            p.add_argument("--timeline-only", action="store_true", help="only write the Resolve timeline")
        if name == "upload":
            p.add_argument("--privacy", choices=["private", "unlisted", "public"])
            p.add_argument("--thumbnail-only", action="store_true",
                           help="just set thumbnail.jpg on the already-uploaded video")
        if name in ("thumbnail", "carousel"):
            p.add_argument("--test", action="store_true", help="don't copy it to Google Drive")
        if name == "thumb-frames":
            p.add_argument("--around", type=float, help="close look (0.5 s steps) around this second")
            p.add_argument("--raw", action="store_true",
                           help="with --around: every raw-camera frame within ±0.6 s, scored for sharpness")
            p.add_argument("--face", help="with --raw: face position as x,y fractions of the frame, e.g. 0.11,0.36")
    a = ap.parse_args()
    globals()[f"cmd_{a.cmd.replace('-', '_')}"](a)


if __name__ == "__main__":
    main()
