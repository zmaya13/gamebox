#!/usr/bin/env python3
"""
Proves the library store keeps what the user owns.

The whole point of library.json is that a rescan may refresh what the scanner
found and must not touch anything else. That is what this checks, plus the id
scheme that makes it possible.

  python tools/test_library.py

Runs against a throwaway directory, so it never touches a real library.
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import library as L  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (" - " + detail if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def scanned(gid, title, path="", **kw):
    rec = {"id": gid, "title": title, "store": kw.pop("store", "steam"),
           "path": path, "bytes": kw.pop("bytes", 1 << 30), "launch": ["steam", "1", ""]}
    rec.update(kw)
    return rec


def main():
    tmp = tempfile.mkdtemp(prefix="gamebox-test-")
    L.app_dir = lambda: tmp
    print("library store, in " + tmp + "\n")

    # ---- ids are stable and normalised
    print("ids")
    check("store id is the store's own key", L.game_id("steam", "620") == "steam:620")
    a = L.game_id("local", path="Z:/Games/Foo/")
    b = L.game_id("local", path="Z:" + chr(92) + "Games" + chr(92) + "foo")
    check("path id ignores slash direction, case and trailing slash", a == b,
          a + " != " + b)
    c = L.game_id("local", path="Z:/Games/Bar")
    check("a different path is a different id", a != c)
    check("an id is safe as a filename", ":" not in L.art_name(L.game_id("steam", "620")))

    # ---- a first scan
    print("\nfirst scan")
    data, added, updated, missing, rekeyed = L.merge([
        scanned("steam:620", "Portal 2"),
        scanned("steam:400", "Portal"),
        scanned("path:abc123", "Some Folder Game", store="local"),
    ], data=L.load())
    L.save(data)
    check("three games added", added == 3, "added=%d" % added)
    check("nothing to update yet", updated == 0)
    check("library round-trips through disk", len(L.load()["games"]) == 3)

    # ---- the user makes it theirs
    print("\nuser edits")
    data = L.load()
    g = next(x for x in data["games"] if x["id"] == "steam:620")
    g["user"]["favorite"] = True
    g["user"]["tags"] = ["co-op", "finished"]
    g["user"]["notes"] = "the one with the turrets"
    g["user"]["completion"] = "Beaten"
    g["user"]["score"] = 95
    g["user"]["title"] = "Portal 2 (my copy)"
    L.save(data)
    L.record_session("steam:620", 3600)
    L.record_session("steam:620", 1800)
    p = next(x for x in L.load()["games"] if x["id"] == "steam:620")["play"]
    check("two sessions recorded", p["count"] == 2, "count=%d" % p["count"])
    check("playtime accumulates", p["seconds"] == 5400, "seconds=%d" % p["seconds"])

    # ---- the rescan: THE test
    print("\nrescan")
    before = len(L.load()["games"])
    data, added, updated, missing, rekeyed = L.merge([
        # Portal 2 moved and grew; Portal is gone; the folder game is unchanged
        scanned("steam:620", "Portal 2", path="D:/Steam/Portal2", bytes=2 << 30),
        scanned("path:abc123", "Some Folder Game", store="local"),
    ], data=L.load())
    L.save(data)
    g = next(x for x in L.load()["games"] if x["id"] == "steam:620")

    check("no games were duplicated", len(L.load()["games"]) == before,
          "%d -> %d" % (before, len(L.load()["games"])))
    check("scanner fields refreshed", g["path"] == "D:/Steam/Portal2"
          and g["bytes"] == 2 << 30)
    check("favorite survived", g["user"]["favorite"] is True)
    check("tags survived", g["user"]["tags"] == ["co-op", "finished"])
    check("notes survived", g["user"]["notes"] == "the one with the turrets")
    check("completion survived", g["user"]["completion"] == "Beaten")
    check("score survived", g["user"]["score"] == 95)
    check("title override survived", g["user"]["title"] == "Portal 2 (my copy)")
    check("playtime survived", g["play"]["seconds"] == 5400)
    check("play count survived", g["play"]["count"] == 2)

    gone = next(x for x in L.load()["games"] if x["id"] == "steam:400")
    check("an uninstalled game is kept, not deleted", gone is not None)
    check("...and marked not installed", gone["installed"] is False)
    check("one game reported missing", missing == 1, "missing=%d" % missing)

    # ---- exclusions
    print("\nexclusions")
    data = L.load()
    data["exclusions"] = ["path:junk999"]
    L.save(data)
    data, added, _u, _m, _r = L.merge([scanned("path:junk999", "Redist Junk", store="local")],
                                 data=L.load(), prune=False)
    check("an excluded id is never imported", added == 0, "added=%d" % added)

    # ---- manual games
    print("\nmanual games")
    data = L.load()
    data["games"].append(dict(L.new_record(
        scanned("manual:1", "Hand Added", store="local")), source="manual"))
    L.save(data)
    data, _a, _u, _m, _r = L.merge([], data=L.load())
    L.save(data)
    m = next(x for x in L.load()["games"] if x["id"] == "manual:1")
    check("a scan does not disown a hand-added game", m["installed"] is True)

    # ---- the page shape
    print("\npage shape")
    page = L.to_page(L.load())
    row = next(x for x in page if x["id"] == "steam:620")
    check("title override wins in the page shape", row["t"] == "Portal 2 (my copy)")
    check("bytes become GB", row["gb"] == 2.0, "gb=%s" % row["gb"])
    check("favorite is a field, not a localStorage list", row["fav"] is True)
    check("playtime reaches the page", row["secs"] == 5400)

    # ---- backups
    print("\nbackups")
    b = L.backup("test")
    check("a backup is written before a merge", b and os.path.exists(b))

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d checks, %d failed" % (
        len([l for l in open(__file__, encoding="utf-8") if "    check(" in l]), len(FAILED)))
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
