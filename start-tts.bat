@echo off
REM ============================================================================
REM  Local voice service for the AI stream - IndexTTS2.5.
REM
REM  TWO RULES FOR THIS FILE, both learned the hard way:
REM
REM   1. ASCII ONLY.  cmd.exe parses .bat in the system codepage (GBK here), so
REM      UTF-8 Chinese text eats the characters after it and the script breaks.
REM   2. EVERY LINE OF A CONTINUED COMMAND ENDS WITH A CARET.  cmd joins the
REM      lines itself, and a missing caret turns the next line into a command
REM      that does not exist - which killed the service and broke the loop.
REM
REM  This must stay running: it loads a 5GB model into VRAM, about 25 seconds.
REM  It cannot be started per sentence.
REM
REM  These settings must match the "voice" block of config\program.json:
REM      reference voice : tts/voices/demo/voice_09.wav
REM      duration        : 1.0    - bigger is slower
REM      emotion         : none
REM      seed            : 1234   - fixed, so one line always sounds the same
REM
REM  --low-vram: the model enables chunking by itself only below 10GB of TOTAL
REM  vram, which is the wrong question on a 12GB card that uses 5GB. Forcing it
REM  off was tried and MEASURED: 3.96s vs 4.07s per line, i.e. no difference -
REM  the model is resident either way, so this is left at the default.
REM
REM  Self-check: http://127.0.0.1:8790/health
REM  Closing this window stops the voice; the agent falls back to edge-tts and
REM  retries this service every 20 seconds until it is back.
REM ============================================================================

setlocal
cd /d "%~dp0"

if not exist "tts\.venv\Scripts\python.exe" goto novenv

set PYTHONIOENCODING=utf-8
set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

REM Auto-restart loop. The service has died before with nothing in its log, and
REM when it dies the voice silently drops to edge-tts until someone restarts the
REM window. Better that it stands itself back up.
:loop
echo.
echo Loading model, about 20-30 seconds...
"tts\.venv\Scripts\python.exe" tools\tts_server.py ^
    --port 8790 ^
    --ref "tts/voices/demo/voice_09.wav" ^
    --duration 1.0 ^
    --beams 1 ^
    --seed 1234

echo.
echo [!] voice service exited with code %ERRORLEVEL% - restarting in 5 seconds
echo     Close this window to stop it for good.
timeout /t 5 /nobreak >nul
goto loop

:novenv
echo Missing tts\.venv\Scripts\python.exe
echo That environment is TTS-only: Python 3.11 plus torch. The main .venv cannot run torch.
pause
exit /b 1
