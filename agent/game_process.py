"""Launch and stop Stardew Valley through SMAPI.

The mod only loads when the game is started via StardewModdingAPI.exe.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

DEFAULT_GAME_DIR = Path(r"E:\Program Files (x86)\Steam\steamapps\common\Stardew Valley")

LAUNCHER = "StardewModdingAPI.exe"
GAME_EXE = "Stardew Valley.exe"
TASKLIST_NAMES = (LAUNCHER, GAME_EXE)


def resolve_game_dir(game_dir: str | os.PathLike[str] | None = None) -> Path:
    """Pick the game directory from the argument, then STARDEW_GAME_DIR, then the default."""
    candidate = game_dir or os.environ.get("STARDEW_GAME_DIR") or DEFAULT_GAME_DIR
    path = Path(candidate)
    if not (path / LAUNCHER).exists():
        raise FileNotFoundError(f"{LAUNCHER} not found in {path}")
    return path


def running_processes() -> list[str]:
    """Names from TASKLIST_NAMES that are currently running."""
    found: list[str] = []
    for name in TASKLIST_NAMES:
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                capture_output=True, timeout=20,
            )
        except Exception:
            continue

        # tasklist prints in the OEM codepage on non-English Windows; the image names we look
        # for are ASCII, so decoding permissively is enough to spot them.
        out = completed.stdout.decode("utf-8", "ignore")
        if name.lower() in out.lower():
            found.append(name)
    return found


def is_running() -> bool:
    return bool(running_processes())


def launch(game_dir: str | os.PathLike[str] | None = None) -> subprocess.Popen:
    """Start the game in its own console window.

    SMAPI is a console application: launched detached (or with its stdio redirected to NUL)
    it dies immediately with a .NET unhandled exception, so it needs a real console.
    """
    path = resolve_game_dir(game_dir)

    return subprocess.Popen(
        [str(path / LAUNCHER)],
        cwd=str(path),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        close_fds=True,
    )


def stop(force: bool = True, wait_s: float = 30.0) -> None:
    """Kill the game and wait for it to disappear."""
    for name in running_processes():
        cmd = ["taskkill", "/IM", name]
        if force:
            cmd.append("/F")
        subprocess.run(cmd, capture_output=True, text=True)

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and is_running():
        time.sleep(1.0)


def ensure_running(game_dir: str | os.PathLike[str] | None = None) -> bool:
    """Launch the game if it is not already up. Returns True if we started it."""
    if is_running():
        return False
    launch(game_dir)
    return True


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        stop()
        print("stopped")
    elif len(sys.argv) > 1 and sys.argv[1] == "start":
        print("started" if ensure_running() else "already running")
    else:
        print(f"running: {running_processes() or 'no'}")
