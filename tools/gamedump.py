"""Dump the game's own data tables through the StateBridge mod.

This is a read-only survey: it loads the agent's own save, asks the mod what data exists, then
fetches the tables that matter for play and writes them to notes/game-data/.

    python tools/gamedump.py            # catalog + the priority tables
    python tools/gamedump.py --all      # every asset in the catalog (slow, large)
    python tools/gamedump.py --what Data/Shops
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

OUT = PROJECT / "notes" / "game-data"

# The tables worth reading first, in the order they unlock things.
PRIORITY = [
    "Data/WorldMap",
    "Strings/WorldMap",
    "Data/Shops",
    "Data/Crops",
    "Data/Objects",
    "Data/Characters",
    "Data/NPCGiftTastes",
    "Data/Bundles",
    "Data/Locations",
    "Data/Tools",
    "Data/BigCraftables",
    "Data/FruitTrees",
    "Data/WildTrees",
    "Data/FarmAnimals",
    "Data/Buildings",
    "Data/Weapons",
    "Data/Machines",
    "Data/Quests",
    "Data/SpecialOrders",
    "Data/Movies",
]


def log(message: str) -> None:
    print(message, flush=True)


def drive_to_save(client: StateClient, steps: int = 120) -> bool:
    """Click through title/load menus until the agent's own save is in game.

    Each menu is entered once and then waited on: the title screen takes a while to hand over to the
    load screen, and re-clicking in the meantime just sprays input at a menu that is already leaving.
    """
    ai_save = load_ai_save()
    log(f"[save] on record: {ai_save.get('character_name') if ai_save else '(none)'}")

    used: set[str] = set()

    for _ in range(steps):
        state = client.state()
        game = state.get("game") or {}
        ui = state.get("ui") or {}
        menu = ui.get("menu_name") or ""

        if game.get("has_save"):
            return True

        if menu == "LoadGameMenu":
            slot = match_slot(ui.get("save_slots") or [], ai_save)
            if slot is None:
                log("[boot] the agent's own save is not in the list - refusing to touch the others")
                return False

            if "load" not in used:
                used.add("load")
                log(f"[boot] loading slot {slot['index']} ({slot.get('farmer_name')})")
                client.action("menu_click", name=str(slot["index"]))
            time.sleep(2)
            continue

        if menu == "TitleMenu":
            if "load-button" not in used:
                used.add("load-button")
                log("[boot] title menu -> Load")
                client.action("menu_click", name="Load")
            time.sleep(2)
            continue

        time.sleep(1.5)

    return False


def fetch(client: StateClient, what: str, limit: int = 200) -> dict:
    return client.action("game_data", what=what, limit=limit)


def save(name: str, payload: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / (name.replace("/", "_") + ".json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[dump] {name:32s} -> {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--what", action="append", default=[], help="dump just this asset (repeatable)")
    parser.add_argument("--all", action="store_true", help="dump every asset in the catalog")
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()

    if not args.no_launch:
        if game_process.ensure_running():
            log("[game] launched")

    with StateClient(args.base_url) as client:
        log("[api ] waiting for StateBridge...")
        if not client.wait_until_up():
            log("[api ] never came up")
            return 1

        if not client.wait_until_loaded(timeout_s=30):
            if not drive_to_save(client):
                log("[api ] no save in game - load the agent's save and retry")
                return 1

        catalog = fetch(client, "catalog", limit=1)
        if not catalog.get("ok"):
            log(f"[api ] catalog failed: {catalog.get('detail')}")
            return 1

        names = (catalog.get("data") or {}).get("assets") or []
        log(f"[data] the game ships {len(names)} data assets")
        save("_catalog", {"count": len(names), "assets": names})

        targets = args.what or (names if args.all else PRIORITY)

        for name in targets:
            result = fetch(client, name)
            log(f"[data] {name:32s} {result.get('ok')} - {result.get('detail')}")
            if result.get("ok"):
                save(name, result)

    log(f"[done] wrote to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
