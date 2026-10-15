"""Shrink a /state payload to what the LLM actually needs, to keep token cost down."""

from __future__ import annotations

MAX_BUTTONS = 40
MAX_LIST = 25


def _buttons(ui: dict) -> list[dict]:
    out = []
    for button in (ui.get("buttons") or [])[:MAX_BUTTONS]:
        entry = {"name": button.get("name")}
        if button.get("source") and button.get("source") != button.get("name"):
            entry["src"] = button.get("source")
        if button.get("label"):
            entry["label"] = button["label"]
        entry["xywh"] = [button.get("x"), button.get("y"), button.get("width"), button.get("height")]
        if button.get("visible") is False:
            entry["hidden"] = True
        out.append(entry)
    return out


def compact_state(state: dict) -> dict:
    game = state.get("game", {}) or {}
    player = state.get("player", {}) or {}
    location = state.get("location") or {}
    ui = state.get("ui") or {}

    compact: dict = {
        "game": {
            "day": game.get("day_of_month"),
            "season": game.get("season"),
            "year": game.get("year"),
            "time": game.get("time_of_day"),
            "raining": game.get("is_raining"),
            "festival": game.get("is_festival"),
            "has_save": game.get("has_save"),
            "player_free": game.get("is_player_free"),
            "game_paused": game.get("is_game_paused"),
            "blocked_reason": game.get("blocked_reason"),
        },
        "player": {
            "name": player.get("name"),
            "money": player.get("money"),
            "health": player.get("health"),
            "stamina": player.get("stamina"),
            "max_stamina": player.get("max_stamina"),
            "tile": [player.get("tile_x"), player.get("tile_y")],
            "facing": player.get("facing"),
            "location": player.get("current_location"),
            "holding": (player.get("current_item") or {}).get("name"),
            "selected_slot": player.get("selected_slot_index"),
            "auto_moving": player.get("is_auto_moving"),
            "bed_tile": player.get("bed_tile"),
        },
        "inventory": [
            {"slot": i.get("slot"), "name": i.get("name"), "count": i.get("stack"), "kind": i.get("kind")}
            for i in (state.get("inventory") or [])[:MAX_LIST]
        ],
    }

    if location:
        compact["location"] = {
            "name": location.get("name"),
            "outdoors": location.get("is_outdoors"),
            "farm": location.get("is_farm"),
        }

    crops = [
        {"tile": [c.get("tile_x"), c.get("tile_y")], "name": c.get("name"),
         "ready": c.get("is_ready"), "watered": c.get("is_watered")}
        for c in (state.get("crops") or [])[:MAX_LIST]
    ]
    if crops:
        compact["crops"] = crops

    objects = [
        {"tile": [o.get("tile_x"), o.get("tile_y")], "name": o.get("name"), "ready": o.get("is_ready")}
        for o in (state.get("nearby_objects") or [])[:MAX_LIST]
    ]
    if objects:
        compact["objects"] = objects

    characters = [
        {"name": c.get("display_name") or c.get("name"), "tile": [c.get("tile_x"), c.get("tile_y")],
         "hearts": c.get("friendship_hearts")}
        for c in (state.get("nearby_characters") or [])[:10]
    ]
    if characters:
        compact["characters"] = characters

    quests = [
        {"name": q.get("name"), "objective": q.get("objective"), "done": q.get("is_completed")}
        for q in (state.get("quests") or [])[:8]
    ]
    if quests:
        compact["quests"] = quests

    # Menus: only include what is actually open.
    menu_name = ui.get("menu_name")
    if menu_name:
        compact["ui"] = {
            "menu": menu_name,
            "parent": ui.get("parent_menu") or None,
            "buttons": _buttons(ui),
        }
        if ui.get("save_slots"):
            compact["ui"]["save_slots"] = [
                {"index": s.get("index"), "farmer": s.get("farmer_name"),
                 "farm": s.get("slot_name"), "sub": s.get("slot_sub_name")}
                for s in ui["save_slots"]
            ]
        if ui.get("text_fields"):
            compact["ui"]["text_fields"] = [
                {"field": t.get("field"), "value": t.get("value")} for t in ui["text_fields"]
            ]
        if ui.get("dialogue"):
            dialogue = ui["dialogue"]
            compact["ui"]["dialogue"] = {
                "speaker": dialogue.get("speaker"),
                "text": dialogue.get("text"),
                "responses": [
                    {"index": r.get("index"), "text": r.get("text")}
                    for r in (dialogue.get("responses") or [])
                ],
            }
        if ui.get("character_design"):
            compact["ui"]["character_design"] = ui["character_design"]

    return compact
