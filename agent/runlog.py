"""Append-only logs: one file per run, one stream per agent.

Nothing here ever truncates. An overnight run that dies at 3am must still leave everything it
learned behind, and the next run must not erase the previous one's report.

Layout:
    logs/runs/run-<timestamp>.md     human report for one run (never rewritten)
    logs/agents/<agent>.jsonl        one JSON line per event, appended across all runs
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

# Pulls a coordinate out of the bridge's English failure text, so a failure can be shown in Chinese
# without inventing a translation for every sentence the bridge can produce.
_COORD = re.compile(r"\((\d{1,3}),\s*(\d{1,3})\)")


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


# Everything the audience reads is Chinese, except the agent names on the board. These turn the
# bridge's own vocabulary - action names, reason codes, verdicts, field names - into words.

ACTIONS = {
    "go_to": "走过去", "move": "挪一步", "warp": "传送", "enter": "进门",
    "exit_building": "出门", "sleep": "睡觉", "new_day": "过天",
    "work": "干活", "use_tool_at": "对着用工具", "use_tool": "用手上的工具",
    "interact": "互动", "interact_at": "对着互动", "select_slot": "换手持",
    "clear_area": "清理一片", "till_area": "翻一片地", "plant_all": "全部播种",
    "water_all": "全部浇水", "harvest_all": "全部收割", "plant": "种一格",
    "refill": "打满水壶", "scan": "扫一圈", "observe": "看一格",
    "menu_click": "点菜单", "dialogue_advance": "推进对话",
    "dialogue_choose": "选择对话", "skip_intro": "跳过开场",
    "focus_window": "让游戏到前台", "set_display": "调整画面",
    "pause_time": "暂停时间", "cancel_chore": "取消当前动作",
    "clear_event": "清除卡住的事件", "press_key": "按键", "stop": "停下",
    "noop": "待机", "chore": "查询当前动作", "game_data": "查游戏数据",
    "overlay": "更新面板",
}

REASONS = {
    "FAILED": "失败", "NOT_FREE": "玩家被占用", "NO_PATH": "无路可走",
    "NO_MENU": "没有菜单", "WRONG_TOOL": "用错工具", "ALREADY_OUTDOORS": "已经在户外",
    "NO_BED": "没有床", "NO_SUCH_BUTTON": "没有这个按钮", "BLOCKED": "被挡住",
}

VERDICTS = {
    "good": "好", "ok": "一般", "poor": "差", "bad": "很差",
    "excellent": "优秀", "great": "很好",
}

GAP_VERDICTS = {
    "BROKEN": "坏了", "LYING": "报成功却没用", "NOOP": "空转",
    "SLOW": "偏慢", "HEALTHY": "正常", "LOW-DATA": "数据不够",
}

BLOCKED = {
    "dialogue is open": "有对话打开",
    "dialogue is waiting for a choice": "对话等着选",
    "cutscene/event playing": "过场演出中",
    "farmer cannot move right now": "农民暂时不能动",
    "no active save": "没有存档",
}

PIPELINE_STAGES = {
    "game": "启动游戏", "api": "等接口", "save": "读档", "day": "玩一天",
}

FIELDS = {
    "thirsty_crops": "渴作物", "ready_crops": "可收作物", "empty_soil": "空地",
    "tillable": "可耕", "debris": "杂物", "money": "金钱",
    "charges": "壶中水", "stamina": "体力", "quests": "任务",
}

EVENTS = {
    "remedy": "处置", "gave_up": "放弃", "no progress": "没进展",
    "stalled": "卡住", "cleared": "已解开", "done": "完成",
    "ok": "成功", "failed": "失败", "clicked slot": "点了存档位",
    "stage failed": "阶段失败", "crash": "崩溃", "forced_day_end": "强制过天",
    "cleared_stuck_event": "清掉卡住的事件",
}

STOPPED = {
    "no progress": "没有进展", "stalled": "走不动",
    "over budget": "超出本轮预算", "timeout": "超时", "done": "完成",
}


def _zh(mapping: dict, key) -> str:
    text = str(key or "")
    return mapping.get(text, text)


def translate_evidence(text: str) -> str:
    """The witness writes evidence with field names and arrows; make it read like Chinese."""
    out = str(text or "")
    for english, chinese in FIELDS.items():
        out = out.replace(english, chinese)
    for english, chinese in BLOCKED.items():
        out = out.replace(english, chinese)
    return out.replace(" -> ", " → ").replace("->", "→")


def summarize(name: str, f: dict) -> str:
    """One readable Chinese line for the audience, per agent.

    The structured streams are for the machine; this is what a person watching the stream reads, so
    it keeps the decision and drops the bookkeeping - and it never shows them an English identifier
    they would have to be told the meaning of.
    """
    def short(value, limit=90) -> str:
        if value is None:
            return ""

        if isinstance(value, dict):
            parts = []
            for key, item in value.items():
                label = FIELDS.get(key, key)
                parts.append(f"{label}={item}")
            value = " ".join(parts)
        elif isinstance(value, list):
            value = " ".join(str(v) for v in value)

        return translate_evidence(str(value).replace("\n", " "))[:limit]

    def where(text: str) -> str:
        """Pull a coordinate out of the bridge's English failure text, if there is one."""
        match = _COORD.search(str(text or ""))
        return f"（坐标 {match.group(1)},{match.group(2)}）" if match else ""

    if name == "planner":
        goals = f.get("goals") or []
        first = str(goals[0])[:70] if goals else ""
        focus = str(f.get("focus") or "")[:80]
        return focus + (f" · {first}" if first else "")

    if name == "streamer":
        action = _zh(ACTIONS, f.get("action"))
        args = f.get("args") or {}
        target = args.get("target") or ""
        if not target and args.get("x") is not None:
            target = f"({args.get('x')},{args.get('y')})"

        if f.get("ok"):
            return f"{action} {target} → 成功 {str(f.get('reason') or '')[:40]}".strip()

        reason = _zh(REASONS, f.get("reason_code"))
        return f"{action} {target} → 失败 {reason}{where(f.get('detail'))}".strip()

    if name == "supervisor":
        return str(f.get("note") or "")[:96]

    if name == "referee":
        return f"评分 {f.get('score')}（{_zh(VERDICTS, f.get('verdict'))}）{str(f.get('why') or '')[:96]}"

    if name == "watcher":
        event = _zh(EVENTS, f.get("event"))
        action = _zh(ACTIONS, f.get("action"))
        held = f.get("reason") or f.get("stopped") or ""
        reason = _zh(STOPPED, held) if held else ""
        if reason == held:
            reason = translate_evidence(str(held))[:40]
        remedy = _zh(ACTIONS, f.get("remedy")) if f.get("remedy") else ""
        return f"{event}｜{action} {reason}".strip() + (f" → {remedy}" if remedy else "")

    if name == "doctor":
        cause = str(f.get("cause") or "")[:96]
        cls = {"cognitive": "认知", "capability": "能力", "bridge": "桥"}.get(
            str(f.get("defect_class") or ""), "")
        return (f"【{cls}缺口】" if cls else "") + cause

    if name == "pipeline":
        stage = _zh(PIPELINE_STAGES, f.get("stage"))
        event = _zh(EVENTS, f.get("event"))
        detail = str(f.get("detail") or "")
        if event == "成功" and "loaded" in detail:
            detail = f"已进入 {detail.split('loaded')[-1].strip()} 的存档"
        return f"{stage} {event} {detail[:70]}".strip()

    if name == "witness":
        action = _zh(ACTIONS, f.get("action"))
        mark = "世界变了" if f.get("worked") else "世界没变"
        return f"{action} {mark}（{short(f.get('evidence'), 60)}）"

    if name == "gatekeeper":
        return str(f.get("note") or "")[:110]

    if name == "prescription":
        return (f"{_zh(ACTIONS, f.get('action'))} 前置不足 → "
                f"先做了 {_zh(ACTIONS, f.get('satisfied_with'))}")

    if name == "gaps":
        return f"{_zh(ACTIONS, f.get('action'))} 判定 {_zh(GAP_VERDICTS, f.get('verdict'))}"

    if name in ("defect", "outcome"):
        return str(f.get("title") or f.get("detail") or "")[:110]

    return short({k: v for k, v in f.items() if k not in ("t", "run")}, 110)
