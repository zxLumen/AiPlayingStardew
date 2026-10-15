"""Measure real latency for the real prompt, across the models available on the Go plan.

Run this before and after any prompt change. Latency is dominated by how much the model has to
think, and thinking is driven by the prompt, so a benchmark on a synthetic prompt tells you
nothing useful.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

import prompts  # noqa: E402
from compact import compact_state  # noqa: E402
from llm_config import resolve  # noqa: E402

# Models on the OpenAI-compatible Go endpoint. (minimax/qwen use the Anthropic path instead.)
CANDIDATES = [
    ("deepseek-v4.1-flash", "low"),    # current baseline
    ("deepseek-v4.1-flash", None),     # same model, no explicit effort
    ("mimo-v2.6-flash", None),
    ("glm-5.3-flash", None),
    ("longcat-2.0", None),
    ("space-bunny", None),
]

ROUNDS = 3


def fetch_state() -> dict:
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get("http://127.0.0.1:8787/state")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        # Fall back to a representative state so the benchmark still runs offline.
        return {
            "game": {"day_of_month": 1, "season": "spring", "year": 1, "time_of_day": 730,
                     "has_save": True, "is_player_free": True, "minutes_until_morning": 1080},
            "player": {"name": "Juniper", "money": 500, "stamina": 250, "max_stamina": 270,
                       "tile_x": 64, "tile_y": 17, "facing": "down", "current_location": "Farm",
                       "selected_slot_index": 0,
                       "current_item": {"name": "Axe"}, "is_auto_moving": False},
            "inventory": [{"slot": i, "name": n, "stack": 1, "kind": "tool"} for i, n in
                          enumerate(["Axe", "Hoe", "Pickaxe", "Watering Can", "Scythe"])],
            "nearby_objects": [{"tile_x": 66, "tile_y": 19, "name": "Weeds", "is_ready": False}],
            "nearby_characters": [],
            "quests": [{"id": "1", "name": "Introductions", "objective": "Greet 28 people",
                        "is_completed": False, "is_primary": True}],
            "ui": {},
        }


def build_prompt(state: dict, ep) -> list[dict]:
    history = ["go_to {'x': 66, 'y': 19} -> ok=True", "use_tool_at {'x': 66, 'y': 19} -> ok=False"]
    return [
        {"role": "system", "content": prompts.streamer_system("Play a full day.")},
        {"role": "user", "content": prompts.streamer_user(compact_state(state), history, None)},
    ]


def measure(ep, model: str, effort: str | None, messages: list[dict]) -> dict:
    body: dict = {"model": model, "messages": messages, "temperature": 0.4, "max_tokens": 900}
    if effort:
        body["reasoning_effort"] = effort

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=180) as client:
            resp = client.post(
                ep.chat_url,
                headers={"Authorization": f"Bearer {ep.api_key}", "Content-Type": "application/json",
                         **ep.headers},
                json=body,
            )
    except Exception as exc:
        return {"error": str(exc)[:80]}

    elapsed = time.perf_counter() - started

    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code} {resp.text[:90]}"}

    data = resp.json()
    message = data["choices"][0]["message"]
    usage = data.get("usage") or {}

    content = (message.get("content") or "").strip()
    valid = False
    try:
        json.loads(content)
        valid = True
    except Exception:
        pass

    return {
        "s": elapsed,
        "prompt": usage.get("prompt_tokens", 0),
        "cached": (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
        "out": usage.get("completion_tokens", 0),
        "reasoning": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
        "valid": valid,
    }


def main() -> int:
    ep = resolve("llm")
    if not ep.is_configured:
        print("no API key configured")
        return 2

    state = fetch_state()
    messages = build_prompt(state, ep)
    prompt_chars = sum(len(m["content"]) for m in messages)

    print(f"prompt: {prompt_chars} chars  (~{prompt_chars // 4} tokens)  rounds={ROUNDS}\n")
    print(f"{'model':<24} {'effort':<7} {'latency':<22} {'in':>6} {'cached':>7} {'out':>5} {'think':>6} {'json':>5}")
    print("-" * 92)

    for model, effort in CANDIDATES:
        results = [measure(ep, model, effort, messages) for _ in range(ROUNDS)]
        good = [r for r in results if "error" not in r]

        if not good:
            print(f"{model:<24} {str(effort):<7} ERROR: {results[0].get('error', '?')}")
            continue

        times = sorted(r["s"] for r in good)
        median = statistics.median(times)
        spread = f"{times[0]:.1f}-{times[-1]:.1f}s (med {median:.1f})"
        last = good[-1]
        valid = sum(1 for r in good if r["valid"])
        print(f"{model:<24} {str(effort or '-'):<7} {spread:<22} "
              f"{last['prompt']:>6} {last['cached']:>7} {last['out']:>5} {last['reasoning']:>6} "
              f"{valid}/{len(good):>3}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
