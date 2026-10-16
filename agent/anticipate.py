"""Thinking while the farmer is still moving.

Measured over a real day: a step's median cost was 4.68s - 1.82s of model and 2.63s of action - and the
two never overlapped. The farmer walked, then the model thought about what to do after the walk. The
brain was idle for 69% of the wall clock (10531s of action against 4742s of thought), and that is
exactly what the stream feels like: one stall per action instead of play.

So the next decision is started as soon as the current action is dispatched, on a PREDICTED world, and
adopted only if the world it was decided for is the one we are actually in.

Two rules are the whole design, and they are what keeps this from being a guess:

  * PREDICT ONLY WHAT IS EXACTLY KNOWABLE. A walk ends on the tile it was sent to (go_to either
    arrives there or fails, and a failure does not match). `enter` lands on the warp's own target tile,
    which the payload already carries. A look changes nothing. A click, a macro and a dialogue are
    NOT predicted: their world changes in ways this module does not model, and a wrong adoption costs
    more than the stall it saves.

  * ADOPT ONLY IF THE ACTION STILL MAKES SENSE. The predicted world can be wrong in ways the
    fingerprint cannot see - a dialogue opened mid-walk, the bag changed, the patch was finished - so
    the proposed action is checked against the world we actually got: a menu click needs a menu open,
    a tool swing or an interact needs the player free and near the tile, a macro needs work left to do.

A discarded speculation is reported out loud. A silent one would look exactly like a fast model, and
the whole point of this project is that "it seems fine" is not evidence.
"""

from __future__ import annotations

import threading
import time

from witness import CHORE_WORK_FIELD


def _player(state: dict) -> dict:
    return state.get("player") or {}


def fingerprint(state: dict) -> dict:
    """What the world has to look like for a decision made about it to still be adoptable.

    Kept as named fields because the DIAGNOSIS matters: "the prediction missed" is useless next to
    "the prediction missed on where, tile". Two of these are enough to adopt on:
    where the farmer is (reasoning about the wrong map breaks a plan) and whether it can act. The
    tile, the menu and the hands are already covered by still_valid, which asks the real state the
    precise question the chosen action needs answered - and requiring them here threw away most of
    the speculations for a difference the action's own check would have caught anyway.
    """
    ui = state.get("ui") or {}
    player = _player(state)
    game = state.get("game") or {}

    return {
        "where": player.get("current_location"),
        "free": bool(game.get("is_player_free")),
        "tile": (player.get("tile_x"), player.get("tile_y")),
        "menu": ui.get("menu_name") or "",
        "hand": ui.get("held_item") or "",
        "slot": player.get("selected_slot_index"),
    }


# The fields an answer must still agree on to be adopted. See fingerprint.
#
# `where` only. Not the tile: a walk that ends one tile short is still the same world for deciding
# what to do next, and requiring the exact tile threw away speculations for a difference the action's
# own `still_valid` check already catches. Not `free` either - whether the farmer can act right now is
# asked again, precisely, by still_valid.
ADOPT_ON = ("where",)


def _diff(predicted: dict, actual: dict) -> str:
    """Which fields the prediction got wrong, for the log. Silence hides a broken predictor."""
    return ", ".join(f"{k}: {predicted.get(k)!r}->{actual.get(k)!r}"
                     for k in predicted
                     if predicted.get(k) != actual.get(k)) or "nothing"


def predict(state: dict, action: str, args: dict) -> dict | None:
    """The world this action is expected to leave behind, or None when that is not knowable."""
    if action in ("scan", "observe", "noop", "stop"):
        return state

    if action == "go_to":
        x, y = args.get("x"), args.get("y")
        if isinstance(x, int) and isinstance(y, int):
            copy = dict(state)
            copy["player"] = {**_player(state), "tile_x": x, "tile_y": y}
            return copy
        return None

    if action == "select_slot":
        index = args.get("index")
        if isinstance(index, int):
            copy = dict(state)
            copy["player"] = {**_player(state), "selected_slot_index": index}
            return copy
        return None

    # NOT `enter`. It looks predictable - the warp's own target tile is right there in the payload -
    # but entering can chain: a fade, a cutscene, a second warp, and the farmer ends up somewhere the
    # payload never mentioned. Measured, most of those speculations were thrown away, which means paid
    # for and not used. A prediction that is wrong more often than right is not worth making.
    return None


def still_valid(action: str, args: dict, state: dict) -> bool:
    """Does this action still make sense, given the world we actually got?"""
    ui = state.get("ui") or {}
    player = _player(state)
    game = state.get("game") or {}
    menu = str(ui.get("menu_name") or "")
    free = bool(game.get("is_player_free"))

    field = CHORE_WORK_FIELD.get(action)
    if field:
        work = ((state.get("location") or {}).get("work") or {}).get(field)
        return bool(work)

    if action in ("go_to", "stop", "noop", "scan", "observe", "exit_building", "cancel_chore",
                  "select_slot", "press_key", "clear_event"):
        return True

    if action in ("menu_click", "dialogue_choose", "dialogue_advance", "title_button"):
        return bool(menu)

    if action == "enter":
        return free and not menu

    if action in ("interact_at", "use_tool_at", "work", "plant", "interact", "use_tool"):
        if menu or not free:
            return False

        x, y = args.get("x"), args.get("y")
        tx, ty = player.get("tile_x"), player.get("tile_y")
        if not all(isinstance(v, int) for v in (x, y, tx, ty)):
            return True

        return abs(x - tx) + abs(y - ty) <= 3

    return False


class Speculation:
    """One decision, being thought about while the farmer acts on the previous one."""

    def __init__(self, streamer, state: dict, history: list[str], directive: str | None) -> None:
        self.state = state
        self.state_fingerprint = fingerprint(state)
        self._box: dict = {}
        self._started = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, args=(streamer, state, list(history), directive), daemon=True,
            name="anticipate",
        )
        self._thread.start()

    def _run(self, streamer, state, history, directive) -> None:
        try:
            self._box["decision"] = streamer.decide(state, history, directive)
        except Exception as exc:                       # a speculation must never take the run down
            self._box["error"] = f"{type(exc).__name__}: {exc}"
        self._box["seconds"] = time.monotonic() - self._started

    def claim(self, timeout_s: float) -> tuple[dict | None, float, str]:
        """Wait for it (we are at the decision point anyway) and hand back what it decided."""
        self._thread.join(timeout_s)

        if self._thread.is_alive():
            return None, 0.0, "没想完"

        decision = self._box.get("decision")
        if decision is None:
            return None, 0.0, f"想的时候出错（{self._box.get('error')}）"

        return decision, float(self._box.get("seconds") or 0.0), ""


def take(holder: dict, state: dict) -> tuple[dict | None, float, str]:
    """Claim whatever was being thought about. Returns (decision, seconds, why-not)."""
    spec: Speculation | None = holder.pop("spec", None)
    if spec is None:
        return None, 0.0, "没有抢跑的决策"

    decision, seconds, why = spec.claim(timeout_s=60.0)
    if decision is None:
        return None, 0.0, why

    # The world it was decided for is not the world we are in: the prediction missed.
    here = fingerprint(state)
    wrong = {k: (spec.state_fingerprint.get(k), here.get(k)) for k in ADOPT_ON
             if spec.state_fingerprint.get(k) != here.get(k)}
    if wrong:
        return None, 0.0, f"猜的那个世界没成真（{_diff(spec.state_fingerprint, here)}）"

    return decision, seconds, ""
