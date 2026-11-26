r"""Planning Center Services notes for each Sunday — song video links + lead singer.

  pco.cmd links  [--dates 2026-10-11 ...] [--apply]    Audio/Visual note on each song (Drive video link)
                                                       + General plan note (Worship / Sermon folder links)
  pco.cmd vocals --date 2026-10-11 --json leads.json [--apply]
                                                       Vocals note on each song ("Lead: Alana · …")

Without --apply it only prints what it would do (dry run). Notes this tool writes start with
"[NLC media]" — re-running updates those notes instead of adding duplicates, and never touches
notes people typed by hand. Uses the personal access token in secrets/pco.json.
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nlc import CONFIG, ROOT, drive_cloud_id, log  # noqa: E402
from platform_paths import localize_path  # noqa: E402

API = "https://api.planningcenteronline.com/services/v2"
TAG = "[NLC media]"
WORSHIP = Path(CONFIG.get("worship_songs_folder") or localize_path(r"G:\Shared drives\Worship"))
SERMONS = Path(CONFIG.get("sermons_folder") or localize_path(r"G:\Shared drives\Sermons"))


class PCO:
    def __init__(self):
        c = json.loads((ROOT / "secrets" / "pco.json").read_text(encoding="utf-8"))
        self.s = requests.Session()
        self.s.auth = (c["app_id"].strip(), c["secret"].strip())
        st = self.get("/service_types")["data"]
        want = CONFIG.get("pco_service_type", "Worship")
        self.st = next(x["id"] for x in st if x["attributes"]["name"] == want)
        self.item_cat = {x["attributes"]["name"]: x["id"] for x in self.get(f"/service_types/{self.st}/item_note_categories")["data"]}
        self.plan_cat = {x["attributes"]["name"]: x["id"] for x in self.get(f"/service_types/{self.st}/plan_note_categories")["data"]}

    def _req(self, method, path, **kw):
        """Planning Center allows 100 requests / 20 s — wait and retry when we hit it."""
        import time
        for _ in range(8):
            r = self.s.request(method, API + path, timeout=60, **kw)
            if r.status_code != 429:
                return r
            time.sleep(int(r.headers.get("Retry-After", 20)) + 1)
        return r

    def get(self, path, **params):
        r = self._req("GET", path, params=params)
        r.raise_for_status()
        return r.json()

    def send(self, method, path, attrs):
        r = self._req(method, path, json={"data": {"attributes": attrs}})
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def plans_by_date(self, since="2024-01-01"):
        out, url = {}, f"/service_types/{self.st}/plans"
        params = {"filter": "past", "order": "-sort_date", "per_page": 100}
        while url:
            j = self.get(url, **params)
            for p in j["data"]:
                d = p["attributes"]["sort_date"][:10]
                if d < since:
                    return out
                out.setdefault(d, p["id"])
            nxt = j.get("links", {}).get("next")
            url, params = (nxt.replace(API, ""), {}) if nxt else (None, {})
        return out

    def items(self, plan_id):
        return self.get(f"/service_types/{self.st}/plans/{plan_id}/items", per_page=100, include="item_notes")

    def upsert_item_note(self, plan_id, item, existing, category, content, apply):
        cat = self.item_cat[category]
        mine = [n for n in existing if n["attributes"].get("category_name") == category
                and (n["attributes"].get("content") or "").startswith(TAG)]
        base = f"/service_types/{self.st}/plans/{plan_id}/items/{item['id']}/item_notes"
        if mine and mine[0]["attributes"]["content"] == content:
            return "same"
        if apply:
            if mine:
                self.send("PATCH", f"{base}/{mine[0]['id']}", {"content": content})
            else:
                self.send("POST", base, {"content": content, "item_note_category_id": cat})
        return "update" if mine else "add"

    def upsert_plan_note(self, plan_id, category, content, apply):
        base = f"/service_types/{self.st}/plans/{plan_id}/notes"
        notes = self.get(base, per_page=100)["data"]
        mine = [n for n in notes if n["attributes"].get("category_name") == category
                and (n["attributes"].get("content") or "").startswith(TAG)]
        if mine and mine[0]["attributes"]["content"] == content:
            return "same"
        if apply:
            if mine:
                self.send("PATCH", f"{base}/{mine[0]['id']}", {"content": content})
            else:
                self.send("POST", base, {"content": content, "plan_note_category_id": self.plan_cat[category]})
        return "update" if mine else "add"


def norm(s):
    s = re.sub(r"\(.*?\)", "", s.lower())
    return re.sub(r"[^a-z0-9]", "", s)


def drive_link(path: Path, folder: bool):
    cid = drive_cloud_id(path)
    if not cid:
        return None
    return f"https://drive.google.com/drive/folders/{cid}" if folder else f"https://drive.google.com/file/d/{cid}/view"


def song_files(date):
    """{normalized title: file} for that Sunday's Worship folder."""
    out = {}
    for f in sorted((WORSHIP / date).glob(f"{date} - * - *.mp4")):
        title = f.stem.split(" - ", 2)[-1]
        out.setdefault(norm(title), f)
    return out


