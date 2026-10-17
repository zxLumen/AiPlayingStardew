"""主播AI (streamer) + 监工AI (supervisor).

Launches Stardew Valley, creates the AI's OWN save on first run (never touches the player's saves),
then plays a full day autonomously while the supervisor keeps it on track.

Usage:
    .venv\\Scripts\\python.exe agent\\agent.py                 # resume / create, then play
    .venv\\Scripts\\python.exe agent\\agent.py --new-game      # force a brand new AI save
    .venv\\Scripts\\python.exe agent\\agent.py --steps 60
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import authenticity  # noqa: E402
import game_process  # noqa: E402
import gaps  # noqa: E402
import memory as memory_mod  # noqa: E402
import program as program_mod  # noqa: E402
import prompts  # noqa: E402
import runner  # noqa: E402
import watch_audit  # noqa: E402
import witness  # noqa: E402
from brainwatch import BrainWatch  # noqa: E402
from chatfeed import ChatFeed  # noqa: E402
from compact import compact_state  # noqa: E402
import anticipate  # noqa: E402
import world as world_mod  # noqa: E402
from defects import DefectLedger  # noqa: E402
from doctor import Doctor  # noqa: E402
from learning import NOT_ACTIONABLE, LearningStore  # noqa: E402
from outcomes import OutcomeWatch  # noqa: E402
from prescribe import Prescriber  # noqa: E402
from watcher import WatchResult, Watcher  # noqa: E402
from witness import TILE_ACTIONS  # noqa: E402
from witness import INFORMATIONAL  # noqa: E402
from llm import LlmClient  # noqa: E402
from planner import PlannerAI  # noqa: E402
from referee import RefereeAI  # noqa: E402
from runlog import RunLog  # noqa: E402
from state_client import StateClient  # noqa: E402
from streamer import StreamerAI  # noqa: E402
from supervisor import SupervisorAI  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AI_SAVE_PATH = PROJECT_ROOT / "config" / "ai_save.json"

DEFAULT_GOAL = (
    "Play a full Stardew Valley day by yourself. Wake up in the farmhouse, care for your crops, "
    "explore, gather, talk to whoever you meet, then go to bed so the next day begins. "
    "It is your farm - make your own choices."
)


def log(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------------------
# The AI's own save
# ---------------------------------------------------------------------------

def load_ai_save() -> dict | None:
    if not AI_SAVE_PATH.exists():
        return None
    try:
        data = json.loads(AI_SAVE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def store_ai_save(design: dict) -> None:
    AI_SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "character_name": design.get("name", ""),
        "farm_name": design.get("farm_name", ""),
        "persona": design.get("persona", ""),
        "start_goal": design.get("start_goal", ""),
        "design": design,
    }
    AI_SAVE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def match_slot(slots: list[dict], ai_save: dict | None) -> dict | None:
    """Find the AI's slot in the load menu, by character name first, then farm name."""
    if not ai_save:
        return None

    character = (ai_save.get("character_name") or "").strip()
    farm = (ai_save.get("farm_name") or "").strip()

    for slot in slots:
        if character and (slot.get("farmer_name") or "").strip() == character:
            return slot
    for slot in slots:
        if farm and (slot.get("slot_name") or "").strip() == farm:
            return slot
    return None


# ---------------------------------------------------------------------------
# Bootstrap: get the AI's own save in game
# ---------------------------------------------------------------------------

def bootstrap(client: StateClient, streamer: StreamerAI, ai_save: dict | None,
              force_new: bool, max_iterations: int) -> dict | None:
    """Drive the title / load / character menus until the AI's own save is loaded.

    Returns the design dict when it created a new save this run, else None.
    """
    created: dict | None = None
    want_new = force_new or ai_save is None

    for _ in range(max_iterations):
        state = client.state()
        game = state.get("game") or {}
        ui = state.get("ui") or {}
        menu = ui.get("menu_name") or ""

        if game.get("has_save"):
            return created

        if menu == "CharacterCustomization":
            design_state = ui.get("character_design") or {}
            available = design_state.get("available_farm_types") or []

            log("[boot] CharacterCreation is open - the streamer is designing its character...")
            chosen = streamer.design_character(available)
            log("[boot] streamer chose: " + json.dumps(chosen, ensure_ascii=False))

            appearance = {
                key: chosen[key]
                for key in ("gender", "skin", "hair_style", "accessory",
                            "hair_color", "eye_color", "pants_color")
                if key in chosen
            }
            if appearance:
                log("[boot] set_appearance -> " + json.dumps(client.action("set_appearance", **appearance), ensure_ascii=False))
                time.sleep(1.5)

            result = client.action(
                "create_character",
                name=str(chosen.get("name", "AI")),
                farm_name=str(chosen.get("farm_name", "AI Farm")),
                favorite_thing=str(chosen.get("favorite_thing", "farming")),
                gender=str(chosen.get("gender", "")),
                farm_type=str(chosen.get("farm_type", "")),
                random_appearance_clicks=0,
                skip_intro=True,
            )
            log("[boot] create_character -> " + json.dumps(result, ensure_ascii=False))

            if result.get("ok"):
                created = chosen
                store_ai_save(chosen)
                ai_save = load_ai_save()
                want_new = False

            time.sleep(4)
            continue

        if menu == "LoadGameMenu":
            slots = ui.get("save_slots") or []
            slot = None if want_new else match_slot(slots, ai_save)

            if slot:
                log(f"[boot] loading the AI's own save - slot {slot['index']} ({slot['farmer_name']})")
                client.action("menu_click", name=str(slot["index"]))
            else:
                log("[boot] the AI has no save in this list - going back to create one")
                client.action("menu_click", name="backButton")
                want_new = True

            time.sleep(2.5)
            continue

        if menu == "TitleMenu":
            target = "New" if want_new else "Load"
            log(f"[boot] title menu -> {target}")
            client.action("menu_click", name=target)
            time.sleep(2.5)
            continue

        time.sleep(1.5)

    return created


# ---------------------------------------------------------------------------
# Play loop
# ---------------------------------------------------------------------------

def session_finished(day_start: dict, game: dict) -> bool:
    """The day rolled over.

    Both values must be real: during a transition the game briefly reports day 0 or a stale day,
    and treating that as "the day is over" ends the session two steps in.
    """
    if not game.get("has_save"):
        return False

    day = game.get("day_of_month")
    start = day_start.get("day_of_month")
    return bool(day) and bool(start) and day != start


def stable_day(client: StateClient, tries: int = 20, gap_s: float = 1.5) -> dict:
    """Wait for the in-game day to settle before playing it."""
    previous = None
    state = client.state()

    for _ in range(tries):
        state = client.state()
        game = state.get("game") or {}
        day = game.get("day_of_month")
        location = (state.get("player") or {}).get("current_location") or ""

        if game.get("has_save") and day and location and day == previous:
            return state

        previous = day
        time.sleep(gap_s)

    return state


CHORE_ACTIONS = {"clear_area", "till_area", "plant_all", "water_all", "harvest_all", "refill"}

# Informational actions that are nevertheless still DOING something: a sleep is a walk to the bed and
# then a cutscene, and the character menus animate. The rest of the informational set is instant, and
# following them only spends the settle timeout.
SLOW_INFORMATIONAL = {"sleep", "new_day", "create_character", "set_appearance", "set_text",
                      "title_button", "new_game", "open_screen"}


