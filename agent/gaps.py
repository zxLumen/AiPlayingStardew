"""Capability gap detector.

Works out which actions are not actually doing anything, so a stuck run produces a precise defect
report instead of silently burning hours.

The judgement is statistics, not opinion:

    BROKEN  the action was attempted repeatedly and never even claimed to work
    LYING   the action kept reporting success while the world never changed
    SLOW    it works, but most attempts fail - worth a better action

It is deliberately not an LLM. "This verb has failed 23 times and changed nothing" is a fact to be
counted, not a question to be asked.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# - step 12: `use_tool_at` {"x": 69, "y": 25} -> ok=True rc=NOTHING_DONE detail...
JOURNAL_LINE = re.compile(
    r"^-\s*step\s*(?P<step>\d+):\s*`(?P<action>[^`]+)`\s*(?P<args>\{.*?\})\s*"
    r"->\s*ok=(?P<ok>True|False)\s*rc=(?P<rc>\S*)\s*(?P<detail>.*)$"
)

MIN_ATTEMPTS = 4

# A reply can say ok=True while its own message admits nothing happened. Those are not successes.
NOOP_MARKERS = (
    "nothing to do", "nothing nearby", "nothing to work", "did nothing",
    "would not go into", "already at", "already outdoors",
)


@dataclass
class Gap:
    action: str
    attempts: int = 0
    ok_count: int = 0         # reported ok=True
    noop: int = 0             # reported ok=True but the message admits nothing happened
    claimed: int = 0          # ok=True and it really claims to have done work
    verified: int = 0         # world actually changed (only known for tile actions)
    verifiable: int = 0       # attempts where a world-change verdict exists
    reasons: dict[str, int] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.claimed / self.attempts if self.attempts else 0.0

    @property
    def verdict(self) -> str:
        if self.attempts < MIN_ATTEMPTS:
            return "LOW-DATA"
        if self.verifiable >= MIN_ATTEMPTS and self.verified == 0:
            return "LYING" if self.claimed else "BROKEN"
        if self.claimed == 0:
            return "NOOP" if self.noop else "BROKEN"
        if self.success_rate < 0.25:
            return "SLOW"
        return "HEALTHY"


def load_journal_events(journal_dir: Path) -> list[dict]:
    """Parse the step lines written to memory/journal/*.md."""
    events: list[dict] = []
    if not journal_dir.exists():
        return events

    for path in sorted(journal_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line in text.splitlines():
            match = JOURNAL_LINE.match(line.strip())
            if not match:
                continue
            events.append({
                "day": path.stem,
                "step": int(match.group("step")),
                "action": match.group("action").strip(),
                "args": match.group("args"),
                "ok": match.group("ok") == "True",
                "reason_code": "" if match.group("rc") in ("-", "") else match.group("rc"),
                "detail": match.group("detail").strip(),
                "worked": None,
            })

    return events


def load_agent_events(agent_dir: Path, agent: str = "streamer") -> list[dict]:
    """Parse logs/agents/<agent>.jsonl, which carries the world-change verdict."""
    events: list[dict] = []
    path = agent_dir / f"{agent}.jsonl"
    if not path.exists():
        return events

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("action"):
            events.append(event)

    return events


def analyse(events: list[dict]) -> list[Gap]:
    gaps: dict[str, Gap] = {}

    for event in events:
        action = str(event.get("action") or "").strip()
        if not action:
            continue

        gap = gaps.setdefault(action, Gap(action=action))
        gap.attempts += 1

        ok = bool(event.get("ok"))
        detail = str(event.get("detail") or "")
        is_noop = any(marker in detail.lower() for marker in NOOP_MARKERS)

        if ok:
            gap.ok_count += 1
        if ok and is_noop:
            gap.noop += 1
        if ok and not is_noop:
            gap.claimed += 1

        worked = event.get("worked")
        if worked is not None:
            gap.verifiable += 1
            if worked:
                gap.verified += 1

        reason = str(event.get("reason_code") or "").strip() or "(none)"
        gap.reasons[reason] = gap.reasons.get(reason, 0) + 1

        if len(gap.examples) < 3 and detail:
            gap.examples.append(f"{reason}: {detail[:90]}")

    order = {"BROKEN": 0, "LYING": 1, "NOOP": 2, "SLOW": 3, "HEALTHY": 4, "LOW-DATA": 5}
    return sorted(gaps.values(), key=lambda g: (order.get(g.verdict, 9), -g.attempts))


def report(gaps: list[Gap], title: str = "能力缺口报告") -> str:
    lines = [f"# {title}", ""]

    bad = [g for g in gaps if g.verdict in ("BROKEN", "LYING", "NOOP", "SLOW")]
    if not bad:
        lines.append("没有检测到能力缺口（每条动作都至少在成功过）。")
        return "\n".join(lines)

    lines.append(f"检测到 **{len(bad)}** 条有问题的动作：\n")
    lines.append("| 动作 | 判定 | 尝试 | 报OK | 空转OK | 世界变化 | 主要原因 |")
    lines.append("|---|---|---|---|---|---|---|")

    for gap in bad:
        reasons = ", ".join(f"{k}x{v}" for k, v in
                            sorted(gap.reasons.items(), key=lambda kv: -kv[1])[:3])
        verified = f"{gap.verified}/{gap.verifiable}" if gap.verifiable else "—"
        lines.append(f"| `{gap.action}` | **{gap.verdict}** | {gap.attempts} | "
                     f"{gap.ok_count} | {gap.noop} | {verified} | {reasons} |")

    lines.append("\n## 证据\n")
    for gap in bad:
        lines.append(f"### `{gap.action}` — {gap.verdict}")
        for example in gap.examples:
            lines.append(f"- {example}")
        if gap.verdict == "LYING":
            lines.append("- 它一直报告成功，但世界从未改变 —— 这个「成功」是假的")
        elif gap.verdict == "NOOP":
            lines.append("- 它每次都返回 OK，但返回的说明自己承认「什么都没做」")
            lines.append("- 这种最阴：AI 以为下达了命令，于是反复重发")
        elif gap.verdict == "BROKEN":
            lines.append("- 从未成功过一次 —— 很可能现在没有任何动作能做到这件事")
        elif gap.verdict == "SLOW":
            lines.append("- 偶尔成功，大多数失败 —— 换更可靠的动作，或把缺的能力补上")
        lines.append("")

    lines.append("## 怎么用\n")
    lines.append("- **BROKEN / LYING / NOOP**：拉黑这些动作，不要再让主播AI 重试")
    lines.append("- 缺的能力需要 **mod 侧新增动词**（这是修复执行器要做的）")
    lines.append("- **SLOW**：能用但不好用，考虑换更可靠的动作")
    return "\n".join(lines)


def run(project_root: Path, write: bool = True) -> str:
    journal_events = load_journal_events(project_root / "memory" / "journal")
    agent_events = load_agent_events(project_root / "logs" / "agents", "streamer")
    events = journal_events + agent_events

    text = report(analyse(events),
                  title=f"能力缺口报告（{len(journal_events)} 条流水 + {len(agent_events)} 条结构化）")

    if write:
        out = project_root / "logs" / "capability-gaps.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")

    return text


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parent.parent
    print(run(root, write="--no-write" not in sys.argv))
