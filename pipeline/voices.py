r"""Who sang lead? Voice profiles for the New Light worship team (kept on this laptop only).

  voices.cmd samples  --dates 2026-10-11 2026-10-04 ...   separate vocals + fingerprint each song
  voices.cmd cluster  [--k 9]                             group the fingerprints, make a listening page
  voices.cmd label    --map "1=Mike,2=Sarah,3=team"       save named groups as singer profiles
  voices.cmd identify --file <song.mp4> [--json out.json] who sang which part of one song

Songs come from G:\Shared drives\Worship\<date>\*.mp4 (the 480p copies are fine for voices).
Vocals are separated from the band with Demucs, then each ~4 s window of singing gets a
voice fingerprint (SpeechBrain ECAPA). Everything lives in work/voices/ (gitignored).
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nlc import CONFIG, WORK, hms, log  # noqa: E402
from platform_paths import localize_path  # noqa: E402

VW = WORK / "voices"
WIN, HOP = 4.0, 2.0          # fingerprint window / step, seconds
MIN_DB = -30                 # vocal-stem loudness needed to count as singing
MATCH = 0.30                 # similarity (centered fingerprints) needed to call a voice by name


def worship_root():
    return Path(CONFIG.get("worship_songs_folder") or localize_path(r"G:\Shared drives\Worship"))


# ------------------------------------------------------------------ audio helpers

def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)])
    if r.returncode:
        raise RuntimeError("ffmpeg failed")


def vocals_for(song: Path) -> Path:
    """Demucs vocal stem for one song (cached)."""
    out = VW / "stems" / (song.stem + ".vocals.wav")
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = VW / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    mix = tmp / "mix.wav"
    ffmpeg("-i", song, "-vn", "-ac", "2", "-ar", "44100", mix)
    log(f"  separating vocals: {song.name}")
    subprocess.run([sys.executable, "-m", "demucs", "--two-stems", "vocals", "-n", "htdemucs",
                    "-j", str(max(1, (os.cpu_count() or 2) // 2)), "-o", str(tmp), str(mix)], check=True,
                   stdout=subprocess.DEVNULL)
    stem = tmp / "htdemucs" / "mix" / "vocals.wav"
    stem.replace(out)
    return out


def load_16k_mono(path: Path):
    import soundfile as sf
    raw = VW / "tmp" / "v16.wav"
    ffmpeg("-i", path, "-ac", "1", "-ar", "16000", raw)
    audio, _ = sf.read(raw, dtype="float32")
    return audio


_model = None


def embedder():
    global _model
    if _model is None:
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy
        # COPY, not the default symlink — Windows won't let a normal user create symlinks.
        _model = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                                savedir=str(VW / "model"), run_opts={"device": "cpu"},
                                                local_strategy=LocalStrategy.COPY)
    return _model


def fingerprints(vocals: Path):
    """[(start_sec, embedding)] for every loud-enough window of the vocal stem."""
    import torch
    audio = load_16k_mono(vocals)
    sr, w, h = 16000, int(WIN * 16000), int(HOP * 16000)
    starts, chunks = [], []
    for s in range(0, max(0, len(audio) - w), h):
        seg = audio[s:s + w]
        if 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9) > MIN_DB:
            starts.append(s / sr)
            chunks.append(seg)
    if not chunks:
        return []
    m = embedder()
    out = []
    for i in range(0, len(chunks), 32):
        batch = torch.tensor(np.stack(chunks[i:i + 32]))
        e = m.encode_batch(batch).squeeze(1).detach().numpy()
        e /= np.linalg.norm(e, axis=1, keepdims=True)
        out += list(zip(starts[i:i + 32], e))
    return out


# ------------------------------------------------------------------ commands

def cmd_samples(a):
    db = VW / "fingerprints.npz"
    meta, embs = [], []
    if db.exists():
        d = np.load(db, allow_pickle=True)
        meta, embs = list(d["meta"]), list(d["embs"])
    done = {m[0] for m in meta}
    for date in a.dates:
        for song in sorted((worship_root() / date).glob("*.mp4")):
            if str(song) in done:
                continue
            fp = fingerprints(vocals_for(song))
            meta += [(str(song), t) for t, _ in fp]
            embs += [e for _, e in fp]
            np.savez(db, meta=np.array(meta, dtype=object), embs=np.array(embs))
            log(f"  {song.name}: {len(fp)} singing windows")
    log(f"{len(embs)} fingerprints from {len({m[0] for m in meta})} songs")


def centered(embs, mean):
    """Remove the 'everyone is singing' direction so the differences between voices dominate."""
    e = embs - mean
    return e / np.linalg.norm(e, axis=-1, keepdims=True)


def roster_hints(meta, idx):
    """Who was scheduled to sing on the Sundays this group shows up (work/voices/rosters.json)."""
    rf = VW / "rosters.json"
    if not rf.exists():
        return []
    rosters = json.loads(rf.read_text())
    dates = [Path(meta[i][0]).name[:10] for i in idx]
    score = {}
    for name in {n for v in rosters.values() for n in v}:
        on = sum(1 for d in dates if name in rosters.get(d, []))
        score[name] = on / len(dates)
    return [n for n, s in sorted(score.items(), key=lambda x: -x[1]) if s >= 0.85][:4]


def cmd_cluster(a):
    from sklearn.cluster import AgglomerativeClustering
    d = np.load(VW / "fingerprints.npz", allow_pickle=True)
    meta, embs = d["meta"], d["embs"]
    mean = embs.mean(0)
    np.save(VW / "mean.npy", mean)
    embs = centered(embs, mean)
    labels = AgglomerativeClustering(n_clusters=a.k, linkage="ward").fit_predict(embs)
    clip_dir = VW / "samples"
    clip_dir.mkdir(parents=True, exist_ok=True)
    for f in clip_dir.glob("*.mp3"):
        f.unlink()
    groups, n = [], 0
    for c in sorted(set(labels), key=lambda c: -np.sum(labels == c)):
        idx = np.where(labels == c)[0]
        if len(idx) < 8:  # tiny clusters = noise / crowd; skip
            continue
        n += 1
        centroid = embs[idx].mean(0)
        centroid /= np.linalg.norm(centroid)
        order = idx[np.argsort(-(embs[idx] @ centroid))]
        picks, songs_used = [], set()
        for i in order:  # most typical windows, from different songs
            song, t = meta[i]
            if song in songs_used:
                continue
            songs_used.add(song)
            picks.append((song, float(t)))
            if len(picks) == 3:
                break
        clips = []
        for j, (song, t) in enumerate(picks, 1):
            stem = VW / "stems" / (Path(song).stem + ".vocals.wav")
            out = clip_dir / f"voice{n}_{j}.mp3"
            ffmpeg("-ss", max(0, t - 2), "-t", 8, "-i", stem, "-ac", "1", "-b:a", "96k", out)
            clips.append({"file": out.name, "song": Path(song).stem, "at": hms(t)})
        songs = sorted({Path(meta[i][0]).stem.split(" - ", 2)[-1] for i in idx})
        groups.append({"voice": n, "cluster": int(c), "windows": int(len(idx)), "clips": clips,
                       "songs": songs[:12], "likely": roster_hints(meta, idx)})
    (VW / "clusters.json").write_text(json.dumps({"labels": labels.tolist(), "groups": groups}, indent=1))
    rows = "".join(
        f"<section><h2>Voice {g['voice']}</h2><p class=meta>{g['windows']} moments · e.g. "
        f"{', '.join(g['songs'][:5])}</p>" +
        (f"<p class=hint>Scheduled every Sunday this voice shows up: <b>{', '.join(g['likely'])}</b></p>"
         if g["likely"] else "") +
        "".join(f"<div class=clip><audio controls preload=none src='samples/{c['file']}'></audio>"
                f"<span>{c['song']} @ {c['at']}</span></div>" for c in g["clips"]) + "</section>"
        for g in groups)
    (VW / "voices.html").write_text(f"""<!doctype html><meta charset=utf-8>
