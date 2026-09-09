#!/usr/bin/env python3
"""
The launchers GameBox did not know about: Xbox, Ubisoft, EA, Battle.net, and a
couple of smaller ones.

Every one of these is read locally, from what the launcher has already written
to this machine - a registry key, a manifest, a small SQLite database. Nothing
here signs in, asks for a token, or talks to a server, which means it reports
what is *installed* rather than what is owned. That matches what GameBox is:
the games actually on this PC.

Each scanner returns records in the shape scan-library.py expects:

    {"title", "store", "path", "bytes", "via", "launch": (kind, target, folder),
     plus whichever store key makes the id stable}

Where a store has its own identifier for a game, it is carried through so the
game keeps one identity across rescans.
"""

import io
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET

# Reading the registry is optional: everything here degrades to "found nothing"
# on a machine where a key is missing or unreadable.
try:
    import winreg
except ImportError:                      # not Windows - every scanner returns []
    winreg = None

HKLM = getattr(winreg, "HKEY_LOCAL_MACHINE", None)
HKCU = getattr(winreg, "HKEY_CURRENT_USER", None)
WOW64 = getattr(winreg, "KEY_WOW64_32KEY", 0)
READ = getattr(winreg, "KEY_READ", 0)


# ------------------------------------------------------------------ registry
def _open(root, path, wow32=False):
    if not winreg:
        return None
    try:
        return winreg.OpenKey(root, path, 0, READ | (WOW64 if wow32 else 0))
    except OSError:
        return None


