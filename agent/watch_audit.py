"""Adjudicate the watcher: was each intervention warranted?

The watcher is the one component that makes a decision with no opponent. It ends an action early and
applies a "remedy", and what it used to write down was an *event* - `stopped: timeout`,
`remedies: [...]`, `seconds`. An event is a description of what happened; it cannot be wrong, so
nothing could ever contradict it, and the same false verdict was fed into the record the agent
learns from, run after run. `logs/agents/watcher.jsonl` had records and no judge.

This module gives every intervention an opponent. It re-reads the same logs the run wrote and asks
the one question that can come back "no":

    the watcher said the action was stuck. Did the world change anyway?

Everything is decided from evidence that already exists - no model, because "this click timed out
31 times while its witness shows money moving" is a fact to be counted, not an opinion to be asked:

  R1 world-changed     the step's own witness (witness.jsonl) says the world changed although the
                       watcher called it stuck
  R2 action-is-capable the same action succeeded later with no intervention - the watcher's "this
                       action cannot do the job" (timeout / over budget) was a claim about the action,
                       and the action disproves it
  R3 remedy-on-own-result  the action itself opened something (a menu, a dialogue) and the watcher's
                       remedy immediately took over / closed it - helping with the action's own work
  R4 no-witness        the intervention stopped an action that has no witness at all, so it could
                       never be shown to be stuck - a claim with nothing to check it (the `press_key`
                       bug: it reported OK for a whole day because it had no witness)

An intervention shown unnecessary is raised as a `developer` defect - the same principle as
`phantom` and `LYING`: a verdict that is not true belongs to whoever made it.

A false-intervention rate per reason (`timeout` / blocked-by-dialogue / `over budget` / ...) is
written to `logs/watch-audit.md`, the way the capability-gap report is written to
`logs/capability-gaps.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import witness as witness_mod

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The watcher only writes to its own log when it applied a remedy; a plain `timeout` used to leave no
# record there at all. The agent's own streamer log carries the ones the watcher dropped, as
# `WATCHER_*` reason codes. Reading both is what makes the audit cover every intervention instead of
# only the ones that happened to leave a note.
STREAMER_PREFIX = "WATCHER_"

# Reason buckets, in the order the report presents them. "blocked" is the watcher holding up an
# action because the game is showing something (dialogue / menu / cutscene).
REASON_ORDER = ("timeout", "blocked", "over budget", "no progress", "stalled", "no witness")

MIN_DEFECT_COUNT = 1

# What the witness says when an action is one that by design changes nothing. Used to tell "this
# action genuinely has no witness" (a defect) apart from "this action is informational" (fine).
NO_WITNESS_MARK = "informational - no world change expected"


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(text).lower()).strip("-")[:40]


def _stop_kind(stopped) -> str:
    """Turn a `stopped` string into one of the report's reason buckets."""
    text = str(stopped or "").strip().lower()
    if text.startswith("blocked"):
        return "blocked"
    if text.startswith("over budget"):
        return "over budget"
    if text in ("timeout", "no progress", "stalled"):
        return text
    return ""


def load_jsonl(path: Path, run_id: str = "") -> list[dict]:
    """Read an append-only agent log. A half-written last line is skipped, not fatal.

    The game is playing while this runs, so the file this reads is being appended to. A partial line
    is expected, not an error.
    """
    rows: list[dict] = []
    if not path.exists():
        return rows

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if run_id and str(row.get("run") or "") != run_id:
            continue
        rows.append(row)

    return rows


@dataclass
class Intervention:
    """One decision the watcher made about one action, with the evidence that can falsify it."""

    run: str = ""
    day: str = ""
    step: int = 0
    action: str = ""
    kind: str = ""
    reasons: list[str] = field(default_factory=list)
    remedies: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    seconds: float = 0.0
    claim: str = ""
    expectation: str = ""
    falsifier: str = ""
    detail: str = ""

    # Filled in by adjudicate().
    verdict: str = ""          # justified | unnecessary | unverifiable
    rule: str = ""
    worked: bool | None = None
    witness: str = ""
    why: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (self.run, self.day, self.step, self.action)

    def merge(self, other: "Intervention") -> None:
        order = {"": 0, "blocked": 1, "no witness": 1, "stalled": 2, "no progress": 2,
                 "timeout": 3, "over budget": 3}
        if order.get(other.kind, 0) > order.get(self.kind, 0):
            self.kind = other.kind
        for value in other.reasons:
            if value and value not in self.reasons:
                self.reasons.append(value)
        for value in other.remedies:
            if value and value not in self.remedies:
                self.remedies.append(value)
        for value in other.sources:
            if value and value not in self.sources:
                self.sources.append(value)
        self.seconds = max(self.seconds, other.seconds)
        for attr in ("claim", "expectation", "falsifier", "detail"):
            if not getattr(self, attr) and getattr(other, attr):
                setattr(self, attr, getattr(other, attr))