def settle_timeout(action: str, args: dict) -> float:
    """How long to wait (by polling state, never by asking the model) for an action to finish."""
    if action == "go_to":
        return 25.0
    if action == "enter":
        # `enter` walks to a door and then goes through it, so it needs as long as a walk. Judging
        # it after a few seconds reads the still-walking farmer as "the location did not change",
        # which is how `enter` got blacklisted for lying while it was working perfectly.
        return 30.0
    if action == "move":
        try:
            ms = float(args.get("duration_ms", 400))
        except (TypeError, ValueError):
            ms = 400.0
        return min(8.0, ms / 1000.0 + 2.0)
    if action in {"warp", "exit_building", "sleep", "new_day"}:
        return 12.0
    if action in {"use_tool_at", "use_tool", "interact", "interact_at", "menu_click", "dialogue_choose"}:
        return 4.0
    return 0.0


class StepClock:
    """Where the time between two actions goes.

    The number the audience actually feels is the wall clock between one step line and the next, and
    for a long time nobody measured it: a step's median was 5.00s while think (1.56s) and action
    (0.20s) accounted for less than half of it. A third of every step was going somewhere no log named,
    which is exactly the kind of cost that survives by being invisible.
    """

    def __init__(self) -> None:
        self._t = time.perf_counter()
        self._start = self._t
        self.parts: dict[str, float] = {}

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.parts[name] = self.parts.get(name, 0.0) + (now - self._t)
        self._t = now

    def total(self) -> float:
        return time.perf_counter() - self._start

    def report(self, threshold_s: float = 4.0) -> str:
        """One line, worst part first. Silent for steps fast enough not to be worth reading."""
        total = self.total()
        if total < threshold_s:
            return ""

        ordered = sorted(self.parts.items(), key=lambda kv: -kv[1])
        tail = " ".join(f"{name}={seconds:.1f}" for name, seconds in ordered[:4] if seconds >= 0.3)
        return f"{total:.1f}s ({tail})"


def _day_never_ended(client, day_start: dict, sleep_attempts: int, label: str, log, runlog) -> None:
    """Say out loud that the day did not end, and why - instead of leaving it to a person watching.

    This is the failure that has cost the most in this project and been noticed the least: the round
    budget runs out, the harness moves on, and the farmer is still standing in the kitchen at the next
    session. For days the only thing that knew was whoever was watching the stream. A monitor that
    waits 12 seconds and a guard that blocks an action can both be wrong in silence; this line cannot.
    """
    try:
        state = client.state()
    except Exception as exc:
        log(f"[日子] 结束不了，而且连状态都读不到：{exc}")
        return

    game = state.get("game") or {}
    if session_finished(day_start, game):
        return

    where = (state.get("player") or {}).get("current_location")
    block = game.get("blocked_reason") or ""
    log(f"[日子] 这一天没有结束：现在是 {game.get('season')} {game.get('day_of_month')} "
        f"{game.get('time_of_day')}，人在 {where}"
        + (f"，被「{block}」挡住" if block else "")
        + f"，sleep 试过 {sleep_attempts} 次。"
        + (" clock 已经过了 2:00，游戏本该把他送上床。" if not game.get("is_player_free", True) else ""))

    if runlog is not None:
        try:
            runlog.agent("run", day=label, event="day_did_not_end", time=game.get("time_of_day"),
                         location=where, blocked=block, sleep_attempts=sleep_attempts)
        except Exception:
            pass


