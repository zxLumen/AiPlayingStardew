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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import game_process  # noqa: E402
import gaps  # noqa: E402
import memory as memory_mod  # noqa: E402
import runner  # noqa: E402
import witness  # noqa: E402
from compact import compact_state  # noqa: E402
from defects import DefectLedger  # noqa: E402
from doctor import Doctor  # noqa: E402
from learning import NOT_ACTIONABLE, LearningStore  # noqa: E402
from outcomes import OutcomeWatch  # noqa: E402
from prescribe import Prescriber  # noqa: E402
from watcher import Watcher  # noqa: E402
from witness import TILE_ACTIONS  # noqa: E402
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


def play(client: StateClient, streamer: StreamerAI, supervisor: SupervisorAI,
         max_steps: int, store: "memory_mod.MemoryStore",
         learning: LearningStore, runlog: RunLog, budget_s: float = 0.0,
         watcher: Watcher | None = None,
         prescriber: "Prescriber | None" = None) -> tuple[str, str, int]:
    history: list[str] = []
    directive: str | None = None
    total_llm = total_action = total_watch = 0.0
    queued: list[dict] = []
    batch_reason = ""
    stuck_events = 0
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
    frozen = client.action("pause_time", paused=True)
    if frozen.get("ok"):
        log("[day] in-game clock frozen (the day ends when the streamer sleeps)")
    else:
        log(f"[day] could not freeze the clock: {frozen.get('detail')}")

    for step in range(1, max_steps + 1):
        # A day is a round, and a round that drags on teaches nothing. When the budget is gone the
        # day is ended and scored as it stands - a fast failure is a data point, a slow one is a loss.
        if deadline and time.monotonic() > deadline:
            log(f"[day] round budget ({budget_s:.0f}s) spent at step {step} - ending the day here")
            return "budget_exhausted", label, step - 1

        state = client.state()
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
            stuck_events += 1
            if stuck_events >= 2:
                released = client.action("clear_event")
                log(f"[恢复] 事件标志卡住、屏幕上没有内容 -> clear_event: {released.get('detail')}")
                runlog.agent("run", event="cleared_stuck_event", day=label, step=step,
                             detail=str(released.get("detail")))
                stuck_events = 0
                continue
        else:
            stuck_events = 0

        if supervisor.should_review(step, history):
            started = time.perf_counter()
            verdict = supervisor.review(state, history, step, day_start)
            total_watch += time.perf_counter() - started

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

        # One model call per queue, not per action. The queue is what makes this play at a human
        # pace: a person does not re-decide between walking somewhere and opening the door.
        if queued:
            llm_s = 0.0
        else:
            started = time.perf_counter()
            decision = streamer.decide(state, history, directive)
            llm_s = time.perf_counter() - started
            directive = None
            queued = streamer.extract_actions(decision)
            batch_reason = str(decision.get("reason", ""))
            if len(queued) > 1:
                log(f"[思考] {batch_reason[:70]}  -> {len(queued)} 步")

        item = queued.pop(0)

        action = str(item.get("action") or "noop").strip()

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

        action, args, note = learning.gatekeep(action, args, kind)
        if note:
            runlog.agent("gatekeeper", day=label, step=step, action=action, args=args,
                         kind=kind, note=note)
            log(f"[守门] {note}")

        # A prescribed precondition is enforced here, by the system, not remembered by the model.
        # This is the difference between "the doctor found out that a watering can has to be full"
        # and "the farmer actually fills it before watering".
        if prescriber is not None and prescriber.unmet(action, state):
            helper = prescriber.satisfy(action, state)
            if helper:
                runlog.agent("prescription", day=label, step=step, action=action,
                             satisfied_with=helper)
                time.sleep(1.5)

        learning.remember_attempt(action, args)

        started = time.perf_counter()
        result = client.action(action, **args)

        # Watch the world while it happens. The blind waits this replaces polled the state once a
        # second and looked at a single boolean, so a farmer held by a greeting dialogue waited out
        # the whole five-minute deadline while every agent sat idle.
        shared_budget = max(10.0, deadline - time.monotonic()) if deadline else 0.0
        watch = watcher.watch(
            action, args, state,
            day=label, step=step,
            budget_s=shared_budget,
            timeout_s=settle_timeout(action, args) or 60.0,
            progress=(lambda s: witness.progress_value(action, s)) if action in CHORE_ACTIONS else None,
        )
        after_state = watch.state

        if watch.stopped not in ("done",):
            log(f"[守望] {action} 提前结束：{watch.stopped}"
                + (f"（处置：{', '.join(watch.remedies)}）" if watch.remedies else ""))
            if watch.remedies:
                result = dict(result)
                result["detail"] = f"{result.get('detail', '')} | 守望者处置: {', '.join(watch.remedies)}"

        # A macro reports what it *queued*, not what it *did*. The real outcome - how many tiles
        # it finished and how many it gave up on - only exists once the chore has stopped.
        if action in CHORE_ACTIONS:
            outcome = str(after_state.get("chore_last") or "").strip()
            if outcome:
                result = dict(result)
                result["queued"] = result.get("detail", "")
                result["detail"] = outcome

        action_s = time.perf_counter() - started

        total_llm += llm_s
        total_action += action_s

        # Judge by whether the world changed, not by what the action claimed. Every action class has
        # a witness; when it does not, that is recorded as no-verdict rather than as success.
        if tracking and action in TILE_ACTIONS:
            time.sleep(0.6)        # let the swing land before reading the tile back
        after = observe_tile(client, tx, ty) if (tracking and action in TILE_ACTIONS) else None
        worked, evidence = witness.judge(action, args, state, after_state, before, after)

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
                         detail=str(result.get("detail", ""))[:160])

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
        store.log_step(
            label,
            f"- step {step}: `{action}` {json.dumps(args, ensure_ascii=False)} "
            f"-> ok={result.get('ok')} rc={result.get('reason_code') or '-'} {detail}",
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

    elapsed = total_llm + total_action + total_watch
    log(f"[perf] steps={max_steps} total={elapsed:.0f}s | llm={total_llm:.0f}s "
        f"action={total_action:.0f}s supervisor={total_watch:.0f}s")
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


def observe_tile(client: StateClient, x: int, y: int) -> dict | None:
    """Read one tile. Cheap HTTP, never an LLM call - this is how outcomes get measured."""
    result = client.action("observe", x=x, y=y)
    data = result.get("data")
    return data if isinstance(data, dict) else None


def enforce_day_end(client: StateClient) -> None:
    """If the play loop ran out of steps without sleeping, end the day anyway.

    Without this an unattended run can sit on day 1 forever, which is exactly the failure mode
    that makes an overnight run worthless.
    """
    result = client.action("sleep")
    if result.get("ok"):
        log(f"[run ] forced sleep -> {result.get('detail')}")
        return

    log(f"[run ] sleep unavailable ({result.get('detail')}) - using the raw new-day routine")
    raw = client.action("new_day")
    log(f"[run ] new_day -> ok={raw.get('ok')} {raw.get('detail')}")


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

    with StateClient(args.base_url) as client, LlmClient("llm") as smart, LlmClient("fast") as fast:
        log(f"[llm ] fast brain = {fast.endpoint.model} (every step) | "
            f"smart brain = {smart.endpoint.model} (plan / judge / reflect)")
        ai_save = load_ai_save()
        known = store.cheat_sheet()
        streamer = StreamerAI(fast, args.goal, persona=(ai_save or {}).get("persona", ""),
                              memory_block=known)
        supervisor = SupervisorAI(smart)
        planner = PlannerAI(smart)
        referee = RefereeAI(smart)
        watcher = Watcher(client, ledger=ledger, runlog=runlog, log=log)
        pipeline = runner.Pipeline(client, ledger, runlog=runlog, log=log)
        doctor = Doctor(smart, client, log=log)
        prescriber = Prescriber(client, ledger, learning, store, log=log)
        outcomes = OutcomeWatch(ledger=ledger, log=log)

        # Everything the agents say also goes on screen, for whoever is watching.
        runlog.attach_overlay(
            lambda batch: client.action("overlay", lines=batch)
        )
        runlog.feed("run", "agents attached - watch this panel")
        runlog.feed("outcome", outcomes.summary())

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
                + gaps.load_agent_events(memory_mod.PROJECT_ROOT / "logs" / "agents", "streamer")
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
                ledger.raise_defect(
                    f"gap:{gap.action}",
                    f"`{gap.action}` 被判定 {gap.verdict}",
                    "agent",
                    detail=reason,
                    evidence="; ".join(gap.examples[:2]) if gap.examples else "",
                )
                runlog.agent("gaps", action=gap.action, verdict=gap.verdict,
                             attempts=gap.attempts, ok_count=gap.ok_count, noop=gap.noop,
                             reasons=gap.reasons, examples=gap.examples)
                runlog.line(f"- [缺口] `{gap.action}` **{gap.verdict}** — {reason}")

            try:
                # Every stage before a day is played now has an owner, a retry, and a record.
                # "Could not load the save" used to be an exception nobody wrote down.
                pipeline.ensure(ai_save)
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
                plan = planner.plan_day(start_state, persona=streamer.persona, memory=known,
                                        goal=streamer.goal, recent_days=store.recent_days())
                goals = plan.get("plan") or []
                runlog.agent("planner", day=label_hint, focus=plan.get("focus", ""), goals=goals,
                             notes=plan.get("notes", ""), sleep_by=plan.get("sleep_by"))
                log(f"[规划] {plan.get('focus', '')}")
                for goal in goals:
                    log(f"[规划]   - {goal}")
                streamer.set_plan(plan)

                outcome, label, steps = play(client, streamer, supervisor, args.steps, store,
                                             learning, runlog, budget_s=args.day_seconds,
                                             watcher=watcher, prescriber=prescriber)

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
                verdict = referee.score_day(plan, store.read_journal(label),
                                            compact_state(start_state), compact_state(end_state), steps)

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
                                  memory_block=known)
            supervisor = SupervisorAI(smart)

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
            return 0 if outcome in ("day_complete", "supervisor_done") else 4


if __name__ == "__main__":
    raise SystemExit(main())
