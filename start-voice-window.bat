@echo off
REM ============================================================================
REM  Launcher for the "AiPlaying commentator" window.
REM
REM  ASCII ONLY. cmd.exe parses a .bat in the system codepage (GBK on this box),
REM  so UTF-8 Chinese text eats the characters after it and the script dies
REM  silently instead of starting anything.
REM  Also: no parentheses inside an if ( ... ) block - a ')' in an echo closes
REM  the block early and cmd reports "X was unexpected at this time".
REM
REM  Why a window at all: the capture software can only grab audio per window,
REM  so the voice needs a window of its own. It synthesises with the "voice"
REM  settings from config\program.json and shows the line being spoken.
REM
REM  IMPORTANT: config\program.json must have voice.enabled = false, otherwise
REM  the agent also speaks and the audience hears everything twice.
REM
REM  Window title : AiPlaying
REM  Self-check   : http://127.0.0.1:8792/health   (shows busy / said / link)
REM  Log          : logs\voice-window.log
REM ============================================================================

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" goto noenv

REM One window only. Two would speak every line twice, which is worse than none.
netstat -an | findstr /c:":8792" | findstr /i "LISTENING" >nul
if not errorlevel 1 goto alreadyrunning

echo Starting the commentator window...
start "" ".venv\Scripts\pythonw.exe" "agent\voice_window.py"

echo.
echo Window title : AiPlaying
echo Capture      : add source - window capture - pick that title
echo Self-check   : http://127.0.0.1:8792/health
echo Log          : logs\voice-window.log
echo.
timeout /t 6 /nobreak >nul
exit /b 0

:alreadyrunning
echo Port 8792 is already listening, so the window looks like it is running.
echo Not starting a second one: two windows would speak every line twice.
echo Close the existing window first, or open http://127.0.0.1:8792/health to check it.
pause
exit /b 1

:noenv
echo Missing .venv\Scripts\pythonw.exe
echo Run this from the project root.
pause
exit /b 1
