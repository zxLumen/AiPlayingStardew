"""The watcher: the reflex for "something is going wrong right now".

The harness used to wait for an action by polling the world once a second and looking at exactly one
boolean - "is the chore still running?". Everything else in that state was read and thrown away, so
a farmer held by a greeting dialogue sat still for a full five minutes while every agent waited.

A person playing does not do that. They notice within a second, and they do the obvious thing. This
module is that noticing, and the obvious thing:

    blocked by a dialogue   -> advance it
    blocked by a menu       -> close it
    blocked by an event     -> clear it
    walking but not moving  -> cancel and pick another target
    no progress             -> cancel; the action cannot do this job
    burning the round       -> cancel and let the day end

Nothing here needs a model, because none of it is a judgement call. When it *is* a judgement call -
a remedy that fails, or a blockage with no known remedy - it raises a defect and hands over to the
diagnostic agent instead of guessing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

POLL_S = 0.5
BLOCKED_GRACE_S = 2.5     # how long a dialogue or menu may hold us before we take over
TRANSIENT_GRACE_S = 7.0   # some blockers are just animation and clear themselves
STALL_S = 5.0             # position frozen for this long while "walking" means stuck
NO_PROGRESS_S = 12.0      # the world counter not moving for this long means the action cannot work
MAX_REMEDIES_PER_REASON = 2

# An event with nothing on screen for this long is wreckage, not a scene. Real cutscenes are
# always showing something - dialogue, a letterbox, a fade - and the audience is watching them.
STUCK_EVENT_S = 45.0

# Blockers that are not a fault: the farmer is mid-animation or mid-fade and will move on.
# Treating these as defects fills the ledger with noise and aborts actions that would have worked.
TRANSIENT_BLOCKERS = {
    "farmer cannot move right now",
}


@dataclass
class WatchResult:
    state: dict
    stopped: str = "done"
    remedies: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    seconds: float = 0.0
    # What the watcher decided, in a form that can be WRONG. Everything above is an event - "stopped:
    # timeout", "remedies: [...]" - and an event is a description of what happened, so nothing can
    # ever contradict it and nobody can adjudicate the watcher. These four fields are the assertion
    # behind the event: `reason` names what it thought was wrong, `claim` states it as a claim about
    # the world, `expectation` is the prediction that follows, and `falsifier` is the observation that
    # would prove it false. `watch_audit.py` reads them back and files the ones the logs contradict.
    reason: str = ""
    claim: str = ""
    expectation: str = ""
    falsifier: str = ""


def _claim(kind: str, *, action: str, reason: str = "", remedy: str | None = None,
           value=None, where=None, held: float = 0.0, timeout_s: float = 0.0) -> tuple[str, str, str]:
    """The falsifiable claim behind one intervention: what I assert, what follows, what would break it.

    Kept in one place so every branch of the watch loop says what it believes in the same shape, and
    so a future reason cannot be added without someone being forced to write down how to disprove it.
    """
    if kind == "remedy":
        return (
            f"「{reason}」挡住了 {action}，处置「{remedy}」能解开它",
            f"做完 {remedy} 之后卡住会消失，这一步的世界会变",
            f"若 {remedy} 之后世界仍没变，或 {action} 后来不用 {remedy} 也成功了，则这个判断是错的",
        )

    if kind == "gave_up":
        return (
            f"{action} 被「{reason}」挡住且没有可用的处置",
            f"重试 {action} 会以同样的方式卡住",
            f"若 {action} 后来在没人介入时成功了，则放弃得太早",
        )

    if kind == "timeout":
        return (
            f"{action} 在 {timeout_s:.0f} 秒内没有推动世界",
            "这一步的世界不会变（见证应显示没变）",
            "若见证显示这一步世界变了（金钱/背包/光标/菜单等），则超时判断是错的",
        )

    if kind == "over budget":
        return (
            f"{action} 花光了这一轮的预算仍未完成",
            "继续等下去这一步也做不完",
            "若它其实在预算内完成了（世界变了），则提前结束是错的",
        )

    if kind == "no progress":
        return (
            f"{action} 的计数停在 {value}，人也不动",
            "计数不会再变，直到取消",
            f"若计数在这之后又变了，则 {action} 并没有卡住",
        )

    if kind == "stalled":
        return (
            f"{action} 在 {where} 原地走了 {held:.0f} 秒",
            "位置不会再变，直到取消",
            f"若位置在这之后又变了，则 {action} 并没有卡住",
        )

    return (f"{action}：{reason}", "", "")


def _player(state: dict) -> dict:
    return state.get("player") or {}


def _pos(state: dict):
    p = _player(state)
    return (p.get("tile_x"), p.get("tile_y"))


def _counter(state: dict, field_name: str):
    return ((state.get("location") or {}).get("work") or {}).get(field_name)


def _slug(text: str) -> str:    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


# Menus that were opened by something the agent did on purpose. They are the agent's to use, not
# a blockage to clear: a shop is how you buy, a dialogue is how you talk, a letter is something you
# opened. The watcher closed a ShopMenu once while the streamer was walking up to buy seeds, which
# is a fine example of helping too much.
HANDS_OFF_MENUS = (
    "ShopMenu", "DialogueBox", "LetterViewerMenu", "ItemGrabMenu", "LevelUpMenu",
    "MuseumMenu", "CarpenterMenu", "AnimalQueryMenu", "PurchaseAnimalsMenu", "ForgeMenu",
)

# Actions whose whole job happens INSIDE a menu. While one of these is being watched the menu being
# open is the point, not an obstacle, and there is nothing to wait for: the click has already
# happened, and whether it worked is a question for the witness (money / bag / either hand) - not for
# the farmer's feet. Watching them the old way spun here until the settle timeout and then announced
# "timeout - the world did not move" about a click that had just put four parsnip seeds in the bag.
MENU_ACTIONS = {"menu_click", "dialogue_choose", "dialogue_advance"}


class Watcher:
    """Watches one running action and takes the remedy for anything going wrong."""

    def __init__(self, client, ledger=None, runlog=None, log=print) -> None:
        self.client = client
        self.ledger = ledger
        self.runlog = runlog
        self.log = log

    # -- remedies ----------------------------------------------------------

    def _remedy_for(self, reason: str) -> tuple[str, dict] | None:
        """The obvious thing to do about a known blockage. None when it is not obvious."""
        if reason == "dialogue is open":
            return "dialogue_advance", {}

        if reason == "dialogue is waiting for a choice":
            return "dialogue_choose", {"index": 0}

        # NOT clear_event. A cutscene is content - the audience is watching it, and skipping it is
        # the one thing a stream must not do. Click through it the way the person holding the mouse
        # would, and only treat an event as abandoned when it has sat there with nothing on screen
        # for far longer than any real scene lasts (see STUCK_EVENT_S).
        if reason == "cutscene/event playing":
            return "dialogue_advance", {}

        if reason.startswith("menu open"):
            # Only the pause/inventory menu is a stray. A shop, a dialogue or a letter was opened by
            # something the agent did on purpose, and closing it throws its own work away - the
            # watcher closed a ShopMenu once while the streamer was trying to buy seeds.
            if any(name in reason for name in HANDS_OFF_MENUS):
                return None

            return "menu_click", {"name": "upperRightCloseButton"}

        return None

    def _is_abandoned_event(self, held_for: float) -> bool:
        """An event with nothing on screen for this long is not a scene, it is wreckage."""
        return held_for > STUCK_EVENT_S

    def _note(self, day: str, step: int, **fields) -> None:
        if self.runlog is not None:
            try:
                self.runlog.agent("watcher", day=day, step=step, **fields)
            except Exception:
                pass

    # -- the watch loop ----------------------------------------------------

    def watch(self, action: str, args: dict, first_state: dict, *, day: str = "", step: int = 0,
              budget_s: float = 0.0, timeout_s: float = 240.0,
              progress=None, witness_field: str | None = None) -> WatchResult:
        """Follow one action to its end, or until something is clearly wrong.

        `progress` is a callable that returns the number this action should be moving; it is what
        tells "working" apart from "stuck". A macro that walks between targets has a counter that
        sits still for several seconds while it makes perfectly good progress, so a stalled counter
        alone is not enough to give up on it.
        """
        started = time.monotonic()
        state = first_state
        result = WatchResult(state=state)

        is_chore = progress is not None

        blocked_since = 0.0
        blocked_reason = ""
        remedies_for_reason: dict[str, int] = {}

        last_pos = _pos(state)
        pos_since = time.monotonic()
        last_counter = progress(state) if progress else None
        counter_since = time.monotonic()

        while True:
            elapsed = time.monotonic() - started
            result.seconds = elapsed

            if elapsed > timeout_s:
                result.stopped = "timeout"
                result.reason = f"timeout after {timeout_s:.0f}s"
                result.claim, result.expectation, result.falsifier = _claim(
                    "timeout", action=action, timeout_s=timeout_s)
                break

            if budget_s and elapsed > budget_s:
                self._cancel_chore(is_chore)
                result.stopped = "over budget"
                result.reason = "over budget"
                result.claim, result.expectation, result.falsifier = _claim(
                    "over budget", action=action)
                break

            state = self.client.state()
            game = state.get("game") or {}
            player = _player(state)

            # 1. held by something the game is showing.
            if not game.get("is_player_free"):
                reason = str(game.get("blocked_reason") or "unknown")

                # A menu the agent opened on purpose is not a blockage - it is the agent's own work
                # in progress. Leave it alone and keep waiting; the agent will click or close it
                # when it is done. Only a menu nobody asked for (a stray Escape) is a fault.
                if reason.startswith("menu open") and any(n in reason for n in HANDS_OFF_MENUS):
                    if not is_chore:
                        # For anything that is not a running macro the menu is not holding the action
                        # up: the action has already returned, and the menu is simply the state it
                        # returned into. Waiting here until the settle timeout was the largest single
                        # cost in a step - a refused exit_building cost 12.5 seconds of live stream,
                        # a refused interact_at 4.2 - and it bought nothing, because the verdict
                        # belongs to the witness (money / bag / hand / screen), not to the clock.
                        result.stopped = "done"
                        break

                    result.state = state
                    time.sleep(POLL_S)
                    continue

                if blocked_since == 0.0 or reason != blocked_reason:
                    blocked_since = time.monotonic()
                    blocked_reason = reason

                held = time.monotonic() - blocked_since

                if reason in TRANSIENT_BLOCKERS:
                    # Not a fault - just wait for the animation to finish.
                    if held > TRANSIENT_GRACE_S:
                        self._raise(f"stuck:{_slug(reason)}", f"长时间「{reason}」",
                                    "agent", detail=f"{action} 被 {reason} 挡住 {held:.0f} 秒",
                                    cost_s=elapsed)
                        result.stopped = f"blocked: {reason}"
                        result.reason = reason
                        result.claim, result.expectation, result.falsifier = _claim(
                            "gave_up", action=action, reason=reason)
                        self._note(day, step, event="gave_up", action=action, reason=reason,
                                   held_s=round(held, 1), claim=result.claim,
                                   expectation=result.expectation, falsifier=result.falsifier)
                        break

                    result.state = state
                    time.sleep(POLL_S)
                    continue

                if held > BLOCKED_GRACE_S:
                    attempt = remedies_for_reason.get(reason, 0)
                    remedy = self._remedy_for(reason)

                    # A cutscene is a scene, and the audience is watching it. Click through as many
                    # lines as it has - a real event always has something on screen - and only clear a
                    # flag that has held the farmer this long with nothing to look at. This is the one
                    # place the show must never be skipped, so the attempt cap does not apply here.
                    if reason == "cutscene/event playing":
                        remedy = ("clear_event", {}) if self._is_abandoned_event(held) \
                            else ("dialogue_advance", {})
                        attempt = 0

                    if remedy is None or attempt >= MAX_REMEDIES_PER_REASON:
                        # Not obvious, or the obvious thing did not work. This is a judgement call.
                        self._raise(f"blocked:{_slug(reason)}", f"卡在「{reason}」且守望者处理不了",
                                    "agent", detail=f"{action} 被 {reason} 挡住，处方{'不存在' if remedy is None else '无效'}",
                                    cost_s=elapsed)
                        result.stopped = f"blocked: {reason}"
                        result.reason = reason
                        result.claim, result.expectation, result.falsifier = _claim(
                            "gave_up", action=action, reason=reason,
                            remedy=remedy[0] if remedy else None)
                        self._note(day, step, event="gave_up", action=action, reason=reason,
                                   held_s=round(held, 1),
                                   claim=result.claim, expectation=result.expectation,
                                   falsifier=result.falsifier)
                        break

                    remedies_for_reason[reason] = attempt + 1
                    name, remedy_args = remedy
                    applied = self.client.action(name, **remedy_args)
                    result.remedies.append(name)
                    result.reason = reason
                    result.claim, result.expectation, result.falsifier = _claim(
                        "remedy", action=action, reason=reason, remedy=name)
                    self._note(day, step, event="remedy", action=action, reason=reason,
                               remedy=name, ok=bool(applied.get("ok")),
                               detail=str(applied.get("detail", ""))[:120],
                               claim=result.claim, expectation=result.expectation,
                               falsifier=result.falsifier)
                    self.log(f"[守望] 被「{reason}」挡住 {held:.1f}s -> {name} "
                             f"({'ok' if applied.get('ok') else 'failed'}: {applied.get('detail')}) "
                             f"| 判断：{result.claim}")
                    blocked_since = time.monotonic()
                else:
                    result.state = state
                    time.sleep(POLL_S)
                    continue

            else:
                if blocked_reason and blocked_reason not in TRANSIENT_BLOCKERS:
                    # It got through, and something real was holding it. Worth knowing about.
                    self._raise(f"stall:{_slug(blocked_reason)}", f"被「{blocked_reason}」卡住",
                                "watcher",
                                detail=f"{action} 曾被 {blocked_reason} 挡住，守望者已解开")
                    self._note(day, step, event="cleared", action=action, reason=blocked_reason)
                blocked_since = 0.0
                blocked_reason = ""

            # 2. a chore is running: is it actually getting anywhere?
            if is_chore:
                if (state.get("chore") or "idle") == "idle":
                    result.stopped = "done"
                    break

                counter = progress(state)
                if counter != last_counter:
                    last_counter = counter
                    counter_since = time.monotonic()

                here = _pos(state)
                if here != last_pos:
                    last_pos = here
                    pos_since = time.monotonic()

                moving = bool(player.get("is_auto_moving"))
                still_for = time.monotonic() - pos_since
                idle_for = time.monotonic() - counter_since

                if moving and still_for > STALL_S:
                    result.stopped = "stalled"
                    result.reason = f"stalled at {here}"
                    result.claim, result.expectation, result.falsifier = _claim(
                        "stalled", action=action, where=here, held=still_for)
                    self._note(day, step, event="stalled", action=action, at=here,
                               moving_for_s=round(still_for, 1), claim=result.claim,
                               expectation=result.expectation, falsifier=result.falsifier)
                    self.log(f"[守望] {action} 走了 {still_for:.0f} 秒没挪窝 -> 取消")
                    self._cancel_chore(True)
                    self._raise(f"stalled:{action}", f"{action} 走不到目标",
                                "agent", detail=f"在 {here} 原地走了 {still_for:.0f} 秒",
                                cost_s=elapsed)
                    break

                # Give up only when NOTHING is moving: not the count, not the farmer. A macro
                # walking to its next target sits still on the count for a while and is fine.
                if idle_for > NO_PROGRESS_S and still_for > NO_PROGRESS_S and not moving:
                    result.stopped = "no progress"
                    result.reason = f"counter stuck at {counter}"
                    result.claim, result.expectation, result.falsifier = _claim(
                        "no progress", action=action, value=counter)
                    self._note(day, step, event="no progress", action=action,
                               value=counter, for_s=round(idle_for, 1),
                               claim=result.claim, expectation=result.expectation,
                               falsifier=result.falsifier)
                    self.log(f"[守望] {action} 的计数停在 {counter}、人也不动 "
                             f"{idle_for:.0f} 秒 -> 取消")
                    self._cancel_chore(True)
                    break

                time.sleep(POLL_S)
                continue

            # 3. a single action: wait for the farmer to stop moving, then it is done.
            if not player.get("is_auto_moving"):
                result.stopped = "done"
                break

            time.sleep(POLL_S)

        result.state = state
        # Record the whole intervention, not just the fact that a remedy was applied. A pure
        # `timeout` used to write nothing at all: `result.remedies` was empty, so this note never
        # fired, and the single most common decision the watcher makes left no trace to adjudicate.
        if result.remedies or result.claim:
            self._note(day, step, event="watch done", action=action,
                       stopped=result.stopped, remedies=result.remedies,
                       seconds=round(result.seconds, 1), reason=result.reason,
                       claim=result.claim, expectation=result.expectation,
                       falsifier=result.falsifier)
        return result

    def _cancel_chore(self, is_chore: bool) -> None:
        if not is_chore:
            return
        try:
            self.client.action("cancel_chore")
        except Exception:
            pass

    def _raise(self, key: str, title: str, owner: str, *, detail: str = "",
               cost_s: float = 0.0) -> None:
        if self.ledger is None:
            return
        try:
            self.ledger.raise_defect(key, title, owner, detail=detail, cost_s=cost_s)
        except Exception:
            pass
