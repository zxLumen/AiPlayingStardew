"""Ask the game a set of questions a player would ask, using only its own data.

Every answer here must come out of the game's content tables - if one of these needs a hardcoded
rule, that rule is a gap in what the mod exposes.

    python tools/gamequiz.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from agent import game_process  # noqa: E402
from agent.agent import load_ai_save, match_slot  # noqa: E402
from agent.state_client import StateClient  # noqa: E402


def log(message: str) -> None:
    print(message, flush=True)


def load_save(client: StateClient, steps: int = 120) -> bool:
    ai_save = load_ai_save()
    used: set[str] = set()

    for _ in range(steps):
        state = client.state()
        if (state.get("game") or {}).get("has_save"):
            return True

        menu = (state.get("ui") or {}).get("menu_name") or ""

        if menu == "LoadGameMenu":
            slot = match_slot((state.get("ui") or {}).get("save_slots") or [], ai_save)
            if slot is None:
                log("[boot] agent save not found")
                return False
            if "load" not in used:
                used.add("load")
                client.action("menu_click", name=str(slot["index"]))
            time.sleep(2)
            continue

        if menu == "TitleMenu":
            if "load-btn" not in used:
                used.add("load-btn")
                client.action("menu_click", name="Load")
            time.sleep(2)
            continue

        time.sleep(1.5)

    return False


def ask(client: StateClient, question: str, what: str, **kw) -> None:
    result = client.action("game_data", what=what, **kw)
    detail = result.get("detail")
    log(f"\nQ: {question}")
    log(f"   game_data(what={what!r}, {kw}) -> {detail}")

    if not result.get("ok"):
        return

    entries = ((result.get("data") or {}).get("entries"))
    if isinstance(entries, list):
        for item in entries[:6]:
            text = json.dumps(item, ensure_ascii=False)
            log(f"     {text[:200]}")
    elif isinstance(entries, dict):
        for name, item in list(entries.items())[:6]:
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            log(f"     {name}: {str(text)[:180]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()

    if not args.no_launch and game_process.ensure_running():
        log("[game] launched")

    with StateClient(args.base_url, timeout_s=60.0) as client:
        if not client.wait_until_up():
            log("[api ] never came up")
            return 1
        if not client.wait_until_loaded(timeout_s=20) and not load_save(client):
            log("[api ] no save in game")
            return 1

        ask(client, "游戏里到底有哪些数据可以查？", "catalog")

        ask(client, "皮埃尔的杂货店几点开门、哪天休息？",
            "Data/WorldMap", query="皮埃尔")

        ask(client, "一共有哪些商店开门时间被写在游戏里？",
            "Data/WorldMap", query="开放")

        ask(client, "防风草种子几天长成？什么季节能种？",
            "Data/Crops", key="472")

        ask(client, "哪些东西的名字里带'防风草'？", "Data/Objects", query="防风草")

        ask(client, "皮埃尔生日是哪天？他住哪？", "Data/Characters", key="Pierre")

        ask(client, "皮埃尔喜欢什么礼物？", "Data/NPCGiftTastes", key="Pierre")

        ask(client, "社区中心春季作物包要交什么？", "Data/Bundles", query="春季作物")

        ask(client, "镇上的人都有些什么日程？", "Strings/schedules/Abigail")

        ask(client, "现在能钓到什么鱼？", "Data/Locations", key="Town")

        ask(client, "有哪些节日？", "Data/Festivals/FestivalDates")

        ask(client, "现在有什么任务可以做？", "Data/Quests", query="Introductions")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
