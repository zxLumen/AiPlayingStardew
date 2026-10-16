"""Watching the agent's own body: is the brain answering, and is it answering in time?

The outcomes watchdog only knows whether the GAME is moving. It cannot tell "the farmer is not
moving" apart from "the brain is not answering" - and from the outside those look identical, which
is how a dead model spent a whole night looking like a slow one, and how a round budget got spent
entirely on waiting.

This is the missing half: the system noticing its own condition. Two conditions, both of which an
agent should never have to be told by a person:

    brain:unavailable   the model stopped answering. Nothing above this works - not the planner,
                        not the referee, not the watcher. It has to be said out loud.
    brain:slow          the model answers, but slowly enough that a round is one or two actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BrainWatch:
    """Tracks decision latency and failures, and raises a defect when either stops being workable."""

    ledger: object = None
    log: object = None
    window: int = 12
    slow_s: float = 12.0          # a decision slower than this is eating the round
    fail_streak: int = 3          # this many in a row and the model is simply down

    recent: list[float] = field(default_factory=list)
    failures: int = 0
    reported_slow: bool = False

    def __post_init__(self) -> None:
        if self.log is None:
            self.log = lambda _m: None

    def observe(self, seconds: float, failed: bool) -> None:
        self.recent.append(float(seconds))
        self.recent = self.recent[-self.window:]

        self.failures = self.failures + 1 if failed else 0

        if self.ledger is None:
            return

        if self.failures >= self.fail_streak:
            self.ledger.raise_defect(
                "brain:unavailable",
                f"模型接口连续 {self.failures} 次没有回复",
                "developer",
                detail=("连续拿不到回复（超时或报错）。在它之上的一切 —— 规划、裁判、守望 —— "
                        "都只是在空转，而且看起来和'游戏很慢'一模一样。"),
            )
            self.log(f"[脑健康] 模型连续 {self.failures} 次无回复 —— 已记入台账")
            return

        if len(self.recent) < 4:
            return

        ordered = sorted(self.recent)
        median = ordered[len(ordered) // 2]

        if median > self.slow_s:
            if not self.reported_slow:
                self.ledger.raise_defect(
                    "brain:slow",
                    f"决策中位耗时 {median:.0f} 秒",
                    "developer",
                    detail=(f"最近 {len(self.recent)} 次决策中位数 {median:.0f} 秒。"
                            f"回合预算被'等'吃掉，一天只能走几步 —— 这不是游戏卡住，是想得太慢。"),
                )
                self.log(f"[脑健康] 决策中位 {median:.0f} 秒，已记入台账")
                self.reported_slow = True
        else:
            self.reported_slow = False

    def summary(self) -> str:
        if not self.recent:
            return "还没有决策数据"
        ordered = sorted(self.recent)
        return (f"最近 {len(self.recent)} 次：中位 {ordered[len(ordered) // 2]:.1f}s "
                f"最快 {ordered[0]:.1f}s 最慢 {ordered[-1]:.1f}s")
