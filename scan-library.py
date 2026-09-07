#!/usr/bin/env python3
"""
GameBox library scanner
=======================

Finds the games installed on this PC, resolves cover art and store metadata,
and writes the result straight into the app.

  python scan-library.py                     scan and print a report
  python scan-library.py --json library.json write the raw scan out as JSON
  python scan-library.py --apply             build the library into GameBox.html
  python scan-library.py --root Z:\\ --root D:\\Games

What it reads
-------------
  Steam       <drive>\\SteamLibrary\\steamapps\\appmanifest_*.acf
              real title, appid, size on disk, last played, last updated
  GOG         %ProgramData%\\GOG.com\\Galaxy\\storage\\galaxy-2.0.db
              owned and installed releases, plus each game's cover hashes
  Epic        %ProgramData%\\Epic\\EpicGamesLauncher\\Data\\Manifests\\*.item
              display name, app id and launch executable
  Folders     one game per top-level folder under each --root

Cover art, in priority order
----------------------------
  1. Steam's own cache      %ProgramFiles(x86)%\\Steam\\appcache\\librarycache
  2. GOG Galaxy's webcache  %ProgramData%\\GOG.com\\Galaxy\\webcache
  3. Steam's public CDN     matched by title through Steam's app search
  4. Generated              a platform plate for disc images and ROMs

With --apply it writes two things:
  GameBox.html   the library array the UI renders
  launch.json    how each game starts, which the app reads at startup

It only ever reads your games. It never moves, deletes or modifies them.
"""

import argparse, base64, io, json, os, re, sqlite3, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(HERE, "GameBox.html")
LAUNCH_JSON = os.path.join(HERE, "launch.json")
STEAM_CACHE = os.path.expandvars(r"%ProgramFiles(x86)%\Steam\appcache\librarycache")
GOG_DB = os.path.expandvars(r"%ProgramData%\GOG.com\Galaxy\storage\galaxy-2.0.db")
GOG_CACHE = os.path.expandvars(r"%ProgramData%\GOG.com\Galaxy\webcache")
EPIC_MANIFESTS = os.path.expandvars(r"%ProgramData%\Epic\EpicGamesLauncher\Data\Manifests")
UA = {"User-Agent": "GameBox-Scanner/1.0"}
GB = 1024 ** 3

EMULATORS = {  # extension -> candidate emulator executables
    ".iso": [r"Z:\pcsx2-v2.8.1-windows-x64-Qt\pcsx2-qt.exe",
             os.path.expandvars(r"%ProgramFiles%\PCSX2\pcsx2-qt.exe"),
             r"Z:\RPCS3\rpcs3.exe",
             os.path.expandvars(r"%ProgramFiles%\RPCS3\rpcs3.exe")],
    ".gb": [os.path.expandvars(r"%ProgramFiles%\mGBA\mGBA.exe")],
    ".gbc": [os.path.expandvars(r"%ProgramFiles%\mGBA\mGBA.exe")],
}
SKIP_FOLDERS = {"system volume information", "$recycle.bin", "windows", "program files",
                "program files (x86)", "programdata", "users", "temp", "tmp", "downloads",
                "backup", "tools", "code", "files", "media & captures", "projects & code"}
SKIP_TITLES = {"unreal engine", "quixel bridge", "fab ue plugin", "epic online services"}


def human(n):
    return "%.1f GB" % (n / GB)


def folder_size(path, budget=400_000):
    total = seen = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            seen += 1
            if seen > budget:
                return total
    return total


def ts(v, fmt="%b %d, %Y"):
    v = int(v or 0)
    return time.strftime(fmt, time.localtime(v)) if v > 0 else None


def get(url, timeout=25):
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()
    except Exception:
        return None


norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ------------------------------------------------------------------ sources
def scan_steam(roots):
    out, seen = [], set()
    dirs = []
    for r in roots:
        dirs += [os.path.join(r, "SteamLibrary", "steamapps"), os.path.join(r, "steamapps"),
                 os.path.join(r, "Steam", "steamapps")]
    dirs.append(os.path.expandvars(r"%ProgramFiles(x86)%\Steam\steamapps"))
    for d in dirs:
        if not os.path.isdir(d) or d.lower() in seen:
            continue
        seen.add(d.lower())
        for f in os.listdir(d):
            if not (f.startswith("appmanifest_") and f.endswith(".acf")):
                continue
            try:
                t = open(os.path.join(d, f), encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            g = lambda k: (re.search(r'"%s"\s+"([^"]*)"' % k, t) or [None, None])[1]
            if not g("name"):
                continue
            out.append({"title": g("name"), "store": "steam", "appid": g("appid"),
                        "bytes": int(g("SizeOnDisk") or 0), "updated": ts(g("LastUpdated")),
                        "played": ts(g("LastPlayed")),
                        "path": os.path.join(d, "common", g("installdir") or g("name")),
                        "via": "Steam runtime",
                        "launch": ("steam", g("appid"))})
    return out


def scan_gog():
    if not os.path.exists(GOG_DB):
        return []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % GOG_DB.replace("\\", "/"), uri=True)
        cur = con.cursor()
        cur.execute("select releaseKey from LibraryReleases")
        keys = [r[0] for r in cur.fetchall()]
        installed = set()
        try:
            cur.execute("select productId from InstalledBaseProducts")
            installed = {"gog_%s" % r[0] for r in cur.fetchall()}
        except sqlite3.Error:
            pass
    except sqlite3.Error:
        return []

    def piece(key, tid):
        cur.execute("select value from GamePieces where releaseKey=? and gamePieceTypeId=?", (key, tid))
        row = cur.fetchone()
        try:
            return json.loads(row[0]) if row else None
        except (TypeError, ValueError):
            return None

    out = []
    for k in keys:
        if k not in installed:
            continue
        title = (piece(k, 236) or {}).get("title")
        if not title:
            continue
        img = piece(k, 145) or piece(k, 209) or {}
        grab = lambda u: (re.search(r"/([0-9a-f]{64})_", u or "") or [None, None])[1] if isinstance(u, str) else None
        out.append({"title": title, "store": "gog", "via": "GOG Galaxy",
                    "cover_hash": grab(img.get("verticalCover") if isinstance(img, dict) else ""),
                    "bg_hash": grab(img.get("background") if isinstance(img, dict) else "")})
    return out