def collect_interventions(root: Path, run_id: str = "") -> list[Intervention]:
    """Every watcher decision in the logs, one per (run, day, step, action)."""
    agents = root / "logs" / "agents"
    found: dict[tuple, Intervention] = {}

    def add(item: Intervention) -> None:
        existing = found.get(item.key)
        if existing is None:
            found[item.key] = item
        else:
            existing.merge(item)

    # 1. The watcher's own log: what it decided, and (since ①1) the claim behind it.
    for row in load_jsonl(agents / "watcher.jsonl", run_id):
        event = str(row.get("event") or "")
        action = str(row.get("action") or "")
        if not action:
            continue

        item = Intervention(
            run=str(row.get("run") or ""), day=str(row.get("day") or ""),
            step=int(row.get("step") or 0), action=action,
            claim=str(row.get("claim") or ""), expectation=str(row.get("expectation") or ""),
            falsifier=str(row.get("falsifier") or ""), sources=["watcher"],
        )
        reason = str(row.get("reason") or "")
        if reason:
            item.reasons.append(reason)
        remedy = str(row.get("remedy") or "")
        if remedy:
            item.remedies.append(remedy)

        if event in ("remedy", "gave_up"):
            item.kind = "blocked"
        elif event in ("no progress", "stalled"):
            item.kind = event
        elif event == "watch done":
            item.kind = _stop_kind(row.get("stopped")) or ""
            try:
                item.seconds = float(row.get("seconds") or 0.0)
            except (TypeError, ValueError):
                pass
        else:
            continue
        add(item)

    # 2. The agent's log: the watcher cut the action short and left a WATCHER_* code. This is where
    #    a pure `timeout` lives today - the watcher's own note only fired when a remedy was applied.
    for row in load_jsonl(agents / "streamer.jsonl", run_id):
        code = str(row.get("reason_code") or "")
        action = str(row.get("action") or "")
        if not action or not code.startswith(STREAMER_PREFIX):
            continue
        kind = _stop_kind(code[len(STREAMER_PREFIX):])
        item = Intervention(
            run=str(row.get("run") or ""), day=str(row.get("day") or ""),
            step=int(row.get("step") or 0), action=action,
            kind=kind or "blocked", sources=["streamer"],
        )
        try:
            item.seconds = float(row.get("act_s") or 0.0)
        except (TypeError, ValueError):
            pass
        item.detail = str(row.get("detail") or "")[:200]
        add(item)

    # 3. An action that must have a witness, ran, and has none: the watcher waited on it and could
    #    never tell whether it worked. That is the `press_key` bug, and it has no other record.
    for row in load_jsonl(agents / "streamer.jsonl", run_id):
        action = str(row.get("action") or "")
        if not action or action in witness_mod.INFORMATIONAL:
            continue
        if str(row.get("evidence") or "") != NO_WITNESS_MARK:
            continue
        item = Intervention(
            run=str(row.get("run") or ""), day=str(row.get("day") or ""),
            step=int(row.get("step") or 0), action=action,
            kind="no witness", sources=["streamer"],
        )
        try:
            item.seconds = float(row.get("act_s") or 0.0)
        except (TypeError, ValueError):
            pass
        item.detail = str(row.get("detail") or "")[:200]
        add(item)

    return list(found.values())


def _clean_successes(witness_rows: list[dict], intervened: set) -> dict[tuple, list[tuple]]:
    """Actions that succeeded with NO intervention, per run: {run: {action: [(day, step, evidence)]}}."""
    clean: dict[tuple, list[tuple]] = {}
    for row in witness_rows:
        if row.get("worked") is not True:
            continue
        key = (str(row.get("run") or ""), str(row.get("day") or ""), int(row.get("step") or 0))
        if key in intervened:
            continue
        action = str(row.get("action") or "")
        if not action:
            continue
        clean.setdefault((key[0], action), []).append(
            (key[1], key[2], str(row.get("evidence") or ""))
        )
    return clean


