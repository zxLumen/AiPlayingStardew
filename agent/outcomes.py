"""Is the whole enterprise moving?

Every other watcher looks at one action. Nothing looked at the thing that matters most: after a
night of play, has anything actually improved? Sixty game days with the same quest counter, the same
money and a flat score is the loudest signal the system can produce, and nothing was listening - the
night was spent, the report at the end said "20 days completed", and nobody noticed that not one of
them had changed anything.

This is the cheapest possible defence against wasting a night: a handful of numbers that only ever
move when the player really gets somewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def signals(state: dict) -> dict:
    """The numbers that move when real progress happens. Deliberately few and hard to fake."""
    player = state.get("player") or {}
    quests = state.get("quests") or []
    inventory = state.get("inventory") or []
    work = (state.get("location") or {}).get("work") or {}

    completed = sum(1 for q in quests if q.get("is_completed"))
    kinds = len({str(slot.get("name") or "") for slot in inventory if slot.get("name")})

    return {
        "money": int(player.get("money") or 0),
        "quests_completed": completed,
        "inventory_kinds": kinds,
        "tillable_worked": int(work.get("empty_soil") or 0) + int(work.get("ready_crops") or 0),
    }


@dataclass
class OutcomeWatch:
    """Watches whether anything is getting better, and says so when it is not."""

    ledger: object = None
    log: object = None
    patience: int = 4              # days of no movement before this counts as stalled
    history: list[dict] = field(default_factory=list)
    best: dict = field(default_factory=dict)
    flat_days: int = 0
    reported: bool = False

    def __post_init__(self) -> None:
        if self.log is None:
            self.log = lambda _m: None

    def observe(self, day: str, state: dict, score=None) -> bool:
        """Record one day's signals. Returns True when this day moved something forward."""
        now = signals(state)
        if score is not None:
            try:
                now["score"] = float(score)
            except (TypeError, ValueError):
                pass

        if not self.best:
            self.best = dict(now)
            self.history.append({"day": day, **now})
            return True

        moved = {key: value for key, value in now.items()
                 if key in self.best and value != self.best[key]}

        # Progress counts only upwards; a bad day that loses money is not "progress".
        forward = {
            key: value for key, value in moved.items()
            if isinstance(value, (int, float)) and isinstance(self.best.get(key), (int, float))
            and value > self.best[key]
        }

        self.history.append({"day": day, **now, "moved": sorted(forward)})
        self.history = self.history[-40:]

        for key, value in now.items():
            if isinstance(value, (int, float)) and value > (self.best.get(key) or 0):
                self.best[key] = value

        if forward:
            self.flat_days = 0
            self.reported = False
            return True

        self.flat_days += 1
        self.log(f"[进展] {day} 什么都没往前动（已连续 {self.flat_days} 天）")

        if self.flat_days >= self.patience and not self.reported:
            self._stalled(day, now)
            self.reported = True

        return False

    def _stalled(self, day: str, now: dict) -> None:
        """Raise the loudest defect there is: nothing is getting better."""
        if self.ledger is None:
            return

        frozen = ", ".join(f"{k}={v}" for k, v in sorted(self.best.items()))
        self.ledger.raise_defect(
            "outcome:stalled",
            f"连续 {self.flat_days} 天毫无进展",
            "agent",
            detail=(
                f"金钱 / 任务 / 背包 / 农活 全部停在原地。这是最高优先级的缺陷："
                f"不是某个动作坏了，是整件事没有在往前走。当前值：{frozen}"
            ),
            evidence=f"{day}: {now}",
            days=self.flat_days,
        )
        self.log(f"[进展] !!! 连续 {self.flat_days} 天毫无进展 —— 已记入台账（最高优先级）")

    def summary(self) -> str:
        return (f"连续 {self.flat_days} 天无进展 | 当前最佳：" +
                ", ".join(f"{k}={v}" for k, v in sorted(self.best.items())))
