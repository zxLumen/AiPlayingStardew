"""The streamer's own learning.

This is not prompt text - it is outcome data.

Every tile action is judged by whether the WORLD actually changed, not by what the action
reported. That verdict is stored per (target kind, action). The gatekeeper then does two things:

  * rewrites an action that keeps failing on a kind into one that has been seen to work
  * refuses to re-send an action + argument pair that just failed

So the agent improves inside a session and across sessions, with no extra LLM calls. It starts
with no knowledge at all and has to earn it - the same way a first-time player does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Actions that aim at one tile - these are what experience can correct.
TILE_ACTIONS = {"work", "use_tool_at", "interact_at"}
CANDIDATE_ACTIONS = ("work", "use_tool_at", "interact_at")

# There is nothing to learn about these - they are already settled.
NOT_ACTIONABLE = {None, "", "nothing", "growing_crop", "empty_soil", "machine", "tillable"}

MIN_TRIES = 2      # attempts needed before a rate is trusted
MIN_GAP = 0.35     # how much better a rival action must be before overriding the model


@dataclass
class Tally:
    ok: int = 0
    fail: int = 0

    @property
    def total(self) -> int:
        return self.ok + self.fail

    @property
    def rate(self) -> float:
        return self.ok / self.total if self.total else 0.0

    def to_dict(self) -> dict:
        return {"ok": self.ok, "fail": self.fail}


class LearningStore:
    def __init__(self, path: Path, log=print) -> None:
        self.path = path
        self.log = log
        self.tally: dict[str, Tally] = {}
        self.failures: list[tuple[str, str]] = []
        self.attempts: list[tuple[str, str]] = []   # every attempt, success or not
        self.blocked: dict[str, str] = {}     # action -> why it was blacklisted
        self._load()

    def remember_attempt(self, action: str, args: dict) -> None:
        """Count every attempt, not just the failed ones.

        The worst loops are not failures at all: 'enter the locked shop' returns ok, opens a
        dialogue, and the agent does it again. Repeating one action+args this many times in a day
        is a loop, whatever it reports.
        """
        self.attempts.append((action, json.dumps(args, sort_keys=True, ensure_ascii=False)))

    def is_loop(self, action: str, args: dict, threshold: int = 4) -> bool:
        needle = (action, json.dumps(args, sort_keys=True, ensure_ascii=False))
        return self.attempts.count(needle) >= threshold

    def blacklist(self, action: str, reason: str) -> bool:
        """Stop using an action that has been shown not to work. Returns True when newly blocked."""
        if action in self.blocked:
            return False
        self.blocked[action] = reason
        self.log(f"[拉黑] '{action}' 停用 - {reason}")
        return True

    def unblock(self, action: str) -> None:
        self.blocked.pop(action, None)

    def unblock_all(self) -> None:
        self.blocked.clear()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        for key, value in (raw.get("tally") or {}).items():
            if isinstance(value, dict):
                self.tally[key] = Tally(int(value.get("ok", 0)), int(value.get("fail", 0)))

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {"tally": {k: v.to_dict() for k, v in sorted(self.tally.items())}},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- learning ----------------------------------------------------------

    @staticmethod
    def _key(kind: str, action: str) -> str:
        return f"{kind}|{action}"

    def rate(self, kind: str, action: str) -> Tally:
        return self.tally.get(self._key(kind, action), Tally())

    def record(self, kind: str, action: str, worked: bool) -> None:
        entry = self.tally.setdefault(self._key(kind, action), Tally())
        if worked:
            entry.ok += 1
        else:
            entry.fail += 1
        self.save()

        mark = "成功" if worked else "失败"
        self.log(f"[学习] {kind} + {action} -> {mark}  (累计 {entry.ok}/{entry.total})")

    def best_action(self, kind: str, current: str) -> str | None:
        """A rival action that has clearly done better on this kind of target."""
        own = self.rate(kind, current)
        best: str | None = None
        best_rate = own.rate

        for candidate in CANDIDATE_ACTIONS:
            if candidate == current:
                continue

            tally = self.rate(kind, candidate)
            if tally.total < MIN_TRIES:
                continue
            if tally.rate > 0.5 and tally.rate >= best_rate + MIN_GAP:
                best, best_rate = candidate, tally.rate

        return best

    # -- gatekeeper --------------------------------------------------------

    def remember_failure(self, action: str, args: dict) -> None:
        self.failures.append((action, json.dumps(args, sort_keys=True, ensure_ascii=False)))

    def is_repeat(self, action: str, args: dict, threshold: int = 2) -> bool:
        needle = (action, json.dumps(args, sort_keys=True, ensure_ascii=False))
        return self.failures.count(needle) >= threshold

    def clear_failures(self) -> None:
        self.failures.clear()

    def gatekeep(self, action: str, args: dict, kind: str | None) -> tuple[str, dict, str]:
        """Return (action, args, note). Rewrites what experience says does not work."""
        if action in self.blocked:
            return action, args, f"'{action}' 已被能力缺口检测器拉黑（{self.blocked[action]}）- 换个做法"

        if self.is_loop(action, args):
            return action, args, (f"'{action}' {json.dumps(args, ensure_ascii=False)} "
                                  "本日已重复 4 次 - 这是个循环，别再做了")

        if self.is_repeat(action, args):
            return action, args, f"'{action}' {args} 已连续失败 2 次，被拦截 - 换个做法"

        if kind and kind not in NOT_ACTIONABLE and action in TILE_ACTIONS:
            better = self.best_action(kind, action)
            if better:
                tally = self.rate(kind, action)
                return (
                    better,
                    args,
                    f"经验：'{action}' 对 '{kind}' 一直失败 ({tally.ok}/{tally.total})，改用 '{better}'",
                )

        return action, args, ""

    def summary(self) -> str:
        rows = []
        for key, tally in sorted(self.tally.items(), key=lambda kv: -kv[1].total)[:12]:
            kind, _, action = key.partition("|")
            rows.append(f"{kind}+{action} {tally.ok}/{tally.total}")
        return " | ".join(rows)


def judge_outcome(action: str, args: dict, before: dict | None, after: dict | None,
                  result: dict, state: dict) -> bool | None:
    """Did the WORLD change? This is the only honest measure of success."""
    if action in TILE_ACTIONS:
        if before is None or after is None:
            return None
        return after.get("kind") != before.get("kind")

    if action == "go_to":
        player = state.get("player") or {}
        here = (player.get("tile_x"), player.get("tile_y"))
        want = (args.get("x"), args.get("y"))
        return here == want

    return bool(result.get("ok"))
