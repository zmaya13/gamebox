# Build GameBox.exe and the installer.
# Requires: python, pyinstaller, NSIS
$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt pyinstaller pyyaml

# Fold emulation/*.yaml into the emulators.json the app ships, so the build
# carries the definitions and the app needs no YAML parser.
python tools/build_emulators.py

# scan-library.py is loaded with importlib at runtime (its name has a hyphen in
# it), so PyInstaller cannot see what it imports. emulators, stores and Pillow
# reach the bundle only because they are named here.
python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name GameBox --icon GameBox.ico `
  --add-data "scan-library.py;." `
  --add-data "emulators.json;." `
  --add-data "GameBox.html;." `
  --hidden-import emulators `
  --hidden-import stores `
  --hidden-import library `
  --hidden-import migrate `
  --hidden-import playtime `
  --hidden-import PIL.Image `
  --hidden-import PIL.ImageDraw `
  --hidden-import PIL.ImageFont `
  gamebox.py

Copy-Item dist\GameBox.exe . -Force
& "$env:ProgramFiles(x86)\NSIS\makensis.exe" installer.nsi
Write-Host "Built GameBox.exe and the installer - check the VERSION in installer.nsi"
