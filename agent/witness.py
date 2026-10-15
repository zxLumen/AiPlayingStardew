"""Every action must have a witness.

An action reporting success proves nothing. The only honest question is whether the world changed,
and answering it needs something in the world to look at afterwards:

    tile actions   the tile itself (its kind before vs after)
    reflex macros  the count of the work they were sent to do (location.work)
    refill         how much the can holds
    movement       the farmer's tile
    place changes  the current location
    menus          the open menu / dialogue
    the bag        which slot is held

An action with no witness cannot be learned from, so this module refuses to pretend. It returns
None and says why, and the gap detector treats a long run of unwitnessed actions as a defect in the
bridge rather than as success.

Writing "the world must change" down is not enough - it has to be checked for every action class,
which is what this module is for.
"""

from __future__ import annotations

# Actions aimed at one tile.
TILE_ACTIONS = {"work", "use_tool_at", "interact_at"}

# Reflex macros, and the location.work counter each one is supposed to move (downwards).
CHORE_WORK_FIELD = {
    "clear_area": "debris",
    "till_area": "empty_soil",
    "plant_all": "empty_soil",
    "water_all": "thirsty_crops",
    "harvest_all": "ready_crops",
}

# Actions that are not meant to change anything - asking for a witness is meaningless.
INFORMATIONAL = {
    "noop", "scan", "observe", "game_data", "chore", "pause_time", "focus_window",
    "set_display", "sleep", "new_day", "cancel_chore", "stop", "press_key", "clear_event",
    "title_button", "new_game", "create_character", "set_appearance", "set_text",
}


def _player(state: dict) -> dict:
    return state.get("player") or {}


def _work(state: dict, field: str):
    return ((state.get("location") or {}).get("work") or {}).get(field)


def _pos(state: dict):
    p = _player(state)
    return (p.get("tile_x"), p.get("tile_y"))


def _location(state: dict):
    return _player(state).get("current_location")


def _menu(state: dict) -> str:
    return (state.get("ui") or {}).get("menu_name") or ""


def _dialogue(state: dict) -> str:
    return ((state.get("ui") or {}).get("dialogue") or {}).get("text") or ""


def _charges(state: dict):
    return (_player(state).get("current_item") or {}).get("charges")


def _front(state: dict) -> str:
    """A short fingerprint of the tile the farmer faces - what `interact` acts on."""
    t = _player(state).get("tile_in_front") or {}
    return "|".join(str(t.get(k) or "") for k in ("object", "crop", "character", "ground"))


def judge(action: str, args: dict, before_state: dict, after_state: dict,
          before_tile: dict | None = None, after_tile: dict | None = None) -> tuple[bool | None, str]:
    """Did the world change? Returns (worked, evidence); worked is None when there is no witness."""
    if action in INFORMATIONAL:
        return None, "informational - no world change expected"

    if action in TILE_ACTIONS:
        if before_tile is None or after_tile is None:
            return None, "the tile could not be read before and after"
        was, now = before_tile.get("kind"), after_tile.get("kind")
        return was != now, f"tile kind {was} -> {now}"

    field = CHORE_WORK_FIELD.get(action)
    if field:
        was, now = _work(before_state, field), _work(after_state, field)
        if was is None or now is None:
            return None, f"the state does not report '{field}'"
        return now < was, f"{field} {was} -> {now}"

    if action == "refill":
        was, now = _charges(before_state), _charges(after_state)
        if was is None or now is None:
            return None, "the held item does not report how much it holds"
        return now > was, f"can {was} -> {now}"

    if action == "select_slot":
        was = _player(before_state).get("selected_slot_index")
        now = _player(after_state).get("selected_slot_index")
        want = args.get("index")
        return now == want, f"slot {was} -> {now} (wanted {want})"

    if action in ("menu_click", "dialogue_choose", "dialogue_advance", "skip_intro"):
        was = f"{_menu(before_state)}|{_dialogue(before_state)}"
        now = f"{_menu(after_state)}|{_dialogue(after_state)}"
        return was != now, "screen changed" if was != now else "nothing on screen moved"

    if action in ("go_to", "move", "warp"):
        was, now = _pos(before_state), _pos(after_state)
        return was != now, f"tile {was} -> {now}"

    if action in ("enter", "exit_building"):
        was, now = _location(before_state), _location(after_state)
        return was != now, f"location {was} -> {now}"

    if action == "interact":
        was, now = _front(before_state), _front(after_state)
        return was != now, f"in front {was} -> {now}"

    if action == "use_tool":
        was, now = _front(before_state), _front(after_state)
        return was != now, f"in front {was} -> {now}"

    return None, f"no witness exists for '{action}'"
