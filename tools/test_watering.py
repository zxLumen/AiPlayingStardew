"""Prove that watering can actually water.

Loads the agent's own save through the pipeline, then refills and waters with the watcher in the
loop (so a greeting dialogue is advanced instead of killing the chore), and reports what the WORLD
did - the can's water level and the count of thirsty crops.

    python tools/test_watering.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "agent"))

import witness  # noqa: E402
from defects import DefectLedger  # noqa: E402
from runner import Pipeline  # noqa: E402
from state_client import StateClient  # noqa: E402
from watcher import Watcher  # noqa: E402


def log(m: str) -> None:
    print(m, flush=True)


def can_charges(state: dict):
    for slot in state.get("inventory") or []:
        if slot.get("name") == "喷壶":
            return slot.get("charges")
    item = (state.get("player") or {}).get("current_item") or {}
    return item.get("charges")


def work(state: dict) -> dict:
    return (state.get("location") or {}).get("work") or {}


def main() -> int:
    ledger = DefectLedger(PROJECT, log=log)

    with StateClient(timeout_s=60.0) as client:
        from agent import load_ai_save  # noqa: E402  (imported late: agent pulls in the world)

        pipeline = Pipeline(client, ledger, log=log)

        try:
            pipeline.ensure(load_ai_save())
        except Exception as exc:
            log(f"[pipeline] {exc}")
            ledger.save()
            return 1

        watcher = Watcher(client, ledger=ledger, log=log)
        state = client.state()

        if "FarmHouse" in str((state.get("player") or {}).get("current_location")):
            log(f"[state] indoors - stepping outside")
            client.action("exit_building")
            time.sleep(4)
            state = client.state()

        log(f"[state] location={(state.get('player') or {}).get('current_location')} "
            f"can={can_charges(state)} thirsty={work(state).get('thirsty_crops')}")

        if not work(state).get("thirsty_crops"):
            log("[state] nothing thirsty here - run this on a farm day with crops")
            return 1

        # --- refill, watched ---
        log("\n--- refill (watched) ---")
        started = time.monotonic()
        result = client.action("refill")
        log(f"    start : {result.get('ok')} {result.get('detail')}")
        watch = watcher.watch("refill", {}, state, budget_s=90.0, timeout_s=90.0,
                              progress=lambda s: witness.progress_value("refill", s))
        after = watch.state
        log(f"    watch : stopped={watch.stopped} remedies={watch.remedies} {watch.seconds:.1f}s")
        log(f"    chore : {after.get('chore_last')}")
        log(f"    can   : {can_charges(state)} -> {can_charges(after)}")

        if not can_charges(after):
            log("\n!!! the can is still empty - refill did not work")
            ledger.save()
            return 1

        # --- water, watched ---
        log("\n--- water_all (watched) ---")
        before = work(after).get("thirsty_crops")
        result = client.action("water_all")
        log(f"    start : {result.get('ok')} {result.get('detail')}")
        watch = watcher.watch("water_all", {}, after, budget_s=120.0, timeout_s=120.0,
                              progress=lambda s: witness.progress_value("water_all", s))
        final = watch.state
        after_count = work(final).get("thirsty_crops")

        log(f"    watch : stopped={watch.stopped} remedies={watch.remedies} {watch.seconds:.1f}s")
        log(f"    chore : {final.get('chore_last')}")
        log(f"    thirsty: {before} -> {after_count}")
        log(f"    can   : {can_charges(final)}")

        verdict = "PASS" if (before is not None and after_count is not None and after_count < before) else "FAIL"
        log(f"\n[{verdict}] watering changed the world: {before} -> {after_count}")

    ledger.save()
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
