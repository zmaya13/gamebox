# Build GameBox.exe and the installer.
# Requires: python, pyinstaller, NSIS
$ErrorActionPreference = "Stop"
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name GameBox --icon GameBox.ico gamebox.py
Copy-Item dist\GameBox.exe . -Force
& "$env:ProgramFiles(x86)\NSIS\makensis.exe" installer.nsi
Write-Host "Built GameBox.exe and GameBox-Setup-1.0.0.exe"
