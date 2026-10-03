"""New Light Church Sunday pipeline — the mechanical half.

Claude (via the new-light-sunday skill) makes the judgment calls — where the songs and
sermon are, what's date-specific, titles/descriptions — and writes them to plan.json.
This script does everything deterministic around that:

  python pipeline/nlc.py find        [--date YYYY-MM-DD]   locate that Sunday's Worship recording
  python pipeline/nlc.py notes       [--date YYYY-MM-DD]   print that week's sermon notes file
  python pipeline/nlc.py transcribe  --date YYYY-MM-DD     transcribe the recording (local Whisper)
  python pipeline/nlc.py render      --date YYYY-MM-DD     cut sermon (1080p) + radio MP3 + songs (480p)
  python pipeline/nlc.py slides      --date YYYY-MM-DD     build the 5 Instagram slides
  python pipeline/nlc.py upload      --date YYYY-MM-DD     upload the sermon video to YouTube

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
    today = dt.date.today()
    return today - dt.timedelta(days=(today.weekday() + 1) % 7)  # most recent Sunday (today if Sunday)


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


def find_recording(date):
    hits = []
    for root in search_roots():
        for f in root.glob("*.mp4"):
            n = f.name
            if not n.lower().startswith("worship") or "conflict" in n.lower() or n.startswith("._"):
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
        die(f"No 'Worship…' recording dated {date} in: " + ", ".join(map(str, search_roots())))
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


def render_mp3(plan, sermon_mp4, date, test_root=None):
    folder = (test_root / "Radio Files" if test_root else Path(CONFIG["radio_folder"])) / str(date.year)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{date.isoformat()} - {safe_name(plan['file_title'])}.mp3"
    ffmpeg("-i", sermon_mp4, "-vn", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
           "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k",
           "-metadata", f"title={plan['file_title']}",
           "-metadata", f"artist={plan['speaker']}",
           "-metadata", f"album={CONFIG['church_name']}",
           "-metadata", f"date={date.isoformat()}", out)
    log(f"Radio MP3: {out}")
    return out


def render_songs(plan, src, date, test_root=None):
    """Matches the existing Worship Songs layout: <date>/<date> - <n> - <Song>.mp4 + Song Log.csv."""
    root = test_root / "Worship Songs" if test_root else Path(CONFIG["worship_songs_folder"])
    dest = root / date.isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    outs, rows = [], []
    for n, song in enumerate(plan.get("songs", []), 1):
        name = f"{date.isoformat()} - {n} - {safe_name(song['title'])}.mp4"
        out = dest / name
        ffmpeg("-ss", song["start"], "-to", song["end"], "-i", src,
               "-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out)
        outs.append(str(out))
        rows.append([date.isoformat(), n, song["title"], song.get("artist", ""),
                     mmss(song["end"] - song["start"]), f"{mmss(song['start'])}-{mmss(song['end'])}", name])
        log(f"Song: {out}")

    import csv
    logf = root / "Song Log.csv"
    existing = []
    if logf.exists():
        with open(logf, newline="", encoding="utf-8-sig") as fh:
            existing = [r for r in csv.reader(fh)][1:]
    existing = [r for r in existing if r and r[0] != date.isoformat()]  # re-runs replace that date's rows
    with open(logf, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Order", "Song", "Artist", "Length", "Service time", "File"])
        w.writerows(existing + [[str(c) for c in r] for r in rows])
    log(f"Updated {logf}")
    return outs


def cmd_render(a):
    date = sunday_of(a.date)
    wd = workdir(date)
    plan = load_plan(date)
    src = json.loads((wd / "source.json").read_text())["source"]
    test_root = wd / "TEST OUTPUT" if a.test else None
    if test_root:
        log(f"TEST MODE — songs and MP3 go to {test_root}, not Dropbox")
    results = {}
    if not a.skip_songs:
        results["songs"] = render_songs(plan, src, date, test_root)
    sermon, keeps = render_sermon(plan, src, wd, date)
    results["sermon_video"] = str(sermon)
    results["sermon_kept_ranges"] = [[round(x, 2), round(y, 2)] for x, y in keeps]
    results["radio_mp3"] = str(render_mp3(plan, sermon, date, test_root))
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
        img.save(out, optimize=True)
        outs.append(str(out))
    log("\n".join(outs))


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
                       client_secret=c["client_secret"], token_uri="https://oauth2.googleapis.com/token")


def cmd_upload(a):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    date = sunday_of(a.date)
    wd = workdir(date)
    yt = load_plan(date)["youtube"]
    privacy = a.privacy or yt.get("privacy") or CONFIG["youtube_privacy"]
    api = build("youtube", "v3", credentials=youtube_creds(), cache_discovery=False)
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
    save_json(wd / "youtube_result.json", res)
    log(json.dumps(res, indent=2))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("find", "notes", "transcribe", "audiomap", "render", "slides", "upload"):
        p = sub.add_parser(name)
        p.add_argument("--date", help="the Sunday, YYYY-MM-DD (default: most recent Sunday)")
        if name == "find":
            p.add_argument("--no-wait", action="store_true", help="don't wait for sync to settle")
        if name == "render":
            p.add_argument("--skip-songs", action="store_true")
            p.add_argument("--test", action="store_true", help="write songs/MP3 under work/<date>/TEST OUTPUT")
        if name == "upload":
            p.add_argument("--privacy", choices=["private", "unlisted", "public"])
    a = ap.parse_args()
    globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()
