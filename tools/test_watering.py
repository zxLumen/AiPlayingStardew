"""Prove that watering can actually water.

Loads the agent's own save, reports what the can holds, refills, waters, and prints what the world
did - the numbers, not the action's own report.

    python tools/test_watering.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from agent import game_process  # noqa: E402
from agent.agent import load_ai_save, match_slot  # noqa: E402
from agent.state_client import StateClient  # noqa: E402


def log(m: str) -> None:
    print(m, flush=True)


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
                return False
            if "load" not in used:
                used.add("load")
                client.action("menu_click", name=str(slot["index"]))
            time.sleep(2)
            continue

        if menu == "TitleMenu":
            if "btn" not in used:
                used.add("btn")
                client.action("menu_click", name="Load")
            time.sleep(2)
            continue

        time.sleep(1.5)

    return False


def work(state: dict) -> dict:
    return ((state.get("location") or {}).get("work") or {})


def can(state: dict) -> dict:
    item = (state.get("player") or {}).get("current_item") or {}
    return {"name": item.get("name"), "charges": item.get("charges"), "max": item.get("charges_max")}


def main() -> int:
    if game_process.ensure_running():
        log("[game] launched")

    with StateClient(timeout_s=60.0) as client:
        if not client.wait_until_up():
            log("[api ] never came up")
            return 1
        if not client.wait_until_loaded(timeout_s=25) and not load_save(client):
            log("[api ] no save in game")
            return 1

        time.sleep(3)
        state = client.state()

        # The save may open indoors; the crops are outside.
        loc = (state.get("player") or {}).get("current_location")
        if not work(state).get("thirsty_crops") and "FarmHouse" in str(loc):
            log(f"[state] indoors at {loc} - stepping outside")
            r = client.action("exit_building")
            log(f"    exit_building: {r.get('ok')} {r.get('detail')}")
            time.sleep(4)
            for _ in range(3):
                state = client.state()
                if (state.get("game") or {}).get("is_player_free") and work(state).get("thirsty_crops"):
                    break
                time.sleep(2)

        log(f"[state] {json.dumps(can(state), ensure_ascii=False)} work={json.dumps(work(state), ensure_ascii=False)}")

        if not work(state).get("thirsty_crops"):
            log("[state] nothing thirsty here - the crops are somewhere else; rerun from the field")
            return 1

        log("\n--- refill ---")
        r = client.action("refill")
        log(f"    {r.get('ok')} {r.get('detail')}")
        after = client.wait_until_chore_done(timeout_s=120)
        log(f"    chore_last: {after.get('chore_last')}")
        log(f"    can now: {json.dumps(can(after), ensure_ascii=False)}")

        log("\n--- water_all ---")
        before_thirsty = work(after).get("thirsty_crops")
        r = client.action("water_all")
        log(f"    {r.get('ok')} {r.get('detail')}")
        after2 = client.wait_until_chore_done(timeout_s=300)
        log(f"    chore_last: {after2.get('chore_last')}")
        log(f"    thirsty: {before_thirsty} -> {work(after2).get('thirsty_crops')}")
        log(f"    can now: {json.dumps(can(after2), ensure_ascii=False)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
