"""Keep the stream up and keep watching for the one thing that proves the mouse.

Two jobs, both of them things that were previously done by a person:

  * THE STACK STAYS UP. It has gone down twice for reasons nobody noticed - an agent stopped to pick
    up a change and never restarted, and a machine restart that took the game with it. Both left the
    game sitting there with nobody playing. This notices within a minute and puts it back.

  * THE PROOF IS RECORDED WHEN IT HAPPENS. Every failed click so far was a case the game was entitled
    to refuse (a shop cannot take a purchase it has nowhere to put), so the only click that proves
    anything is one with room to complete: a shop row clicked while the bag has a free slot must move
    money. That may happen at three in the morning with nobody watching, so it goes in a file.

Append-only, timestamped, at logs/click-verdict.txt.
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
STREAM = ROOT / "logs" / "agents" / "streamer.jsonl"
VERDICT = ROOT / "logs" / "click-verdict.txt"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

AGENT_ARGS = ["-u", "agent/agent.py", "--forever", "--max-hours", "12.0",
              "--steps", "200", "--max-days", "200", "--day-seconds", "1800"]

HOURS = 10
POLL_S = 30


def note(text: str) -> None:
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {text}"
    print(line, flush=True)
    try:
        VERDICT.parent.mkdir(parents=True, exist_ok=True)
        with VERDICT.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def agent_running() -> bool:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*agent.py*' } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=30, errors="replace")
        return (out.stdout or "").strip() not in ("", "0")
    except Exception as exc:
        note(f"查进程失败：{exc}")
        return True          # unknown: do not thrash


def start_agent() -> None:
    try:
        subprocess.Popen([str(PYTHON)] + AGENT_ARGS, cwd=str(ROOT),
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        note("agent 不在跑 —— 已重新拉起（它会自己带起游戏）")
    except Exception as exc:
        note(f"拉不起来：{exc}")


def clicks() -> list[dict]:
    try:
        rows = [json.loads(l) for l in open(STREAM, encoding="utf-8", errors="replace") if l.strip()]
    except Exception:
        return []
    return [r for r in rows if r.get("action") == "menu_click"]


def main() -> int:
    note(f"开始盯着：{HOURS} 小时，每 {POLL_S} 秒一次（看栈是否还在，以及那次能证明鼠标的点击）")
    known = {str(r.get("t")) for r in clicks()}
    proven = {"done": False}
    down_checks = 0
    deadline = time.time() + HOURS * 3600

    while time.time() < deadline:
        if agent_running():
            down_checks = 0
        else:
            down_checks += 1
            if down_checks >= 2:
                start_agent()
                down_checks = 0

        for row in clicks():
            stamp = str(row.get("t"))
            if stamp in known:
                continue
            known.add(stamp)

            detail = str(row.get("detail") or "")
            if "with the mouse in" in detail and not proven["done"]:
                proven["done"] = True
                note(f"证明成立 —— 真鼠标完成了点击：{detail[:220]}")
            elif "menu hand" in detail and "->empty" in detail:
                note(f"真鼠标把东西放下了：{detail[:200]}")
            elif "did not react" in detail or "changed nothing" in detail:
                note(f"游戏拒绝了（可能正确）：{detail[:160]}")

        time.sleep(POLL_S)

    note("到点了，收工。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
