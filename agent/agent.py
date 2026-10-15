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
import memory as memory_mod  # noqa: E402
from llm import LlmClient  # noqa: E402
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
    """The day rolled over (or the game returned to a fresh morning)."""
    if not game.get("has_save"):
        return False
    if game.get("day_of_month") != day_start.get("day_of_month"):
        return True
    return False


def settle_timeout(action: str, args: dict) -> float:
    """How long to wait (by polling state, never by asking the model) for an action to finish."""
    if action == "go_to":
        return 25.0
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
         max_steps: int, store: "memory_mod.MemoryStore") -> tuple[str, str]:
    history: list[str] = []
    directive: str | None = None
    total_llm = total_action = total_watch = 0.0

    first = client.state()
    day_start = first.get("game") or {}
    label = memory_mod.day_label(day_start)
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
        state = client.state()
        game = state.get("game") or {}

        if session_finished(day_start, game):
            log(f"[day] COMPLETE - day {day_start.get('day_of_month')} -> {game.get('day_of_month')}")
            return "day_complete", label

        if supervisor.should_review(step, history):
            started = time.perf_counter()
            verdict = supervisor.review(state, history, step, day_start)
            total_watch += time.perf_counter() - started

            directive = (verdict.get("directive") or "").strip() or None
            note = str(verdict.get("note") or "")
            log(f"[监工] status={verdict.get('status')}" + (f" - {note}" if note else "")
                + (f" | directive: {directive}" if directive else ""))
            if verdict.get("status") == "done":
                return "supervisor_done", label

        started = time.perf_counter()
        decision = streamer.decide(state, history, directive)
        llm_s = time.perf_counter() - started

        action = str(decision.get("action") or "noop").strip()

        # Arguments belong at the top level, but tolerate a model that nests them.
        nested = decision.get("args")
        args: dict = dict(nested) if isinstance(nested, dict) else {}
        args.update({k: v for k, v in decision.items() if k not in ("action", "reason", "args")})

        started = time.perf_counter()
        result = client.action(action, **args)
        if settle_timeout(action, args) > 0:
            # Wait for the world to finish, by polling state - NOT by asking the model.
            client.wait_until_settled(timeout_s=settle_timeout(action, args))
        action_s = time.perf_counter() - started

        total_llm += llm_s
        total_action += action_s

        detail = str(result.get("detail", ""))
        history.append(f"{action} {json.dumps(args, ensure_ascii=False)} -> ok={result.get('ok')} {detail}")
        store.log_step(
            label,
            f"- step {step}: `{action}` {json.dumps(args, ensure_ascii=False)} "
            f"-> ok={result.get('ok')} rc={result.get('reason_code') or '-'} {detail}",
        )

        reason = str(decision.get("reason", ""))[:45]
        flag = "OK " if result.get("ok") else "ERR"
        # On failure the detail is the whole point - show it instead of the reason.
        tail = reason if result.get("ok") else f"! {detail[:80]}"
        log(f"[主播 {step:>3}] {action:<15} {json.dumps(args, ensure_ascii=False)[:44]:<44} {flag} "
            f"llm {llm_s:4.1f}s act {action_s:4.1f}s  {tail}")

    elapsed = total_llm + total_action + total_watch
    log(f"[perf] steps={max_steps} total={elapsed:.0f}s | llm={total_llm:.0f}s "
        f"action={total_action:.0f}s supervisor={total_watch:.0f}s")
    return "steps_exhausted", label


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


def reflect(supervisor: SupervisorAI, store: "memory_mod.MemoryStore", known: str, label: str) -> None:
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
    log(f"[mem ] facts: +{report['added']} ~{report['updated']} -{report['dropped']} "
        f"(total {report['total']}) | lessons: +{lessons}")


def main() -> int:
    parser = argparse.ArgumentParser(description="主播AI + 监工AI for Stardew Valley")
    parser.add_argument("--steps", type=int, default=120, help="max decisions this session")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--no-launch", action="store_true", help="do not start the game process")
    parser.add_argument("--new-game", action="store_true", help="force a brand new AI save")
    parser.add_argument("--max-boot", type=int, default=90, help="max menu steps during bootstrap")
    args = parser.parse_args()

    if not args.no_launch:
        started = game_process.ensure_running()
        log(f"[game] {'launched the game' if started else 'the game is already running'}")

    with StateClient(args.base_url) as client:
        log("[api ] waiting for StateBridge...")
        if not client.wait_until_up():
            log("[api ] ERROR: the StateBridge API never became reachable")
            return 1
        log("[api ] StateBridge is up")

        with LlmClient() as llm:
            log(f"[llm ] {llm.endpoint.provider} / {llm.endpoint.model}")

            ai_save = load_ai_save()
            if ai_save:
                log(f"[save] AI save on record: '{ai_save.get('character_name')}' @ '{ai_save.get('farm_name')}'")
            else:
                log("[save] no AI save yet - the streamer will create its own")

            store = memory_mod.MemoryStore()
            stats = store.stats()
            log(f"[mem ] memory: {stats['facts']} facts ({stats['high_confidence']} high-confidence), "
                f"{stats['lessons']} lessons, {stats['journals']} journal(s)")

            known = store.cheat_sheet()

            streamer = StreamerAI(llm, args.goal, persona=(ai_save or {}).get("persona", ""),
                                  memory_block=known)
            supervisor = SupervisorAI(llm)

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

            outcome, label = play(client, streamer, supervisor, args.steps, store)
            log(f"[done] session ended: {outcome}")

            reflect(supervisor, store, known, label)
            return 0 if outcome in ("day_complete", "supervisor_done") else 4


if __name__ == "__main__":
    raise SystemExit(main())
