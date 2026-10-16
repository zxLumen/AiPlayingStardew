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


def _player(state: dict) -> dict:
    return state.get("player") or {}


def _pos(state: dict):
    p = _player(state)
    return (p.get("tile_x"), p.get("tile_y"))


def _counter(state: dict, field_name: str):
    return ((state.get("location") or {}).get("work") or {}).get(field_name)


def _slug(text: str) -> str:    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


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

        if reason == "cutscene/event playing":
            return "clear_event", {}

        if reason.startswith("menu open"):
            return "menu_click", {"name": "upperRightCloseButton"}

        return None

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
                break

            if budget_s and elapsed > budget_s:
                self._cancel_chore(is_chore)
                result.stopped = "over budget"
                break

            state = self.client.state()
            game = state.get("game") or {}
            player = _player(state)

            # 1. held by something the game is showing.
            if not game.get("is_player_free"):
                reason = str(game.get("blocked_reason") or "unknown")

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
                        self._note(day, step, event="gave_up", action=action, reason=reason,
                                   held_s=round(held, 1))
                        result.stopped = f"blocked: {reason}"
                        break

                    result.state = state
                    time.sleep(POLL_S)
                    continue

                if held > BLOCKED_GRACE_S:
                    attempt = remedies_for_reason.get(reason, 0)
                    remedy = self._remedy_for(reason)

                    if remedy is None or attempt >= MAX_REMEDIES_PER_REASON:
                        # Not obvious, or the obvious thing did not work. This is a judgement call.
                        self._raise(f"blocked:{_slug(reason)}", f"卡在「{reason}」且守望者处理不了",
                                    "agent", detail=f"{action} 被 {reason} 挡住，处方{'不存在' if remedy is None else '无效'}",
                                    cost_s=elapsed)
                        self._note(day, step, event="gave_up", action=action, reason=reason,
                                   held_s=round(held, 1))
                        result.stopped = f"blocked: {reason}"
                        break

                    remedies_for_reason[reason] = attempt + 1
                    name, remedy_args = remedy
                    applied = self.client.action(name, **remedy_args)
                    result.remedies.append(name)
                    self._note(day, step, event="remedy", action=action, reason=reason,
                               remedy=name, ok=bool(applied.get("ok")),
                               detail=str(applied.get("detail", ""))[:120])
                    self.log(f"[守望] 被「{reason}」挡住 {held:.1f}s -> {name} "
                             f"({'ok' if applied.get('ok') else 'failed'}: {applied.get('detail')})")
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
                    self._note(day, step, event="stalled", action=action, at=here,
                               moving_for_s=round(still_for, 1))
                    self.log(f"[守望] {action} 走了 {still_for:.0f} 秒没挪窝 -> 取消")
                    self._cancel_chore(True)
                    self._raise(f"stalled:{action}", f"{action} 走不到目标",
                                "agent", detail=f"在 {here} 原地走了 {still_for:.0f} 秒",
                                cost_s=elapsed)
                    result.stopped = "stalled"
                    break

                # Give up only when NOTHING is moving: not the count, not the farmer. A macro
                # walking to its next target sits still on the count for a while and is fine.
                if idle_for > NO_PROGRESS_S and still_for > NO_PROGRESS_S and not moving:
                    self._note(day, step, event="no progress", action=action,
                               value=counter, for_s=round(idle_for, 1))
                    self.log(f"[守望] {action} 的计数停在 {counter}、人也不动 "
                             f"{idle_for:.0f} 秒 -> 取消")
                    self._cancel_chore(True)
                    result.stopped = "no progress"
                    break

                time.sleep(POLL_S)
                continue

            # 3. a single action: wait for the farmer to stop moving, then it is done.
            if not player.get("is_auto_moving"):
                result.stopped = "done"
                break

            time.sleep(POLL_S)

        result.state = state
        if result.remedies:
            self._note(day, step, event="watch done", action=action,
                       stopped=result.stopped, remedies=result.remedies,
                       seconds=round(result.seconds, 1))
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
