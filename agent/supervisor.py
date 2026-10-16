"""The supervisor AI: watches the streamer and keeps it moving until the day is done."""

from __future__ import annotations

import prompts
from compact import compact_state
from llm import LlmClient


class SupervisorAI:
    """Reviews the streamer on a schedule, and immediately whenever it looks stuck."""

    def __init__(self, llm: LlmClient, review_every: int = 12, repeat_threshold: int = 3,
                 escalate: LlmClient | None = None) -> None:
        # Two brains, because this is two different jobs. The scheduled review is a one-line "is it
        # drifting?" and does not need a reasoning model; the review that fires because the farmer is
        # genuinely stuck is a judgement call and does. It used to be the reasoning brain for both,
        # between steps, on a live stream - which is where a large share of the visible stutter came
        # from (every 8th step, unmeasured, unlogged, off the critical path of nothing).
        self.llm = llm
        self.escalate = escalate or llm
        self.review_every = review_every
        self.repeat_threshold = repeat_threshold
        self._escalating = False

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
        stuck = self.looks_stuck(history) or self.repeated_failures(history)
        self._escalating = stuck
        return stuck or steps % self.review_every == 0

    def review(self, state: dict, history: list[str], steps: int, day_start: dict) -> dict:
        messages = [
            {"role": "system", "content": prompts.SUPERVISOR_SYSTEM + prompts.LANGUAGE},
            {
                "role": "user",
                "content": prompts.supervisor_user(compact_state(state), history, steps, day_start),
            },
        ]

        try:
            verdict, _ = (self.escalate if self._escalating else self.llm).chat_json(
                messages, max_tokens=350)
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
            {"role": "system", "content": prompts.REFLECTION_SYSTEM + prompts.LANGUAGE},
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