def _value(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except (OSError, TypeError):
        return None


def _subkeys(key):
    if not key:
        return []
    out, i = [], 0
    while True:
        try:
            out.append(winreg.EnumKey(key, i))
        except OSError:
            return out
        i += 1


def _uninstall_entries(wow32=True):
    """{key name: {DisplayName, InstallLocation, ...}} from Add/Remove Programs.

    This is where most launchers record a readable title for a game whose own
    manifest only has an id.
    """
    out = {}
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for root, w in ((HKLM, wow32), (HKLM, False), (HKCU, False)):
        parent = _open(root, base, w)
        if not parent:
            continue
        for name in _subkeys(parent):
            k = _open(root, base + "\\" + name, w)
            if not k:
                continue
            out.setdefault(name, {
                "name": _value(k, "DisplayName"),
                "path": _value(k, "InstallLocation"),
                "size": _value(k, "EstimatedSize"),
            })
    return out


def _dir_size(path, budget=200_000):
    total = seen = 0
    for root, _d, files in os.walk(path or ""):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            seen += 1
            if seen > budget:
                return total
    return total


# ---------------------------------------------------------------- Xbox / PC
# Game Pass and Microsoft Store games install into <drive>\XboxGames\<Title>\
# with a MicrosoftGame.config naming the package and the executable. They are
# started through the shell rather than by running the exe, which refuses to
# launch outside its package identity.
def scan_xbox(roots=()):
    out = []
    seen = set()
    candidates = [os.path.join(r, "XboxGames") for r in roots]
    candidates += [c + ":\\XboxGames" for c in "CDEFGHZ"]
    for base in candidates:
        if not os.path.isdir(base) or base.lower() in seen:
            continue
        seen.add(base.lower())
        for name in sorted(os.listdir(base)):
            folder = os.path.join(base, name)
            cfg = os.path.join(folder, "Content", "MicrosoftGame.config")
            if not os.path.exists(cfg):
                continue
            title, pfn, app_id, exe = name, None, None, None
            try:
                root = ET.parse(cfg).getroot()
                ident = root.find("Identity")
                if ident is not None:
                    pfn = ident.get("Name")
                sd = root.find("ShellVisuals")
                if sd is not None:
                    title = sd.get("DefaultDisplayName") or title
                ex = root.find("ExecutableList/Executable")
                if ex is not None:
                    exe = ex.get("Name")
                    app_id = ex.get("Id")
            except Exception:
                pass
            if not pfn:
                continue
            content = os.path.join(folder, "Content")
            # "shell:appsFolder\<family name>!<app id>" is how Windows starts a
            # packaged game; running the exe directly fails on its identity.
            aumid = "%s!%s" % (_family_name(pfn), app_id or "Game")
            out.append({
                "title": title, "store": "xbox", "xbox_pfn": pfn,
                "path": content, "bytes": _dir_size(content),
                "via": "Xbox app", "exe_hint": exe,
                "launch": ("shell", aumid, content),
            })
    return out


def _family_name(pfn):
    """A package family name from whatever the config gave us.

    MicrosoftGame.config carries the package *name*; the family name is what
    the shell wants. Look it up where we can, and fall back to the name, which
    is right for a game whose config already recorded the family form.
    """
    if "_" in pfn:
        return pfn
    base = r"SOFTWARE\Classes\ActivatableClasses\Package"
    key = _open(HKLM, base)
    for sub in _subkeys(key):
        if sub.startswith(pfn + "_"):
            # <name>_<version>_<arch>__<publisher id>  ->  <name>_<publisher id>
            bits = sub.split("__")
            if len(bits) == 2:
                return pfn + "_" + bits[1]
    return pfn


# ------------------------------------------------------------ Ubisoft Connect
def scan_ubisoft(roots=()):
    out = []
    key = _open(HKLM, r"SOFTWARE\Ubisoft\Launcher\Installs", True)
    if not key:
        return out
    uninstall = _uninstall_entries()
    for game_id in _subkeys(key):
        sub = _open(HKLM, r"SOFTWARE\Ubisoft\Launcher\Installs\\" + game_id, True)
        path = _value(sub, "InstallDir") if sub else None
        if not path:
            continue
        path = os.path.normpath(path)
        if not os.path.isdir(path):
            continue
        entry = uninstall.get("Uplay Install " + game_id) or {}
        title = entry.get("name") or os.path.basename(path.rstrip(os.sep))
        out.append({
            "title": title, "store": "ubisoft", "ubi_id": game_id,
            "path": path, "bytes": _dir_size(path), "via": "Ubisoft Connect",
            "launch": ("uri", "uplay://launch/%s/0" % game_id, path),
        })
    return out


# --------------------------------------------------------------------- EA app
# Keys under Electronic Arts that are the launcher, not a game.
EA_NOT_GAMES = {"ea core", "ea desktop", "eadm", "electronic arts", "origin",
                "ea games", "install"}


def scan_ea(roots=()):
    """EA games, from the registry and from each install's own installerdata.

    EA writes an entitlement key for a game whether or not it is installed - and
    for a game bought elsewhere - so what makes an entry real is an "Install Dir"
    that exists. The content id that origin2:// needs is not in the registry at
    all; it is in __Installer\\installerdata.xml inside the install.
    """
    out, seen = [], set()
    bases = [r"SOFTWARE\Electronic Arts", r"SOFTWARE\EA Games",
             r"SOFTWARE\Origin Games"]

    def consider(path, fallback_name):
        if not path:
            return
        path = os.path.normpath(str(path))
        if not os.path.isdir(path) or path.lower() in seen:
            return
        seen.add(path.lower())
        content_id = _installer_content_id(path)
        out.append({
            "title": fallback_name, "store": "ea", "ea_id": content_id or fallback_name,
            "path": path, "bytes": _dir_size(path), "via": "EA app",
            "launch": (("uri", "origin2://game/launch?offerIds=%s" % content_id, path)
                       if content_id else ("exe", _first_exe(path) or "", path)),
        })

    for base in bases:
        key = _open(HKLM, base, True)
        for name in _subkeys(key):
            if name.strip().lower() in EA_NOT_GAMES:
                continue
            sub = _open(HKLM, base + "\\" + name, True)
            title = _value(sub, "DisplayName") or name
            path = _value(sub, "Install Dir") or _value(sub, "InstallDir")
            if path:
                consider(path, title)
                continue
            # Some titles put the install under a per-edition subkey instead of
            # on the game key itself.
            for edition in _subkeys(sub):
                esub = _open(HKLM, base + "\\" + name + "\\" + edition, True)
                consider(_value(esub, "Install Dir") or _value(esub, "InstallDir"), title)

    # A game with no launch target is not useful; drop the ones we could not
    # work out how to start rather than listing them as broken.
    return [g for g in out if g["launch"][0] == "uri" or g["launch"][1]]


def _installer_content_id(path):
    f = os.path.join(path, "__Installer", "installerdata.xml")
    if not os.path.exists(f):
        return None
    try:
        root = ET.parse(f).getroot()
        for tag in ("contentIDs/contentID", "contentIDs/contentId",
                    "gameTitles/gameTitle"):
            el = root.find(tag)
            if el is not None and (el.text or "").strip():
                return el.text.strip()
    except Exception:
        pass
    return None


# ----------------------------------------------------------------- Battle.net
# Each Blizzard install carries a .product.db and a .build.info naming the
# product; the launcher starts it with battlenet://<code>.
BNET_CODES = {
    "wow": "WoW", "wow_classic": "WoW_classic", "d3": "D3", "diablo3": "D3",
    "d4": "Fen", "fenris": "Fen", "s2": "S2", "sc2": "S2", "s1": "S1",
    "hero": "Hero", "heroes": "Hero", "hs_beta": "WTCG", "hsb": "WTCG",
    "pro": "Pro", "prometheus": "Pro", "w3": "W3", "viper": "VIPR",
    "odin": "ODIN", "lazr": "LAZR", "zeus": "ZEUS", "rtro": "RTRO",
    "gryphon": "GRY", "anbs": "ANBS",
}


def scan_battlenet(roots=()):
    out = []
    uninstall = _uninstall_entries()
    for key, entry in uninstall.items():
        if not key.startswith("Battle.net"):
            continue
        path = entry.get("path")
        name = entry.get("name")
        if not path or not name or not os.path.isdir(path):
            continue
        if "battle.net" in name.lower():
            continue                     # the launcher itself is not a game
        code = _bnet_code(path) or key.replace("Battle.net", "").strip()
        out.append({
            "title": name, "store": "bnet", "bnet_code": code,
            "path": os.path.normpath(path), "bytes": _dir_size(path),
            "via": "Battle.net",
            "launch": ("uri", "battlenet://" + code, path) if code else ("none", ""),
        })
    return out


def _bnet_code(path):
    """The product code from .build.info, which every Blizzard install has."""
    f = os.path.join(path, ".build.info")
    try:
        lines = io.open(f, encoding="utf-8", errors="ignore").read().splitlines()
    except OSError:
        return None
    if len(lines) < 2:
        return None
    head = [h.split("!")[0].strip().lower() for h in lines[0].split("|")]
    try:
        col = head.index("product")
    except ValueError:
        return None
    for row in lines[1:]:
        bits = row.split("|")
        if len(bits) > col and bits[col].strip():
            p = bits[col].strip()
            return BNET_CODES.get(p.lower(), p)
    return None


# -------------------------------------------------------------- Amazon Games
def scan_amazon(roots=()):
    db = os.path.expandvars(
        r"%LOCALAPPDATA%\Amazon Games\Data\Games\Sql\GameInstallInfo.sqlite")
    if not os.path.exists(db):
        return []
    out = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
        rows = con.execute(
            "select Id, ProductTitle, InstallDirectory, Installed from DbSet").fetchall()
        con.close()
    except sqlite3.Error:
        return []
    for gid, title, path, installed in rows:
        if not installed or not path or not os.path.isdir(path):
            continue
        out.append({
            "title": title or gid, "store": "amazon", "amazon_id": gid,
            "path": os.path.normpath(path), "bytes": _dir_size(path),
            "via": "Amazon Games",
            "launch": ("uri", "amazon-games://play/" + gid, path),
        })
    return out


# ------------------------------------------------------------------- itch.io
def scan_itch(roots=()):
    db = os.path.expandvars(r"%APPDATA%\itch\db\butler.db")
    if not os.path.exists(db):
        return []
    out = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
        rows = con.execute(
            "select c.id, c.verdict, g.title from caves c "
            "left join games g on g.id = c.game_id").fetchall()
        con.close()
    except sqlite3.Error:
        return []
    for cave_id, verdict, title in rows:
        path = None
        try:
            path = (json.loads(verdict or "{}") or {}).get("basePath")
        except ValueError:
            pass
        if not path or not os.path.isdir(path):
            continue
        exe = _first_exe(path)
        out.append({
            "title": title or os.path.basename(path), "store": "itch",
            "itch_id": str(cave_id), "path": os.path.normpath(path),
            "bytes": _dir_size(path), "via": "itch.io",
            "launch": ("exe", exe, path) if exe else ("none", ""),
        })
    return out


def _first_exe(folder):
    bad = re.compile(r"unins|crash|setup|updater|helper|redist", re.I)
    best = None
    for root, _d, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".exe") and not bad.search(f):
                p = os.path.join(root, f)
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                if best is None or size > best[0]:
                    best = (size, p)
        if root.count(os.sep) - folder.count(os.sep) > 2:
            break
    return best[1] if best else None


