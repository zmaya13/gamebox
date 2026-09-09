#!/usr/bin/env python3
"""
Checks the store scanners, and reports what they find on this machine.

These read what each launcher has already written locally - a registry key, a
manifest, a small SQLite database - so on a machine without a given launcher
the honest answer is an empty list. What is always checked is that each scanner
is safe to call, and that anything it does return is usable: a title, a real
folder, a stable id, and a launch target the app knows how to run.

  python tools/test_stores.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import stores as ST  # noqa: E402

FAILED = []
KINDS = {"exe", "uri", "shell", "emu", "web", "steam", "epic", "none"}


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (" - " + detail if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main():
    roots = [c + ":" + os.sep for c in "CDEZ" if os.path.isdir(c + ":" + os.sep)]
    print("looking in " + ", ".join(roots) + "\n")

    print("what is installed here")
    present = ST.installed_launchers()
    for k, v in present.items():
        print("  %-16s %s" % (k, "yes" if v else "-"))
    check("the launcher probe answers for every store",
          len(present) == len(ST.SCANNERS), str(len(present)))

    total = 0
    for label, key, fn in ST.SCANNERS:
        print("\n" + label)
        try:
            found = fn(roots)
        except Exception as e:
            check("%s scans without raising" % label, False, "%s: %s" % (type(e).__name__, e))
            continue
        check("%s scans without raising" % label, True)
        check("%s returns a list" % label, isinstance(found, list))
        total += len(found)
        if not found:
            print("       nothing found (not installed, or no games)")
            continue

        for g in found[:6]:
            print("       %-34s %s" % (g["title"][:34], (g.get("launch") or ["", ""])[1] or "(no target)"))

        check("%s: every game has a title" % label, all(g.get("title") for g in found))
        check("%s: every game says which store it is from" % label,
              all(g.get("store") == key for g in found))
        check("%s: every install folder exists" % label,
              all(os.path.isdir(g.get("path") or "") for g in found),
              str([g["path"] for g in found if not os.path.isdir(g.get("path") or "")][:2]))
        idk = ST.ID_KEYS[key]
        check("%s: every game carries its store id (%s)" % (label, idk),
              all(g.get(idk) for g in found),
              str([g["title"] for g in found if not g.get(idk)][:3]))
        check("%s: every store id is unique" % label,
              len({g.get(idk) for g in found}) == len(found))
        kinds = {(g.get("launch") or ["none"])[0] for g in found}
        check("%s: every launch kind is one the app runs" % label,
              kinds <= KINDS, str(kinds - KINDS))
        runnable = [g for g in found if (g.get("launch") or ["none"])[0] != "none"]
        if len(runnable) < len(found):
            print("       ! %d of %d have no launch target"
                  % (len(found) - len(runnable), len(found)))
        for g in runnable:
            kind, tgt = g["launch"][0], g["launch"][1]
            if kind == "exe":
                check("%s: %s points at a real file" % (label, g["title"][:24]),
                      os.path.exists(tgt), tgt)
            elif kind == "uri":
                check("%s: %s has a scheme" % (label, g["title"][:24]), "://" in tgt, tgt)

    print("\n%d games found across all of these" % total)
    if not total:
        print("none of these launchers are installed here - the shape checks above")
        print("are what this run proves; the scanners are exercised on a machine")
        print("that has them")

    print("\n%d failed" % len(FAILED))
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
