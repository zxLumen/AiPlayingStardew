@echo off
REM ============================================================================
REM  The commentary process. ASCII ONLY - cmd.exe parses .bat in the system
REM  codepage (GBK here), so UTF-8 Chinese text eats the characters after it and
REM  the script dies silently. Also: no parentheses inside an if ( ... ) block.
REM
REM  Why this is separate from agent.py:
REM    The commentary used to live inside the playing loop, so every reworded
REM    sentence required restarting it - which kills the game day in progress.
REM    It has no need to be there: it reads agent events from
REM    logs\agents\bus.jsonl, and writes its lines to logs\agents\commentary.jsonl.
REM
REM  So: restart this as often as you like. The game does not notice.
REM  It starts in about a second - no torch, no game attached.
REM
REM  Order does not matter much, but the voice window should be up before this
REM  one, because the commentary asks it whether it is still speaking:
REM      start-tts.bat            -> the voice (IndexTTS2)
REM      start-voice-window.bat   -> the window that speaks and is captured
REM      start-commentary.bat     -> this: the brain that decides what to say
REM ============================================================================

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto noenv

set PYTHONIOENCODING=utf-8

REM Auto-restart loop. A dead commentary is a silent stream with no error
REM anywhere, so it stands itself back up instead of waiting to be noticed.
:loop
echo.
echo Starting the commentary process...
".venv\Scripts\python.exe" agent\commentary_daemon.py

echo.
echo [!] commentary exited with code %ERRORLEVEL% - restarting in 5 seconds
echo     Close this window to stop it for good.
timeout /t 5 /nobreak >nul
goto loop

:noenv
echo Missing .venv\Scripts\python.exe
echo Run this from the project root.
pause
exit /b 1