def play(client: StateClient, streamer: StreamerAI, supervisor: SupervisorAI,
         max_steps: int, store: "memory_mod.MemoryStore",
         learning: LearningStore, runlog: RunLog, budget_s: float = 0.0,
         watcher: Watcher | None = None,
         prescriber: "Prescriber | None" = None,
         brainwatch: "BrainWatch | None" = None) -> tuple[str, str, int]:
    history: list[str] = []
    directive: str | None = None
    total_llm = total_action = total_watch = 0.0
    queued: list[dict] = []

    # Where a decision that is being thought about while the farmer acts is kept. See anticipate.py.
    spec_holder: dict = {}

    # Where the seconds of each step actually go. See StepClock.
    clock = StepClock()

    # THE RULER's state, not the ruler: when the world last moved, and what moved it. See the note where
    # it is put in front of the agent.
    changed_step = 0
    changed_at = time.monotonic()
    changed_note = "nothing has moved yet this session"

    # The player's own notes on how this game behaves. See world.py.
    world = world_mod.current(log=log)

    # How many times it tried to go to bed. A day that ends in bed is the one thing a round MUST be
    # able to do, and "it never once managed" was noticed by a person, not by the code.
    sleep_attempts = 0

    # A supervisor review being worked out in the background. See anticipate.Review.
    pending_review: object | None = None
    batch_reason = ""
    # How long an event flag may hold the farmer with nothing on screen before it counts as
    # wreckage rather than a scene. Generous on purpose: a cutscene IS content, the audience is
    # watching it, and killing it is the one thing a stream must never do.
    stuck_since = 0.0
    STUCK_EVENT_S = 45.0
    deadline = time.monotonic() + budget_s if budget_s > 0 else 0.0

    first = stable_day(client)
    day_start = first.get("game") or {}
    label = memory_mod.day_label(day_start)
    learning.attempts.clear()      # loop detection is per day

    if watcher is None:
        watcher = Watcher(client, log=log)
    log(f"[day] starting {day_start.get('season')} {day_start.get('day_of_month')}, "
        f"year {day_start.get('year')}, time {day_start.get('time_of_day')}")

    # Freeze the in-game clock while we play. The clock runs in real time, so a slow-thinking AI
    # would burn the whole day before finishing anything; the day now ends when it sleeps.
    # The clock runs. It used to be frozen for the whole day, which is not something a player can do
    # - and it broke the game's own rules in the worst possible way: the clock never reached 9:00,
    # so the shop never opened and "go and buy seeds at nine" could never happen, for sixty days.
    log("[day] the clock is running (a day takes as long as it takes)")

    for step in range(1, max_steps + 1):
        # A day is a round, and a round that drags on teaches nothing. When the budget is gone the
        # day is ended and scored as it stands - a fast failure is a data point, a slow one is a loss.
        if deadline and time.monotonic() > deadline:
            log(f"[day] round budget ({budget_s:.0f}s) spent at step {step} - ending the day here")
            _day_never_ended(client, day_start, sleep_attempts, label, log, runlog)
            return "budget_exhausted", label, step - 1

        state = client.state()
        clock.mark("state")

        # THE RULER. A fact, not a rule: how long it has been since anything in the world moved, and what
        # the last thing that moved was.
        #
        # The witness has measured this every step all along; it went into a log nobody read in the
        # moment. Here it is where the agent can see it - which is the whole difference between an agent
        # NOTICING "I have done six things and nothing has changed" and a person having to notice it.
        # No threshold here and no action attached: a rule ("if N steps, then do Y") is what produced
        # every dead end in this project. This hands over a number and lets the player decide, the same
        # way "bag: 12/12 full" and "shows: 防风草" did today with no rule behind them.
        state["world"] = {
            "steps_since_change": step - changed_step,
            "seconds_since_change": round(time.monotonic() - changed_at),
            "last_change": changed_note,
        }

        game = state.get("game") or {}

        # Whatever the game says out loud is a fact the world just handed us. Record it now -
        # waiting for the end-of-day referee means losing anything learned mid-day.
        dialogue = ((state.get("ui") or {}).get("dialogue") or {})
        spoken = str(dialogue.get("text") or "").strip()
        if spoken and store.observe(spoken, label, int(game.get("time_of_day") or 0)):
            runlog.agent("observation", day=label, time=game.get("time_of_day"), text=spoken)
            runlog.history_line(f"\n- **{time.strftime('%m-%d %H:%M')}** 游戏说：{spoken[:120]}\n")
            log(f"[观察] 游戏说：{spoken[:90]}")

        if session_finished(day_start, game):
            log(f"[day] COMPLETE - day {day_start.get('day_of_month')} -> {game.get('day_of_month')}")
            return "day_complete", label, step

        # An event flag with nothing on screen is abandoned state, not a cutscene: the game will
        # never clear it on its own, and every action fails while it is set. A real cutscene always
        # has dialogue, a menu, or a choice, so those are left alone.
        ui_now = state.get("ui") or {}
        if (not game.get("is_player_free")
                and (game.get("blocked_reason") or "") == "cutscene/event playing"
                and not spoken
                and not ui_now.get("menu_name")):
            # NOT a step counter. An event is full of moments with nothing on screen - a fade, the
            # camera panning, an NPC walking into shot - and a two-step counter cleared the event
            # during exactly those. That is how a cutscene "vanished, it never played at all".
            # Time is the only honest measure here, and it has to be a long time.
            if stuck_since == 0.0:
                stuck_since = time.monotonic()

            if time.monotonic() - stuck_since > STUCK_EVENT_S:
                released = client.action("clear_event")
                log(f"[恢复] 事件标志卡了 {STUCK_EVENT_S:.0f} 秒且屏幕上什么都没出现过 "
                    f"-> 当作残局清掉: {released.get('detail')}")
                runlog.agent("run", event="cleared_abandoned_event", day=label, step=step,
                             detail=str(released.get("detail")))
                stuck_since = 0.0
                continue
        else:
            stuck_since = 0.0

        # The supervisor, thought about while the farmer plays instead of in front of it.
        #
        # The scheduled review runs on the fast brain; the one that fires because the farmer is
        # genuinely stuck is a judgement call and goes to the reasoning brain - and that call was
        # taking 13.2 seconds, measured, in front of the audience, between two actions. Its answer is
        # a directive for the next few steps, not something that has to exist this instant, so it is
        # worked out in the background and applied when it lands. The step never waits for it.
        verdict = pending_review.take() if pending_review is not None else None
        if verdict is not None:
            pending_review = None

        if verdict is None and pending_review is None and supervisor.should_review(step, history):
            pending_review = anticipate.Review(supervisor, state, history, step, day_start)

        if verdict is not None:
            directive = (verdict.get("directive") or "").strip() or None
            note = str(verdict.get("note") or "")
            runlog.agent("supervisor", day=label, step=step, status=verdict.get("status"),
                         note=note, directive=directive or "")
            log(f"[监工] status={verdict.get('status')}" + (f" - {note}" if note else "")
                + (f" | directive: {directive}" if directive else ""))
            if verdict.get("status") == "done":
                return "supervisor_done", label, step

            # A directive means "stop what you are doing" - drop the rest of the queue.
            queued.clear()

        clock.mark("supervisor")

        # One model call per queue, not per action. The queue is what makes this play at a human
        # pace: a person does not re-decide between walking somewhere and opening the door.
        #
        # And if a decision was already being thought about while the last action ran (see
        # anticipate.py), it costs nothing at all - it is taken here, and only if the world it was
        # decided for is the world we are actually in.
        if queued:
            llm_s = 0.0
        else:
            decision, llm_s, why = anticipate.take(spec_holder, state)

            if decision is not None:
                actions = streamer.extract_actions(decision)
                first = actions[0] if actions else {}
                if not anticipate.still_valid(str(first.get("action") or ""), first, state):
                    decision = None
                    why = "它想好的那一步在真实世界里不成立"

            if decision is None and why != "没有抢跑的决策":
                log(f"[抢先] 丢掉一个抢跑的决策（{why}）")

            if decision is None:
                started = time.perf_counter()
                decision = streamer.decide(state, history, directive)
                llm_s = time.perf_counter() - started
            directive = None
            queued = streamer.extract_actions(decision)
            batch_reason = str(decision.get("reason", ""))

            # A dead brain must be loud. The fallback used to hand back an ordinary-looking "scan",
            # so a model that had stopped answering looked exactly like an agent choosing to look
            # around - for eight turns, until the day was gone.
            if streamer.last_error:
                log(f"[脑死] 模型不可用（连续第 {streamer.consecutive_failures} 次）：{streamer.last_error}")
                runlog.agent("brain", day=label, step=step, event="model unavailable",
                             error=streamer.last_error,
                             consecutive=streamer.consecutive_failures)

            # The system watching its own body: a model that is down, or one that answers so slowly
            # that every round is one action, is a defect - and it looks exactly like the game being
            # stuck, so it has to be said out loud rather than discovered by a person watching.
            if brainwatch is not None:
                brainwatch.observe(llm_s, failed=bool(streamer.last_error))

            if len(queued) > 1:
                log(f"[思考] {batch_reason[:70]}  -> {len(queued)} 步")

        item = queued.pop(0)
        clock.mark("think")

        # What the model worked out while deciding this turn - the game's rules in its own words. It
        # rides along with the decision it is already making, so learning costs no extra call.
        try:
            learned = decision.get("learned") if isinstance(decision, dict) else None
            if not isinstance(learned, dict) and queued:
                # The model sometimes puts it inside the first action instead of at the top of the
                # reply. That is a shape difference, not a failure - take it from there rather than
                # losing what it worked out (and rather than passing it to the bridge as an argument).
                learned = {k: v for k, v in queued[0].items() if k != "action"} or None
                if isinstance(learned, dict):
                    learned = learned.get("learned")
                    queued[0] = {k: v for k, v in queued[0].items() if k != "learned"}
            if isinstance(learned, dict):
                world.learn(str(learned.get("about") or ""), str(learned.get("action") or ""),
                            str(learned.get("result") or ""), str(learned.get("note") or ""))
        except Exception as exc:
            log(f"[世界] 记不下来这次学到的：{exc}")

        action = str(item.get("action") or "noop").strip()
        if action == "sleep":
            sleep_attempts += 1

        # Arguments belong at the top level, but tolerate a model that nests them.
        nested = item.get("args")
        args: dict = dict(nested) if isinstance(nested, dict) else {}
        args.update({k: v for k, v in item.items() if k not in ("action", "reason", "args")})

        # --- learning: look at the target, let experience correct the action, then judge what
        # actually happened. All of this is cheap HTTP; it never costs an extra model call.
        tx, ty = args.get("x"), args.get("y")
        tracking = (action in TILE_ACTIONS or action == "go_to") and isinstance(tx, int) and isinstance(ty, int)
        before = observe_tile(client, tx, ty) if tracking else None
        kind = (before or {}).get("kind")

        original_action, original_args = action, dict(args)
        action, args, note = learning.gatekeep(action, args, kind)
        if note:
            runlog.agent("gatekeeper", day=label, step=step, action=action, args=args,
                         kind=kind, note=note)
            log(f"[守门] {note}")

            # A note used to be advice nobody read: it went to the log, the action ran anyway, and
            # the model was never told - so it repeated it, every step, for a whole day. So the note
            # goes into the history the model reads.
            #
            # But the note does NOT stop the action any more, and that reversal is the important one.
            # Blocking it for the rest of the day sounds like safety and is the opposite: the guard
            # blocks on an action+argument pair with no knowledge of why it failed, and the situation
            # changes - the shop opens at nine, the farmer moves next to the door, the queue empties.
            # It blocked `menu_click forSaleButtons.0` so a whole day could not buy a seed, and it
            # blocked `enter Farm` so a whole day could not go home. A rule that can only ever say no
            # is not a guard, it is a wall.
            #
            # What actually stops a person repeating a failure is that they are told, in plain words,
            # what happened last time - which is what the note is for.
            history.append(f"YOU HAVE ALREADY TRIED THIS AND IT DID NOT WORK - {note}. "
                           f"Think about what was different this time before you do it again.")
            store.log_step(label, f"- step {step}: 重复提醒（仍然执行） - {note}")
            log(f"[守门] 提醒已给（仍会执行）：{note[:60]}")

        # A prescribed precondition is enforced here, by the system, not remembered by the model.
        # This is the difference between "the doctor found out that a watering can has to be full"
        # and "the farmer actually fills it before watering".
        if prescriber is not None and prescriber.unmet(action, state, args):
            helper = prescriber.satisfy(action, state, args)
            if helper:
                runlog.agent("prescription", day=label, step=step, action=action,
                             satisfied_with=helper)
                time.sleep(0.4)

        attempt_entry = learning.remember_attempt(action, args)

        started = time.perf_counter()
        result = client.action(action, **args)
        clock.mark("dispatch")

        # Think while the farmer acts. The next step is decided against the world this action is
        # expected to leave - a walk lands on its tile, `enter` lands on the warp's target, a look
        # changes nothing - and it is adopted only if that world came true AND the action it wants
        # still makes sense. Skipped when the next step is already queued (that one is free already).
        spec_holder.clear()
        if not queued and streamer is not None:
            predicted = anticipate.predict(state, action, args)
            if predicted is not None:
                spec_holder["spec"] = anticipate.Speculation(streamer, predicted, history, directive)

        # Watch the world while it happens. The blind waits this replaces polled the state once a
        # second and looked at a single boolean, so a farmer held by a greeting dialogue waited out
        # the whole five-minute deadline while every agent sat idle.
        #
        # A REFUSED action is not watched at all. It moved nothing by definition, so following it can
        # only cost the settle timeout - and those were the worst stalls in the stream: 4 seconds for
        # a refused interact_at, 12.5 for a refused exit_building, every one of them pure delay.
        shared_budget = max(10.0, deadline - time.monotonic()) if deadline else 0.0

        # Actions that change nothing by definition are not followed either. `noop` was being watched
        # for its full settle timeout - sixty seconds of the farmer standing still, on a live stream,
        # waiting for a world change the witness itself declares cannot happen. `sleep` and the
        # character-creation menu steps are the exceptions: those really are in progress.
        followable = action not in INFORMATIONAL or action in SLOW_INFORMATIONAL

        if result.get("ok") and followable:
            watch = watcher.watch(
                action, args, state,
                day=label, step=step,
                budget_s=shared_budget,
                timeout_s=settle_timeout(action, args) or 60.0,
                progress=(lambda s: witness.progress_value(action, s)) if action in CHORE_ACTIONS else None,
            )
        else:
            try:
                settled = client.state()
            except Exception:
                settled = state
            watch = WatchResult(state=settled, stopped="refused")

        after_state = watch.state
        clock.mark("watch")

        if watch.stopped not in ("done",):
            log(f"[守望] {action} 提前结束：{watch.stopped}"
                + (f"（处置：{', '.join(watch.remedies)}）" if watch.remedies else "")
                + (f" | 判断：{watch.claim}" if getattr(watch, "claim", "") else ""))
            if watch.remedies:
                result = dict(result)
                result["detail"] = f"{result.get('detail', '')} | 守望者处置: {', '.join(watch.remedies)}"

        # A macro reports what it *queued*, not what it *did*. The real outcome - how many tiles it
        # finished and how many it gave up on - only exists once the chore has stopped, and it arrives
        # in `chore_last`.
        #
        # But `chore_last` is ONE slot that anything can leave a value in, so reading it without asking
        # "is this mine?" pins somebody else's result onto this action: `refill` came back reporting
        # till_area's "5 tiles, 0 gave up", and the AI then reasoned about a failure that never
        # happened, in the wrong action, on the wrong evidence. (`refill` is not even a macro over a
        # patch - it is one swing at water - yet it was in the list that reads this slot.)
        #
        # It is this action's outcome only if it MOVED during this action. Same rule as everywhere else
        # in this project: a value that did not change is not evidence of anything.
        if action in CHORE_ACTIONS:
            outcome = str(after_state.get("chore_last") or "").strip()
            was = str((state or {}).get("chore_last") or "").strip()

            if outcome and outcome != was:
                result = dict(result)
                result["queued"] = result.get("detail", "")
                result["detail"] = outcome

        action_s = time.perf_counter() - started

        total_llm += llm_s
        total_action += action_s

        # Judge by whether the world changed, not by what the action claimed. Every action class has
        # a witness; when it does not, that is recorded as no-verdict rather than as success.
        if tracking and action in TILE_ACTIONS:
            time.sleep(0.25)       # let the swing land before reading the tile back
        after = observe_tile(client, tx, ty) if (tracking and action in TILE_ACTIONS) else None
        worked, evidence = witness.judge(action, args, state, after_state, before, after, result)

        # Hand the verdict back to the attempt it judged: the loop check asks what the repeats DID
        # (see LearningStore.is_loop), and until now it could only ask how many there were.
        learning.note_attempt_outcome(attempt_entry, worked)
        clock.mark("judge")

        # The ruler reading: the world moved, so reset it - with what moved, so "last_change" is a fact
        # and not a feeling.
        if worked is True:
            changed_step = step
            changed_at = time.monotonic()
            changed_note = f"step {step}: {action} -> {evidence[:90]}"

        # The player's own notes on how this game behaves, written from what just happened.
        #
        # It already had the game's words once: the witness recorded "上锁了……（营业时间：9:00~21:00）"
        # and nothing learned it, so the same door was knocked on seventeen times. This is the line
        # that turns an attempt into knowledge, and it is deliberately the KIND of thing (a shop door)
        # rather than the tile, because a tile is this morning and a rule of the game is forever.
        world.observe(action, args, after_state, worked,
                      says=_spoken_by_the_game(after_state, result))

        # Reconcile the two verdicts. "提前结束: timeout" and "OK" used to be printed one line apart,
        # and only the OK was handed to the agent. If the watcher had to step in AND the world did not
        # move, this was not a success on any reading, whatever the action claimed. (A walk that ran
        # out of budget but did move the farmer still counts - the witness saw the change.)
        if watch.stopped not in ("done",) and worked is False and result.get("ok"):
            result = dict(result)
            result["ok"] = False
            result["reason_code"] = (result.get("reason_code")
                                     or f"WATCHER_{str(watch.stopped).upper()}")
            result["detail"] = (
                f"{result.get('detail', '')} | 守望者提前结束（{watch.stopped}）"
                f"且世界没有变化：{evidence}"
            )

        # `state` here is the pre-action picture, so re-read before the before/after are mixed up.
        outcome_kind = kind or ("area" if action in CHORE_ACTIONS else None)

        if worked is not None and outcome_kind not in NOT_ACTIONABLE:
            learning.record(outcome_kind or "area", action, worked)
            runlog.agent("learning", day=label, step=step, kind=outcome_kind, action=action,
                         worked=worked, evidence=evidence,
                         ok=learning.rate(outcome_kind or "area", action).ok,
                         total=learning.rate(outcome_kind or "area", action).total)
            if worked:
                learning.clear_failures()
            else:
                learning.remember_failure(action, args)

        if worked is not None:
            runlog.agent("witness", day=label, step=step, action=action, worked=worked,
                         evidence=evidence, ok=bool(result.get("ok")),
                         detail=str(result.get("detail", ""))[:160],
                         # Carry what the watcher believed onto the witness record, so the claim and
                         # the world-change verdict that can contradict it sit on the same line.
                         watch_claim=getattr(watch, "claim", ""))

        # Verification half of the loop: did the prescribed fix actually fix it?
        if prescriber is not None:
            prescriber.note_outcome(action, worked)

        # A failure invalidates the rest of the queue: it was planned against a world that turned
        # out not to be true. Re-decide immediately rather than replaying stale intentions.
        if not result.get("ok"):
            if queued:
                log(f"[思考] {action} 失败，丢弃队列剩下的 {len(queued)} 步，重新决策")
            queued.clear()

        detail = str(result.get("detail", ""))
        history.append(f"{action} {json.dumps(args, ensure_ascii=False)} -> ok={result.get('ok')} {detail}")

        # The hand, on every step line.
        #
        # A shop will not close while it holds an item, and this transcript is what the supervisor, the
        # referee and the doctor reason over - so "there is a seed in your hand" has to be in the
        # HISTORY, not only in the live payload. Otherwise the day's own record says the farmer had
        # empty hands and the menu refused to shut for no reason, and the复盘 learns the wrong lesson.
        hand = str((after_state.get("ui") or {}).get("held_item") or "")
        cursor = str(((after_state.get("player") or {}).get("cursor_item") or {}).get("name") or "")
        hand_note = f" [hand: {hand or cursor}]" if (hand or cursor) else ""

        store.log_step(
            label,
            f"- step {step}: `{action}` {json.dumps(args, ensure_ascii=False)} "
            f"-> ok={result.get('ok')} rc={result.get('reason_code') or '-'} {detail}{hand_note}",
        )

        reason = str(item.get("reason") or batch_reason)[:45]
        flag = "OK " if result.get("ok") else "ERR"
        # On failure the detail is the whole point - show it instead of the reason.
        tail = reason if result.get("ok") else f"! {detail[:80]}"
        runlog.agent("streamer", day=label, step=step, action=action, args=args,
                     reason=str(item.get("reason") or batch_reason), ok=bool(result.get("ok")),
                     reason_code=str(result.get("reason_code") or ""), detail=detail,
                     kind=kind, llm_s=round(llm_s, 2), act_s=round(action_s, 2),
                     directive=directive or "",
                     worked=worked, evidence=evidence)
        log(f"[主播 {step:>3}] {action:<15} {json.dumps(args, ensure_ascii=False)[:44]:<44} {flag} "
            f"llm {llm_s:4.1f}s act {action_s:4.1f}s  {tail}")

        # The number the audience feels. Printed whenever a step was slow enough to notice, with the
        # worst parts named - a cost nobody can see is a cost nobody fixes.
        timing = clock.report()
        if timing:
            log(f"[步时] step {step}: {timing}")
        clock = StepClock()

    elapsed = total_llm + total_action + total_watch
    log(f"[perf] steps={max_steps} total={elapsed:.0f}s | llm={total_llm:.0f}s "
        f"action={total_action:.0f}s supervisor={total_watch:.0f}s")
    _day_never_ended(client, day_start, sleep_attempts, label, log, runlog)
    return "steps_exhausted", label, max_steps


