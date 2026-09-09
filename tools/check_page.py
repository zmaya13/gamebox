#!/usr/bin/env python3
"""
Syntax-check the page, and check it against the API the backend actually offers.

GameBox.html is one 1,700-line file with no build step, so nothing catches a
typo until the app is running and a handler dies silently. This does three
cheap things that catch most of it:

  1. runs `node --check` over the script block, if node is on PATH
  2. flags every getElementById(...) whose id is nowhere in the markup - that
     is what left a dead closeScanner() branch throwing on every Escape press
  3. flags every api.<name>() the page calls that Api does not define

  python tools/check_page.py
"""

import ast
import io
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE = os.path.join(ROOT, "GameBox.html")
APP = os.path.join(ROOT, "gamebox.py")

# Called on the bridge but not part of Api - these are pywebview's own.
BRIDGE_BUILTINS = {"then", "catch"}


def script_of(src):
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", src, re.S)
    return blocks[0] if blocks else ""


def api_methods():
    """The methods class Api actually exposes to the page."""
    tree = ast.parse(io.open(APP, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Api":
            return {n.name for n in node.body
                    if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}
    return set()


def main():
    src = io.open(PAGE, encoding="utf-8").read()
    js = script_of(src)
    problems = []

    # 1. does it parse
    try:
        tmp = os.path.join(tempfile.gettempdir(), "gamebox_page_check.js")
        io.open(tmp, "w", encoding="utf-8").write(js)
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        if r.returncode == 0:
            print("  ok   the script block parses")
        else:
            problems.append("syntax: " + r.stderr.strip().splitlines()[0])
            print("  FAIL syntax\n" + r.stderr[:600])
    except FileNotFoundError:
        print("  --   node not on PATH, skipping the syntax check")

    # 2. every id the script reaches for should exist in the markup
    markup = re.sub(r"<script[^>]*>.*?</script>", "", src, flags=re.S)
    present = set(re.findall(r'id="([A-Za-z0-9_-]+)"', markup))
    # ids the page creates for itself at runtime
    present |= set(re.findall(r'id="([A-Za-z0-9_-]+)"', js))
    wanted = set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js))
    missing = sorted(wanted - present)
    if missing:
        problems.append("missing elements: " + ", ".join(missing))
        print("  FAIL getElementById for ids that do not exist: " + ", ".join(missing))
    else:
        print("  ok   every getElementById target exists (%d checked)" % len(wanted))

    # 3. a rule that hides or restyles something should name a real class
    #    (writing `body.fs .bar` when the element is `.tbar` hides nothing, and
    #    nothing complains - the fullscreen title bar just stays put)
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", src, re.S))
    classes = set(re.findall(r'class="([^"]+)"', markup))
    classes |= set(re.findall(r'class="([^"]+)"', js))
    have = set()
    for group in classes:
        have.update(group.replace("${", " ").split())
    # classes the script adds itself
    have.update(re.findall(r'classList\.(?:add|toggle|remove)\("([A-Za-z0-9_-]+)"', js))
    have.update(re.findall(r'classList\.contains\("([A-Za-z0-9_-]+)"', js))
    styled = set(re.findall(r"body\.fs\s+\.([A-Za-z0-9_-]+)", css))
    unknown = sorted(styled - have)
    if unknown:
        problems.append("fullscreen styles no element: " + ", ".join(unknown))
        print("  FAIL fullscreen rules target classes nothing uses: " + ", ".join(unknown))
    else:
        print("  ok   every fullscreen rule targets a real class (%d checked)" % len(styled))

    # 4. a game id must never be coerced to a number
    #    Ids were scan-order integers until 1.3.0 and are now strings like
    #    "steam:620". A leftover `+el.dataset.play` makes one NaN, byId falls
    #    back to the first game, and the Play button silently starts the wrong
    #    one - no error anywhere.
    id_attrs = ("play", "inspect", "detail", "folder", "id", "fav", "hide",
                "drop", "emu", "done", "scoreClear", "tagDel", "catDel",
                "tagAdd", "catAdd", "tagAddNow", "catAddNow", "note", "score")
    coerced = re.findall(r"\+\s*\w+\.dataset\.(\w+)", js)
    bad = sorted({c for c in coerced if c in id_attrs})
    if bad:
        problems.append("game ids coerced to numbers: " + ", ".join(bad))
        print("  FAIL a game id is coerced with + (it is a string): "
              + ", ".join("dataset." + b for b in bad))
    else:
        print("  ok   no game id is coerced to a number (%d coercions checked)"
              % len(coerced))

    # 5. every bridge call should be a method the backend has
    have = api_methods()
    called = set(re.findall(r"\bapi\.([a-z_][a-z0-9_]*)\b", js)) - BRIDGE_BUILTINS
    # callApi("name", arg) reaches the bridge by name rather than by attribute
    called |= set(re.findall(r'callApi\("([a-z_][a-z0-9_]*)"', js))
    unknown = sorted(called - have)
    if unknown:
        problems.append("unknown api methods: " + ", ".join(unknown))
        print("  FAIL the page calls Api methods that do not exist: " + ", ".join(unknown))
    else:
        print("  ok   every api.* call exists on Api (%d checked)" % len(called))

    unused = sorted(have - called - {"status"})
    if unused:
        print("  --   Api methods the page never calls: " + ", ".join(unused))

    print("\n%d problems" % len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
