#!/usr/bin/env python3
"""
One-way migration: the old in-page library -> library.json

Before this, a library lived as a `const G=[...]` array inside GameBox.html,
its launch targets in launch.json, and the user's favourites and hidden games
in the WebView2 profile's localStorage - all three keyed by the same scan-order
integer. Renumbering was the thing that broke, so this is the one moment where
those integers still mean something and must be spent carefully.

  1. read the array out of GameBox.html and the targets out of launch.json
  2. derive a stable id for each game (from the store's own key where there is
     one, otherwise from the install path)
  3. write the covers out of their base64 data: URIs into art/ files
  4. leave behind a {old integer -> new id} table

Step 4 is what lets the page hand its localStorage favourites and hidden games
back across the gap; see Api.migrate_user(). The table is dropped once it has
been claimed.

This runs at most once. If library.json already exists, nothing happens.
"""

import base64
import io
import json
import os
import re

import library as L

# Covers are the only artwork worth carrying over; heroes and screenshots are
# cheap to re-fetch and this keeps the migration quick.
ART_KEYS = (("c", ""), ("a", "-h"))


def _write_data_url(uri, stem):
    """A base64 data: URI -> a file in art/. Returns the relative path, or None."""
    if not isinstance(uri, str) or not uri.startswith("data:"):
        return uri or None          # already a path, or nothing
    m = re.match(r"data:image/(\w+);base64,(.*)$", uri, re.S)
    if not m:
        return None
    ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return None
    os.makedirs(L.art_dir(), exist_ok=True)
    name = stem + "." + ext
    try:
        with io.open(os.path.join(L.art_dir(), name), "wb") as fh:
            fh.write(raw)
    except OSError:
        return None
    return "art/" + name


def read_old_library(html_path):
    """The `const G=[...]` array out of a page, or []."""
    try:
        src = io.open(html_path, encoding="utf-8").read()
    except OSError:
        return []
    m = re.search(r"const G=(\[.*?\]);\s*\nconst STORES", src, re.S)
    if not m:
        return []
    try:
        games = json.loads(m.group(1))
    except ValueError:
        return []
    return games if isinstance(games, list) else []


def read_old_launch(path):
    """launch.json -> {int id: [kind, target, folder]}."""
    try:
        raw = json.load(io.open(path, encoding="utf-8-sig"))
    except Exception:
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[int(k)] = list(v)
        except (TypeError, ValueError):
            pass
    return out


def derive_id(old, launch):
    """A stable id for an old record, using its launch target where it helps.

    The launch target carries the store's own key - a Steam appid, an Epic
    AppName - which is exactly what makes an id survive a rescan. Without one,
    fall back to the install path.
    """
    kind = launch[0] if launch else ""
    target = launch[1] if launch and len(launch) > 1 else ""
    store = old.get("s") or "local"
    if kind == "steam" and target:
        return L.game_id("steam", target)
    if kind == "epic" and target:
        return L.game_id("epic", target)
    path = old.get("path") or ""
    if path:
        return L.game_id(store, path=path)
    # no key and no path: fall back to the title, which is at least not the
    # scan order
    return L.game_id(store, key="title-" + re.sub(r"[^a-z0-9]", "", old.get("t", "").lower()))


def convert(old_games, launch):
    """Old page records -> library records, plus the {old id: new id} table."""
    records, table, used = [], {}, {}
    for old in old_games:
        oid = old.get("id")
        la = launch.get(oid)
        gid = derive_id(old, la)
        # two folders can hash to one id only if they are the same folder, but
        # a title fallback can collide; make it unique rather than lose a game
        if gid in used:
            used[gid] += 1
            gid = "%s~%d" % (gid, used[gid])
        else:
            used[gid] = 0

        art = {}
        for key, suffix in ART_KEYS:
            got = _write_data_url((old.get("art") or {}).get(key), L.art_name(gid, suffix))
            if got:
                art[key] = got
        if (old.get("art") or {}).get("s"):
            art["s"] = old["art"]["s"]

        meta = old.get("meta")
        if meta and meta.get("shots"):
            shots = []
            for i, shot in enumerate(meta["shots"]):
                got = _write_data_url(shot, L.art_name(gid, "-s%d" % i))
                if got:
                    shots.append(got)
            meta = dict(meta, shots=shots)

        rec = L.new_record({
            "id": gid,
            "title": old.get("t") or "?",
            "store": old.get("s") or "local",
            "path": old.get("path") or "",
            "bytes": int(round(float(old.get("gb") or 0) * (1024 ** 3))),
            "updated": old.get("upd") if old.get("upd") != "-" else None,
            "played": old.get("played"),
            "state": old.get("state") or "",
            "via": old.get("via") or "",
            "launch": la,
            "art": art,
            "meta": meta,
            "files": old.get("files"),
        })
        if old.get("note"):
            rec["user"]["notes"] = old["note"]
        records.append(rec)
        if oid is not None:
            table[str(oid)] = gid
    return records, table


def run(html_path, launch_path, force=False):
    """Migrate if there is something to migrate. Returns a short report."""
    if os.path.exists(L.library_file()) and not force:
        return {"ok": True, "migrated": False, "reason": "library.json already exists"}
    old = read_old_library(html_path)
    if not old:
        return {"ok": True, "migrated": False, "reason": "no in-page library to migrate"}

    records, table = convert(old, read_old_launch(launch_path))
    data = L.load()
    data["games"] = records
    data["migration"] = table          # claimed once by Api.migrate_user()
    if not L.save(data):
        return {"ok": False, "migrated": False, "reason": "could not write library.json"}
    return {"ok": True, "migrated": True, "games": len(records),
            "art": len([g for g in records if (g.get("art") or {}).get("c")])}


if __name__ == "__main__":
    import sys
    here = L.app_dir()
    print(run(os.path.join(here, "GameBox.html"),
              os.path.join(here, "launch.json"),
              force="--force" in sys.argv))