# ---------------------------------------------------------------------------

def run_calibration(supervisor: SupervisorAI, store: "memory_mod.MemoryStore", known: str) -> dict:
    """Measure how familiar the streamer is before it plays, against the catalogued situations."""
    questions = store.load_calibration()
    if not questions:
        return {}

    result = supervisor.calibration(questions, known)
    if result.get("error"):
        log(f"[cal ] familiarity check skipped: {result['error']}")
        return {}

    answers = {
        str(a.get("id")): str(a.get("action", "")).strip().lower()
        for a in (result.get("answers") or [])
        if isinstance(a, dict)
    }

    right = 0
    missed: list[tuple[str, str, str]] = []

    for question in questions:
        qid = str(question.get("id", ""))
        want = str(question.get("expect_action", "")).strip()
        got = answers.get(qid, "")

        if want and (want.lower() in got or (got and got in want.lower())):
            right += 1
        else:
            missed.append((qid, want, got or "(none)"))

    total = len(questions)
    log(f"[cal ] familiarity check: {right}/{total} ({right / total:.0%})")
    for qid, want, got in missed:
        log(f"[cal ]   MISS {qid}: expected '{want}', answered '{got}'")

    return {"right": right, "total": total, "missed": missed}


def _spoken_by_the_game(state: dict, result: dict) -> str:
    """The game's own words about what just happened, if it said any.

    A shut door prints its opening hours; a shop tells you what you just bought; a refusal often says
    why. That sentence is the single most valuable thing a step produces - it is the game teaching the
    rules - and it used to be written into a log file that nothing ever read back.
    """
    dialogue = ((state.get("ui") or {}).get("dialogue") or {})
    spoken = str(dialogue.get("text") or "").strip()
    if spoken:
        return spoken

    detail = str((result or {}).get("detail") or "")
    # The bridge quotes the game inside its own sentence: ... ('上锁了……（营业时间：9:00~21:00）')
    match = re.search(r"'([^']{4,})'", detail)
    return match.group(1).strip() if match else ""


