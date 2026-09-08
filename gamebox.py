#!/usr/bin/env python3
"""
GameBox - a unified game library for Windows.

A frameless native window: the title bar is part of the app, not a Windows
box bolted on top. One process - closing the window ends everything, and
there is no background service and no network port.

  python gamebox.py            run from source
  GameBox.exe                  the built application

Everything that touches the machine - launching a game, opening a folder,
scanning drives - is a Python call over the pywebview bridge.
"""

import ctypes
import glob
import json
import os
import queue
import string
import subprocess
import sys
import threading
import webbrowser

import webview

APP_NAME = "GameBox"
VERSION = "1.2.0"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

# Populated by scan-library.py into launch.json next to the app.
# id -> (kind, target, folder); kind is steam | epic | exe | emu | web | none
LAUNCH = {}
TABLE = {}


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def user_file(name):
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


SETTINGS = user_file("gamebox-settings.json")


def load_launch_table():
    path = user_file("launch.json")
    if os.path.exists(path):
        try:
            data = json.load(open(path, encoding="utf-8"))
            table = {}
            for k, v in data.items():
                kind = v[0]
                target = v[1] if len(v) > 1 else ""
                folder = v[2] if len(v) > 2 else os.path.dirname(target)
                table[int(k)] = (kind, target, folder)
            if table:
                return table
        except Exception:
            pass
    return dict(LAUNCH)


def disc_image(folder):
    for ext in ("*.iso", "*.ISO", "*.bin", "*.chd", "*.pkg"):
        hits = glob.glob(os.path.join(folder, ext)) + glob.glob(os.path.join(folder, "*", ext))
        if hits:
            return hits[0]
    return None


# --------------------------------------------------------------- window chrome
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
SWP_FLAGS = 0x0002 | 0x0001 | 0x0004 | 0x0020  # NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED


def make_resizable_frameless():
    """A frameless window still needs resize borders and Aero Snap.

    Windows hands those to WS_THICKFRAME, which a borderless form drops. Put
    it back and the window resizes from every edge, snaps, and honours
    Win+Arrow - with no caption drawn.
    """
    import time

    def find_hwnd():
        """A frameless window has no caption to search for, so go by process."""
        try:
            h = int(webview.windows[0].native.Handle)
            if h:
                return h
        except Exception:
            pass
        user32 = ctypes.windll.user32
        found = []
        pid = os.getpid()
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, _lparam):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        try:
            user32.EnumWindows(WNDENUMPROC(cb), 0)
        except Exception:
            return 0
        return found[0] if found else 0

    def apply():
        user32 = ctypes.windll.user32
        # The form is created and restyled by the toolkit as it shows, so keep
        # re-asserting the flags for a couple of seconds until they stick.
        for _ in range(40):
            try:
                hwnd = find_hwnd()
                if hwnd:
                    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                    if not style & WS_THICKFRAME:
                        style |= WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
                        user32.SetWindowLongW(hwnd, GWL_STYLE, style)
                        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)
                    # A packaged build can leave the frameless form created but
                    # never shown, so show it ourselves once it exists.
                    if not user32.IsWindowVisible(hwnd):
                        user32.ShowWindow(hwnd, 5)      # SW_SHOW
                        user32.SetForegroundWindow(hwnd)
                    elif _ > 8:
                        return  # settled
            except Exception:
                return
            time.sleep(0.1)

    threading.Thread(target=apply, daemon=True).start()


