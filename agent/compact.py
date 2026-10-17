"""Shrink a /state payload to what the LLM actually needs, to keep token cost down."""

from __future__ import annotations

MAX_BUTTONS = 60
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

        # What the control is SHOWING, and whether the game would take it from here.
        #
        # Without these the agent sees a grid of buttons called "8", "12", "inventory.0" - names, and a
        # name is not a thing. It asked to "go and sell the parsnips to make room" and had nothing to
        # click, because the one slot that holds a parsnip was described only by its number. The bridge
        # has known what is in each slot since the shop rows were joined onto their goods; this is the
        # last step of that, and it is the difference between a list and something you can see.
        if button.get("item"):
            entry["shows"] = button["item"]
            if button.get("sellable"):
                entry["sells_for"] = True

        out.append(entry)
    return out


def _cursor_name(player: dict) -> str | None:
    """What is stuck to the mouse right now, or None.

    Worth a field of its own because it is a state a player sees instantly and the agent could not
    see at all: an item can sit on the cursor indefinitely - a shop purchase that was never put down
    lives there - while the toolbar happily reads "axe".
    """
    held = player.get("cursor_item") or {}
    name = held.get("name")
    if not name:
        return None
    count = held.get("stack") or 1
    return f"{name} x{count}" if count > 1 else name


def _bag_room(state: dict) -> str:
    """Free room in the bag, and which slot to use.

    Measured reason, not a guess: a shop's panel is the twelve hotbar slots, so a purchase has nowhere
    to go once they are taken - the seeds stay in the shop's hand and the menu then refuses to close.
    The streamer spent a whole day clicking a shelf for seeds while its bag was full, and the only
    thing that ever said so was the supervisor's prose, which arrives after the damage.
    """
    used = {int(i.get("slot", -1)) for i in (state.get("inventory") or []) if i.get("name")}
    free = [slot for slot in range(12) if slot not in used]

    if not free:
        return (f"{len(used)} slots used, HOTBAR FULL - a shop has nowhere to put a purchase, so make "
                f"room first (a bag slot, or drop something)")

    if len(free) == 1:
        return f"{len(used)} slots used, hotbar slot {free[0]} free"

    return f"{len(used)} slots used, hotbar slots {free[0]}-{free[-1]} free ({len(free)} free)"


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
            # "holding" above is the toolbar SELECTION, not what is in the hand. A farmer can be
            # carrying a whole stack on the cursor while the toolbar still reads "axe" - and while
            # that stack is there, a shop will not close. Naming it separately is the difference
            # between "the bridge is lying about the shop" and "there is a bag of seeds in my hand".
            "on_cursor": _cursor_name(player),
            "selected_slot": player.get("selected_slot_index"),
            "auto_moving": player.get("is_auto_moving"),
            "bed_tile": player.get("bed_tile"),
        },
        # How much room there is. A shop cannot take a purchase it has nowhere to put, and "my bag is
        # full" was the real reason the seed run failed while the streamer kept clicking a shelf that
        # could give it nothing. The supervisor's prose was the only thing that ever mentioned it; now
        # it is a fact in front of the agent, with the number of the slot to use.
        "bag": _bag_room(state),
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

        # Shops whose door shuts outside opening hours. The hours are on the door, and the plan needs
        # them before the walk, not after the knock.
        shops = location.get("shops") or []
        if shops:
            compact["location"]["shops"] = [
                {
                    "at": s.get("at"),
                    "to": s.get("leads_to"),
                    "hours": f"{s.get('opens_text') or s.get('opens_at')}~"
                             f"{s.get('closes_text') or s.get('closes_at')}",
                    "open_now": s.get("open_now"),
                    "note": s.get("note") or "",
                }
                for s in shops[:6]
            ]

        # The character map - the only way the agent can see walls.
        ascii_map = location.get("ascii_map")
        if ascii_map:
            compact["location"]["map"] = ascii_map

        warps = location.get("warps") or []
        if warps:
            # One entry per DESTINATION, not the first ten raw tiles.
            #
            # A location lists a warp for every TILE that leads somewhere, so the farm spends its
            # first ten entries on three edge tiles to BusStop and four to the Mountain, and the
            # farmhouse door - whose warp the game creates under the building's humanDoor - is cut off
            # the end. `enter <Name>` can only work on a name the agent has been shown, so a
            # truncation like that is the whole difference between "the house is not an exit" and
            # "the house is an exit I was never told about".
            #
            # The tile reported is a standing tile (x, y >= 0) whenever the destination has one:
            # an edge warp sits at -1 and cannot be walked to, but its target is still a real exit.
            best: dict = {}
            for warp in warps:
                key = (str(warp.get("target") or ""), bool(warp.get("is_door")))
                x, y = warp.get("x"), warp.get("y")
                standable = isinstance(x, int) and isinstance(y, int) and x >= 0 and y >= 0

                if key not in best:
                    best[key] = {"at": [x, y], "to": warp.get("target"),
                                 "door": warp.get("is_door"), "tiles": 1, "standable": standable}
                    continue

                entry = best[key]
                entry["tiles"] += 1
                if standable and not entry["standable"]:
                    entry["at"] = [x, y]
                    entry["standable"] = standable

            compact["location"]["warps"] = [
                {k: v for k, v in entry.items() if k != "standable"}
                for entry in list(best.values())[:MAX_LIST]
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
        if ui.get("held_item"):
            # The menu's own hand. While it holds something, the shop will not close and clicks stop
            # landing - and until now nothing said so, so "the shop is ignoring me" had no cause.
            compact["ui"]["held_item"] = ui["held_item"]
        if ui.get("shop"):
            # The shelves, joined onto the slot names menu_click takes. Without this the agent gets
            # a column of blank rows called "0".."3" and can only click them blind.
            compact["ui"]["shop"] = [
                {"slot": s.get("slot"), "item": s.get("item"), "price": s.get("price"),
                 "stock": s.get("stock"), "affordable": s.get("affordable")}
                for s in ui["shop"]
            ]
        container = ui.get("container") or []
        if container:
            # What is IN the open container (a chest, a fridge, the shipping bin). The shipping bin is
            # how you sell from home, so when the bag is full this is the list that matters.
            compact["ui"]["container"] = [
                {"slot": item.get("slot"), "item": item.get("name"), "count": item.get("stack")}
                for item in container[:MAX_LIST]
            ]

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

    # ---- the ruler, passed through verbatim ----
    #
    # How long since anything in the world moved. Written by the loop from the witness, read by the agent
    # (and by the supervisor, which is given this same compacted state). It is deliberately NOT filtered,
    # summarised or thresholded: the whole point is that the number is available and the judgement is the
    # agent's.
    if state.get("world"):
        compact["world"] = state["world"]

    return compact