def observe_tile(client: StateClient, x: int, y: int) -> dict | None:
    """Read one tile. Cheap HTTP, never an LLM call - this is how outcomes get measured."""
    result = client.action("observe", x=x, y=y)
    data = result.get("data")
    return data if isinstance(data, dict) else None


def enforce_day_end(client: StateClient) -> None:
    """Try to end the day the way a player would: go to bed.

    A day cannot be rolled over directly any more - `new_day` is not a thing a player can do, and it
    used to be the routine that left the game permanently unable to act when it was called on top of
    a live cutscene. If sleeping is not possible right now, the honest end to the day is to keep
    playing: the clock runs, and at 2am the game puts the farmer to bed itself, exactly as it would
    for anyone else.
    """
    result = client.action("sleep")
    if result.get("ok"):
        log(f"[run ] 去睡觉 -> {result.get('detail')}")
        return

    log(f"[run ] 现在睡不了（{result.get('detail')}）—— 让时间继续走，2 点游戏会自己送他上床")


def write_progress(path: Path, header: str, rows: list[str]) -> None:
    """Keep a human-readable progress file so the morning check is a single read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n\n" + "\n".join(rows) + "\n", encoding="utf-8")


def reflect(supervisor: SupervisorAI, store: "memory_mod.MemoryStore", known: str,
            label: str, runlog: RunLog) -> None:
    """Ask the supervisor to turn today's transcript into facts and lessons for next time."""
    journal = store.read_journal(label)
    if not journal.strip():
        log("[mem ] nothing was logged today; skipping reflection")
        return

    log(f"[mem ] reflecting on {label} ({len(journal)} chars of transcript)...")
    result = supervisor.reflect(journal, known)

    if result.get("error"):
        log(f"[mem ] reflection failed: {result['error']}")
        return

    report = store.merge_facts(
        result.get("new_facts") or [],
        [str(x) for x in (result.get("confirm_facts") or [])],
        result.get("contradict_facts") or [],
    )
    lessons = store.append_lessons([str(x) for x in (result.get("lessons") or [])])

    summary = str(result.get("summary") or "")
    if summary:
        log(f"[mem ] {summary}")

    runlog.agent("reflection", day=label, summary=summary,
                 facts_added=report["added"], facts_updated=report["updated"],
                 facts_dropped=report["dropped"], facts_total=report["total"],
                 lessons=result.get("lessons") or [], new_facts=result.get("new_facts") or [])
    runlog.line(f"\n### 复盘 {label}\n")
    runlog.line(f"- 总结：{summary}")
    runlog.line(f"- 事实：+{report['added']} ~{report['updated']} -{report['dropped']}（共 {report['total']}）")
    runlog.line(f"- 教训：+{lessons}")
    for item in (result.get("new_facts") or [])[:6]:
        if isinstance(item, dict) and item.get("claim"):
            runlog.line(f"  - 新事实：{item['claim']}")

    log(f"[mem ] facts: +{report['added']} ~{report['updated']} -{report['dropped']} "
        f"(total {report['total']}) | lessons: +{lessons}")


