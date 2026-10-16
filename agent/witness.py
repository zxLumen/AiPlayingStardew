"""Every action must have a witness.

An action reporting success proves nothing. The only honest question is whether the world changed,
and answering it needs something in the world to look at afterwards:

    tile actions   the tile itself (its kind before vs after)
    reflex macros  the count of the work they were sent to do (location.work)
    refill         how much the can holds
    movement       the farmer's tile
    place changes  the current location
    menus          the open menu / dialogue
    clicks         money, the bag and the cursor (a purchase does not move the screen)
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

# Every action the bridge actually accepts. The doctor is given this list, because a prescription
# that names an action which does not exist is not a prescription - it is a sentence. (The first
# time the doctor ran it prescribed "refill_watering_can", which the bridge has never heard of.)
ACTION_NAMES = (
    "go_to", "move", "warp", "enter", "exit_building", "sleep", "new_day",
    "work", "use_tool_at", "use_tool", "interact", "interact_at", "select_slot",
    "clear_area", "till_area", "plant_all", "water_all", "harvest_all", "plant", "refill",
    "scan", "observe", "menu_click", "dialogue_advance", "dialogue_choose",
    "focus_window", "pause_time", "cancel_chore", "clear_event", "press_key", "stop",
    "noop", "chore", "game_data", "overlay",
)


def resolve_action(name: str) -> str | None:
    """Match a name the model invented against the actions that exist."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None

    if wanted in ACTION_NAMES:
        return wanted

    # "refill_watering_can" -> "refill", "water all" -> "water_all", "WaterAll" -> "water_all"
    squashed = wanted.replace(" ", "_").replace("-", "_")
    flat = "".join(c for c in squashed if c.isalnum())

    for action in ACTION_NAMES:
        if squashed == action or squashed.startswith(action + "_") or squashed.endswith("_" + action):
            return action

    for action in ACTION_NAMES:
        if flat == action.replace("_", ""):
            return action

    for action in ACTION_NAMES:
        if action in squashed:
            return action

    return None

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
    "set_display", "sleep", "new_day", "cancel_chore", "stop", "clear_event",
    "title_button", "new_game", "create_character", "set_appearance", "set_text",
    "actions", "inspect", "open_screen", "narrate", "chat", "progress", "overlay",
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


def _money(state: dict):
    return _player(state).get("money")


def _cursor(state: dict) -> str:
    held = _player(state).get("cursor_item") or {}
    name = held.get("name")
    return f"{name} x{held.get('stack')}" if name else "empty"


def _hand(state: dict) -> str:
    """What is being carried, wherever it is being carried.

    There are TWO places a stack can sit in the hand and only one of them was watched: the player's
    cursor, and the open MENU's own held item. A shop purchase lands on the menu's hand - so four
    parsnip seeds were bought, money 500 -> 420, while this witness reported success four times and
    the bag stayed empty. The purchase never reached the bag, the shop would not close while holding,
    and nothing anywhere said the hand was full. Watching the cursor alone was watching the wrong hand.
    """
    cursor = _cursor(state)
    menu = str((state.get("ui") or {}).get("held_item") or "").strip()

    if not menu:
        return cursor
    if cursor == "empty":
        return f"menu holds {menu}"
    return f"menu holds {menu} + cursor holds {cursor}"


def _bag(state: dict) -> tuple:
    """What is in the bag, made comparable. A purchase that lands changes this."""
    return tuple(
        f"{s.get('slot')}:{s.get('name')}x{s.get('stack')}"
        for s in (state.get("inventory") or [])
    )


def _front(state: dict) -> str:
    """A short fingerprint of the tile the farmer faces - what `interact` acts on."""
    t = _player(state).get("tile_in_front") or {}
    return "|".join(str(t.get(k) or "") for k in ("object", "crop", "character", "ground"))


def progress_value(action: str, state: dict):
    """The number that should be moving while this action works, or None if there is not one.

    The watcher uses this to tell "the action is working" apart from "the action is stuck". It has
    to be the thing the action is *supposed* to change, not the action's own report of itself.
    """
    if action == "refill":
        return _charges(state)

    field = CHORE_WORK_FIELD.get(action)
    if field:
        return _work(state, field)

    return None


def judge(action: str, args: dict, before_state: dict, after_state: dict,
          before_tile: dict | None = None, after_tile: dict | None = None) -> tuple[bool | None, str]:
    """Did the world change? Returns (worked, evidence); worked is None when there is no witness."""
    if action in INFORMATIONAL:
        return None, "informational - no world change expected"

    if action in TILE_ACTIONS:
        if before_tile is None or after_tile is None:
            return None, "the tile could not be read before and after"
        was, now = before_tile.get("kind"), after_tile.get("kind")
        if was != now:
            return True, f"tile kind {was} -> {now}"

        # Acting on a tile can also OPEN something - a shop counter, a door, a dialogue - and when it
        # does, the tile is unchanged by design. Judging only the tile reported "nothing happened" for
        # a successful interaction, which is a false negative in exactly the place the agent most needs
        # a straight answer: it was told its shop-opening interact had failed.
        was_screen = f"{_menu(before_state)}|{_dialogue(before_state)}"
        now_screen = f"{_menu(after_state)}|{_dialogue(after_state)}"
        if was_screen != now_screen:
            return True, f"opened something: {was_screen} -> {now_screen}"

        return False, f"tile kind {was} -> {now}"

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

    if action == "menu_click":
        # A click in a shop is supposed to change the WORLD, not the screen. Buying seeds leaves the
        # shop menu exactly where it was, so judging a click by "did the screen move" made every real
        # purchase look like a lie - and made two very different outcomes look identical:
        #
        #   money down, bag up        a purchase went through
        #   money down, hand full     a purchase got picked up and never put down
        #
        # What actually moves is money, the bag and the hand. Those are the witness.
        before = (_menu(before_state), _dialogue(before_state), _money(before_state),
                  _bag(before_state), _hand(before_state))
        after = (_menu(after_state), _dialogue(after_state), _money(after_state),
                 _bag(after_state), _hand(after_state))

        if before == after:
            return False, "nothing moved (screen, money, bag and hand all identical)"

        notes = []
        if before[2] != after[2]:
            notes.append(f"money {before[2]} -> {after[2]}")
        if before[3] != after[3]:
            notes.append("the bag changed")
        if before[4] != after[4]:
            notes.append(f"hand {before[4]} -> {after[4]}")
        if before[0] != after[0]:
            notes.append(f"menu {before[0] or '(none)'} -> {after[0] or '(none)'}")

        # A hand left holding something is NOT a success. It is a purchase that never reached the bag,
        # and it is what stops the shop closing - the thing that froze a whole day while the agent was
        # being told every step had worked.
        if not after[4].startswith("empty"):
            notes.append(f"WARNING: hand is not empty - '{after[4]}'")

        return True, "; ".join(notes) if notes else "the world changed"

    if action == "press_key":
        # A key press had no witness at all, so `press_key escape` reported OK whether or not the menu
        # closed - and that is how a stuck shop menu looked like a successful escape for a whole day,
        # with the agent pressing escape over and over and being told each time that it worked.
        was = f"{_menu(before_state)}|{_dialogue(before_state)}"
        now = f"{_menu(after_state)}|{_dialogue(after_state)}"
        return was != now, f"screen {was} -> {now}"

    if action in ("dialogue_choose", "dialogue_advance", "skip_intro"):
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
