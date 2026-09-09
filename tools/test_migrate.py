#!/usr/bin/env python3
"""
Proves the old in-page library survives the move to library.json.

Runs against a real saved library if one is present - by default the 34-game
scan in library-backups/ - and otherwise against a small synthetic page. The
real one is the interesting case: it carries data: URI artwork, Steam and Epic
launch targets, GOG entries with no key, and folder games.

  python tools/test_migrate.py [path\\to\\GameBox.html] [path\\to\\launch.json]
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

import library as L      # noqa: E402
import migrate as M      # noqa: E402

FAILED = []

# The saved libraries live outside the repo, beside it. Each is paired with the
# launch.json that was written by the same scan.
BACKUPS = os.path.join(ROOT, "..", "library-backups")
CANDIDATES = [
    (os.path.join(BACKUPS, "scanned-34-games.html"),
     os.path.join(BACKUPS, "scanned-34-games.launch.json")),
    (os.path.join(ROOT, "..", "source", "GameBox.html"),
     os.path.join(ROOT, "..", "source", "launch.json")),
]

SYNTHETIC = '''<html><script>
const G=[{"id":0,"t":"Portal 2","s":"steam","gb":12.5,"path":"Z:\\\\SteamLibrary\\\\Portal 2",
  "upd":"Sep 1, 2026","played":"Sep 2, 2026","state":"","via":"Steam runtime","note":"",
  "art":{"c":"data:image/jpeg;base64,/9j/4AAQSkZJRg==","s":"steam-cache"}},
 {"id":1,"t":"Some Folder Game","s":"local","gb":3.0,"path":"D:\\\\Games\\\\SomeFolder",
  "upd":"-","played":null,"state":"","via":"Direct executable","note":"kept for later",
  "art":{"c":"data:image/jpeg;base64,/9j/4AAQSkZJRg==","s":"generated"}}];
const STORES={};
</script></html>'''

SYNTHETIC_LAUNCH = {"0": ["steam", "620", ""],
                    "1": ["exe", "D:\\Games\\SomeFolder\\game.exe", "D:\\Games\\SomeFolder"]}


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (" - " + detail if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def pick_source():
    pairs = CANDIDATES
    if len(sys.argv) > 1:
        pairs = [(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")]
    for html, lj in pairs:
        html = os.path.abspath(html)
        if os.path.exists(html) and M.read_old_library(html):
            lj = os.path.abspath(lj) if lj else ""
            return html, (lj if lj and os.path.exists(lj) else None), True
    return None, None, False


def main():
    tmp = tempfile.mkdtemp(prefix="gamebox-migrate-")
    L.app_dir = lambda: tmp
    M.L = L

    src, launch, real = pick_source()
    if real:
        print("migrating the real library: " + os.path.basename(src))
        html = os.path.join(tmp, "GameBox.html")
        shutil.copyfile(src, html)
        lj = os.path.join(tmp, "launch.json")
        if launch:
            shutil.copyfile(launch, lj)
    else:
        print("no saved library found - using a synthetic page")
        html = os.path.join(tmp, "GameBox.html")
        io.open(html, "w", encoding="utf-8").write(SYNTHETIC)
        lj = os.path.join(tmp, "launch.json")
        json.dump(SYNTHETIC_LAUNCH, io.open(lj, "w", encoding="utf-8"))

    old = M.read_old_library(html)
    old_launch = M.read_old_launch(lj)
    print("  %d games in the page, %d launch targets\n" % (len(old), len(old_launch)))

    rep = M.run(html, lj)
    check("migration ran", rep.get("migrated") is True, str(rep))
    data = L.load()

    check("every game came across", len(data["games"]) == len(old),
          "%d of %d" % (len(data["games"]), len(old)))
    ids = [g["id"] for g in data["games"]]
    check("every id is unique", len(set(ids)) == len(ids),
          "%d ids, %d unique" % (len(ids), len(set(ids))))
    check("no id is a bare integer", not any(str(i).isdigit() for i in ids))
    check("a migration table was left behind", len(data.get("migration") or {}) == len(old))

    titles_old = sorted(g.get("t", "") for g in old)
    titles_new = sorted(g.get("title", "") for g in data["games"])
    check("no title was lost or changed", titles_old == titles_new)

    with_launch = [g for g in data["games"] if g.get("launch")]
    check("every launch target came across", len(with_launch) == len(old_launch),
          "%d of %d" % (len(with_launch), len(old_launch)))

    # Steam entries should key off the appid, which is what makes them stable.
    steam = [g for g in data["games"] if g.get("store") == "steam"]
    if steam:
        check("steam games key off their appid",
              all(g["id"].startswith("steam:") for g in steam),
              str([g["id"] for g in steam if not g["id"].startswith("steam:")][:3]))

    # Artwork must have left the JSON and landed on disk.
    covered = [g for g in data["games"] if (g.get("art") or {}).get("c")]
    check("covers were carried over", len(covered) > 0,
          "%d of %d games have a cover" % (len(covered), len(data["games"])))
    check("no cover is still a data: URI",
          not any(str((g.get("art") or {}).get("c", "")).startswith("data:")
                  for g in data["games"]))
    on_disk = [g for g in covered
               if os.path.exists(os.path.join(tmp, (g["art"]["c"]).replace("/", os.sep)))]
    check("every cover exists as a file", len(on_disk) == len(covered),
          "%d of %d" % (len(on_disk), len(covered)))

    size = os.path.getsize(L.library_file())
    check("library.json is small without the artwork", size < 2_000_000,
          "%.2f MB" % (size / 1e6))
    print("  library.json is %.0f KB; art/ holds %d files"
          % (size / 1e3, len(os.listdir(L.art_dir())) if os.path.isdir(L.art_dir()) else 0))

    # A migration must never run twice over a live library.
    rep2 = M.run(html, lj)
    check("a second migration is refused", rep2.get("migrated") is False, str(rep2))

    # And the merge must recognise the migrated games rather than duplicate them.
    scanned = [{"id": g["id"], "title": g["title"], "store": g.get("store", "local"),
                "path": g.get("path", ""), "bytes": g.get("bytes", 0)}
               for g in data["games"]]
    data2, added, updated, missing, rekeyed = L.merge(scanned, data=L.load())
    check("a rescan updates the migrated games rather than re-adding them",
          added == 0 and updated == len(scanned),
          "added=%d updated=%d" % (added, updated))

    # The case that matters most: a library migrated with no launch.json keys
    # its Steam games off their install path, because nothing knew the appid.
    # The next real scan does know it and offers "steam:<appid>" for the same
    # folder. That must re-key the record the user has tagged, not import a
    # second copy of the game beside it.
    print("\nre-keying")
    data = L.load()
    victim = next((g for g in data["games"] if g.get("path")), None)
    if victim:
        victim["id"] = L.game_id(victim.get("store", "local"), path=victim["path"])
        victim["user"]["favorite"] = True
        victim["user"]["tags"] = ["carried over"]
        L.save(data)
        before = len(L.load()["games"])
        better = {"id": "steam:999999", "title": victim["title"],
                  "store": "steam", "path": victim["path"], "bytes": victim.get("bytes", 0)}
        data, added, updated, missing, rekeyed = L.merge([better], data=L.load(), prune=False)
        L.save(data)
        after = L.load()["games"]
        check("a better id re-keys the record instead of duplicating it",
              len(after) == before and rekeyed == 1,
              "%d -> %d games, rekeyed=%d" % (before, len(after), rekeyed))
        moved = next((g for g in after if g["id"] == "steam:999999"), None)
        check("the re-keyed record kept its user data",
              moved is not None and moved["user"]["favorite"] is True
              and moved["user"]["tags"] == ["carried over"])

    # Telling an old page from the current one has to be exact. A pattern loose
    # enough to spot the old `const G=[...]` block also matches the comment in
    # the current page that explains what that block was - which had every
    # launch filing a "rescued" copy of a page with nothing to rescue.
    print("\ntelling the pages apart")
    import gamebox
    gamebox.migrate = M
    current = os.path.join(ROOT, "GameBox.html")
    check("the current page is not mistaken for an old one",
          not gamebox.holds_a_library(current),
          "matched something in " + os.path.basename(current))
    check("a real pre-1.3 page is recognised", gamebox.holds_a_library(html))
    empty = os.path.join(tmp, "empty.html")
    io.open(empty, "w", encoding="utf-8").write(
        "<script>const G=[];\nconst STORES={};</script>")
    check("an empty 1.2 page has nothing to migrate", not gamebox.holds_a_library(empty))
    check("a file that is not a page at all is handled",
          not gamebox.holds_a_library(os.path.join(tmp, "does-not-exist.html")))

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d failed" % len(FAILED))
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
