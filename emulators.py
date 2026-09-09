#!/usr/bin/env python3
"""
Emulators: what is installed, what opens a ROM, and how to start it.

GameBox used to hold four extensions and six executable names in a dict, find
the executable by guessing at a few folders, and launch it with the image path
and *no arguments at all* - which is why PCSX2-Qt and RPCS3 did not actually
boot a game. The definitions in emulation/ replace all of that: 92 emulators,
561 profiles, 326 extensions, each profile carrying the platforms it covers,
the extensions it accepts, a regex matching its executable, and the command
line to start a game with. They are Playnite's, MIT-licensed; see
emulation/NOTICE.

  defs()                     the compiled definitions
  find_installed(roots)      -> {emulator id: {profile index: exe path}}
  resolve(rom, installed)    -> a Match for the best profile that opens this ROM
  command_line(match, rom)   -> the string to hand CreateProcess

Everything here is read-only and does no I/O beyond looking for executables.
"""

import io
import json
import os
import re
import sys

_defs = None
_exe_cache = None

# Where emulators are usually installed. Scan roots are added on top.
BASES = (
    os.environ.get("ProgramFiles", ""),
    os.environ.get("ProgramFiles(x86)", ""),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
    os.path.expandvars(r"%USERPROFILE%"),
)
# Folder names that hold emulators, worth descending into on a scan root.
EMU_FOLDERS = ("emulators", "emulation", "emu", "roms")
MAX_DEPTH = 2


def _search_dirs():
    """Where emulators.json might be, nearest first.

    In a build it is packed inside the exe (sys._MEIPASS), but a copy beside
    the exe wins - that is how a newer set of definitions can be dropped in
    without rebuilding.
    """
    out = []
    if getattr(sys, "frozen", False):
        out.append(os.path.dirname(sys.executable))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.append(meipass)
    else:
        out.append(os.path.dirname(os.path.abspath(__file__)))
    return out


def defs():
    """The compiled definitions, or an empty set if they were not shipped."""
    global _defs
    if _defs is None:
        for d in _search_dirs():
            try:
                with io.open(os.path.join(d, "emulators.json"), encoding="utf-8-sig") as fh:
                    _defs = json.load(fh)
                break
            except Exception:
                continue
        if _defs is None:
            _defs = {"version": 0, "platforms": {}, "regions": [],
                     "emulators": [], "by_ext": {}}
    return _defs


def extensions():
    """Every ROM extension any known emulator can open, without the dot."""
    return set(defs().get("by_ext") or {})


def emulator(eid):
    return next((e for e in defs()["emulators"] if e["id"] == eid), None)


def platform_name(pid):
    p = (defs().get("platforms") or {}).get(pid)
    return p["name"] if p else pid


# ------------------------------------------------------------------ detection
def _exe_patterns():
    """One compiled regex per distinct StartupExecutable, with its owners.

    561 profiles share far fewer executables than that - every RetroArch core
    is the same retroarch.exe - so compile the distinct ones once and test each
    executable we find against those, rather than per profile.
    """
    global _exe_cache
    if _exe_cache is None:
        pats = {}
        for e in defs()["emulators"]:
            for i, prof in enumerate(e["profiles"]):
                pat = prof.get("exe")
                if not pat:
                    continue
                pats.setdefault(pat, []).append((e["id"], i))
        _exe_cache = [(re.compile(p, re.I), owners) for p, owners in pats.items()]
    return _exe_cache


def _profile_usable(prof, exe_path):
    """A profile can need files beside its executable - a RetroArch core is a DLL."""
    folder = os.path.dirname(exe_path)
    for rel in prof.get("needs") or []:
        if not os.path.exists(os.path.join(folder, rel.replace("/", os.sep))):
            return False
    return True


def _candidate_dirs(roots):
    """Folders worth looking in, nearest first, without walking whole drives."""
    seen, out = set(), []

    def add(d):
        if d and os.path.isdir(d) and d.lower() not in seen:
            seen.add(d.lower())
            out.append(d)

    for b in BASES:
        add(b)
    for r in roots or ():
        add(r)
        for name in EMU_FOLDERS:
            add(os.path.join(r, name))
    return out


