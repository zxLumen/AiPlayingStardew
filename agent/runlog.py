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

    # -- convenience -------------------------------------------------------

    def event(self, kind: str, **fields) -> None:
        self.agent("run", event=kind, **fields)