# ---------------------------------------------------------------------- the API
class Api:
    """Everything the page is allowed to ask the machine to do."""

    def __init__(self):
        self._scan_lines = queue.Queue()
        self._scan_thread = None

    # ---- window
    def status(self):
        st = self.settings_get()
        return {"ok": True, "app": APP_NAME, "version": VERSION,
                "games": len(TABLE), "native": True,
                "setup_done": bool(st.get("setup_done")),
                "profile": st.get("profile") or {},
                # a first name to offer during setup; the user can change it
                "user": (os.environ.get("USERNAME") or "").split("@")[0]}

    def minimize(self):
        webview.windows[0].minimize()
        return {"ok": True}

    def toggle_maximize(self):
        w = webview.windows[0]
        if getattr(w, "_max", False):
            w.restore()
        else:
            w.maximize()
        w._max = not getattr(w, "_max", False)
        return {"ok": True, "maximized": w._max}

    def close(self):
        webview.windows[0].destroy()
        return {"ok": True}

    def reload(self):
        html = user_file("GameBox.html")
        webview.windows[0].load_url("file:///" + html.replace("\\", "/"))
        return {"ok": True}

    # ---- settings
    def settings_get(self):
        try:
            return json.load(open(SETTINGS, encoding="utf-8"))
        except Exception:
            return {}

    def settings_set(self, data):
        cur = self.settings_get()
        try:
            cur.update(data or {})
            json.dump(cur, open(SETTINGS, "w", encoding="utf-8"), indent=1)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    # ---- setup
    def drives(self):
        """Fixed drives worth scanning, plus which launchers are installed."""
        out = []
        for letter in string.ascii_uppercase:
            root = "%s:\\" % letter
            if not os.path.isdir(root):
                continue
            try:
                if ctypes.windll.kernel32.GetDriveTypeW(root) != 3:  # DRIVE_FIXED
                    continue
                free = ctypes.c_ulonglong(0)
                total = ctypes.c_ulonglong(0)
                ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                    ctypes.c_wchar_p(root), None, ctypes.byref(total), ctypes.byref(free))
            except Exception:
                continue
            hints = []
            for probe, label in ((("SteamLibrary", "steamapps"), "Steam library"),
                                 (("Program Files (x86)", "Steam"), "Steam"),
                                 (("Games",), "Games folder"),
                                 (("Epic Games",), "Epic")):
                if os.path.isdir(os.path.join(root, *probe)):
                    hints.append(label)
            out.append({"root": root,
                        "total": round(total.value / 1024 ** 3),
                        "free": round(free.value / 1024 ** 3),
                        "hints": sorted(set(hints)),
                        "suggested": bool(hints) or letter == "C"})
        launchers = {
            "Steam": os.path.isdir(os.path.expandvars(r"%ProgramFiles(x86)%\Steam")),
            "GOG Galaxy": os.path.exists(os.path.expandvars(
                r"%ProgramData%\GOG.com\Galaxy\storage\galaxy-2.0.db")),
            "Epic Games": os.path.isdir(os.path.expandvars(
                r"%ProgramData%\Epic\EpicGamesLauncher\Data\Manifests")),
            "EA App": os.path.isdir(os.path.expandvars(r"%ProgramFiles%\Electronic Arts")),
        }
        return {"ok": True, "drives": out, "launchers": launchers,
                "scanner": os.path.exists(user_file("scan-library.py"))}

    def start_scan(self, roots):
        """Run the real scanner, streaming its output back to the page."""
        script = user_file("scan-library.py")
        if not os.path.exists(script):
            return {"ok": False, "msg": "scan-library.py is not installed next to the app"}
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return {"ok": False, "msg": "A scan is already running"}
        roots = [r for r in (roots or []) if r]
        if not roots:
            return {"ok": False, "msg": "Pick at least one drive"}
        exe = "python" if getattr(sys, "frozen", False) else sys.executable
        # -u: unbuffered, or the child's prints sit in a pipe buffer and the
        # wizard shows nothing until the whole scan has finished.
        cmd = [exe, "-u", script]
        for r in roots:
            cmd += ["--root", r]
        cmd.append("--apply")

        def run():
            try:
                p = subprocess.Popen(cmd, cwd=os.path.dirname(script), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                     errors="replace", creationflags=NO_WINDOW)
                for line in p.stdout:
                    self._scan_lines.put(line.rstrip())
                p.wait()
                self._scan_lines.put("__DONE__%d" % p.returncode)
            except FileNotFoundError:
                self._scan_lines.put("Python 3.9+ with Pillow is needed to scan, and was not found.")
                self._scan_lines.put("__DONE__1")
            except Exception as e:
                self._scan_lines.put("Scanner failed: %s" % e)
                self._scan_lines.put("__DONE__1")

        self._scan_thread = threading.Thread(target=run, daemon=True)
        self._scan_thread.start()
        return {"ok": True, "msg": "Scanning " + ", ".join(roots)}

    def scan_progress(self):
        lines, done, code = [], False, 0
        while True:
            try:
                line = self._scan_lines.get_nowait()
            except queue.Empty:
                break
            if line.startswith("__DONE__"):
                done, code = True, int(line[8:] or 0)
            else:
                lines.append(line)
        return {"ok": True, "lines": lines, "done": done, "code": code}

    # ---- games
    def launch(self, gid):
        try:
            gid = int(gid)
        except (TypeError, ValueError):
            return {"ok": False, "msg": "Bad game id"}
        if gid not in TABLE:
            return {"ok": False, "msg": "No launch target recorded - run a scan"}
        kind, target, folder = TABLE[gid]
        try:
            if kind == "steam":
                os.startfile("steam://rungameid/" + target)
                return {"ok": True, "msg": "Handed off to Steam"}
            if kind == "epic":
                os.startfile("com.epicgames.launcher://apps/%s?action=launch&silent=true" % target)
                return {"ok": True, "msg": "Handed off to the Epic launcher"}
            if kind == "exe":
                if not os.path.exists(target):
                    return {"ok": False, "msg": "Missing: " + os.path.basename(target)}
                subprocess.Popen([target], cwd=folder or os.path.dirname(target),
                                 close_fds=True, creationflags=NO_WINDOW)
                return {"ok": True, "msg": "Started " + os.path.basename(target)}
            if kind == "emu":
                if not os.path.exists(target):
                    return {"ok": False, "msg": "Emulator not found: " + os.path.basename(target)}
                img = folder if (folder and os.path.isfile(folder)) else disc_image(folder)
                if not img:
                    return {"ok": False, "msg": "No disc image in " + os.path.basename(folder or "")}
                subprocess.Popen([target, img], cwd=os.path.dirname(target),
                                 close_fds=True, creationflags=NO_WINDOW)
                return {"ok": True, "msg": "%s loading %s" % (os.path.basename(target),
                                                              os.path.basename(img))}
            if kind == "web":
                if not os.path.exists(target):
                    return {"ok": False, "msg": "index.html not found"}
                webbrowser.open("file:///" + target.replace("\\", "/"))
                return {"ok": True, "msg": "Opened the local web build"}
            return {"ok": False, "msg": "No files on disk - nothing to launch"}
        except Exception as e:
            return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    def folder(self, gid):
        try:
            path = TABLE[int(gid)][2]
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "msg": "Unknown game"}
        if path and os.path.isdir(path):
            subprocess.Popen(["explorer", path])
            return {"ok": True, "msg": "Opened " + path}
        return {"ok": False, "msg": "Folder is gone: " + str(path)}


def main():
    global TABLE
    TABLE = load_launch_table()
    html = user_file("GameBox.html")
    if not os.path.exists(html):
        html = resource("GameBox.html")

    webview.create_window(
        APP_NAME,
        url="file:///" + html.replace("\\", "/"),
        width=1600, height=940,
        min_size=(1100, 660),
        background_color="#06070B",
        resizable=True,
        frameless=True,          # the title bar lives in the UI
        easy_drag=False,         # dragging is limited to .pywebview-drag-region
        text_select=False,
        js_api=Api(),
    )
    webview.start(make_resizable_frameless, gui="edgechromium", private_mode=False,
                  storage_path=os.path.join(os.environ.get("LOCALAPPDATA", "."), "GameBox"))


if __name__ == "__main__":
    main()
