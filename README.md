# GameBox

One library for every game on your PC — Steam, GOG, Epic, EA App, emulated discs and
plain folders — and one button that launches them.

GameBox is a small native Windows app. It scans what is actually installed on your
drives, pulls the cover art your launchers already cached, and gives you a single
library to browse and launch from. No account, no telemetry, no background service.

## Why

Most people's games are scattered across four launchers and a folder of disc images.
Every launcher wants to be the front end, none of them shows the others, and none of
them touches the ISO sitting on your D: drive. GameBox is the shelf that holds all of it.

## What it does

- **Finds your games.** Reads Steam `appmanifest` files, GOG Galaxy's database, Epic's
  manifests, and sizes every game folder on the drives you point it at.
- **Launches them properly.** Steam and Epic titles are handed to their own launchers
  through `steam://` and `com.epicgames.launcher://`. GOG, EA and folder installs run
  their executable. PS2/PS3 disc images open in PCSX2 or RPCS3.
- **Finds the artwork.** Covers come from your Steam and GOG caches first, then from
  Steam's public CDN by title match, and finally a generated plate for console discs
  and ROMs that no store carries.
- **Shows real information.** Size on disk, last played, last updated, install path,
  what's inside the install folder, cloud-save state, developer, publisher, genre.
- **A 3D case for every game.** Click a game and its case flies out of the tile; drag
  to rotate it, press `F` to flip it over. The back is a real box back — screenshots,
  blurb, feature list, credits, rating box, spec panel, barcode.

## Views

| View | For |
|---|---|
| **Grid** | Browsing by cover |
| **List** | Sorting by size, date, store, artwork source; seeing install paths |
| **Details** | One game at a time: Overview, Files, Saves, Activity |

Plus Home, Continue playing, Installed, Never played, Favorites, Collections,
an Artwork manager and Settings.

## Install

Download `GameBox-Setup-1.0.0.exe` from
[Releases](https://github.com/zmaya13/gamebox/releases) and run it. It installs per
user, needs no administrator rights, and uninstalls from Add/Remove Programs.

Then scan your drives:

```powershell
cd "$env:LOCALAPPDATA\Programs\GameBox"
python scan-library.py --root C:\ --root D:\ --apply
```

Reopen GameBox and your library is there.

## Run from source

```powershell
git clone https://github.com/zmaya13/gamebox
cd gamebox
pip install -r requirements.txt
python scan-library.py --root C:\ --apply
python gamebox.py
```

## Build

```powershell
./build.ps1     # needs pyinstaller and NSIS
```

Produces `GameBox.exe` and `GameBox-Setup-1.0.0.exe`.

## How it is put together

```
gamebox.py         native window (pywebview + WebView2) and the launch API
GameBox.html       the whole UI: one file, no framework, no build step
scan-library.py    the scanner: finds games, resolves artwork, writes the library
installer.nsi      NSIS installer
```

The UI is plain HTML, CSS and JavaScript in a single file. Anything that touches the
machine — launching a game, opening a folder, rescanning — is a Python call over the
pywebview bridge. There is no local web server and no listening port, and closing the
window ends the process.

The scanner writes two things: the library array inside `GameBox.html`, and
`launch.json`, which tells the app how to start each game.

## Requirements

- Windows 10 or 11 (the WebView2 runtime ships with Windows 11)
- Python 3.9+ and Pillow, for the scanner

## A note on artwork

GameBox displays covers, screenshots and descriptions for games you already own. That
material is fetched on your machine at scan time — from your own launcher caches and
from Steam's public endpoints — and belongs to its publishers. **None of it is
distributed with this repository**, and a scanned `GameBox.html` (which embeds your
artwork) is deliberately git-ignored.

## Known limits

- Windows only.
- The scanner matches non-Steam titles by name, so an oddly named folder can miss.
- Playtime history only exists for Steam games; other stores expose no play record,
  which the UI reports as "no play record" rather than "never played".
- Xbox / Game Pass and Ubisoft Connect are not implemented yet.

## Licence

MIT — see [LICENSE](LICENSE).
