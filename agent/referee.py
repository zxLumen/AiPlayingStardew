"""The referee AI.

After the day ends, judges it against the plan that was set in the morning. This is what turns
"is the AI getting better?" into a number instead of a feeling - without it we have no way to tell
whether a change to the prompts or the mod actually helped.
"""

from __future__ import annotations

import prompts
from llm import LlmClient


class RefereeAI:
    def __init__(self, llm: LlmClient) -> None:
        self.llm = llm

    def score_day(self, plan: dict, journal: str, day_start: dict, day_end: dict,
                  steps: int) -> dict:
        messages = [
            {"role": "system", "content": prompts.REFEREE_SYSTEM},
            {
                "role": "user",
                "content": prompts.referee_user(plan, journal, day_start, day_end, steps),
            },
        ]

        try:
            verdict, _ = self.llm.chat_json(messages, max_tokens=900)
        except Exception as exc:
            return {"score": None, "why": f"referee unavailable: {exc}", "error": str(exc)}

        if not isinstance(verdict, dict):
            return {"score": None, "why": "referee returned a non-object"}

        try:
            verdict["score"] = max(0, min(100, int(verdict.get("score", 0))))
        except (TypeError, ValueError):
            verdict["score"] = None

        for key in ("completed", "partial", "missed"):
            value = verdict.get(key)
            verdict[key] = [str(v) for v in value] if isinstance(value, list) else []

        verdict.setdefault("why", "")
        verdict.setdefault("verdict", "ok")
        return verdict
