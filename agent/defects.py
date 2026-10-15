"""The defect ledger: the one place that answers "what is still broken?".

Every other agent reports on the day's plan. None of them owned the system itself, so a broken
action could be diagnosed by the referee night after night and nothing ever changed - the note went
into narrative memory and died there. The watering can that held no water was described correctly
six times and reported to nobody.

A finding only counts when it is written down with an owner, a cost and a status, and when it is
raised again the next time it happens. "Owner" is the part that was missing:

    watcher    the harness can fix this itself, and did
    agent      an AI can work it out from the game's own data
    developer  the game can do this and the bridge cannot - a human has to write code

Rendered to logs/defects.md, ranked by what each defect has cost. That file - not the raw logs - is
what to read first.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

OWNERS = ("watcher", "agent", "developer")

SEVERITY = {"developer": 3, "agent": 2, "watcher": 1}


@dataclass
class Defect:
    key: str                       # stable identity, so repeats raise the same defect
    title: str
    owner: str                     # watcher | agent | developer
    detail: str = ""
    severity: int = 1
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    cost_s: float = 0.0            # seconds of real time this has burnt
    days: int = 0                  # game days it has spoilt
    status: str = "open"           # open | mitigated | fixed
    evidence: list[str] = field(default_factory=list)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


class DefectLedger:
    def __init__(self, root: Path = PROJECT_ROOT, log=None) -> None:
        self.root = root
        self.path = root / "memory" / "defects.json"
        self.report = root / "logs" / "defects.md"
        self.log = log or (lambda _m: None)
        self.items: dict[str, Defect] = {}
        self._load()

    # -- storage -----------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        for entry in raw.get("defects", []):
            try:
                defect = Defect(**entry)
            except TypeError:
                continue
            self.items[defect.key] = defect

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"defects": [asdict(d) for d in self.items.values()]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.write_report()

    # -- raising -----------------------------------------------------------

    def raise_defect(self, key: str, title: str, owner: str, *, detail: str = "",
                     cost_s: float = 0.0, days: int = 0, evidence: str = "") -> Defect:
        """Record that something is wrong. Repeating a key increments it rather than duplicating."""
        defect = self.items.get(key)

        if defect is None:
            defect = Defect(
                key=key, title=title, owner=owner, detail=detail,
                severity=SEVERITY.get(owner, 1), first_seen=_now(),
            )
            self.items[key] = defect
            self.log(f"[台账] 新增缺陷 [{owner}] {title}")

        defect.last_seen = _now()
        defect.count += 1
        defect.cost_s += max(0.0, cost_s)
        defect.days += max(0, days)
        if detail:
            defect.detail = detail
        if evidence and evidence not in defect.evidence:
            defect.evidence = (defect.evidence + [evidence])[-5:]

        # A defect that keeps coming back after being marked fixed is open again.
        if defect.status == "fixed":
            defect.status = "open"

        return defect

    def set_status(self, key: str, status: str) -> None:
        defect = self.items.get(key)
        if defect is not None:
            defect.status = status

    def open_items(self) -> list[Defect]:
        return [d for d in self.items.values() if d.status == "open"]

    def for_owner(self, owner: str) -> list[Defect]:
        return [d for d in self.open_items() if d.owner == owner]

    # -- reporting ---------------------------------------------------------

    def render(self) -> str:
        items = sorted(self.items.values(),
                       key=lambda d: (d.status != "open", -d.severity, -d.cost_s, -d.count))

        lines = [
            "# 缺陷台账",
            "",
            "系统自己发现的问题。**先读这个文件，不要读原始日志。**",
            "",
            "- `owner=watcher`：守望者自己就地解决了（不该再出现）",
            "- `owner=agent`：AI 能从游戏数据里查出病因并自愈",
            "- `owner=developer`：**游戏能做、桥做不到 —— 需要人写代码**",
            "",
            "| 状态 | 归属 | 缺陷 | 次数 | 代价 | 首次 | 最近 |",
            "|---|---|---|---|---|---|---|",
        ]

        for d in items:
            cost = f"{d.cost_s / 60:.1f}分" if d.cost_s >= 60 else f"{d.cost_s:.0f}秒"
            lines.append(
                f"| {d.status} | **{d.owner}** | {d.title} | {d.count} | {cost} | {d.first_seen} | {d.last_seen} |"
            )

        open_dev = self.for_owner("developer")
        if open_dev:
            lines += ["", "## 需要我动手的（按代价排序）", ""]
            for d in sorted(open_dev, key=lambda x: -x.cost_s):
                lines.append(f"### {d.title}")
                lines.append(f"- 键：`{d.key}`")
                lines.append(f"- 发生 {d.count} 次，累计 {d.cost_s:.0f} 秒")
                if d.detail:
                    lines.append(f"- 说明：{d.detail}")
                if d.evidence:
                    lines.append("- 证据：")
                    lines += [f"  - `{e}`" for e in d.evidence]

        return "\n".join(lines) + "\n"

    def write_report(self) -> None:
        try:
            self.report.parent.mkdir(parents=True, exist_ok=True)
            self.report.write_text(self.render(), encoding="utf-8")
        except OSError:
            pass

    def summary(self) -> str:
        open_items = self.open_items()
        if not open_items:
            return "无"

        by_owner: dict[str, int] = {}
        for d in open_items:
            by_owner[d.owner] = by_owner.get(d.owner, 0) + 1

        return ", ".join(f"{owner}:{count}" for owner, count in sorted(by_owner.items()))
