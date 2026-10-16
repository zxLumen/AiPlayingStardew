"""Append-only logs: one file per run, one stream per agent.

Nothing here ever truncates. An overnight run that dies at 3am must still leave everything it
learned behind, and the next run must not erase the previous one's report.

Layout:
    logs/runs/run-<timestamp>.md     human report for one run (never rewritten)
    logs/agents/<agent>.jsonl        one JSON line per event, appended across all runs
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class RunLog:
    def __init__(self, project_root: Path, log=print) -> None:
        self.log = log
        self.logs = project_root / "logs"
        self.runs = self.logs / "runs"
        self.agents = self.logs / "agents"
        for directory in (self.runs, self.agents):
            directory.mkdir(parents=True, exist_ok=True)

        self.run_id = time.strftime("%Y-%m-%d_%H%M%S")
        self.report = self.runs / f"run-{self.run_id}.md"

        # The on-screen panel. Lines are batched so the API is not called once per event.
        self._push = None
        self._feed: list[dict] = []
        self._last_push = 0.0

        # One continuous human record across every run, so there is always a single file to read
        # even when a run is restarted many times.
        self.history = self.logs / "history.md"
        if not self.history.exists():
            self._write(self.history,
                        "# 全部历史（跨运行，只追加）\n\n"
                        "| 时间 | 运行 | 游戏日 | 分数 | 评级 | 步数 | 备注 |\n"
                        "|---|---|---|---|---|---|---|\n", mode="w")

        self._write(self.report,
                    f"# 运行 {self.run_id}\n\n"
                    f"- 报告文件：`{self.report.name}`（本次运行专用，不会被后续运行覆盖）\n"
                    f"- 全部历史：`logs/history.md`（跨运行，一个文件看全）\n"
                    f"- 逐 agent 原始日志：`logs/agents/*.jsonl`（跨运行累积）\n\n",
                    mode="w")

    # -- primitives --------------------------------------------------------

    @staticmethod
    def _append(path: Path, text: str) -> None:
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass

    @staticmethod
    def _write(path: Path, text: str, mode: str = "a") -> None:
        try:
            with path.open(mode, encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass

    # -- human-readable report --------------------------------------------

    def line(self, text: str) -> None:
        self._append(self.report, text.rstrip() + "\n")

    def section(self, title: str) -> None:
        self._append(self.report, f"\n## {title}\n\n")

    def table_header(self, columns: list[str]) -> None:
        self._append(self.report, "| " + " | ".join(columns) + " |\n")
        self._append(self.report, "|" + "|".join(["---"] * len(columns)) + "|\n")

    def table_row(self, cells: list) -> None:
        safe = [str(c).replace("|", "/").replace("\n", " ").strip() for c in cells]
        self._append(self.report, "| " + " | ".join(safe) + " |\n")
        # Mirror every day into the continuous history so one file has the whole story.
        self._append(self.history,
                     f"| {time.strftime('%m-%d %H:%M')} | {self.run_id} | "
                     + " | ".join(safe) + " |\n")

    def history_line(self, text: str) -> None:
        self._append(self.history, text.rstrip() + "\n")

    # -- per-agent machine-readable stream ---------------------------------

    def agent(self, name: str, **fields) -> None:
        """Append one event for one agent."""
        event = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "run": self.run_id}
        event.update(fields)
        self._append(self.agents / f"{name}.jsonl",
                     json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self.feed(name, summarize(name, fields))

    # -- the on-screen feed ------------------------------------------------

    def attach_overlay(self, push) -> None:
        """Give the log somewhere to send lines for the in-game panel.

        push(batch) is called with a list of {"tag", "text"} when there is something worth showing.
        """
        self._push = push

    def feed(self, tag: str, text: str) -> None:
        """Queue one line for the on-screen panel, without spamming the API per event."""
        if self._push is None or not text:
            return

        self._feed.append({"tag": tag, "text": text})

        now = time.monotonic()
        if len(self._feed) < 8 and now - self._last_push < 1.5:
            return

        batch, self._feed = self._feed, []
        self._last_push = now
        try:
            self._push(batch)
        except Exception:
            pass        # the panel is a nicety; it must never break the run

    # -- convenience -------------------------------------------------------

    def event(self, kind: str, **fields) -> None:
        self.agent("run", event=kind, **fields)


def summarize(name: str, f: dict) -> str:
    """One readable line for the audience, per agent.

    The structured streams are for the machine; this is what a person watching the stream reads, so
    it keeps the decision and drops the bookkeeping.
    """
    def short(value, limit=90) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        return str(value).replace("\n", " ")[:limit]

    if name == "planner":
        goals = f.get("goals") or []
        first = short(goals[0] if goals else "", 80)
        return f"{short(f.get('focus'), 70)}" + (f" · {first}" if first else "")

    if name == "streamer":
        flag = "OK " if f.get("ok") else "ERR"
        detail = short(f.get("reason"), 46) if f.get("ok") else short(f.get("detail"), 60)
        return f"{short(f.get('action'), 14):<14} {short(f.get('args'), 34):<34} {flag} {detail}"

    if name == "supervisor":
        return f"{short(f.get('status'), 8)} {short(f.get('note'), 90)}"

    if name == "referee":
        return f"score={f.get('score')} ({short(f.get('verdict'), 8)}) {short(f.get('why'), 90)}"

    if name == "watcher":
        what = f.get("action") or ""
        because = f.get("reason") or f.get("stopped") or ""
        remedy = f" -> {f.get('remedy')}" if f.get("remedy") else ""
        return f"{short(f.get('event'), 12)} {short(what, 12)} {short(because, 40)}{remedy}"

    if name == "doctor":
        return f"[{short(f.get('defect_class'), 10)}] {short(f.get('cause'), 90)}"

    if name == "pipeline":
        return f"{short(f.get('stage'), 8)} {short(f.get('event'), 14)} {short(f.get('detail'), 70)}"

    if name == "witness":
        mark = "世界变了" if f.get("worked") else "世界没变"
        return f"{short(f.get('action'), 12)} {mark} ({short(f.get('evidence'), 50)})"

    if name == "learning":
        return f"{short(f.get('kind'), 12)}+{short(f.get('action'), 12)} " + \
               ("成功" if f.get("worked") else "失败")

    if name == "gatekeeper":
        return short(f.get("note"), 100)

    if name == "prescription":
        return f"{short(f.get('action'), 12)} 前置不足 -> 先做 {short(f.get('satisfied_with'), 12)}"

    if name == "memory":
        return short(f.get("summary") or f.get("fact") or f.get("text"), 100)

    if name == "gaps":
        return f"{short(f.get('action'), 12)} {short(f.get('verdict'), 8)}"

    if name in ("defect", "outcome"):
        return short(f.get("title") or f.get("detail") or f, 100)

    return short({k: v for k, v in f.items() if k not in ("t", "run")}, 100)
