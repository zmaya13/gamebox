#!/usr/bin/env python3
"""
Compile emulation/*.yaml into a single emulators.json the app ships.

Playnite keeps one emulator.yaml per emulator, plus a platform and a region
table. That is a good shape to maintain and a poor shape to load: 94 files and
a YAML parser, to answer "what opens a .iso". So the build folds them into one
JSON with the lookup already inverted, and the app carries no YAML dependency.

  python tools/build_emulators.py            write emulators.json
  python tools/build_emulators.py --check     report, write nothing

The source files are Playnite's, MIT-licensed - see emulation/NOTICE.
"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "emulation")
OUT = os.path.join(ROOT, "emulators.json")

VERSION = 1


def load_yaml(path):
    import yaml
    # several of these files carry a UTF-8 BOM
    with io.open(path, encoding="utf-8-sig") as fh:
        return yaml.safe_load(fh)


def build():
    platforms = {}
    for p in load_yaml(os.path.join(SRC, "Platforms.yaml")) or []:
        if p.get("Id"):
            platforms[p["Id"]] = {"name": p.get("Name") or p["Id"],
                                  "igdb": p.get("IgdbId")}

    regions = []
    for r in load_yaml(os.path.join(SRC, "Regions.yaml")) or []:
        if r.get("Id"):
            regions.append({"id": r["Id"], "name": r.get("Name") or r["Id"],
                            "codes": r.get("Codes") or []})

    emulators, by_ext = [], {}
    root = os.path.join(SRC, "Emulators")
    for name in sorted(os.listdir(root)):
        f = os.path.join(root, name, "emulator.yaml")
        if not os.path.exists(f):
            continue
        d = load_yaml(f) or {}
        if not d.get("Id"):
            continue
        profiles = []
        for prof in d.get("Profiles") or []:
            exts = [str(e).lower().lstrip(".") for e in (prof.get("ImageExtensions") or [])]
            entry = {
                "name": prof.get("Name") or "Default",
                "exe": prof.get("StartupExecutable") or "",
                "args": prof.get("StartupArguments") or "",
                "platforms": prof.get("Platforms") or [],
                "ext": exts,
                # A profile can need more than its executable: a RetroArch core
                # is a DLL, and a profile whose core is not installed cannot
                # open anything. Detection checks these before offering it.
                "needs": prof.get("ProfileFiles") or [],
                # Some platforms are not files at all - a PS3 game is a folder
                # with an EBOOT.BIN in it - so those profiles declare no
                # extensions and are matched by folder shape instead.
                "script": bool(prof.get("ScriptGameImport")),
            }
            idx = len(profiles)
            profiles.append(entry)
            for e in exts:
                by_ext.setdefault(e, []).append([d["Id"], idx])
        if not profiles:
            continue
        emulators.append({"id": d["Id"], "name": d.get("Name") or d["Id"],
                          "site": d.get("Website") or "", "profiles": profiles})

    return {"version": VERSION, "platforms": platforms, "regions": regions,
            "emulators": emulators, "by_ext": by_ext}


def main():
    if not os.path.isdir(SRC):
        print("! %s is missing - the emulator definitions are not vendored" % SRC)
        return 1
    try:
        data = build()
    except ImportError:
        print("! PyYAML is needed to compile the definitions:  pip install pyyaml")
        return 1

    profiles = sum(len(e["profiles"]) for e in data["emulators"])
    print("  %d emulators, %d profiles, %d platforms, %d extensions"
          % (len(data["emulators"]), profiles, len(data["platforms"]), len(data["by_ext"])))

    if "--check" in sys.argv:
        return 0
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print("  wrote %s (%.0f KB)" % (os.path.basename(OUT), os.path.getsize(OUT) / 1e3))
    return 0


if __name__ == "__main__":
    sys.exit(main())
