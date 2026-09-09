#!/usr/bin/env python3
"""
Drives the real Api the page talks to, with no window.

Every call the page makes goes through class Api, so exercising it directly
catches the things a browser cannot: that the library reaches the page in the
shape it expects, that a user edit is actually written to disk, and that the
handover of the old localStorage favourites happens exactly once.

  python tools/test_api.py

Runs against a throwaway directory. It never launches a game and never touches
a real library.
"""

import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (" - " + detail if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


OLD_PAGE = '''<html><script>
const G=[{"id":0,"t":"Portal 2","s":"steam","gb":12.5,"path":"Z:/SteamLibrary/Portal 2",
  "upd":"Sep 1, 2026","played":"Sep 2, 2026","state":"","via":"Steam runtime","note":"",
  "art":{"c":"data:image/jpeg;base64,/9j/4AAQSkZJRg==","s":"steam-cache"}},
 {"id":1,"t":"Some Folder Game","s":"local","gb":3.0,"path":"D:/Games/SomeFolder",
  "upd":"-","played":null,"state":"","via":"Direct executable","note":"",
  "art":{"c":"data:image/jpeg;base64,/9j/4AAQSkZJRg==","s":"generated"}},
 {"id":2,"t":"Third Game","s":"local","gb":1.0,"path":"D:/Games/Third",
  "upd":"-","played":null,"state":"","via":"Direct executable","note":"","art":{"s":"generated"}}];
const STORES={};
</script></html>'''

OLD_LAUNCH = {"0": ["steam", "620", ""],
              "1": ["exe", "D:/Games/SomeFolder/game.exe", "D:/Games/SomeFolder"],
              "2": ["none", ""]}


def main():
    tmp = tempfile.mkdtemp(prefix="gamebox-api-")

    # Point everything at the sandbox before the app module reads any path.
    import library as L
    L.app_dir = lambda: tmp

    import gamebox
    gamebox.user_file = lambda name: os.path.join(tmp, name)
    gamebox.SETTINGS = os.path.join(tmp, "gamebox-settings.json")
    import migrate as M
    M.L = L
    gamebox.L = L
    gamebox.migrate = M

    print("Api, in " + tmp + "\n")

    # An install from before 1.3.0: a library inside the page, targets beside it.
    io.open(os.path.join(tmp, "GameBox.html"), "w", encoding="utf-8").write(OLD_PAGE)
    json.dump(OLD_LAUNCH, io.open(os.path.join(tmp, "launch.json"), "w", encoding="utf-8"))

    print("upgrade from an older install")
    rep = M.run(os.path.join(tmp, "GameBox.html"), os.path.join(tmp, "launch.json"))
    check("the old in-page library is migrated on startup", rep.get("migrated") is True, str(rep))

    api = gamebox.Api()

    # ---- the library reaches the page
    print("\nlibrary()")
    j = api.library()
    check("library() answers", j.get("ok") is True)
    check("all three games came through", len(j["games"]) == 3, str(len(j["games"])))
    row = next(g for g in j["games"] if g["t"] == "Portal 2")
    for key in ("id", "t", "short", "s", "gb", "path", "art", "fav", "hide",
                "tags", "cats", "done", "score", "secs", "plays", "added"):
        if key not in row:
            check("the page shape carries " + key, False)
            break
    else:
        check("the page shape carries every field the page reads", True)
    check("ids are strings, not scan positions", isinstance(row["id"], str))
    check("the steam game kept its appid as its id", row["id"] == "steam:620", row["id"])
    check("a pending migration is announced", j.get("pending_migration") is True)

    # ---- the handover of the old localStorage lists
    print("\nmigrate_user()")
    r = api.migrate_user([0], [2])          # Portal 2 favourited, Third Game hidden
    check("the handover is claimed", r.get("claimed") is True and r.get("moved") == 2, str(r))
    j = api.library()
    fav = next(g for g in j["games"] if g["id"] == "steam:620")
    check("the favourite landed on the right game", fav["fav"] is True)
    hid = [g for g in j["games"] if g["hide"]]
    check("the hidden game landed on the right game",
          len(hid) == 1 and hid[0]["t"] == "Third Game",
          str([g["t"] for g in hid]))
    check("the handover is not offered twice", api.library().get("pending_migration") is False)
    check("a second handover changes nothing", api.migrate_user([1], [1]).get("claimed") is False)

    # ---- user edits are written through
    print("\nsave_game()")
    r = api.save_game("steam:620", {"tags": ["co-op"], "completion": "Beaten", "score": 90,
                                    "notes": "the one with the turrets"})
    check("save_game() answers", r.get("ok") is True, str(r))
    row = next(g for g in api.library()["games"] if g["id"] == "steam:620")
    check("the edit is readable back through library()",
          row["tags"] == ["co-op"] and row["done"] == "Beaten" and row["score"] == 90)
    on_disk = next(g for g in L.load()["games"] if g["id"] == "steam:620")
    check("the edit reached the disk, not just memory",
          on_disk["user"]["notes"] == "the one with the turrets")

    check("an unknown game is refused",
          api.save_game("steam:nope", {"favorite": True}).get("ok") is False)
    check("a field the user does not own is ignored",
          api.save_game("steam:620", {"path": "C:/evil"}).get("ok") is True
          and L.load()["games"][0].get("path") != "C:/evil")

    # ---- launching resolves through the library
    print("\nlaunch targets")
    check("a steam target resolves", L.launch_target("steam:620") == ("steam", "620", ""))
    check("a game with no target is reported, not crashed",
          api.launch("path:nothing-here").get("ok") is False)

    # ---- playtime reaches the page
    print("\nplaytime")
    L.record_session("steam:620", 7200)
    L.record_session("steam:620", 1800)
    row = next(g for g in api.library()["games"] if g["id"] == "steam:620")
    check("playtime reaches the page", row["secs"] == 9000, str(row["secs"]))
    check("play count reaches the page", row["plays"] == 2, str(row["plays"]))
    check("last played is set from the session, not the store",
          bool(row["played"]))
    check("playing() answers with nothing running",
          api.playing() == {"ok": True, "playing": {}, "waiting": []},
          str(api.playing()))
    check("a session survives a rescan",
          next(g for g in L.load()["games"] if g["id"] == "steam:620")["play"]["seconds"] == 9000)
    check("stop_tracking is safe when nothing is tracked",
          api.stop_tracking().get("ok") is True)

    # ---- organising: hand-added games and exclusions
    print("\norganising")
    exe = os.path.join(tmp, "HandAdded.exe")
    io.open(exe, "w").write("not really a program")
    r = api.add_game({"title": "Hand Added", "target": exe})
    check("a game can be added by hand", r.get("ok") is True, str(r))
    gid = r.get("id")
    check("the same game is not added twice",
          api.add_game({"title": "Hand Added", "target": exe}).get("ok") is False)
    check("a game needs a name", api.add_game({"target": exe}).get("ok") is False)
    check("a game needs somewhere to launch from",
          api.add_game({"title": "Nowhere"}).get("ok") is False)
    check("a target that does not exist is refused",
          api.add_game({"title": "Ghost", "target": "C:/nope/nope.exe"}).get("ok") is False)
    r = api.add_game({"title": "By Link", "target": "steam://rungameid/440"})
    check("a launcher link is accepted", r.get("ok") is True, str(r))
    check("it launches as a uri", L.launch_target(r["id"])[0] == "uri")

    added = next(g for g in L.load()["games"] if g["id"] == gid)
    check("a hand-added game is marked as such", added["source"] == "manual")
    data, _a, _u, missing, _r = L.merge([], data=L.load())
    L.save(data)
    check("a scan does not disown it",
          next(g for g in L.load()["games"] if g["id"] == gid)["installed"] is True)

    # tags, categories, completion and score go through save_game
    api.save_game(gid, {"tags": ["couch", "short"], "categories": ["Backlog"],
                        "completion": "Playing", "score": 80})
    row = next(g for g in api.library()["games"] if g["id"] == gid)
    check("tags reach the page", row["tags"] == ["couch", "short"], str(row["tags"]))
    check("categories reach the page", row["cats"] == ["Backlog"])
    check("completion reaches the page", row["done"] == "Playing")
    check("score reaches the page", row["score"] == 80)

    print("\nremoving")
    check("nothing is excluded to begin with", api.exclusions()["ids"] == [])
    r = api.remove_game(gid, forever=False)
    check("a game can be removed", r.get("ok") is True and r.get("excluded") is False)
    check("...and is gone", not any(g["id"] == gid for g in L.load()["games"]))
    check("removing it again is refused", api.remove_game(gid).get("ok") is False)

    api.add_game({"title": "Junk Tool", "target": exe})
    junk = next(g["id"] for g in L.load()["games"] if g["title"] == "Junk Tool")
    api.remove_game(junk, forever=True)
    check("removing for good adds an exclusion", api.exclusions()["ids"] == [junk],
          str(api.exclusions()["ids"]))
    data, added_n, _u, _m, _r = L.merge(
        [{"id": junk, "title": "Junk Tool", "store": "local", "path": exe}], data=L.load())
    check("an excluded game is not re-imported by a scan", added_n == 0, "added=%d" % added_n)
    api.unexclude()
    check("exclusions can be cleared", api.exclusions()["ids"] == [])
    data, added_n, _u, _m, _r = L.merge(
        [{"id": junk, "title": "Junk Tool", "store": "local", "path": exe}], data=L.load())
    check("and then it imports again", added_n == 1, "added=%d" % added_n)

    # ---- status counts the library
    print("\nstatus()")
    st = api.status()
    check("status() counts the library",
          st.get("games") == len(L.load()["games"]),
          "%s vs %d in the library" % (st.get("games"), len(L.load()["games"])))
    check("status() reports the version", bool(st.get("version")))

    # ---- settings still work
    print("\nsettings")
    api.settings_set({"setup_done": True})
    check("settings round-trip", api.settings_get().get("setup_done") is True)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d failed" % len(FAILED))
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
