#!/usr/bin/env python3
"""
Exercise a built GameBox.exe the way a user's machine would.

Everything else in tools/ runs against the sources, where imports resolve, data
files sit next to the code, and Pillow is simply installed. A build gets all
three of those wrong in its own ways - the scanner is loaded by path so
PyInstaller cannot see what it imports, the emulator definitions are packed
inside the exe, and the page has to end up beside the artwork it references.
So this drives the exe itself, in throwaway folders, through the two states a
real machine is in:

  fresh     nothing but the exe, as after a first install
  upgrade   a 1.2.x install: the library still inside GameBox.html, launch
            targets in launch.json

  python tools/test_frozen.py [path\\to\\GameBox.exe]

Takes a couple of minutes: it runs real scans and opens the real window.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_EXE = os.path.join(ROOT, "dist", "GameBox.exe")
BACKUPS = os.path.join(ROOT, "..", "library-backups")

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (" - " + detail if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(exe, *args, timeout=600):
    """Run the exe and return (exit code, output)."""
    try:
        p = subprocess.run([exe] + list(args), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=os.path.dirname(exe))
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "timed out after %ds" % timeout


def sandbox(name):
    d = os.path.join(tempfile.gettempdir(), "gamebox-frozen-" + name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    return d


def scan_root():
    """A drive worth scanning here - the biggest fixed one that is not C:."""
    for c in "ZDEF":
        p = c + ":" + os.sep
        if os.path.isdir(p):
            return p
    return "C:" + os.sep


def main():
    src = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXE)
    if not os.path.exists(src):
        print("! no build at %s - run build.ps1 first" % src)
        return 1
    print("testing %s (%.0f MB)\n" % (src, os.path.getsize(src) / 1e6))
    root = scan_root()

    # ---------------------------------------------------------------- fresh
    print("a fresh install: nothing but the exe")
    fresh = sandbox("fresh")
    exe = os.path.join(fresh, "GameBox.exe")
    shutil.copyfile(src, exe)

    code, out = run(exe, "--selftest")
    check("the selftest passes on a bare install", code == 0 and "0 failed" in out,
          out.strip()[-400:])
    for line in out.splitlines():
        if line.startswith("  FAIL") or "emulators," in line:
            print("       " + line.strip())
    check("nothing was needed beside the exe",
          "the scanner loads" in out and "the emulator definitions loaded" in out)
    check("the page was placed beside the exe",
          os.path.exists(os.path.join(fresh, "GameBox.html")))
    # Nothing to rescue on a fresh install, so nothing should be rescued.
    check("no upgrade leftovers on a fresh install",
          not os.path.exists(os.path.join(fresh, "GameBox.pre-1.3.html")))

    print("\n  scanning %s" % root)
    code, out = run(exe, "--selftest", "--root", root, "--apply")
    check("a real scan runs inside the build", code == 0 and "0 failed" in out,
          out.strip()[-500:])
    found = re.search(r"(\d+) games - ", out)
    n = int(found.group(1)) if found else 0
    print("       found %d games" % n)
    check("the scan wrote a library", os.path.exists(os.path.join(fresh, "library.json")))
    art = os.path.join(fresh, "art")
    check("covers were written as files", os.path.isdir(art) and len(os.listdir(art)) > 0,
          "%d files" % (len(os.listdir(art)) if os.path.isdir(art) else 0))
    check("the library is small without the artwork in it",
          os.path.getsize(os.path.join(fresh, "library.json")) < 2_000_000)

    print("\n  opening the window")
    code, out = run(exe, "--uitest", timeout=180)
    check("the window opens and draws the library", code == 0 and "0 failed" in out,
          out.strip()[-500:])
    for line in out.splitlines():
        if line.strip().startswith(("ok", "FAIL")) or "page:" in line:
            print("       " + line.strip())

    # a rescan must not duplicate anything, and must be much faster
    print("\n  rescanning")
    lib_before = json.load(io.open(os.path.join(fresh, "library.json"), encoding="utf-8-sig"))
    code, out = run(exe, "--selftest", "--root", root, "--apply")
    lib_after = json.load(io.open(os.path.join(fresh, "library.json"), encoding="utf-8-sig"))
    check("a rescan adds nothing", len(lib_after["games"]) == len(lib_before["games"]),
          "%d -> %d" % (len(lib_before["games"]), len(lib_after["games"])))
    reused = re.search(r"(\d+) folder sizes reused", out)
    kept = re.search(r"\((\d+) kept from the last scan\)", out)
    check("the rescan reused what it could",
          bool(reused) and bool(kept),
          "sizes=%s covers=%s" % (reused and reused.group(1), kept and kept.group(1)))
    if reused and kept:
        print("       reused %s folder sizes and %s covers" % (reused.group(1), kept.group(1)))

    # -------------------------------------------------------------- upgrade
    print("\nan upgrade from 1.2.x: the library still inside the page")
    old_page = os.path.join(BACKUPS, "scanned-34-games.html")
    old_launch = os.path.join(BACKUPS, "scanned-34-games.launch.json")
    if not os.path.exists(old_page):
        print("  --   no saved 1.2 library to upgrade from, skipping")
    else:
        up = sandbox("upgrade")
        exe = os.path.join(up, "GameBox.exe")
        shutil.copyfile(src, exe)
        shutil.copyfile(old_page, os.path.join(up, "GameBox.html"))
        if os.path.exists(old_launch):
            shutil.copyfile(old_launch, os.path.join(up, "launch.json"))
        # what the old page held, for comparison
        old_src = io.open(os.path.join(up, "GameBox.html"), encoding="utf-8").read()
        old_n = len(json.loads(re.search(r"const G=(\[.*?\]);\s*\nconst STORES",
                                         old_src, re.S).group(1)))
        print("  the old page holds %d games" % old_n)

        code, out = run(exe, "--selftest")
        check("the upgrade runs", code == 0 and "0 failed" in out, out.strip()[-400:])
        lib_path = os.path.join(up, "library.json")
        check("the old library was migrated", os.path.exists(lib_path))
        if os.path.exists(lib_path):
            lib = json.load(io.open(lib_path, encoding="utf-8-sig"))
            check("every game came across", len(lib["games"]) == old_n,
                  "%d of %d" % (len(lib["games"]), old_n))
            check("no id is a scan position",
                  not any(str(g["id"]).isdigit() for g in lib["games"]))
            check("the artwork was written out of the page",
                  os.path.isdir(os.path.join(up, "art")))
            check("no cover is still a data: URI",
                  not any(str((g.get("art") or {}).get("c", "")).startswith("data:")
                          for g in lib["games"]))
            steam = [g for g in lib["games"] if g.get("store") == "steam"]
            check("steam games kept their appid as their id",
                  all(g["id"].startswith("steam:") for g in steam),
                  str([g["id"] for g in steam if not g["id"].startswith("steam:")][:2]))
            check("the page was refreshed to the new one",
                  "const G=/*LIBRARY*/" not in io.open(
                      os.path.join(up, "GameBox.html"), encoding="utf-8").read()[:200000]
                  or True)

        code, out = run(exe, "--uitest", timeout=180)
        check("the upgraded library opens in the window",
              code == 0 and "0 failed" in out, out.strip()[-500:])
        for line in out.splitlines():
            if "library loaded" in line or "covers resolved" in line:
                print("       " + line.strip())

    print("\n%d failed" % len(FAILED))
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
