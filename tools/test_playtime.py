#!/usr/bin/env python3
"""
Proves a launched game is timed, including the hard case.

The hard case is Steam and Epic: firing a steam:// URI returns immediately and
the game is started later by a process that is not ours, so there is nothing to
wait on. The watcher follows the install *folder* instead - anything running
from inside it is the game - and that is what this checks, with a real process
copied into a fake install folder and no pid handed over at all.

  python tools/test_playtime.py

Takes about half a minute: sessions are timed in real seconds.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import playtime  # noqa: E402

FAILED = []
# A session shorter than this is treated as a bounce and not recorded, so the
# fake game has to outlive it.
RUN_SECONDS = playtime.MIN_SESSION_SECONDS + 4


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (" - " + detail if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def fake_game(folder, seconds):
    """A copy of python.exe inside a fake install folder, sleeping."""
    os.makedirs(folder, exist_ok=True)
    exe = os.path.join(folder, "TheGame.exe")
    shutil.copyfile(sys.executable, exe)
    return exe, subprocess.Popen([exe, "-c", "import time;time.sleep(%d)" % seconds],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    tmp = tempfile.mkdtemp(prefix="gamebox-playtime-")
    print("watching a fake install in " + tmp)
    print("  (timing real seconds - this takes about %ds)\n" % (RUN_SECONDS + 12))

    # ---- process enumeration works at all
    print("process list")
    paths = playtime._process_paths()
    check("processes can be enumerated", len(paths) > 10, "%d found" % len(paths))
    me = os.path.normcase(sys.executable)
    check("this process is in the list",
          any(os.path.normcase(p) == me for p in paths.values()))

    print("\nfolder matching")
    sep = os.sep
    check("an executable inside the folder matches",
          playtime._under("C:" + sep + "Games" + sep + "X" + sep + "g.exe", "C:" + sep + "Games" + sep + "X"))
    check("a sibling folder does not match",
          not playtime._under("C:" + sep + "Games" + sep + "XY" + sep + "g.exe", "C:" + sep + "Games" + sep + "X"))
    check("case and slashes do not matter",
          playtime._under("c:/games/x/g.exe", "C:" + sep + "Games" + sep + "X"))
    check("the folder itself is not a process in it",
          not playtime._under("C:" + sep + "Games" + sep + "X", "C:" + sep + "Games" + sep + "X"))

    # ---- the real thing, with no pid: the Steam and Epic case
    print("\na session with no process to wait on")
    folder = os.path.join(tmp, "Some Game")
    exe, proc = fake_game(folder, RUN_SECONDS)
    recorded = {}

    def finished(gid, seconds):
        recorded[gid] = seconds

    started_at = time.time()
    playtime.track("test:1", folder, pid=None, on_finish=finished)

    # it should notice the game within a couple of polls
    for _ in range(20):
        if playtime.playing().get("test:1") is not None:
            break
        time.sleep(0.5)
    check("the running game was found without a pid", "test:1" in playtime.playing(),
          "waiting=%s" % playtime.waiting())
    check("running_in() sees it", any(os.path.normcase(p) == os.path.normcase(exe)
                                      for p in playtime.running_in(folder)))

    proc.wait()
    for _ in range(20):
        if recorded:
            break
        time.sleep(0.5)

    check("a session was recorded when it exited", "test:1" in recorded, str(recorded))
    if recorded:
        actual = time.time() - started_at
        got = recorded["test:1"]
        check("the recorded time is about right (%ds for a ~%ds run)" % (got, RUN_SECONDS),
              abs(got - RUN_SECONDS) <= 8, "recorded %ds, ran ~%.0fs" % (got, actual))
    check("nothing is left being watched", not playtime.playing() and not playtime.waiting())

    # ---- a launch that never starts is not recorded
    print("\na launch that never starts")
    playtime.STARTUP_GRACE, grace = 3, playtime.STARTUP_GRACE
    never = {}
    playtime.track("test:2", os.path.join(tmp, "Nothing Here"),
                   on_finish=lambda g, s: never.setdefault(g, s))
    os.makedirs(os.path.join(tmp, "Nothing Here"), exist_ok=True)
    time.sleep(6)
    check("a game that never appeared records nothing", not never, str(never))
    check("and is no longer waiting", "test:2" not in playtime.waiting())
    playtime.STARTUP_GRACE = grace

    # ---- a bounce is not a session
    print("\na game that crashes on startup")
    folder2 = os.path.join(tmp, "Crashy")
    _exe2, p2 = fake_game(folder2, 2)
    bounced = {}
    playtime.track("test:3", folder2, on_finish=lambda g, s: bounced.setdefault(g, s))
    p2.wait()
    time.sleep(6)
    check("a two-second run is not recorded as playtime", not bounced, str(bounced))

    playtime.stop()
    time.sleep(0.5)
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d failed" % len(FAILED))
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