def adjudicate(items: list[Intervention], witness_rows: list[dict]) -> None:
    """Decide, in place and from the logs alone, whether each intervention was warranted."""
    by_step: dict[tuple, list[dict]] = {}
    for row in witness_rows:
        key = (str(row.get("run") or ""), str(row.get("day") or ""), int(row.get("step") or 0))
        by_step.setdefault(key, []).append(row)

    intervened = {(i.run, i.day, i.step) for i in items}
    clean = _clean_successes(witness_rows, intervened)

    for item in items:
        rows = by_step.get((item.run, item.day, item.step), [])
        own = [r for r in rows if str(r.get("action") or "") == item.action] or rows

        if rows:
            flags = [r.get("worked") for r in own if r.get("worked") is not None]
            item.worked = True if any(f is True for f in flags) else (False if flags else None)
            item.witness = str((own[0].get("evidence") if own else "") or "")

        if not rows:
            # No witness at all. If the watcher stopped an action that has no witness, that is R4 -
            # unwarranted by construction. Otherwise there is simply nothing to check it against.
            if item.kind == "no witness":
                item.verdict, item.rule = "unnecessary", "R4 no-witness"
                item.why = [f"这一步没有见证（{NO_WITNESS_MARK}），却按「卡住」处理了"]
            else:
                item.verdict, item.rule = "unverifiable", "no witness for this step"
                item.why = ["witness.jsonl 里没有这一步，无法裁决"]
            continue

        if item.worked is True and not item.remedies:
            # R1 - the world changed on the very step the watcher called stuck, and the watcher did
            # not apply a remedy: there is nothing that could have caused the change but the action.
            # (With a remedy, a change is exactly what the remedy predicted - see R2/R3.)
            item.verdict, item.rule = "unnecessary", "R1 world-changed"
            item.why = [f"见证说这一步世界变了：{item.witness}"]
            continue

        if item.kind == "no witness":
            # A witness row exists, so this is not the missing-witness case after all.
            item.verdict, item.rule = "justified", "the step does have a witness"
            continue

        # R3 - the remedy acted on what the action itself opened. Either the witness saw the action
        # open something and the watcher then handled it, or the watcher's own note says a menu was
        # open while the witness says nothing changed (the remedy closed it before the witness read).
        opened_self = "opened something" in item.witness
        opened_note = [r for r in item.reasons if "menu open" in r]
        if item.remedies and (opened_self or (opened_note and item.worked is False)):
            item.verdict, item.rule = "unnecessary", "R3 remedy-on-own-result"
            if opened_self:
                item.why = [f"动作自己打开了东西（{item.witness}），处置 {', '.join(item.remedies)} "
                            f"紧接着把动作自己的成果接手了"]
            else:
                item.why = [
                    f"守望者自己记到「{opened_note[0]}」已经打开，见证却说没变化",
                    f"处置 {', '.join(item.remedies)} 把动作刚打开的东西关掉了",
                ]
            continue

        if item.kind in ("timeout", "over budget"):
            successes = clean.get((item.run, item.action), [])
            if successes:
                day, step, evidence = successes[-1]
                # R2 - the watcher claimed the ACTION could not do the job; the same action did it,
                # unaided, in the same run.
                item.verdict, item.rule = "unnecessary", "R2 action-is-capable"
                item.why = [
                    f"同一个动作 {item.action} 在 {day} 第 {step} 步无人介入也成功过"
                    f"（{evidence}）"
                ]
                continue

        if item.worked is None:
            item.verdict, item.rule = "justified", "witness has no verdict"
        else:
            item.verdict, item.rule = "justified", "evidence agrees with the watcher"


def _rate(items: list[Intervention]) -> list[tuple]:
    counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = counts.setdefault(item.kind or "?", {"total": 0, "unnecessary": 0, "unverifiable": 0})
        bucket["total"] += 1
        if item.verdict == "unnecessary":
            bucket["unnecessary"] += 1
        elif item.verdict == "unverifiable":
            bucket["unverifiable"] += 1

    order = {name: i for i, name in enumerate(REASON_ORDER)}
    return sorted(counts.items(), key=lambda kv: (order.get(kv[0], 99), -kv[1]["total"]))


