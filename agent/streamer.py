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

        # A single decision may not take longer than this. Beyond it the answer is worthless anyway -
        # the world it was reasoning about has moved on - so the harness gives up and looks around
        # instead of leaving the farmer standing still.
        self.deadline_s = 25.0
        self.last_error = ""
        self.consecutive_failures = 0

    def set_plan(self, plan: dict) -> None:
        """Hand over the plan for today (from the planner AI)."""
        self.plan = plan or {}

    def extract_actions(self, decision: dict) -> list[dict]:
        """The queue of actions the model asked for.

        A reply is meant to be a short queue, but a model that answers with a single flat action is
        still understood - the harness should not lose a whole turn over a shape difference.
        """
        queued = decision.get("actions")
        if isinstance(queued, list):
            items = [item for item in queued if isinstance(item, dict) and item.get("action")]
            if items:
                return items

        return [decision]

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

        # One slow decision must not eat the round. A model call that has not answered inside the
        # deadline is not going to be worth the wait: the world it was reasoning about has moved on,
        # and every second spent waiting is a second the farmer is standing still. So the wait is
        # bounded, and a timed-out turn falls back to looking around - which is cheap, useful, and
        # never leaves the farmer frozen.
        try:
            decision, _ = self.llm.chat_json(messages, max_tokens=2500, timeout_s=self.deadline_s)
            self.consecutive_failures = 0
        except Exception as exc:
            self.consecutive_failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"

            # The fallback must not look like a normal decision. Returning a tidy "scan" here was a
            # mistake: scan is a legal action, so the log, the referee and the witness all read it as
            # the agent choosing to look around - and a dead model became invisible. It also looped:
            # eight scans in a row while the day drained away.
            #
            # So: one failed call looks around once (cheap, sometimes useful), a second in a row
            # stands still instead, and either way the line says plainly that the brain is down.
            action = "noop" if self.consecutive_failures >= 2 else "scan"

            return {
                "reason": f"!! 模型不可用（{type(exc).__name__}）第 {self.consecutive_failures} 次 - "
                          f"{'停手' if action == 'noop' else '先看一眼'}",
                "actions": [{"action": action, "radius": 8, "reason": "model unavailable"}],
            }

        self.last_error = ""
        return decision