def find_installed(roots=(), overrides=None):
    """Look for emulator executables. -> {emulator id: {profile index: exe path}}

    Only the top couple of levels of each candidate folder are examined; an
    emulator that buries its executable deeper than that can be pointed at
    directly with emulators.json (see overrides).
    """
    found = {}

    def offer(path):
        base = os.path.basename(path)
        for rx, owners in _exe_patterns():
            if not rx.match(base):
                continue
            for eid, idx in owners:
                prof = emulator(eid)["profiles"][idx]
                if _profile_usable(prof, path):
                    found.setdefault(eid, {}).setdefault(idx, path)

    for base in _candidate_dirs(roots):
        stack = [(base, 0)]
        while stack:
            d, depth = stack.pop()
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_file() and e.name.lower().endswith(".exe"):
                        offer(e.path)
                    elif e.is_dir() and depth < MAX_DEPTH and not e.name.startswith("."):
                        stack.append((e.path, depth + 1))
                except OSError:
                    pass

    # A user-supplied emulators.json wins over anything found on disk. The old
    # format was {".iso": ["C:\\emu\\pcsx2-qt.exe"]}; both are accepted.
    for path in (overrides or {}).values() if isinstance(overrides, dict) else ():
        for p in (path if isinstance(path, list) else [path]):
            if isinstance(p, str) and os.path.exists(p):
                offer(p)
    return found


# ------------------------------------------------------------------- matching
class Match(object):
    """One way to open one ROM."""

    def __init__(self, eid, idx, exe):
        self.emulator_id = eid
        self.profile_index = idx
        self.exe = exe

    @property
    def profile(self):
        return emulator(self.emulator_id)["profiles"][self.profile_index]

    @property
    def name(self):
        return "%s (%s)" % (emulator(self.emulator_id)["name"], self.profile["name"])

    @property
    def platforms(self):
        return self.profile.get("platforms") or []

    def as_dict(self):
        return {"emulator": self.emulator_id, "profile": self.profile_index,
                "exe": self.exe, "name": self.name}


def candidates(rom, installed):
    """Every installed profile that accepts this ROM's extension."""
    ext = os.path.splitext(rom)[1].lower().lstrip(".")
    out = []
    for eid, idx in (defs().get("by_ext") or {}).get(ext, []):
        exe = (installed.get(eid) or {}).get(idx)
        if exe:
            out.append(Match(eid, idx, exe))
    return out


# Platforms whose "ROM" is a folder rather than a file. Each is a probe that,
# given a game folder, returns the path to hand the emulator - or None.
def _ps3_target(folder):
    """A PS3 game is a folder; RPCS3 wants its EBOOT.BIN (or the folder)."""
    for rel in (("PS3_GAME", "USRDIR", "EBOOT.BIN"), ("USRDIR", "EBOOT.BIN")):
        p = os.path.join(folder, *rel)
        if os.path.exists(p):
            return p
    if os.path.isdir(os.path.join(folder, "PS3_GAME")):
        return folder
    return None


FOLDER_PLATFORMS = (("sony_playstation3", _ps3_target),)


def resolve_folder(folder, installed):
    """(Match, target path) for a game folder an emulator opens, or (None, None).

    Extensions cannot answer this: RPCS3 declares none, because a PlayStation 3
    game is a directory tree rather than a disc image. Playnite imports these
    with a per-emulator script; the shape of the folder is enough to recognise.
    """
    for platform, probe in FOLDER_PLATFORMS:
        target = probe(folder)
        if not target:
            continue
        for eid, profiles in installed.items():
            emu = emulator(eid)
            for idx in profiles:
                prof = emu["profiles"][idx]
                if platform in (prof.get("platforms") or []):
                    return Match(eid, idx, profiles[idx]), target
    return None, None


