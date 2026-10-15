"""
Smoke-test the configured LLM endpoint against a realistic agent prompt.

Usage:
    .venv\\Scripts\\python.exe agent\\selftest_llm.py

Exits non-zero with a clear message when the key is missing or rejected.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from llm_config import resolve  # noqa: E402

SYSTEM = (
    "You are the decision engine for a Stardew Valley AI player. "
    "You receive structured game state as JSON and reply with ONE action as strict JSON. "
    "Never output markdown, never output prose, output JSON only."
)

STATE = {
    "game": {"day": 3, "season": "spring", "year": 1, "time": 620},
    "player": {"tile_x": 64, "tile_y": 15, "stamina": 270, "money": 500, "location": "Farm"},
    "inventory": [{"slot": 0, "name": "Hoe", "kind": "tool"},
                  {"slot": 1, "name": "Watering Can", "kind": "tool"}],
    "crops": [{"tile_x": 65, "tile_y": 15, "name": "Parsnip", "is_watered": False, "is_ready": False}],
}

PROMPT = (
    "Available actions: move{direction,duration_ms}, use_tool, interact, "
    "go_to{x,y}, water_all_crops, harvest_all, sleep, noop.\n"
    "Choose the single best next action. Reply as:\n"
    '{"action": "...", "args": {...}, "reason": "..."}\n\n'
    "STATE:\n" + json.dumps(STATE, ensure_ascii=False)
)


def main() -> int:
    ep = resolve("llm")
    print("=== resolved endpoint ===")
    print(ep.describe())
    print()

    if not ep.is_configured:
        print("!! No API key found.")
        print("   Set the environment variable OPENCODE_API_KEY, or paste it into")
        print("   config/secrets.json -> llm.api_key")
        return 2

    body = {
        "model": ep.model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT},
        ],
        "temperature": ep.temperature,
        "max_tokens": ep.max_tokens,
    }
    if ep.reasoning_effort:
        body["reasoning_effort"] = ep.reasoning_effort

    print(f"=== POST {ep.chat_url} ===")
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=ep.timeout_s) as client:
            resp = client.post(
                ep.chat_url,
                headers={
                    "Authorization": f"Bearer {ep.api_key}",
                    "Content-Type": "application/json",
                    **ep.headers,
                },
                json=body,
            )
    except httpx.HTTPError as exc:
        print(f"!! network error: {exc}")
        return 3

    elapsed = time.perf_counter() - started
    print(f"HTTP {resp.status_code}  ({elapsed:.2f}s)")

    if resp.status_code != 200:
        print("!! request rejected. body:")
        print(resp.text[:1500])
        if resp.status_code in (401, 403):
            print("\n-> the key is missing, wrong, or has no access to OpenCode inference.")
        return 4

    data = resp.json()
    msg = data["choices"][0]["message"]
    print("\n=== model reply ===")
    print(msg.get("content"))
    reasoning = msg.get("reasoning_content")
    if reasoning:
        print("\n=== reasoning (trimmed) ===")
        print(reasoning[:400])
    print("\n=== usage ===")
    print(json.dumps(data.get("usage", {}), ensure_ascii=False))

    content = (msg.get("content") or "").strip()
    try:
        parsed = json.loads(content)
        print("\n=== JSON parse: OK ===")
        print(json.dumps(parsed, ensure_ascii=False))
        return 0
    except json.JSONDecodeError as exc:
        print(f"\n=== JSON parse: FAILED ({exc}) ===")
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
