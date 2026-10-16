"""The commentary, on its own.

It used to live inside agent.py, and that had a cost that was easy to stop noticing: every reworded
sentence needed a restart of the playing loop, which kills the game day in progress. A subtitle fix
should not cost a day of farming.

It does not have to, because none of its inputs live inside that process:

    agent events   ->  logs/agents/bus.jsonl      (the playing process appends, and forgets)
    the room       ->  bilibili danmaku, connected directly
    the game       ->  the bridge at 127.0.0.1:8787

and its output is another file:

    what it said   ->  logs/agents/commentary.jsonl   (the voice window reads this and speaks it)

So it starts in about a second, has no torch and no game attached, and can be restarted as often as
you like while a day is being played.

    .venv\\Scripts\\python.exe agent\\commentary_daemon.py
    start-commentary.bat        (same thing, with auto-restart)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory as memory_mod  # noqa: E402
from program import Program, load_config  # noqa: E402
from runlog import RunLog  # noqa: E402
from state_client import StateClient  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AI_SAVE = PROJECT_ROOT / "config" / "ai_save.json"


def log(message: str) -> None:
    print(message, flush=True)


def read_persona() -> tuple[str, str]:
    """Who the streamer is. Re-read rather than cached: the playing process designs the character
    on a first run, and the commentary has to be able to notice that it now has a name."""
    try:
        saved = json.loads(AI_SAVE.read_text(encoding="utf-8"))
        return str(saved.get("persona") or ""), str(saved.get("character_name") or "")
    except Exception:
        return "", ""


def make_context():
    """What this machine is stuck on, read from the files the other agents write.

    Files, not live objects: this process does not share those objects, which is the entire point of
    it being separate. The channel is called "teaching an AI to play this game", so the machine's own
    difficulties are not details to hide - they are the material.
    """

    def context() -> str:
        lines: list[str] = []

        try:
            data = json.loads((PROJECT_ROOT / "memory" / "defects.json").read_text(encoding="utf-8"))
            open_items = [d for d in (data.get("defects") or []) if d.get("status") == "open"]

            for owner, label in (
                ("developer", "- 这个游戏做得到、但我的桥还做不到，要请人写代码才行："),
                ("agent", "- 还在查的问题："),
            ):
                chosen = sorted([d for d in open_items if d.get("owner") == owner],
                                key=lambda d: -(d.get("cost_s") or 0))[:3]
                for defect in chosen:
                    note = f"{label}{defect.get('title')}"
                    if owner == "developer" and defect.get("detail"):
                        note += f"（{str(defect['detail'])[:90]}）"
                    lines.append(note)
        except Exception:
            pass

        try:
            data = json.loads((PROJECT_ROOT / "memory" / "learning.json").read_text(encoding="utf-8"))
            rows = []
            for key, value in (data.get("tally") or {}).items():
                ok, fail = int(value.get("ok", 0)), int(value.get("fail", 0))
                total = ok + fail
                if total >= 3:
                    rows.append((ok / total, key, ok, total))
            rows.sort()
            worst = [f"{key}({ok}/{total})" for _, key, ok, total in rows[:4]]
            if worst:
                lines.append("- 哪些做法老失败：" + "、".join(worst))
        except Exception:
            pass

        return "\n".join(lines)

    return context


def main() -> int:
    parser = argparse.ArgumentParser(description="解说进程：跟随事件流，开口说话")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--sync-s", type=float, default=30.0,
                        help="每隔多久重新读一次人设")
    parser.add_argument("--watch-s", type=float, default=5.0,
                        help="每隔多久看一眼房间号（窗口改房间后要跟上）")
    args = parser.parse_args()

    config = load_config()
    persona, speaker = read_persona()

    runlog = RunLog(PROJECT_ROOT, log=log)
    store = memory_mod.MemoryStore()

    log(f"[解说] 独立进程 | 角色={speaker or '主播'} | 人设={'有' if persona else '（还没有）'}")

    with StateClient(args.base_url) as client:
        show = Program(
            client=client,
            runlog=runlog,
            store=store,
            persona=persona,
            speaker=speaker or "主播",
            log=log,
            config=config,
            context=make_context(),
            role="commentary",
        )

        if not client.wait_until_up(timeout_s=60):
            log("[解说] 桥接口没响应 —— 游戏可能没在跑。仍然继续，等它起来。")

        show.start()
        log("[解说] 已上线。游戏那边怎么改、怎么重启，都不影响这里。")

        try:
            elapsed = 0.0
            while True:
                time.sleep(args.watch_s)
                elapsed += args.watch_s

                # Everything the operator can change while the stream is running is read here, from
                # the file, rather than trusted from startup. A commentary answering a room nobody is
                # in is worse than no commentary, and a rhythm that can only be changed by a restart
                # is a rhythm nobody tunes while watching.
                try:
                    fresh = load_config()
                except Exception:
                    fresh = {}

                room = str((fresh.get("chat") or {}).get("bilibili_room") or "").strip()

                if room and room != show.current_room():
                    show.switch_room(room)
                    continue

                if (fresh.get("commentary") or {}).get("auto_tune", True):
                    try:
                        changes = show.apply_pacing(fresh)
                    except Exception as exc:
                        changes = []
                        log(f"[解说] 节奏参数读不了：{type(exc).__name__}: {exc}")
                    if changes:
                        log("[解说] 节奏已更新：" + "，".join(changes))

                # One mouth, always. If the voice window was started after us, this process has to
                # stop speaking or the same sentence comes out twice.
                show.enforce_one_mouth()

                if elapsed >= args.sync_s:
                    elapsed = 0.0
                    latest, who = read_persona()
                    if latest and latest != show.persona:
                        show.refresh_persona(latest, who)
                        log(f"[解说] 人设已同步：{who}")
        except KeyboardInterrupt:
            pass
        finally:
            show.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