def for_platform(platform, installed):
    """Any installed profile that covers a platform, ignoring extensions.

    Needed because the most important case declares no extensions: RPCS3 opens
    PlayStation 3 games, which are folders, so nothing about a .iso says it is
    a PS3 disc except where it is filed.
    """
    out = []
    for eid, profiles in installed.items():
        emu = emulator(eid)
        for idx in profiles:
            if platform in (emu["profiles"][idx].get("platforms") or []):
                out.append(Match(eid, idx, profiles[idx]))
    return out


def resolve(rom, installed, prefer=None, platform=None):
    """The profile to open this ROM with, or None.

    prefer    {"emulator": id, "profile": index} - a per-game override, used
              when it is installed and still accepts the file
    platform  the console the ROM's location implies. A .iso is a PS2 disc or a
              PS3 disc depending only on which folder it is in, so when the
              caller knows, it decides.
    """
    hits = candidates(rom, installed)
    if prefer:
        # An explicit choice outranks the extension index, which is only a
        # guess and is silent about emulators that declare no extensions at
        # all. Any installed profile the user picked is honoured.
        eid, idx = prefer.get("emulator"), prefer.get("profile")
        exe = (installed.get(eid) or {}).get(idx)
        if exe:
            return Match(eid, idx, exe)
    if platform:
        on_platform = [m for m in hits if platform in m.platforms]
        if on_platform:
            hits = on_platform
        elif not any(platform in m.platforms for m in hits):
            # nothing that takes this extension covers the platform the folder
            # says it is - trust the folder, if something here can run it
            fallback = for_platform(platform, installed)
            if fallback:
                hits = fallback
    if not hits:
        return None

    # A profile that names one platform is a better answer than a general one,
    # and a dedicated emulator is a better answer than a RetroArch core.
    def rank(m):
        return (m.emulator_id == "retroarch",
                len(m.platforms) != 1,
                m.profile_index)
    return sorted(hits, key=rank)[0]


# ------------------------------------------------------------------ launching
def command_line(match, rom):
    """The full command line for a ROM, as Windows wants it.

    The argument templates are real command lines - '-L ".\\cores\\x.dll"
    "{ImagePath}"', '--exec="{ImagePath}" --batch', '-fullscreen -slowboot --
    {ImagePath}' - so they are handed to CreateProcess as a string rather than
    split into a list. Splitting them would mean re-quoting, and a Windows path
    is full of backslashes that any POSIX-shaped splitter treats as escapes.
    """
    args = match.profile.get("args") or ""
    if not args:
        return '"%s" "%s"' % (match.exe, rom)
    base = os.path.basename(rom)
    values = {
        "ImagePath": rom,
        "ImageName": base,
        # MAME and FinalBurnNeo take the ROM *set* name rather than a path -
        # "sfiii3", not "D:\roms\sfiii3.zip" - so these are not paths at all.
        "ImageNameNoExt": os.path.splitext(base)[0],
        "StartupDir": os.path.dirname(match.exe),
    }
    for key, value in values.items():
        # Some templates quote the placeholder themselves and some do not; a
        # value with a space in it has to survive either way, and must not end
        # up double-quoted.
        args = args.replace('"{%s}"' % key, '"%s"' % value)
        args = args.replace("{%s}" % key, '"%s"' % value)
    return '"%s" %s' % (match.exe, args)


def working_dir(match, rom):
    """Where to start the emulator.

    A profile that takes a ROM set name rather than a path (MAME, FinalBurnNeo)
    only finds its set if it is run where the set lives.
    """
    args = match.profile.get("args") or ""
    if "{ImageName" in args and "{ImagePath}" not in args:
        return os.path.dirname(rom)
    return os.path.dirname(match.exe)


def summary():
    d = defs()
    return "%d emulators, %d profiles, %d extensions" % (
        len(d["emulators"]),
        sum(len(e["profiles"]) for e in d["emulators"]),
        len(d.get("by_ext") or {}))
