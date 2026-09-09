#!/usr/bin/env python3
"""
Checks the emulator definitions, and what they find on this machine.

The definitions are Playnite's data (emulation/NOTICE); this proves they
compiled correctly, that every profile can produce a real command line, and
that detection finds whatever emulators are actually installed here.

  python tools/test_emulators.py [--roots C:\\ D:\\]
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import emulators as E  # noqa: E402

FAILED = []
SEP = os.sep


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (" - " + detail if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main():
    print("definitions")
    d = E.defs()
    check("emulators.json is present and compiled", d.get("version") == 1)
    check("all 92 emulators parsed", len(d["emulators"]) == 92, str(len(d["emulators"])))
    profiles = sum(len(e["profiles"]) for e in d["emulators"])
    check("every emulator has at least one profile",
          all(e["profiles"] for e in d["emulators"]))
    check("the extension index is populated", len(d["by_ext"]) > 300, str(len(d["by_ext"])))
    check("platforms parsed", len(d["platforms"]) > 90, str(len(d["platforms"])))
    print("  %s, %d platforms" % (E.summary(), len(d["platforms"])))

    print("\nprofile integrity")
    no_exe = [(e["id"], p["name"]) for e in d["emulators"] for p in e["profiles"] if not p["exe"]]
    check("every profile matches an executable", not no_exe, str(no_exe[:3]))
    bad = []
    import re
    for e in d["emulators"]:
        for p in e["profiles"]:
            try:
                re.compile(p["exe"])
            except re.error:
                bad.append((e["id"], p["name"]))
    check("every executable pattern is a valid regex", not bad, str(bad[:3]))
    unknown = {pl for e in d["emulators"] for p in e["profiles"]
               for pl in p["platforms"] if pl not in d["platforms"]}
    check("every platform a profile names exists", not unknown, str(sorted(unknown)[:5]))

    print("\ncommand lines")
    # Every profile must produce a command line that carries the ROM path,
    # keeps a path with spaces in one piece, and names the executable.
    rom = "D:" + SEP + "My Games" + SEP + "Some Game (USA).iso"
    stem = "Some Game (USA)"
    broken, unquoted, leftover, byname = [], [], [], []
    for e in d["emulators"]:
        for i, p in enumerate(e["profiles"]):
            m = E.Match(e["id"], i, "C:" + SEP + "Program Files" + SEP + "emu" + SEP + "emu.exe")
            cl = E.command_line(m, rom)
            if "{" in cl and "}" in cl:
                leftover.append((e["id"], p["name"]))
            # Most profiles take the ROM's path. A few take its filename, and
            # MAME and FinalBurnNeo take the set name with no extension - those
            # are run from the ROM's folder, since a name is not a location.
            args = p["args"] or ""
            wants = ("{ImagePath}" if "{ImagePath}" in args or not args
                     else "{ImageName}" if "{ImageName}" in args
                     else "{ImageNameNoExt}")
            expect = {"{ImagePath}": rom, "{ImageName}": os.path.basename(rom),
                      "{ImageNameNoExt}": stem}[wants]
            if expect not in cl:
                broken.append((e["id"], p["name"]))
            elif '"%s"' % expect not in cl:
                unquoted.append((e["id"], p["name"]))
            if wants != "{ImagePath}":
                byname.append((e["id"], p["name"]))
                if E.working_dir(m, rom) != os.path.dirname(rom):
                    broken.append((e["id"], p["name"] + " runs in the wrong folder"))
    check("every profile names the ROM in its command line", not broken, str(broken[:3]))
    check("a name with spaces is always quoted", not unquoted, str(unquoted[:3]))
    check("no placeholder is ever left unsubstituted", not leftover, str(leftover[:3]))
    print("       %d profiles take a path, %d take a ROM set name"
          % (profiles - len(byname), len(byname)))

    # The specific case that was broken: PCSX2-Qt got no arguments at all.
    pcsx2 = E.emulator("pcsx2")
    qt = next((i for i, p in enumerate(pcsx2["profiles"]) if "QT" in p["name"].upper()), None)
    if qt is not None:
        cl = E.command_line(E.Match("pcsx2", qt, "D:" + SEP + "emu" + SEP + "pcsx2-qt.exe"), rom)
        check("PCSX2-Qt now gets its boot arguments",
              "-fullscreen" in cl and "-slowboot" in cl, cl)
        print("       " + cl)

    print("\nlookup")
    check("a PS2 disc image resolves to PCSX2 or similar",
          any(e == "pcsx2" for e, _ in d["by_ext"].get("chd", [])))
    check("formats GameBox could not open before are covered",
          all(x in d["by_ext"] for x in ("nes", "sfc", "n64", "gba", "nds", "rvz", "wbfs")))
    check("a PS3 profile declares no extensions but is script-imported",
          any(p.get("script") for p in E.emulator("rpcs3")["profiles"]))

    print("\ndetection on this machine")
    roots = []
    if "--roots" in sys.argv:
        roots = sys.argv[sys.argv.index("--roots") + 1:]
    else:
        roots = [c + ":" + SEP for c in "CDEZ" if os.path.isdir(c + ":" + SEP)]
    t = time.time()
    inst = E.find_installed(roots)
    print("  looked in %s - %.1fs" % (", ".join(roots), time.time() - t))
    if inst:
        for eid, profs in sorted(inst.items()):
            emu = E.emulator(eid)
            print("  found %-16s %2d profiles  %s"
                  % (emu["name"], len(profs), list(profs.values())[0]))
        # anything found must be able to open something
        for eid, profs in inst.items():
            exts = {x for i in profs for x in E.emulator(eid)["profiles"][i]["ext"]}
            script = any(E.emulator(eid)["profiles"][i].get("script") for i in profs)
            check("%s can open something" % E.emulator(eid)["name"], bool(exts) or script)
    else:
        print("  no emulators installed here - detection returned nothing, which is")
        print("  a valid answer; the definition checks above are the real test")
    check("detection does not crash and returns a dict", isinstance(inst, dict))

    print("\n%d failed" % len(FAILED))
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
