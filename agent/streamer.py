"""The streamer AI: decides what to do next, and designs its own character."""

from __future__ import annotations

import json

import prompts
from compact import compact_state
from llm import LlmClient, LlmError


class StreamerAI:
    def __init__(self, llm: LlmClient, goal: str, persona: str = "",
                 memory_block: str = "", plan: dict | None = None) -> None:
        self.llm = llm
        self.goal = goal
        self.persona = persona
        self.memory_block = memory_block
        self.plan = plan or {}

    def set_plan(self, plan: dict) -> None:
        """Hand over the plan for today (from the planner AI)."""
        self.plan = plan or {}

    def _plan_block(self) -> str:
        goals = self.plan.get("plan") or []
        if not goals:
            return ""

        lines = ["TODAY'S PLAN (set this morning by your planner - follow it, but the live state wins):"]
        if self.plan.get("focus"):
            lines.append(f"  focus: {self.plan['focus']}")
        for goal in goals:
            lines.append(f"  - {goal}")
        if self.plan.get("notes"):
            lines.append(f"  note: {self.plan['notes']}")
        if self.plan.get("sleep_by"):
            lines.append(f"  be in bed by {self.plan['sleep_by']}")
        return "\n".join(lines)

    # -- character creation -------------------------------------------------

    def design_character(self, available_farm_types: list[str]) -> dict:
        """Ask the model to invent the character and farm it wants to play."""
        system = prompts.DESIGNER_SYSTEM
        if self.persona:
            system += f"\n\nPrior persona, keep it consistent:\n{self.persona}"

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompts.designer_user(available_farm_types)},
        ]
        design, _ = self.llm.chat_json(messages)
        return design

    # -- per-turn decisions -------------------------------------------------

    def decide(self, state: dict, history: list[str], directive: str | None) -> dict:
        system = prompts.streamer_system(self.goal)
        if self.persona:
            system += f"\n\nYOUR PERSONA:\n{self.persona}"
        if self.memory_block:
            system += f"\n\n{self.memory_block}"

        plan_block = self._plan_block()
        if plan_block:
            system += f"\n\n{plan_block}"

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompts.streamer_user(compact_state(state), history, directive)},
        ]
        # Per-turn decisions are tiny, but a reasoning model needs room to think and still answer.
        decision, _ = self.llm.chat_json(messages, max_tokens=900)
        return decision