def run_doctor(client: StateClient, ledger: DefectLedger, doctor: Doctor, prescriber: Prescriber,
               store: "memory_mod.MemoryStore", runlog: RunLog, state: dict, limit: int = 2) -> int:
    """Find out why the things we could not fix are failing, and change something about it.

    This is the half of the loop that used to be a person reading logs. It runs on its own, only on
    defects that have repeated (so it is cheap), and every conclusion it reaches is applied and then
    checked against the world.
    """
    pending = [d for d in ledger.for_owner("agent") if d.status == "open" and d.count >= 2]
    if not pending:
        return 0

    done = 0
    for defect in sorted(pending, key=lambda d: -d.cost_s)[:limit]:
        verdict = doctor.diagnose(defect, state, context=defect.detail)

        if verdict.get("error"):
            log(f"[医生] {defect.key}: 诊断失败（{verdict['error']}）")
            runlog.agent("doctor", defect=defect.key, error=str(verdict["error"])[:200])
            continue

        cause = str(verdict.get("cause") or "").strip()
        kind = str(verdict.get("class") or "").strip()
        log(f"[医生] {defect.key}: [{kind}] {cause}")

        applied = prescriber.apply(defect, verdict, state)

        runlog.agent("doctor", defect=defect.key, cause=cause, defect_class=kind,
                     prescription=verdict.get("prescription"), check=verdict.get("check"),
                     applied=applied)
        runlog.line(f"- [医生] `{defect.key}` → [{kind}] {cause} → {applied}")
        runlog.history_line(f"\n- **{time.strftime('%m-%d %H:%M')}** 医生：{cause} → {applied}\n")
        done += 1

    return done


def start_show(client: StateClient, runlog: RunLog, store: "memory_mod.MemoryStore",
               persona: str, speaker: str) -> "program_mod.Program | None":
    """Bring up the part of the show that belongs to the playing process.

    Which is: almost none of it. The commentary, the voice and the chat now live in their own
    process (`agent/commentary_daemon.py`) and hear about the game through `logs/agents/bus.jsonl`.
    That separation is the point - editing a sentence used to cost a restart of the whole game loop,
    which kills the day in progress. This process keeps only the audience's wish list, because the
    planner is the one who decides whether a wish is granted.
    """
    try:
        show = program_mod.Program(
            client=client,
            runlog=runlog,
            store=store,
            persona=persona,
            speaker=speaker or "主播",
            log=log,
            role="agent",
        )
        show.start()
        return show
    except Exception as exc:
        log(f"[节目] 启动失败（{type(exc).__name__}: {exc}）——退化为纯玩游戏")
        return None