# ---------------------------------------------------------------------- all
SCANNERS = (
    ("Xbox", "xbox", scan_xbox),
    ("Ubisoft", "ubisoft", scan_ubisoft),
    ("EA", "ea", scan_ea),
    ("Battle.net", "bnet", scan_battlenet),
    ("Amazon", "amazon", scan_amazon),
    ("itch.io", "itch", scan_itch),
)

# Which key makes a game from each store keep its identity across rescans.
ID_KEYS = {"xbox": "xbox_pfn", "ubisoft": "ubi_id", "ea": "ea_id",
           "bnet": "bnet_code", "amazon": "amazon_id", "itch": "itch_id"}


def installed_launchers():
    """Which of these are present on this machine, for the setup wizard."""
    return {
        # An XboxGames folder exists as soon as the app has run once - it holds
        # a GameSave folder whether or not anything is installed - so ask
        # whether there is actually a game in it.
        "Xbox app": bool(scan_xbox()),
        "Ubisoft Connect": bool(_open(HKLM, r"SOFTWARE\Ubisoft\Launcher", True)),
        "EA app": os.path.isdir(os.path.expandvars(r"%ProgramFiles%\Electronic Arts")),
        "Battle.net": os.path.isdir(os.path.expandvars(r"%ProgramData%\Battle.net")),
        "Amazon Games": os.path.exists(os.path.expandvars(
            r"%LOCALAPPDATA%\Amazon Games\Data\Games\Sql\GameInstallInfo.sqlite")),
        "itch.io": os.path.exists(os.path.expandvars(r"%APPDATA%\itch\db\butler.db")),
    }