<title>Who is this voice?</title>
<style>body{{font:15px system-ui;max-width:760px;margin:24px auto;padding:0 16px;background:#fafafa;color:#222}}
section{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:12px 16px;margin:12px 0}}
h2{{margin:0 0 4px}}.meta{{color:#666;margin:0 0 8px}}.clip{{display:flex;gap:10px;align-items:center;margin:6px 0}}
audio{{height:32px}}span{{color:#555;font-size:13px}}.hint{{background:#eef6ff;border-radius:6px;padding:4px 8px;margin:0 0 8px}}</style>
<h1>Who is this voice?</h1>
<p>Each group is one voice (as best the computer can tell). Listen to the clips — vocals only,
band removed — and reply in chat like <b>1 = Mike, 2 = Sarah, 3 = team</b>. Say "mixed" if a
group has more than one person.</p>{rows}""", encoding="utf-8")
    log(f"{len(groups)} voice groups -> {VW / 'voices.html'}")


def cmd_label(a):
    d = np.load(VW / "fingerprints.npz", allow_pickle=True)
    embs = centered(d["embs"], np.load(VW / "mean.npy"))
    cl = json.loads((VW / "clusters.json").read_text())
    labels = np.array(cl["labels"])
    by_voice = {g["voice"]: g["cluster"] for g in cl["groups"]}
    pf = VW / "profiles.json"
    profiles = json.loads(pf.read_text()) if pf.exists() else {}
    for pair in a.map.split(","):
        v, name = [x.strip() for x in pair.split("=", 1)]
        if name.lower() in ("mixed", "skip", "?"):
            continue
        vecs = embs[labels == by_voice[int(v)]]
        profiles.setdefault(name, [])
        profiles[name] += [vecs.mean(0).tolist()]  # one centroid per labeled group
        log(f"  {name}: +{len(vecs)} moments")
    pf.write_text(json.dumps(profiles))
    log(f"Profiles: {', '.join(profiles)}")


def cmd_label_clips(a):
    """Build profiles from per-clip names in work/voices/clip_labels.json ("voice3_1": "Josh Bryant").
    Uses the fingerprints within ±4 s of each named clip; 'mixed'/'?'/'(lead) +' entries use the lead only."""
    import re
    d = np.load(VW / "fingerprints.npz", allow_pickle=True)
    meta, embs = d["meta"], centered(d["embs"], np.load(VW / "mean.npy"))
    groups = {g["voice"]: g for g in json.loads((VW / "clusters.json").read_text())["groups"]}
    labels = json.loads((VW / "clip_labels.json").read_text())
    vecs = {}
    for key, name in labels.items():
        m = re.fullmatch(r"voice(\d+)_(\d+)", key)
        if not m or not isinstance(name, str) or "?" in name:
            continue
        name = re.sub(r"\s*\(lead\).*$", "", name).strip()  # "Josh Bryant (lead) + Shelley…" -> Josh
        clip = groups[int(m[1])]["clips"][int(m[2]) - 1]
        song_stem = clip["song"]
        h, mi, s = map(int, clip["at"].split(":"))
        t0 = h * 3600 + mi * 60 + s
        idx = [i for i, (song, t) in enumerate(meta) if Path(song).stem == song_stem and abs(float(t) - t0) <= 4]
        vecs.setdefault(name, []).extend(embs[idx])
    profiles = {}
    for name, v in vecs.items():
        c = np.mean(v, 0)
        profiles[name] = [(c / np.linalg.norm(c)).tolist()]
        log(f"  {name}: {len(v)} fingerprints")
    extra = VW / "profiles_extra.json"  # profiles built from uploaded samples (added later)
    if extra.exists():
        for name, cs in json.loads(extra.read_text()).items():
            profiles.setdefault(name, []).extend(cs)
    (VW / "profiles.json").write_text(json.dumps(profiles))
    log(f"Profiles: {', '.join(sorted(profiles))}")


def identify_song(song: Path, candidates=None):
    """candidates = names scheduled that Sunday (from Planning Center); others are never guessed."""
    profiles = json.loads((VW / "profiles.json").read_text())
    if candidates:
        profiles = {n: v for n, v in profiles.items() if n in candidates}
    if not profiles:
        return {"song": song.name, "lead": "unknown", "seconds": {}, "sections": [], "summary": ""}
    names = list(profiles)
    cents = [np.array(profiles[n]) for n in names]
    mean = np.load(VW / "mean.npy")
    sections, tops, bests = [], [], []
    for t, e in fingerprints(vocals_for(song)):
        e = centered(e, mean)
        sims = [float(np.max(c @ e)) for c in cents]
        tops.append(max(sims)); bests.append(int(np.argmax(sims)))
        best = int(np.argmax(sims))
        who = names[best] if sims[best] >= MATCH else "unknown"
        if sections and sections[-1]["who"] == who and t - sections[-1]["end"] <= HOP * 2:
            sections[-1]["end"] = t + WIN
        else:
            sections.append({"who": who, "start": t, "end": t + WIN})
    sections = [s for s in sections if s["end"] - s["start"] >= 10]  # ignore blips
    totals = {}
    for s in sections:
        totals[s["who"]] = totals.get(s["who"], 0) + s["end"] - s["start"]
    named = {k: v for k, v in totals.items() if k not in ("unknown", "team")}
    lead = max(named, key=named.get) if named else "unknown"
    # Group singing: few moments clearly match any one voice. Calibrated (scheduled singers only) on
    # Oct 11 "Lay Me Down" = group (16% clear) vs known solos (52-87% clear).
    if tops and float(np.mean(np.array(tops) > MATCH)) < 0.35:
        lead = "group"
    summary = " · ".join(f"{s['who']} {hms(s['start'])[2:]}" for s in sections)
    return {"song": song.name, "lead": lead, "seconds": {k: round(v) for k, v in totals.items()},
            "sections": sections, "summary": summary}


def scheduled_singers(date):
    """Singers (config "singers") on that Sunday's Planning Center roster, any position (Karen is on Band)."""
    from pco import PCO
    p = PCO()
    pid = p.plans_by_date(date).get(date)
    if not pid:
        return None
    j = p.get(f"/service_types/{p.st}/plans/{pid}/team_members", per_page=100)
    on = {x["attributes"]["name"] for x in j["data"] if x["attributes"].get("status") != "D"}
    return [n for n in CONFIG.get("singers", []) if n in on]


def first(name):
    return name.split()[0]


def match_singer(word):
    """'Alison'/'Allison'/'Haley' -> full name from config singers (first name, forgiving spelling)."""
    import difflib
    singers = CONFIG.get("singers", [])
    firsts = {first(s).lower(): s for s in singers}
    w = word.strip().lower()
    if w in firsts:
        return firsts[w]
    close = difflib.get_close_matches(w, list(firsts), n=1, cutoff=0.75)
    return firsts[close[0]] if close else None


def planned_from_pco(date):
    """{normalized song title: {"text": description, "names": [full names]}} from the Planning Center plan.
    Cory types the soloist(s) in each song's description; blank = no solo."""
    import re
    from pco import PCO, norm
    p = PCO()
    j = p.get(f"/service_types/{p.st}/plans", per_page=100, order="-sort_date")
    pid = next((x["id"] for x in j["data"] if x["attributes"]["sort_date"][:10] == date), None)
    if not pid:
        pid = p.plans_by_date(date).get(date)
    if not pid:
        return {}
    out = {}
    for it in p.get(f"/service_types/{p.st}/plans/{pid}/items", per_page=100)["data"]:
        a = it["attributes"]
        if a["item_type"] != "song":
            continue
        text = (a.get("description") or "").strip()
        names = [n for n in (match_singer(w) for w in re.split(r"[^A-Za-z]+", text) if len(w) > 2) if n]
        out[norm(a["title"])] = {"text": text, "names": list(dict.fromkeys(names))}
    # Older plans have no names at all — that means "not filled in", not "no solos".
    return out if any(v["text"] for v in out.values()) else {}


def lyric_at(date, file_name, sec_in_song):
    """First few words sung near this point of the song (from work/<date>/lyrics.txt + Song Log times)."""
    import csv
    wd = WORK / date
    lf = worship_root() / "Song Log.csv"
    if not (wd / "lyrics.txt").exists() or not lf.exists():
        return ""
    start = None
    with open(lf, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if r.get("File") == file_name:
                m, s = r["Service time"].split("-")[0].split(":")
                start = int(m) * 60 + int(s)
    if start is None:
        return ""
    target, best = start + sec_in_song, None
    for line in (wd / "lyrics.txt").read_text(encoding="utf-8").splitlines():
        if not line.startswith("["):
            continue
        h, mi, s = map(int, line[1:line.index("]")].split(":"))
        t = h * 3600 + mi * 60 + s
        if t >= target - 3 and (best is None or t < best[0]):
            best = (t, line[line.index("]") + 1:].strip())
    return " ".join(best[1].split()[:6]) if best and best[0] - target < 25 else ""


def cmd_sunday(a):
    """Work out who sang lead on each of that Sunday's songs, save work/<date>/vocals_pending.json and
    a Slack message asking Cory to confirm. Nothing goes to Planning Center until `apply`."""
    from pco import norm, song_files
    date = a.date
    wd = WORK / date
    wd.mkdir(parents=True, exist_ok=True)
    cands = scheduled_singers(date) or CONFIG.get("singers")
    planned = planned_from_pco(date)  # soloist names Cory typed in each song's description
    ef = wd / "expected_leads.json"   # fallback: names given in chat ahead of time
    if ef.exists():
        for k, v in json.loads(ef.read_text()).items():
            if not k.startswith("_") and norm(k) not in planned:
                planned[norm(k)] = {"text": first(v), "names": [v]}
    songs = []
    for f in song_files(date).values():
        title = f.stem.split(" - ", 2)[-1]
        pl = planned.get(norm(title)) or next((v for k, v in planned.items()
                                               if k and (k in norm(title) or norm(title) in k)), None)
        plan_names = (pl or {}).get("names", [])
        # Only the planned soloist(s) when Planning Center names them; otherwise anyone scheduled.
        r = identify_song(f, plan_names or cands) if (plan_names or cands) else identify_song(f)
        guess = r["lead"]
        parts = [s for s in r["sections"] if s["who"] != "unknown"]
        if len(plan_names) > 1:  # several soloists: say who sang which part, with the words they started on
            detail = " · ".join(f"{first(s['who'])} {hms(s['start'])[2:]}"
                                + (f" \"{w}…\"" if (w := lyric_at(date, f.name, s["start"])) else "")
                                for s in parts[:6])
        else:
            detail = ""
        if pl is not None and not plan_names:
            status, lead = "no solo", ""
        elif len(plan_names) == 1:
            lone = identify_song(f, cands)["lead"]  # would the voice have picked someone else?
            status = ("confirmed by voice" if lone == plan_names[0] else
                      "conflict" if lone not in ("unknown", "group", plan_names[0]) else "planned")
            lead, guess = plan_names[0], lone
        elif plan_names:
            status, lead = "parts", " / ".join(first(n) for n in plan_names)
        elif guess == "group":
            status, lead = "no solo", ""
        else:
            status, lead = ("voice match" if guess != "unknown" else "unknown"), guess
        songs.append({"n": len(songs) + 1, "file": f.name, "title": title, "lead": lead,
                      "planned": (pl or {}).get("text"), "voice_guess": guess, "status": status, "detail": detail})
    pending = {"date": date, "scheduled": cands, "songs": songs, "confirmed": False}
    (wd / "vocals_pending.json").write_text(json.dumps(pending, indent=1))
    icon = {"confirmed by voice": "✅", "planned": "📝", "voice match": "🎤", "conflict": "⚠️",
            "unknown": "❓", "no solo": "➖", "parts": "🔀"}
    lines = [f"*Who sang lead on {dt_label(date)}?* Please confirm before I add it to Planning Center.", ""]
    for s in songs:
        why = {"confirmed by voice": "planned + voice match", "planned": "as planned (voice unclear)",
               "voice match": "voice match (not in Planning Center)",
               "conflict": f"planned, but the voice sounded like {s['voice_guess']}",
               "unknown": "couldn't tell",
               "no solo": "blank in Planning Center" if s["planned"] is not None else "sounds like group singing",
               "parts": "split: " + (s["detail"] or "couldn't tell who sang which part")}[s["status"]]
        who = s["lead"] if s["lead"] not in ("unknown", "") else ("—" if s["status"] == "no solo" else "?")
        lines.append(f"{s['n']}. *{s['title']}*: {who}  {icon[s['status']]} _{why}_")
    lines += ["", "Reply *all good*, or corrections like _2 = Shelley_ or _3 = Josh, verse 2 Austin_.",
              f"Scheduled singers: {', '.join(first(c) for c in cands)}"]
    (wd / "vocals_slack.md").write_text("\n".join(lines), encoding="utf-8")
    log("\n".join(lines))


def dt_label(date):
    import datetime as dt
    d = dt.date.fromisoformat(date)
    return f"Sunday, {d:%b} {d.day}"


def song_log_leads(pend):
    """{song file: text for the Song Log's Lead column} from a confirmed vocals_pending.json."""
    out = {}
    for s in pend["songs"]:
        if s.get("note"):
            out[s["file"]] = s["note"].removeprefix("Lead: ")
        elif s["lead"] not in ("unknown", "?", "", None):
            out[s["file"]] = s["lead"]
        elif s.get("status") == "no solo":
            out[s["file"]] = "None"
    return out


def cmd_apply(a):
    """After Cory confirms: write Planning Center Vocals notes and learn from the confirmed songs.
    Edit work/<date>/vocals_pending.json first (lead / note per song), then run this."""
    import subprocess as sp
    wd = WORK / a.date
    pend = json.loads((wd / "vocals_pending.json").read_text())
    leads = {}
    for s in pend["songs"]:
        if s["lead"] in ("unknown", "?", "", None):
            continue
        if s.get("note"):  # e.g. "Verse 1: Austin · Verse 2: Alana" from Cory's confirmation
            leads[s["file"]] = s["note"]
        else:
            leads[s["file"]] = "Lead: " + " / ".join(first(x) for x in s["lead"].split(" / "))
    (wd / "vocals_leads.json").write_text(json.dumps(leads, indent=1))
    r = sp.run([sys.executable, str(Path(__file__).with_name("pco.py")), "vocals", "--date", a.date,
                "--json", str(wd / "vocals_leads.json"), "--apply"], capture_output=True, text=True)
    log(r.stdout.strip() or r.stderr.strip())
    from nlc import set_song_leads
    set_song_leads(a.date, song_log_leads(pend))
    # Learn: each confirmed single-lead song becomes another sample for that singer.
    extra_f, src_f = VW / "profiles_extra.json", VW / "extra_sources.json"
    extra = json.loads(extra_f.read_text()) if extra_f.exists() else {}
    srcs = json.loads(src_f.read_text()) if src_f.exists() else []
    mean = np.load(VW / "mean.npy")
    learned = []
    for s in pend["songs"]:
        name = s["lead"]
        # Learn only from songs one confirmed person led start to finish.
        if name not in CONFIG.get("singers", []) or s.get("note") or \
                any(Path(x["song"]).stem == Path(s["file"]).stem for x in srcs):
            continue
        song = worship_root() / a.date / s["file"]
        X = np.array([centered(e, mean) for _, e in fingerprints(vocals_for(song))])
        if len(X) < 20:
            continue
        c = X.mean(0); c /= np.linalg.norm(c)
        keep = X[X @ c >= np.median(X @ c)]
        cen = keep.mean(0); cen /= np.linalg.norm(cen)
        extra.setdefault(name, []).append(cen.tolist())
        srcs.append({"name": name, "song": s["file"], "by": f"Cory confirmed {a.date}"})
        learned.append(f"{first(name)} ({s['title']})")
    extra_f.write_text(json.dumps(extra)); src_f.write_text(json.dumps(srcs, indent=1))
    pend["confirmed"] = True
    (wd / "vocals_pending.json").write_text(json.dumps(pend, indent=1))
    cmd_label_clips(a)
    log("Learned from: " + (", ".join(learned) or "nothing new"))


def cmd_identify(a):
    res = identify_song(Path(a.file), a.candidates.split(",") if a.candidates else None)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))
    log(json.dumps({k: res[k] for k in ("song", "lead", "seconds", "summary")}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("samples"); p.add_argument("--dates", nargs="+", required=True)
    p = sub.add_parser("cluster"); p.add_argument("--k", type=int, default=9)
    p = sub.add_parser("label"); p.add_argument("--map", required=True)
    sub.add_parser("label-clips")
    p = sub.add_parser("sunday"); p.add_argument("--date", required=True)
    p = sub.add_parser("apply"); p.add_argument("--date", required=True)
    p = sub.add_parser("identify"); p.add_argument("--file", required=True); p.add_argument("--json")
    p.add_argument("--candidates", help="comma-separated singers scheduled that Sunday")
    a = ap.parse_args()
    VW.mkdir(parents=True, exist_ok=True)
    globals()[f"cmd_{a.cmd.replace('-', '_')}"](a)


if __name__ == "__main__":
    main()