def run_forever(args: argparse.Namespace) -> int:
    """Unattended loop: play day after day, recover from anything, and keep a progress file."""
    started = time.time()
    deadline = started + args.max_hours * 3600
    store = memory_mod.MemoryStore()
    learning = LearningStore(memory_mod.PROJECT_ROOT / "memory" / "learning.json", log=log)
    runlog = RunLog(memory_mod.PROJECT_ROOT, log=log)
    ledger = DefectLedger(memory_mod.PROJECT_ROOT, log=log)

    runlog.line(f"- 开始时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    runlog.line(f"- 限制：最多 {args.max_hours} 小时 / {args.max_days} 个循环 / 每循环 {args.steps} 步")
    runlog.line("- 存档：AI 自己的档（不会碰玩家的存档）")
    runlog.line("- 宏观目标：尽可能多、尽可能好地完成每一天（通关需要 300~500 游戏日）")
    runlog.line("- 逐 agent 原始日志：`logs/agents/{planner,streamer,supervisor,referee,learning,gatekeeper,reflection}.jsonl`")
    runlog.line("")
    runlog.table_header(["游戏日", "分数", "评级", "步数", "裁判点评"])

    cycle = 0
    days_done = 0
    show = None

    with StateClient(args.base_url) as client, LlmClient("llm") as smart, LlmClient("fast") as fast:
        log(f"[llm ] fast brain = {fast.endpoint.model} (every step) | "
            f"smart brain = {smart.endpoint.model} (plan / judge / reflect)")
        ai_save = load_ai_save()
        known = store.cheat_sheet()
        streamer = StreamerAI(fast, args.goal, persona=(ai_save or {}).get("persona", ""),
                              memory_block=known, world=world_mod.current(log=log))
        supervisor = SupervisorAI(fast, escalate=smart)
        planner = PlannerAI(smart)
        referee = RefereeAI(smart)
        watcher = Watcher(client, ledger=ledger, runlog=runlog, log=log)
        pipeline = runner.Pipeline(client, ledger, runlog=runlog, log=log)
        doctor = Doctor(smart, client, log=log, actions=witness.ACTION_NAMES)
        prescriber = Prescriber(client, ledger, learning, store, log=log)
        outcomes = OutcomeWatch(ledger=ledger, log=log)
        brainwatch = BrainWatch(ledger=ledger, log=log)

        # Everything the agents say also goes on screen, for whoever is watching.
        runlog.attach_overlay(
            lambda batch: client.action("overlay", lines=batch)
        )
        # The room, mirrored into the game on the right, where the audience is actually looking.
        # The room, read straight off the danmaku log the commentary process writes. Binding to the
        # FILE rather than to a room means switching rooms needs no handling at all.
        chatfeed = ChatFeed(memory_mod.PROJECT_ROOT / "logs" / "agents" / "chat.jsonl",
                            lambda batch: client.action("chat", lines=batch), log=log)
        chatfeed.start()
        runlog.feed("run", "agents attached - watch this panel")
        runlog.feed("outcome", outcomes.summary())

        # And the show itself: commentary, voice, and the room. Separate threads with their own
        # queues - the playing loop publishes events and never waits for any of it.
        show = start_show(client, runlog, store, streamer.persona,
                          str((ai_save or {}).get("character_name") or ""))

        def make_own_save() -> dict | None:
            """First run ever, or after the record was lost: the streamer designs its own character.

            This is the missing half of the pipeline. Without it, losing config/ai_save.json does
            not mean "make a new one", it means "park at the title screen until someone notices".
            """
            created = bootstrap(client, streamer, None, force_new=True,
                                max_iterations=args.max_boot)

            if created:
                streamer.persona = created.get("persona", "") or streamer.persona
                if created.get("start_goal"):
                    streamer.goal = created["start_goal"]

                log(f"[boot] 新生角色：{created.get('name')} @ {created.get('farm_name')}")

                # The commentary started before the character existed; teach it who it now is.
                if show is not None:
                    show.refresh_persona(streamer.persona, str(created.get("name") or ""))

            return load_ai_save()

        while time.time() < deadline and cycle < args.max_days:
            cycle += 1
            elapsed = time.time() - started
            log(f"\n{'=' * 72}\n[run ] cycle {cycle} | elapsed {elapsed / 60:.0f} min | "
                f"days completed {days_done}\n{'=' * 72}")

            # Capability audit: work out which actions are not doing anything and stop using them.
            # This is the difference between "burnt another night on the same wall" and "the wall
            # got named, quoted and removed from the action set".
            audited = gaps.analyse(
                gaps.load_journal_events(memory_mod.PROJECT_ROOT / "memory" / "journal")
                + gaps.load_agent_events(memory_mod.PROJECT_ROOT / "logs" / "agents", "streamer",
                                         run_id=runlog.run_id)
            )
            try:
                (memory_mod.PROJECT_ROOT / "logs" / "capability-gaps.md").write_text(
                    gaps.report(audited), encoding="utf-8")
            except OSError:
                pass

            for gap in audited:
                if gap.verdict not in ("BROKEN", "LYING", "NOOP"):
                    continue

                reason = (f"{gap.verdict}: {gap.attempts} 次尝试 / 报 OK {gap.ok_count} 次 / "
                          f"空转 {gap.noop} 次")

                # The detector no longer decides an action's fate. Blacklisting used to be the end
                # of this loop, and it was wrong as often as it was right: it banned `use_tool_at`
                # and `enter` - both fine - while the empty watering can behind the failures went
                # untouched, and the agent lost two things it needed. It now raises a defect, and
                # the doctor works out why.
                # LYING is not the agent's fault, and filing it there sent the fix the wrong way. The
                # action reported success while the world never moved: that is the bridge's own claim
                # being untrue. Under "agent" the remedy became "don't repeat that click" - which can
                # never repair a click that does not work, and left the actual fault (a click with no
                # release, a payload with no cursor field) unowned and unfixed. A claim that is not
                # true belongs to whoever made it.
                owner = "developer" if gap.verdict == "LYING" else "agent"

                ledger.raise_defect(
                    f"gap:{gap.action}",
                    f"`{gap.action}` 被判定 {gap.verdict}",
                    owner,
                    detail=reason,
                    evidence="; ".join(gap.examples[:2]) if gap.examples else "",
                )
                runlog.agent("gaps", action=gap.action, verdict=gap.verdict,
                             attempts=gap.attempts, ok_count=gap.ok_count, noop=gap.noop,
                             reasons=gap.reasons, examples=gap.examples)
                runlog.line(f"- [缺口] `{gap.action}` **{gap.verdict}** — {reason}")

            # The watcher is the only component that decides with no opponent, and until now nobody
            # judged it. This reads back its own log and the witness, works out which interventions
            # the world contradicts, and files them the same way the gap detector does. A verdict
            # that is not true belongs to whoever made it, so these go to `developer`.
            try:
                watch_audit.run(memory_mod.PROJECT_ROOT, ledger=ledger, run_id=runlog.run_id, log=log)
            except Exception as exc:
                log(f"[裁决] 守望者审计失败: {type(exc).__name__}: {exc}")

            # The witness asks "did the world change". It cannot ask "could a player do this", and
            # a teleport changes the world perfectly. So the action set itself is audited, every
            # cycle, against the one rule that matters for a stream: only what a person at the
            # keyboard could do. Findings are raised as defects, not printed into a log to be
            # discovered by a human later.
            try:
                decls = authenticity.load_declarations(memory_mod.PROJECT_ROOT)
                findings = authenticity.audit(memory_mod.PROJECT_ROOT / "mod", decls,
                                              catalogue=prompts.ACTION_CATALOGUE)
                (memory_mod.PROJECT_ROOT / "logs" / "authenticity.md").write_text(
                    authenticity.report(findings, decls), encoding="utf-8")

                for key, title, owner, detail in authenticity.as_defects(findings):
                    ledger.raise_defect(key, title, owner, detail=detail)
                    log(f"[合法性] [{owner}] {title} - {detail}")
            except Exception as exc:
                log(f"[合法性] 审计失败: {type(exc).__name__}: {exc}")

            try:
                # Every stage before a day is played now has an owner, a retry, and a record.
                # "Could not load the save" used to be an exception nobody wrote down - and "there
                # is no save yet" used to be a wall with no door in it.
                ai_save = pipeline.ensure(ai_save or load_ai_save(), create=make_own_save)
            except runner.StageFailure as failure:
                log(f"[流水线] {failure} —— 记录进台账，等 60 秒重试")
                runlog.agent("pipeline", stage=failure.stage, event="stage failed",
                             detail=failure.detail)
                ledger.save()
                time.sleep(60)
                continue
            except Exception as exc:
                log(f"[流水线] 意外错误 {type(exc).__name__}: {exc}")
                runlog.agent("pipeline", stage="?", event="crash",
                             error=type(exc).__name__, message=str(exc))
                ledger.save()
                time.sleep(30)
                continue

            try:
                start_state = client.state()
                label_hint = memory_mod.day_label(start_state.get("game") or {})

                # The room's wishes go into the plan, and the plan is what decides. A suggestion is
                # never an instruction - but it is read, and it is answered.
                viewers = show.viewer_block() if show else ""
                plan = planner.plan_day(start_state, persona=streamer.persona, memory=known,
                                        goal=streamer.goal, recent_days=store.recent_days(),
                                        viewers=viewers)

                if show and viewers:
                    taken = show.take_suggestions()
                    runlog.agent("audience", day=label_hint,
                                 suggestions=[f"{item['user']}: {item['text']}" for item in taken],
                                 focus=plan.get("focus", ""))
                    log(f"[观众] 本轮收到 {len(taken)} 条建议，已交给规划：{plan.get('focus', '')}")

                goals = plan.get("plan") or []
                runlog.agent("planner", day=label_hint, focus=plan.get("focus", ""), goals=goals,
                             notes=plan.get("notes", ""), sleep_by=plan.get("sleep_by"))
                log(f"[规划] {plan.get('focus', '')}")
                for goal in goals:
                    log(f"[规划]   - {goal}")
                streamer.set_plan(plan)

                outcome, label, steps = play(client, streamer, supervisor, args.steps, store,
                                             learning, runlog, budget_s=args.day_seconds,
                                             watcher=watcher, prescriber=prescriber, brainwatch=brainwatch)

                # Never let the run sit on the same day forever.
                if outcome in ("steps_exhausted", "budget_exhausted"):
                    before_day = (client.state().get("game") or {}).get("day_of_month")
                    log(f"[run ] {outcome} - ending the day")
                    runlog.agent("run", event="forced_day_end", day=label, outcome=outcome)
                    enforce_day_end(client)

                    # Wait for the new day to actually appear rather than guessing at a fixed sleep.
                    day_deadline = time.monotonic() + 45
                    while time.monotonic() < day_deadline:
                        day_now = (client.state().get("game") or {}).get("day_of_month")
                        if day_now and day_now != before_day:
                            break
                        time.sleep(2)

                end_state = client.state()

                # NOT `started` - that name already means "when this run began", and reusing it here
                # silently turned the run clock into nonsense (it printed "elapsed 29845464 min").
                referee_at = time.perf_counter()
                verdict = referee.score_day(plan, store.read_journal(label),
                                            compact_state(start_state), compact_state(end_state), steps)
                smart_s = time.perf_counter() - referee_at

                # The smart brain is a different endpoint call from the streamer's, and it was never
                # watched - so the planner, the referee and the doctor could all be timing out while
                # the dashboard said everything was fine.
                if brainwatch is not None:
                    brainwatch.observe(smart_s, failed=verdict.get("score") is None)

                # A referee that could not answer is not "no score for today". The consequence is that
                # the day taught NOTHING: no verdict, no reflection, no lesson, and nobody told. That
                # is a defect, not a missing number in a log file.
                if verdict.get("score") is None:
                    ledger.raise_defect(
                        "referee:unavailable",
                        "裁判拿不到结果 —— 这一天的学习丢了",
                        "developer",
                        detail=str(verdict.get("why"))[:220],
                        cost_s=smart_s,
                    )
                    log(f"[裁判] 没有结果（{str(verdict.get('why'))[:100]}）—— 已记入台账")

                log(f"[裁判] score={verdict.get('score')} ({verdict.get('verdict')}) - {verdict.get('why')}")

                store.record_day({
                    "label": label, "date": time.strftime("%Y-%m-%d %H:%M"),
                    "score": verdict.get("score"), "verdict": verdict.get("verdict"),
                    "why": verdict.get("why"), "plan": goals, "focus": plan.get("focus", ""),
                    "completed": verdict.get("completed"), "missed": verdict.get("missed"),
                    "steps": steps, "outcome": outcome,
                })
                reflect(supervisor, store, known, label, runlog)

                known = store.cheat_sheet()
                streamer.memory_block = known
                days_done += 1

                # Is anything actually getting better? Nothing was watching this, which is how a
                # night of sixty game days could pass with the quest counter, the money and the day
                # scores all exactly where they started.
                outcomes.observe(label, end_state, verdict.get("score"))

                # Then find out why whatever is still broken is broken, and change something.
                run_doctor(client, ledger, doctor, prescriber, store, runlog, end_state)

                ledger.save()          # a defect must survive a crash to be worth writing down

                runlog.agent("referee", day=label, score=verdict.get("score"),
                             verdict=verdict.get("verdict"), why=verdict.get("why"),
                             completed=verdict.get("completed"), partial=verdict.get("partial"),
                             missed=verdict.get("missed"), steps=steps, outcome=outcome)
                runlog.table_row([label, verdict.get("score"), verdict.get("verdict"), steps,
                                  str(verdict.get("why", ""))[:180]])
                runlog.line(f"\n### {label} 计划\n")
                for goal in goals:
                    runlog.line(f"- {goal}")

            except Exception as exc:
                log(f"[run ] cycle {cycle} crashed: {type(exc).__name__}: {exc}")
                runlog.agent("run", event="crash", cycle=cycle, error=type(exc).__name__,
                             message=str(exc))
                runlog.table_row(["—", "—", "CRASH", "—", f"{type(exc).__name__}: {str(exc)[:130]}"])
                try:
                    game_process.stop()
                except Exception:
                    pass
                time.sleep(10)
                try:
                    game_process.launch()
                except Exception:
                    pass
                time.sleep(30)

    if show is not None:
        log(f"[节目] {show.stats()}")
        show.stop()

    stats = store.stats()
    ledger.save()

    log(f"\n[run ] finished: {days_done} day(s) in {(time.time() - started) / 60:.0f} min | "
        f"memory now {stats['facts']} facts / {stats['lessons']} lessons")
    log(f"[台账] 未结缺陷：{ledger.summary()} -> {ledger.report}")
    runlog.section("结束")
    runlog.line(f"- 完成 {days_done} 天，用时 {(time.time() - started) / 60:.0f} 分钟")
    runlog.line(f"- 记忆库：{stats['facts']} 条事实 / {stats['lessons']} 条教训 / {stats['journals']} 篇日记")
    runlog.line(f"- 学习层统计：{learning.summary() or '(空)'}")
    log(f"[run ] report: {runlog.report}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="主播AI + 监工AI for Stardew Valley")
    parser.add_argument("--steps", type=int, default=120, help="max decisions per day")
    parser.add_argument("--day-seconds", type=float, default=120.0,
                        help="wall-clock budget for one round; when it runs out the day is ended and "
                             "scored as it stands, so a bad round costs seconds rather than minutes")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--no-launch", action="store_true", help="do not start the game process")
    parser.add_argument("--new-game", action="store_true", help="force a brand new AI save")
    parser.add_argument("--max-boot", type=int, default=90, help="max menu steps during bootstrap")
    parser.add_argument("--forever", action="store_true",
                        help="unattended: play day after day, recovering from anything")
    parser.add_argument("--max-hours", type=float, default=9.0,
                        help="with --forever: stop after this many hours")
    parser.add_argument("--max-days", type=int, default=200,
                        help="with --forever: stop after this many day cycles")
    args = parser.parse_args()

    if args.forever:
        return run_forever(args)

    if not args.no_launch:
        started = game_process.ensure_running()
        log(f"[game] {'launched the game' if started else 'the game is already running'}")

    show = None

    with StateClient(args.base_url) as client:
        log("[api ] waiting for StateBridge...")
        if not client.wait_until_up():
            log("[api ] ERROR: the StateBridge API never became reachable")
            return 1
        log("[api ] StateBridge is up")

        with LlmClient("llm") as smart, LlmClient("fast") as fast:
            log(f"[llm ] fast brain = {fast.endpoint.model} (every step) | "
                f"smart brain = {smart.endpoint.model} (plan / judge / reflect)")

            ai_save = load_ai_save()
            if ai_save:
                log(f"[save] AI save on record: '{ai_save.get('character_name')}' @ '{ai_save.get('farm_name')}'")
            else:
                log("[save] no AI save yet - the streamer will create its own")

            store = memory_mod.MemoryStore()
            learning = LearningStore(memory_mod.PROJECT_ROOT / "memory" / "learning.json", log=log)
            runlog = RunLog(memory_mod.PROJECT_ROOT, log=log)
            stats = store.stats()
            log(f"[mem ] memory: {stats['facts']} facts ({stats['high_confidence']} high-confidence), "
                f"{stats['lessons']} lessons, {stats['journals']} journal(s)")

            known = store.cheat_sheet()

            streamer = StreamerAI(fast, args.goal, persona=(ai_save or {}).get("persona", ""),
                                  memory_block=known, world=world_mod.current(log=log))
            supervisor = SupervisorAI(fast, escalate=smart)

            # The audience layer, so a single day can be watched the way the stream will be.
            runlog.attach_overlay(lambda batch: client.action("overlay", lines=batch))
            show = start_show(client, runlog, store, streamer.persona,
                              str((ai_save or {}).get("character_name") or ""))

            log("[boot] driving menus to the AI's own save...")
            created = bootstrap(client, streamer, ai_save, args.new_game, args.max_boot)

            state = client.state()
            player = state.get("player") or {}
            if not (state.get("game") or {}).get("has_save"):
                log("[boot] ERROR: no save ended up loaded")
                return 2

            ai_save = load_ai_save() or {}
            expected = (ai_save.get("character_name") or "").strip()
            actual = (player.get("name") or "").strip()

            if expected and actual != expected:
                log(f"[guard] the loaded save belongs to '{actual}', but the AI is '{expected}'.")
                log("[guard] refusing to play - this keeps the player's saves untouched.")
                return 3

            if created:
                streamer.persona = created.get("persona", "")
                if created.get("start_goal"):
                    streamer.goal = created["start_goal"]

            log(f"[guard] OK - playing the AI's own save as '{actual}'")

            run_calibration(supervisor, store, known)

            # --- planner: set today's intention (one call per day, never per step) ---
            start_state = client.state()
            plan = PlannerAI(smart).plan_day(
                start_state,
                persona=streamer.persona,
                memory=known,
                goal=streamer.goal,
                recent_days=store.recent_days(),
                viewers=(show.viewer_block() if show else ""),
            )

            goals = plan.get("plan") or []
            if goals:
                log(f"[规划] {plan.get('focus', '')}")
                for goal in goals:
                    log(f"[规划]   - {goal}")
                if plan.get("notes"):
                    log(f"[规划]   note: {plan['notes']}")
            else:
                log(f"[规划] no plan for today ({plan.get('notes', '')})")
            streamer.set_plan(plan)

            outcome, label, steps_used = play(client, streamer, supervisor, args.steps, store,
                                              learning, runlog)
            log(f"[done] session ended: {outcome}")

            # --- referee: score the day, then the supervisor writes it up as memory ---
            end_state = client.state()
            verdict = RefereeAI(smart).score_day(
                plan,
                store.read_journal(label),
                compact_state(start_state),
                compact_state(end_state),
                steps_used,
            )

            log(f"[裁判] score={verdict.get('score')} ({verdict.get('verdict')}) - {verdict.get('why')}")
            for key in ("completed", "partial", "missed"):
                items = verdict.get(key) or []
                if items:
                    log(f"[裁判]   {key}: " + "; ".join(items))

            store.record_day({
                "label": label,
                "date": time.strftime("%Y-%m-%d %H:%M"),
                "score": verdict.get("score"),
                "verdict": verdict.get("verdict"),
                "why": verdict.get("why"),
                "plan": goals,
                "focus": plan.get("focus", ""),
                "completed": verdict.get("completed"),
                "missed": verdict.get("missed"),
                "steps": steps_used,
                "outcome": outcome,
            })

            reflect(supervisor, store, known, label, runlog)
            if show is not None:
                log(f"[节目] {show.stats()}")
                show.stop()
            return 0 if outcome in ("day_complete", "supervisor_done") else 4


if __name__ == "__main__":
    raise SystemExit(main())
