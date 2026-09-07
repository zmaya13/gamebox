; GameBox installer
; Build with:  makensis installer.nsi
Unicode true

!define APP      "GameBox"
!define VERSION  "1.1.0"
!define PUBLISHER "GameBox contributors"
!define WEBSITE  "https://github.com/zmaya13/gamebox"

Name "${APP} ${VERSION}"
OutFile "GameBox-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\GameBox"
InstallDirRegKey HKCU "Software\${APP}" "InstallDir"
RequestExecutionLevel user          ; per-user install, no UAC prompt
SetCompressor /SOLID lzma
BrandingText "${APP} ${VERSION}"

!include "MUI2.nsh"
!define MUI_ICON "GameBox.ico"
!define MUI_UNICON "GameBox.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\GameBox.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Open GameBox now"
!define MUI_WELCOMEPAGE_TITLE "Install ${APP}"
!define MUI_WELCOMEPAGE_TEXT "GameBox brings your Steam, GOG, Epic, EA and emulated games into one library, and launches them.$\r$\n$\r$\nIt installs for the current user only - no administrator rights needed."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "GameBox" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  File "GameBox.exe"
  File "GameBox.html"
  File "GameBox.ico"
  File "scan-library.py"
  File "README.md"
  File "LICENSE"
  File /nonfatal "launch.json"

  CreateShortcut "$SMPROGRAMS\GameBox.lnk" "$INSTDIR\GameBox.exe" "" "$INSTDIR\GameBox.ico" 0
  CreateShortcut "$DESKTOP\GameBox.lnk"    "$INSTDIR\GameBox.exe" "" "$INSTDIR\GameBox.ico" 0

  WriteRegStr HKCU "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" "DisplayName" "${APP}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" "DisplayIcon" "$INSTDIR\GameBox.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" "URLInfoAbout" "${WEBSITE}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}" "NoRepair" 1
  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\GameBox.exe"
  Delete "$INSTDIR\GameBox.html"
  Delete "$INSTDIR\GameBox.ico"
  Delete "$INSTDIR\scan-library.py"
  Delete "$INSTDIR\launch.json"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  RMDir /r "$LOCALAPPDATA\GameBox"      ; WebView2 profile
  Delete "$SMPROGRAMS\GameBox.lnk"
  Delete "$DESKTOP\GameBox.lnk"
  DeleteRegKey HKCU "Software\${APP}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"
SectionEnd
