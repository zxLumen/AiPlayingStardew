"""The planner AI.

Decides what the day is for, once, before the streamer takes its first action. This exists because
long-horizon planning is the documented weak point of LLM game agents: a purely reactive player
wanders. The plan is short, checkable, and re-stated to the streamer every step.
"""

from __future__ import annotations

import prompts
from compact import compact_state
from llm import LlmClient


class PlannerAI:
    def __init__(self, llm: LlmClient) -> None:
        self.llm = llm

    def plan_day(self, state: dict, persona: str = "", memory: str = "",
                 goal: str = "", recent_days: str = "") -> dict:
        messages = [
            {"role": "system", "content": prompts.PLANNER_SYSTEM},
            {
                "role": "user",
                "content": prompts.planner_user(
                    compact_state(state), persona, memory, goal, recent_days
                ),
            },
        ]

        try:
            plan, _ = self.llm.chat_json(messages, max_tokens=700)
        except Exception as exc:
            return {"focus": "", "plan": [], "notes": f"planner unavailable: {exc}", "error": str(exc)}

        if not isinstance(plan, dict):
            return {"focus": "", "plan": [], "notes": "planner returned a non-object"}

        plan.setdefault("focus", "")
        plan.setdefault("plan", [])
        plan.setdefault("notes", "")
        plan.setdefault("sleep_by", 2200)
        return plan