def scan_epic():
    out = []
    if not os.path.isdir(EPIC_MANIFESTS):
        return out
    for f in os.listdir(EPIC_MANIFESTS):
        if not f.endswith(".item"):
            continue
        try:
            m = json.load(open(os.path.join(EPIC_MANIFESTS, f), encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        name = m.get("DisplayName", "?")
        if norm(name) in {norm(x) for x in SKIP_TITLES}:
            continue
        out.append({"title": name, "store": "epic", "via": "Epic launcher",
                    "path": m.get("InstallLocation", ""), "bytes": int(m.get("InstallSize") or 0),
                    "launch": ("epic", m.get("AppName", ""))})
    return out


def scan_folders(root, min_gb=0.2):
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not os.path.isdir(p) or name.lower() in SKIP_FOLDERS or name.startswith("."):
            continue
        size = folder_size(p)
        if size < min_gb * GB:
            continue
        exe = best_exe(p)
        disc = first_match(p, (".iso", ".gb", ".gbc", ".chd"))
        entry = {"title": name, "store": "local", "path": p, "bytes": size,
                 "updated": ts(os.path.getmtime(p)), "via": "Direct executable"}
        if exe:
            entry["launch"] = ("exe", exe)
        elif disc:
            emu = next((e for e in EMULATORS.get(os.path.splitext(disc)[1].lower(), []) if os.path.exists(e)), None)
            entry["via"] = os.path.basename(emu) if emu else "Disc image"
            entry["store"] = "emu"
            entry["launch"] = ("emu", emu, disc) if emu else ("none", "")
        else:
            entry["launch"] = ("none", "")
        out.append(entry)
    return out


def first_match(folder, exts):
    for root, _d, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in exts:
                return os.path.join(root, f)
        if root.count(os.sep) - folder.count(os.sep) > 2:
            break
    return None


BAD_EXE = re.compile(r"unins|crash|redist|vcredist|setup|touchup|updater|helper|dxsetup|"
                     r"activation|installer|cleanup|trial|launcher_", re.I)


def best_exe(folder):
    best = None
    for root, _d, files in os.walk(folder):
        for f in files:
            if not f.lower().endswith(".exe") or BAD_EXE.search(f):
                continue
            p = os.path.join(root, f)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if best is None or sz > best[0]:
                best = (sz, p)
        if root.count(os.sep) - folder.count(os.sep) > 2:
            break
    return best[1] if best else None


# ----------------------------------------------------------------- artwork
def enc(im, w, h, q):
    from PIL import Image
    im = im.convert("RGB").resize((w, h), Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def steam_appid(title):
    d = get("https://steamcommunity.com/actions/SearchApps/" + urllib.parse.quote(title))
    if not d:
        return None
    try:
        arr = json.loads(d.decode("utf-8", "ignore"))
    except ValueError:
        return None
    target, best = norm(title), None
    for a in arr[:8]:
        n = norm(a.get("name", ""))
        score = 2 if n == target else 1 if target in n or n in target else 0
        if best is None or score > best[0]:
            best = (score, a["appid"])
    return best[1] if best and best[0] else None


def store_meta(appid):
    d = get("https://store.steampowered.com/api/appdetails?appids=%s&l=english" % appid)
    if not d:
        return None
    try:
        j = json.loads(d.decode("utf-8", "ignore"))[str(appid)]
    except Exception:
        return None
    if not j.get("success"):
        return None
    a = j["data"]
    keep = ("Single-player", "Multi-player", "Co-op", "Online Co-op", "Shared/Split Screen",
            "Full controller support", "Partial Controller Support", "Steam Cloud",
            "Steam Achievements", "VR Supported", "VR Only", "PvP")
    req = a.get("pc_requirements") or {}
    txt = re.sub(r"<[^>]+>", " ", req.get("minimum", "") if isinstance(req, dict) else "")
    m = re.search(r"OS[^:]*:\s*(.*?)(?:Processor|Memory|$)", txt)
    meta = {"desc": (a.get("short_description") or "")[:230],
            "dev": ", ".join(a.get("developers", [])[:2]),
            "pub": ", ".join(a.get("publishers", [])[:2]),
            "genres": [g["description"] for g in a.get("genres", [])][:3],
            "rel": (a.get("release_date") or {}).get("date", ""),
            "cats": [c["description"] for c in a.get("categories", []) if c["description"] in keep][:6],
            "os": (m.group(1)[:40].strip(" *:") if m else "Windows"), "shots": []}
    from PIL import Image
    for sc in (a.get("screenshots") or [])[:3]:
        raw = get(sc.get("path_thumbnail") or sc.get("path_full"), 20)
        if raw:
            try:
                im = Image.open(io.BytesIO(raw))
                meta["shots"].append(enc(im, 340, int(im.height * 340 / im.width), 68))
            except Exception:
                pass
    return meta


def generated_plate(title, platform=""):
    from PIL import Image, ImageDraw, ImageFont
    tints = {"PS3": ((24, 52, 74), (8, 16, 26)), "PS2": ((60, 26, 40), (16, 10, 18)),
             "GB": ((176, 150, 40), (40, 34, 14)), "": ((40, 44, 54), (14, 16, 22))}
    c1, c2 = tints.get(platform, tints[""])
    W, H = 300, 450
    im = Image.new("RGB", (W, H), c1)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    d.rectangle([0, 0, W, 46], fill=(12, 14, 20))
    fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

    def font(sz):
        for n in ("segoeuib.ttf", "arialbd.ttf"):
            p = os.path.join(fdir, n)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, sz)
                except OSError:
                    pass
        return ImageFont.load_default()

    d.text((14, 15), platform or "NO STORE ARTWORK", font=font(17), fill=(232, 238, 248))
    d.rectangle([0, 46, W, 49], fill=(255, 122, 26))
    f = font(29)
    lines, cur = [], ""
    for w in title.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= W - 36:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    y = H - 40 - len(lines[:4]) * 33
    for ln in lines[:4]:
        d.text((18, y), ln, font=f, fill=(255, 255, 255))
        y += 33
    d.rectangle([0, 0, W - 1, H - 1], outline=(255, 255, 255), width=1)
    return im


def artwork(game, gog_index, want_meta=True):
    """-> (cover, ambient, source, meta|None)"""
    from PIL import Image
    appid = game.get("appid")
    if appid:
        d = os.path.join(STEAM_CACHE, str(appid))
        cov = os.path.join(d, "library_600x900.jpg")
        if os.path.exists(cov):
            hero = os.path.join(d, "library_hero.jpg")
            return (enc(Image.open(cov), 300, 450, 80),
                    enc(Image.open(hero), 640, 300, 70) if os.path.exists(hero) else None,
                    "steam-cache", store_meta(appid) if want_meta else None)
    if game.get("cover_hash"):
        fc = gog_index.get(game["cover_hash"] + "_glx_vertical_cover.webp")
        if fc:
            fb = gog_index.get((game.get("bg_hash") or "") + "_glx_bg_top_padding_7.webp")
            aid = appid or steam_appid(game["title"])
            return (enc(Image.open(fc), 300, 450, 80),
                    enc(Image.open(fb), 640, 300, 70) if fb else None,
                    "gog-cache", store_meta(aid) if (aid and want_meta) else None)
    aid = appid or steam_appid(game["title"])
    if aid:
        raw = get("https://cdn.akamai.steamstatic.com/steam/apps/%s/library_600x900.jpg" % aid)
        if raw:
            hero = get("https://cdn.akamai.steamstatic.com/steam/apps/%s/library_hero.jpg" % aid)
            return (enc(Image.open(io.BytesIO(raw)), 300, 450, 80),
                    enc(Image.open(io.BytesIO(hero)), 640, 300, 70) if hero else None,
                    "steam-cdn", store_meta(aid) if want_meta else None)
    plat = "PS3" if "ps3" in (game.get("path") or "").lower() else \
           "PS2" if re.search(r"\(usa\)|\(eu\)|iso", game.get("path") or "", re.I) else ""
    im = generated_plate(game["title"], plat)
    return (enc(im, 300, 450, 80), enc(im, 640, 300, 70), "generated", None)


# ------------------------------------------------------------------- write
def apply_to_html(games, target=None):
    path = target or HTML
    if not os.path.exists(path):
        print("! %s not found" % path)
        return False
    src = open(path, encoding="utf-8").read()
    payload = json.dumps(games, separators=(",", ":"), ensure_ascii=False)
    new, n = re.subn(r"const G=.*?;\nconst STORES", "const G=" + payload + ";\nconst STORES",
                     src, count=1, flags=re.S)
    if not n:
        print("! could not find the library block in %s" % os.path.basename(path))
        return False
    open(path, "w", encoding="utf-8").write(new)
    print("+ %s updated - %d games, %.2f MB" % (os.path.basename(path), len(games),
                                                os.path.getsize(path) / 1e6))
    return True


def write_launch(games):
    table = {str(g["id"]): list(g["_launch"]) for g in games if g.get("_launch")}
    json.dump(table, open(LAUNCH_JSON, "w", encoding="utf-8"), indent=1)
    print("+ launch.json written - %d launch targets" % len(table))


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Scan this PC for installed games.")
    ap.add_argument("--root", action="append", default=[], help="drive or folder to scan (repeatable)")
    ap.add_argument("--json", metavar="FILE", help="write the raw scan as JSON")
    ap.add_argument("--apply", nargs="?", const=True, metavar="HTML",
                    help="write the library into GameBox.html (or the file you name)")
    ap.add_argument("--no-meta", action="store_true", help="covers only, skip store metadata")
    a = ap.parse_args()
    roots = a.root or [os.environ.get("GAMEBOX_ROOT", "C:\\")]

    t0 = time.time()
    print("GameBox scanner - roots: %s\n" % ", ".join(roots))
    found = []
    steam = scan_steam(roots)
    print("  Steam       %3d titles" % len(steam))
    found += steam
    gog = scan_gog()
    print("  GOG         %3d installed" % len(gog))
    found += gog
    epic = scan_epic()
    print("  Epic        %3d titles" % len(epic))
    found += epic
    for r in roots:
        f = scan_folders(r)
        print("  %-11s %3d folders" % (r, len(f)))
        found += f

    # merge: a store entry wins over a bare folder with the same name
    merged, index = [], {}
    for g in found:
        k = norm(g["title"])
        if k in index:
            cur = merged[index[k]]
            for field in ("bytes", "path", "updated", "played", "appid", "cover_hash", "bg_hash", "launch"):
                if not cur.get(field) and g.get(field):
                    cur[field] = g[field]
            if cur["store"] == "local" and g["store"] != "local":
                cur["store"], cur["via"] = g["store"], g["via"]
            continue
        index[k] = len(merged)
        merged.append(dict(g))

    total = sum(g.get("bytes", 0) for g in merged)
    print("\n  %d games - %s - %.1f s" % (len(merged), human(total), time.time() - t0))
    for g in merged:
        if g.get("bytes", 1) == 0:
            print("  ! %s: install folder is empty" % g["title"])

    if a.json:
        json.dump(merged, open(a.json, "w", encoding="utf-8"), indent=1, default=str)
        print("+ wrote %s" % a.json)

    if not a.apply:
        return

    gog_index = {}
    for root, _d, fs in os.walk(GOG_CACHE):
        for f in fs:
            gog_index.setdefault(f, os.path.join(root, f))

    games, counts = [], {}
    for i, g in enumerate(merged):
        cover, ambient, src, meta = artwork(g, gog_index, want_meta=not a.no_meta)
        counts[src] = counts.get(src, 0) + 1
        gb = round(g.get("bytes", 0) / GB, 1)
        entry = {"id": i, "t": g["title"], "short": g["title"][:34], "s": g["store"], "gb": gb,
                 "upd": g.get("updated") or "-", "played": g.get("played"),
                 "state": "err" if gb == 0 else ("new" if (g["store"] == "steam" and not g.get("played")) else ""),
                 "rate": "PC", "path": g.get("path", ""), "via": g.get("via", ""),
                 "note": "", "art": {"c": cover, "s": src}}
        if ambient:
            entry["art"]["a"] = ambient
        if meta:
            entry["meta"] = meta
        p = g.get("path")
        if p and os.path.isdir(p):
            try:
                entry["files"] = [{"n": e.name, "d": e.is_dir(),
                                   "sz": 0 if e.is_dir() else e.stat().st_size}
                                  for e in sorted(os.scandir(p), key=lambda e: (not e.is_dir(), e.name))[:10]]
            except OSError:
                pass
        entry["_launch"] = g.get("launch") or ("none", "")
        games.append(entry)
        print("    %-12s %s" % (src, g["title"][:52]))

    print("\n  artwork: " + ", ".join("%d %s" % (v, k) for k, v in sorted(counts.items())))
    write_launch(games)
    for g in games:
        g.pop("_launch", None)
    apply_to_html(games, None if a.apply is True else a.apply)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
