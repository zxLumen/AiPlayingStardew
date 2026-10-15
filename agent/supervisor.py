"""The supervisor AI: watches the streamer and keeps it moving until the day is done."""

from __future__ import annotations

import prompts
from compact import compact_state
from llm import LlmClient


class SupervisorAI:
    """Reviews the streamer on a schedule, and immediately whenever it looks stuck."""

    def __init__(self, llm: LlmClient, review_every: int = 8, repeat_threshold: int = 3) -> None:
        self.llm = llm
        self.review_every = review_every
        self.repeat_threshold = repeat_threshold

    # -- cheap rules first, so we do not pay for an LLM call every turn -----

    def looks_stuck(self, history: list[str]) -> bool:
        """True when the last few actions are identical (the classic stall)."""
        if len(history) < self.repeat_threshold:
            return False
        tail = history[-self.repeat_threshold:]
        return len(set(tail)) == 1

    def repeated_failures(self, history: list[str]) -> bool:
        """True when the last three actions all failed."""
        if len(history) < 3:
            return False
        return all("ok=False" in line for line in history[-3:])

    def should_review(self, steps: int, history: list[str]) -> bool:
        return (
            steps % self.review_every == 0
            or self.looks_stuck(history)
            or self.repeated_failures(history)
        )

    def review(self, state: dict, history: list[str], steps: int, day_start: dict) -> dict:
        messages = [
            {"role": "system", "content": prompts.SUPERVISOR_SYSTEM},
            {
                "role": "user",
                "content": prompts.supervisor_user(compact_state(state), history, steps, day_start),
            },
        ]

        try:
            verdict, _ = self.llm.chat_json(messages, max_tokens=350)
        except Exception as exc:  # the supervisor must never kill the session
            return {"status": "ok", "directive": "", "note": f"supervisor unavailable: {exc}"}

        if not isinstance(verdict, dict):
            return {"status": "ok", "directive": "", "note": "supervisor returned a non-object"}

        verdict.setdefault("status", "ok")
        verdict.setdefault("directive", "")
        verdict.setdefault("note", "")
        return verdict

    # -- teacher duties ----------------------------------------------------

    def reflect(self, journal: str, known: str) -> dict:
        """Turn one day of play into durable facts and lessons."""
        messages = [
            {"role": "system", "content": prompts.REFLECTION_SYSTEM},
            {"role": "user", "content": prompts.reflection_user(journal, known)},
        ]
        try:
            result, _ = self.llm.chat_json(messages, max_tokens=1800)
        except Exception as exc:
            return {"error": str(exc)}
        return result if isinstance(result, dict) else {}

    def calibration(self, questions: list[dict], known: str) -> dict:
        """Answer a familiarity check before playing."""
        if not questions:
            return {}
        messages = [
            {"role": "system", "content": prompts.CALIBRATION_SYSTEM},
            {"role": "user", "content": prompts.calibration_user(questions, known)},
        ]
        try:
            result, _ = self.llm.chat_json(messages, max_tokens=1000)
        except Exception as exc:
            return {"error": str(exc)}
        return result if isinstance(result, dict) else {}
