r"""New Light worship-practice organizer — the mechanical half.

Claude (via the new-light-practice skill) listens through the transcript, identifies each
song and where the band worked on it, and writes plan.json. This script does the rest:

  practice.cmd pending [--claim]        next unprocessed practice recording on the X6 drive (or NONE)
  practice.cmd transcribe --key KEY     transcribe it (speech filter OFF, so lyrics are caught)
  practice.cmd render --key KEY         cut each song at 480p into Worship\Practice Worship Files

KEY is "<date>_<file stem>", printed by `pending`. Working files: work/practice/<KEY>/.
Practice recordings are dated by when they were recorded (file modified time) — the ATEM
re-uses old names (e.g. "Practice_9_30_2026 02.mp4" was recorded 2026-10-07).
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nlc import CONFIG, WORK, die, duration, ffmpeg, hms, log, mmss, safe_name, save_json, search_roots  # noqa: E402

PWORK = WORK / "practice"
LOCK_HOURS = 6


def out_root():
    return Path(CONFIG.get("practice_folder", r"G:\Shared drives\Worship\Practice Worship Files"))


def recordings():
    """Practice recordings on any plugged-in Crucial X6 drive, keyed by KEY."""
    min_bytes = CONFIG.get("practice_min_gb", 0.3) * 1e9
    found = {}
    for root in search_roots():
        if not root.drive or root.drive.upper() == "C:":  # X6 drives only, not Dropbox copies
            continue
        for f in root.glob("*.mp4"):
            n = f.name.lower()
            if not n.startswith("practice") or "conflict" in n or n.startswith("._"):
                continue
            if f.stat().st_size < min_bytes:  # false starts / stubs
                continue
            recorded = dt.datetime.fromtimestamp(f.stat().st_mtime)
            key = f"{recorded.date().isoformat()}_{safe_name(f.stem).replace(' ', '_')}"
            found[key] = f
    return dict(sorted(found.items()))


def pwd(key):
    d = PWORK / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_pending(a):
    for key, f in recordings().items():
        wd = PWORK / key
        if (wd / "done.json").exists():
            continue
        lock = wd / "running.json"
        if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_HOURS * 3600:
            continue
        if a.claim:
            pwd(key)
            save_json(lock, {"started": dt.datetime.now().isoformat(timespec="seconds"), "source": str(f)})
        info = {"key": key, "date": key[:10], "source": str(f), "gb": round(f.stat().st_size / 1e9, 1)}
        save_json(pwd(key) / "source.json", info) if a.claim else None
        log(json.dumps(info))
        return
    log("NONE")


def load_wav(path):
    import wave
    import numpy as np
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


def cmd_transcribe(a):
    wd = pwd(a.key)
    src = json.loads((wd / "source.json").read_text())["source"]
    wav = wd / "audio16k.wav"
    if not wav.exists():
        log("Extracting audio...")
        ffmpeg("-i", src, "-vn", "-ac", "1", "-ar", "16000", wav)
    audio = load_wav(wav)

    from faster_whisper import WhisperModel
    model = WhisperModel(CONFIG["whisper_model"], device="cpu", compute_type="int8", cpu_threads=os.cpu_count())
    log("Transcribing (speech filter off so sung lyrics come through)...")
    segs, t0 = [], time.time()
    segments, _ = model.transcribe(audio, language="en", vad_filter=False,
                                   condition_on_previous_text=False, no_speech_threshold=0.9)
    for s in segments:
        segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        if len(segs) % 100 == 0:
            log(f"  ...{hms(s.end)} ({time.time() - t0:.0f}s)")
    save_json(wd / "transcript.json", segs)
    with open(wd / "transcript.txt", "w", encoding="utf-8") as fh:
        for s in segs:
            fh.write(f"[{hms(s['start'])}] {s['text']}\n")

    # Loudness every 10 s: playing vs. stopped/talking is the easiest way to see the takes.
    import numpy as np
    n = len(audio) // 16000
    db = 20 * np.log10(np.sqrt(np.mean(audio[: n * 16000].reshape(n, 16000) ** 2, axis=1)) + 1e-9)
    lines = ["# time | loudness dB | guess (PLAYING = band playing; quiet = stopped / talking)"]
    for t in range(0, n, 10):
        d = float(np.mean(db[t:t + 10]))
        lines.append(f"{hms(t)} | {d:6.1f} | {'PLAYING' if d > -30 else 'quiet'}")
    (wd / "audio_map.txt").write_text("\n".join(lines), encoding="utf-8")
    log(f"Wrote {wd / 'transcript.txt'} and audio_map.txt ({hms(n)} recording)")


def cmd_render(a):
    wd = PWORK / a.key
    plan = json.loads((wd / "plan.json").read_text(encoding="utf-8"))
    src = json.loads((wd / "source.json").read_text())["source"]
    date = a.key[:10]
    root = (wd / "TEST OUTPUT" / "Practice Worship Files") if a.test else out_root()
    dest = root / date
    dest.mkdir(parents=True, exist_ok=True)
    # Two recordings from the same night share the folder — keep numbering going.
    n0 = len([p for p in dest.glob(f"{date} - * - *.mp4")])
    rows, outs = [], []
    total = duration(src)
    for i, s in enumerate(plan["songs"], 1):
        start, end = max(0.0, s["start"] - 2), min(total, s["end"] + 2)
        title = s["title"] + (f" (part {s['part']})" if s.get("part", 1) > 1 else "")
        name = f"{date} - {n0 + i} - {safe_name(title)}.mp4"
        out = dest / name
        if not out.exists():
            # Low-quality reference copy to save space: 480p, heavy compression, 15 fps;
            # audio kept clear enough to hear the parts.
            ffmpeg("-ss", start, "-to", end, "-i", src, "-vf", "scale=-2:480,fps=15",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                   "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out)
        outs.append(str(out))
        rows.append([date, n0 + i, title, s.get("artist", ""), mmss(end - start),
                     f"{mmss(start)}-{mmss(end)}", s.get("takes", ""), s.get("notes", ""),
                     Path(src).name, name])
        log(f"Song: {out}")

    logf = root / "Practice Log.csv"
    header = ["Date", "Order", "Song", "Artist", "Length", "Recording time", "Takes", "Notes",
              "Recording", "File"]
    existing = []
    if logf.exists():
        with open(logf, newline="", encoding="utf-8-sig") as fh:
            existing = list(csv.reader(fh))[1:]
    existing = [r for r in existing if r and not (r[0] == date and len(r) > 8 and r[8] == Path(src).name)]
    with open(logf, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(existing + [[str(c) for c in r] for r in rows])
    save_json(wd / "render_results.json", {"folder": str(dest), "songs": outs})
    log(f"Updated {logf}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pending")
    p.add_argument("--claim", action="store_true")
    for name in ("transcribe", "render"):
        p = sub.add_parser(name)
        p.add_argument("--key", required=True)
        if name == "render":
            p.add_argument("--test", action="store_true")
    a = ap.parse_args()
    globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()
