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
            "day_of_week": game.get("day_of_week"),
            "total_days": game.get("total_days"),
            "minutes_until_2am": game.get("minutes_until_morning"),
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

    # What the farmer faces right now - the tile an action would act on.
    front = player.get("tile_in_front")
    if front:
        compact["player"]["tile_in_front"] = {
            k: front.get(k)
            for k in ("x", "y", "passable", "water", "diggable", "object",
                      "ground", "crop", "crop_ready", "watered", "character", "can_interact")
            if front.get(k) not in (None, "", False, [])
        }

    # Route progress. A number that stops rising means "stuck", not "still walking".
    if player.get("path_target_x") is not None:
        compact["player"]["path"] = {
            "target": [player.get("path_target_x"), player.get("path_target_y")],
            "progress": player.get("path_progress"),
            "length": player.get("path_length"),
        }

    # A reflex routine is running in the game right now - the agent must not pile actions on top.
    chore = state.get("chore")
    if chore and chore != "idle":
        compact["chore_running"] = chore

    if location:
        compact["location"] = {
            "name": location.get("name"),
            "outdoors": location.get("is_outdoors"),
            "farm": location.get("is_farm"),
        }

        # The character map - the only way the agent can see walls.
        ascii_map = location.get("ascii_map")
        if ascii_map:
            compact["location"]["map"] = ascii_map

        warps = location.get("warps") or []
        if warps:
            compact["location"]["warps"] = [
                {"at": [w.get("x"), w.get("y")], "to": w.get("target"), "door": w.get("is_door")}
                for w in warps[:10]
            ]

        # Where the work actually is, across this whole location. Without it the planner guesses
        # coordinates and ends up hoeing a path.
        work = location.get("work") or {}
        if work:
            compact["location"]["work"] = {
                k: work.get(k) for k in
                ("tillable", "empty_soil", "debris", "thirsty_crops", "ready_crops")
                if work.get(k)
            }
            if work.get("tillable_samples"):
                compact["location"]["work"]["tillable_at"] = work["tillable_samples"]
            if work.get("empty_soil_samples"):
                compact["location"]["work"]["empty_soil_at"] = work["empty_soil_samples"]

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