def match_item(item_title, files):
    n = norm(item_title)
    if n in files:
        return files[n]
    for k, f in files.items():  # "Trust In God" vs "Trust in God (Live)", reprises, etc.
        if n and (n in k or k in n) and min(len(n), len(k)) >= 5:
            return f
    return None


def cmd_links(a, pco):
    plans = pco.plans_by_date()
    dates = a.dates or sorted(d.name for d in WORSHIP.iterdir() if re.match(r"\d{4}-\d{2}-\d{2}$", d.name))
    counts = {"add": 0, "update": 0, "same": 0}
    for date in dates:
        pid = plans.get(date)
        if not pid:
            log(f"{date}: no Planning Center plan found — skipped")
            continue
        files = song_files(date)
        j = pco.items(pid)
        notes = {n["id"]: n for n in j.get("included", []) if n["type"] == "ItemNote"}
        used = set()
        for it in j["data"]:
            if it["attributes"]["item_type"] != "song":
                continue
            f = match_item(it["attributes"]["title"], files)
            if not f or f in used:
                continue
            used.add(f)
            link = drive_link(f, folder=False)
            if not link:
                log(f"{date}: {f.name} not synced to Drive yet — skipped")
                continue
            existing = [notes[n["id"]] for n in it["relationships"]["item_notes"]["data"] if n["id"] in notes]
            content = f"{TAG} Video (480p reference): {link}"
            r = pco.upsert_item_note(pid, it, existing, "Audio/Visual", content, a.apply)
            counts[r] += 1
            log(f"{date}: {r:6} Audio/Visual note on '{it['attributes']['title']}' -> {f.name}")
        missing = set(files.values()) - used
        for f in sorted(missing):
            log(f"{date}: no plan item matched {f.name}")
        lines = [TAG]
        wl = drive_link(WORSHIP / date, folder=True)
        if wl:
            lines.append(f"Worship songs (480p): {wl}")
        for sf in sorted((SERMONS / date[:4]).glob(f"{date} - *")):
            sl = drive_link(sf, folder=True)
            if sl:
                lines.append(f"Sermon video + radio MP3: {sl}")
        if len(lines) > 1:
            r = pco.upsert_plan_note(pid, "General", "\n".join(lines), a.apply)
            counts[r] += 1
            log(f"{date}: {r:6} General plan note ({len(lines) - 1} link(s))")
    log(("APPLIED: " if a.apply else "DRY RUN (nothing written): ") + json.dumps(counts))


def cmd_vocals(a, pco):
    """leads.json: {"<song file name>": "Lead: Alana · Verse 2: Austin", ...}"""
    leads = json.loads(Path(a.json).read_text(encoding="utf-8"))
    # Today's plan isn't "past" yet, so check recent/upcoming plans first.
    j = pco.get(f"/service_types/{pco.st}/plans", per_page=100, order="-sort_date")
    pid = next((x["id"] for x in j["data"] if x["attributes"]["sort_date"][:10] == a.date), None) \
        or pco.plans_by_date().get(a.date)
    if not pid:
        sys.exit(f"No Planning Center plan for {a.date}")
    files = song_files(a.date)
    j = pco.items(pid)
    notes = {n["id"]: n for n in j.get("included", []) if n["type"] == "ItemNote"}
    for it in j["data"]:
        if it["attributes"]["item_type"] != "song":
            continue
        f = match_item(it["attributes"]["title"], files)
        if not f or f.name not in leads:
            continue
        existing = [notes[n["id"]] for n in it["relationships"]["item_notes"]["data"] if n["id"] in notes]
        r = pco.upsert_item_note(pid, it, existing, "Vocals", f"{TAG} {leads[f.name]}", a.apply)
        log(f"{a.date}: {r:6} Vocals note on '{it['attributes']['title']}': {leads[f.name]}")
    log("APPLIED" if a.apply else "DRY RUN (nothing written)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("links"); p.add_argument("--dates", nargs="*"); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("vocals"); p.add_argument("--date", required=True); p.add_argument("--json", required=True)
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    globals()[f"cmd_{a.cmd}"](a, PCO())


if __name__ == "__main__":
    main()
