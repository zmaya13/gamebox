#!/usr/bin/env python3
"""
GameBox - a unified game library for Windows.

A native window (WebView2), not a browser tab. One process: closing the
window ends the app, and nothing is left running in the background.

  python gamebox.py            run from source
  GameBox.exe                  the built application

The UI is HTML/CSS/JS running in the window; everything that touches the
machine - launching a game, opening a folder, rescanning the library - is a
direct Python call through the pywebview bridge. There is no local web
server and no network port.
"""

import glob
import json
import os
import subprocess
import sys
import threading
import webbrowser

import webview

APP_NAME = "GameBox"
VERSION = "1.0.0"


def resource(name):
    """Path to a bundled file, whether running from source or from the exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def user_file(name):
    """Path to a file that lives next to the installed app (writable data)."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


# Populated by scan-library.py into launch.json next to the app.
# id -> (kind, target, folder); kind is steam | epic | exe | emu | web | none
LAUNCH = {}

TABLE = {}
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW, so no console flashes on launch


def load_launch_table():
    """Prefer launch.json written by the scanner; fall back to the built-in map."""
    path = user_file("launch.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
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
    return LAUNCH


def disc_image(folder):
    for ext in ("*.iso", "*.ISO", "*.bin", "*.chd", "*.pkg"):
        hits = glob.glob(os.path.join(folder, ext)) + glob.glob(os.path.join(folder, "*", ext))
        if hits:
            return hits[0]
    return None


class Api:
    """Everything the page is allowed to ask the machine to do."""

    def status(self):
        return {"ok": True, "app": APP_NAME, "version": VERSION,
                "games": len(TABLE), "native": True}

    def launch(self, gid):
        try:
            gid = int(gid)
        except (TypeError, ValueError):
            return {"ok": False, "msg": "Bad game id"}
        if gid not in TABLE:
            return {"ok": False, "msg": "Unknown game"}
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
                return {"ok": True, "msg": "%s loading %s" % (os.path.basename(target), os.path.basename(img))}
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
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path])
            return {"ok": True, "msg": "Opened " + path}
        return {"ok": False, "msg": "Folder is gone: " + path}

    def rescan(self):
        """Run the scanner in the background and report when it finishes."""
        script = user_file("scan-library.py")
        if not os.path.exists(script):
            return {"ok": False, "msg": "scan-library.py is not installed next to the app"}
        def run():
            subprocess.run([sys.executable, script, "--art", "--apply"],
                           cwd=os.path.dirname(script), creationflags=NO_WINDOW)
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "msg": "Scanning drives and stores in the background"}

    def minimize(self):
        webview.windows[0].minimize()
        return {"ok": True}

    def toggle_maximize(self):
        w = webview.windows[0]
        try:
            w.restore() if getattr(w, "_maximized", False) else w.maximize()
            w._maximized = not getattr(w, "_maximized", False)
        except Exception:
            w.maximize()
        return {"ok": True}

    def close(self):
        webview.windows[0].destroy()
        return {"ok": True}


def main():
    global TABLE
    TABLE = load_launch_table()
    html = user_file("GameBox.html")
    if not os.path.exists(html):
        html = resource("GameBox.html")
    if not os.path.exists(html):
        webview.create_window(APP_NAME, html="<h2 style='font:600 16px sans-serif;color:#fff;"
                             "background:#0e1116;padding:2rem'>GameBox.html is missing.</h2>")
        webview.start()
        return

    webview.create_window(
        APP_NAME,
        url="file:///" + html.replace("\\", "/"),
        width=1600, height=940,
        min_size=(1100, 660),
        background_color="#06070B",
        resizable=True,
        text_select=False,
        js_api=Api(),
        frameless=False,
        easy_drag=False,
    )
    # gui='edgechromium' uses the WebView2 control that ships with Windows -
    # a native embedded renderer, not the Edge browser application.
    webview.start(gui="edgechromium", private_mode=False,
                  storage_path=os.path.join(os.environ.get("LOCALAPPDATA", "."), "GameBox"))


if __name__ == "__main__":
    main()