def report(items: list[Intervention], title: str = "守望者裁决报告") -> str:
    total = len(items)
    bad = [i for i in items if i.verdict == "unnecessary"]
    unknown = [i for i in items if i.verdict == "unverifiable"]

    lines = [
        f"# {title}",
        "",
        "对 `logs/agents/watcher.jsonl` 与 `witness.jsonl` 里**每一次守望者介入**做裁决：",
        "守望者说「卡住了」，见证（witness）说世界到底变没变。两者矛盾的那次，就是假介入。",
        "",
        f"- 介入总数：**{total}**",
        f"- 判定多余：**{len(bad)}**（{len(bad) / total * 100:.0f}%）" if total else "- 判定多余：0",
        f"- 无法裁决：**{len(unknown)}**",
        "",
    ]

    lines += ["| 原因 | 介入 | 判定多余 | 假介入率 |", "|---|---|---|---|"]
    for name, bucket in _rate(items):
        rate = bucket["unnecessary"] / bucket["total"] * 100 if bucket["total"] else 0.0
        lines.append(f"| `{name}` | {bucket['total']} | {bucket['unnecessary']} | {rate:.0f}% |")

    if not bad:
        lines += ["", "没有发现多余的介入（每一处都有日志支持）。"]
        return "\n".join(lines) + "\n"

    lines += ["", "## 被判定为多余的介入", ""]
    grouped: dict[tuple, list[Intervention]] = {}
    for item in bad:
        grouped.setdefault((item.kind, item.action, item.rule), []).append(item)

    for (kind, action, rule), group in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"### `{kind}` · `{action}` — {len(group)} 次（{rule}）")
        seen: list[str] = []
        for item in group:
            for note in item.why:
                if note not in seen:
                    seen.append(note)
        for note in seen[:3]:
            lines.append(f"- {note}")
        steps = ", ".join(f"{i.day}#{i.step}" for i in group[:6])
        lines.append(f"- 例子：{steps}" + (" …" if len(group) > 6 else ""))
        falsifier = next((i.falsifier for i in group if i.falsifier), "")
        if falsifier:
            lines.append(f"- 它自己写的可证伪条件：{falsifier}")
        lines.append("")

    lines += ["## 怎么用", "",
              "- **R1/R2/R3/R4 判定的介入**已按 `developer` 立案：守望者的判决与日志矛盾，需要改代码。",
              "- 假介入率高的原因（如 `timeout`）说明守望者等的不是那个动作真正的见证。"]
    return "\n".join(lines) + "\n"


def run(root: Path = PROJECT_ROOT, ledger=None, run_id: str = "",
        write: bool = True, log=print) -> str:
    """Audit the logs, raise defects for the unnecessary interventions, and write the report."""
    agents = root / "logs" / "agents"
    items = collect_interventions(root, run_id=run_id)
    witness_rows = load_jsonl(agents / "witness.jsonl", run_id)
    adjudicate(items, witness_rows)

    text = report(items)
    if write:
        out = root / "logs" / "watch-audit.md"
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
        except OSError:
            pass

    if ledger is not None:
        _file_defects(items, ledger, log)

    return text


def _file_defects(items: list[Intervention], ledger, log) -> None:
    grouped: dict[tuple, list[Intervention]] = {}
    for item in items:
        if item.verdict != "unnecessary":
            continue
        grouped.setdefault((item.kind, item.action, item.rule), []).append(item)

    for (kind, action, rule), group in grouped.items():
        if len(group) < MIN_DEFECT_COUNT:
            continue
        cost = sum(i.seconds for i in group)
        sample = group[0]
        why = "; ".join(sample.why)
        detail = (f"守望者在「{kind}」上对 {action} 介入 {len(group)} 次，日志证明这些介入不必要"
                  f"（{rule}）。{why}")
        evidence = "; ".join(f"{i.run} {i.day}#{i.step}" for i in group[:4])
        ledger.raise_defect(
            f"watch:{_slug(kind)}:{_slug(action)}",
            f"守望者对 `{action}` 的「{kind}」介入多余",
            "developer",
            detail=detail,
            cost_s=cost,
            evidence=evidence,
        )
        log(f"[裁决] 多余介入 [{kind}] {action} x{len(group)} - {rule}")


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parent.parent
    print(run(root, write="--no-write" not in sys.argv))
