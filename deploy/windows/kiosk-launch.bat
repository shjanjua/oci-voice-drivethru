@echo off
REM Booth kiosk launcher for Windows (double-click, or call from a startup task).
REM Windows counterpart of deploy/kiosk-launch.command. Opens the drive-thru in a
REM locked-down Chromium kiosk: fullscreen, mic auto-granted, autoplay allowed.
REM
REM Same-machine kiosk: use the loopback URL below (http://127.0.0.1:7871/ is a
REM secure context, so the mic works WITHOUT HTTPS). For LAN/remote access see the
REM README "Windows deployment" -> "Access from another device" section.

set "URL=http://127.0.0.1:7871/"
set "PROFILE=%LOCALAPPDATA%\voicedt-kiosk-profile"

REM Common flags (Chrome and Edge are both Chromium; flags are identical).
set "FLAGS=--kiosk --app=%URL% --autoplay-policy=no-user-gesture-required --use-fake-ui-for-media-stream --disable-features=TranslateUI --disable-pinch --overscroll-history-navigation=0 --noerrdialogs --disable-session-crashed-bubble --disable-infobars --user-data-dir=%PROFILE%"

REM Prefer Chrome; fall back to Edge if Chrome is not installed.
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"

if exist "%CHROME%" (
  start "" "%CHROME%" %FLAGS%
  goto :eof
)

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if exist "%EDGE%" (
  start "" "%EDGE%" %FLAGS%
  goto :eof
)

echo Could not find Google Chrome or Microsoft Edge. Install one, or edit this file
echo with the full path to your browser's .exe.
pause
