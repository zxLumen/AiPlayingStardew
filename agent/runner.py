"""The runner: the thing that owns the pipeline, not just a game day.

Everything built so far owned a slice. The streamer owned a step, the planner owned a day, the
watcher owned a running action. Nothing owned the pipeline *itself* - so a failure before the play
loop even starts (the game not launching, the mod not loading, the save not loading) had no
detector, no retry and no record. The only thing that could notice was the person watching.

Here every stage has a name, a retry budget, a heal, and an outcome that is written down:

    game   the process is up
    api    the mod answered
    save   the agent's own save is in game
    day    a day was played

A stage that cannot be got past becomes a row in the defect ledger with the stage's name on it.
That is the difference between "someone has to notice" and "the system knows".
"""

from __future__ import annotations

import time
from pathlib import Path

import game_process
from defects import DefectLedger
from state_client import StateClient

STAGES = {
    "game": "启动游戏进程",
    "api": "等待 mod 接口",
    "save": "读入 AI 自己的存档",
    "day": "玩完一天",
}


class StageFailure(Exception):
    """A pipeline stage that could not be got past."""

    def __init__(self, stage: str, detail: str) -> None:
        super().__init__(f"{STAGES.get(stage, stage)}: {detail}")
        self.stage = stage
        self.detail = detail


class Pipeline:
    """Owns every stage before a day can be played, and records what went wrong."""

    def __init__(self, client: StateClient, ledger: DefectLedger, runlog=None, log=print) -> None:
        self.client = client
        self.ledger = ledger
        self.runlog = runlog
        self.log = log
        self.notes: list[str] = []

    # -- bookkeeping -------------------------------------------------------

    def _note(self, stage: str, event: str, **fields) -> None:
        if self.runlog is not None:
            try:
                self.runlog.agent("pipeline", stage=stage, event=event, **fields)
            except Exception:
                pass

    def _failed(self, stage: str, detail: str, *, cost_s: float = 0.0) -> None:
        # A pipeline stage is bridge or configuration territory: no amount of agent cleverness gets
        # past a game that will not launch or a save the bridge cannot click into.
        self.ledger.raise_defect(
            f"stage:{stage}",
            f"流水线卡在「{STAGES.get(stage, stage)}」",
            "developer",
            detail=detail, cost_s=cost_s, evidence=detail,
        )
        self._note(stage, "failed", detail=detail)

    # -- stages ------------------------------------------------------------

    def stage_game(self) -> None:
        """The game process must exist and stay up."""
        started = time.monotonic()

        for attempt in range(3):
            if game_process.is_running():
                self._note("game", "ok", attempt=attempt)
                return

            self.log(f"[流水线] 游戏没在运行，启动（第 {attempt + 1} 次）")
            try:
                game_process.launch()
            except Exception as exc:
                self._failed("game", f"启动失败: {exc}", cost_s=time.monotonic() - started)
                time.sleep(5)
                continue

            time.sleep(8)

        self._failed("game", "启动 3 次都没起来", cost_s=time.monotonic() - started)
        raise StageFailure("game", "启动 3 次都没起来")

    def stage_api(self, timeout_s: float = 240.0) -> None:
        """The mod must answer. Relaunching is the only heal we have for a wedged one."""
        started = time.monotonic()

        if self.client.wait_until_up(timeout_s=timeout_s):
            self._note("api", "ok", seconds=round(time.monotonic() - started, 1))
            return

        self.log("[流水线] 接口一直没响应 —— 重启游戏")
        try:
            game_process.stop()
            time.sleep(3)
            game_process.launch()
        except Exception as exc:
            self._note("api", "heal failed", detail=str(exc))

        if self.client.wait_until_up(timeout_s=timeout_s):
            self._note("api", "ok after relaunch")
            return

        self._failed("api", "接口 2 轮都没起来", cost_s=time.monotonic() - started)
        raise StageFailure("api", "接口 2 轮都没起来")

    def stage_save(self, ai_save: dict | None, timeout_s: float = 180.0) -> None:
        """Get into the agent's own save - and never click any other slot.

        This stage failed silently in a throwaway script once and parked the game at the load menu.
        Here it either succeeds, or it says exactly what it saw and writes it down.
        """
        started = time.monotonic()

        if ai_save is None:
            self._failed("save", "不知道自己的存档长什么样（config/ai_save.json 缺失）")
            raise StageFailure("save", "没有 AI 存档记录")

        want = str(ai_save.get("character_name") or "")
        load_at = 0.0
        slot_at = 0.0
        focus_at = 0.0
        seen_menus: list[str] = []

        # Driving menus needs the game window in front: the game does not act on menu input while
        # its window is behind, so the clicks "succeed" and nothing happens. Belt and braces - the
        # bridge now enforces this itself, but the stage should not depend on another component
        # being correct.
        self.client.action("focus_window")

        while time.monotonic() - started < timeout_s:
            state = self.client.state()
            game = state.get("game") or {}
            ui = state.get("ui") or {}

            if game.get("has_save"):
                farmer = (state.get("player") or {}).get("name")
                if farmer and want and farmer != want:
                    # Never play on the wrong save. This is the one mistake that is unrecoverable.
                    self._failed("save", f"进错了档：在玩 '{farmer}'，应该是 '{want}'")
                    raise StageFailure("save", f"进错了档（{farmer} != {want}）")

                self._note("save", "ok", farmer=farmer,
                           seconds=round(time.monotonic() - started, 1))
                return

            menu = ui.get("menu_name") or ""
            slots = ui.get("save_slots") or []

            # Re-assert focus periodically: the desktop can steal it back mid-load.
            if time.monotonic() - focus_at > 10.0:
                focus_at = time.monotonic()
                self.client.action("focus_window")

            if menu and menu not in seen_menus:
                seen_menus.append(menu)

            if slots:
                slot = self._match_slot(slots, ai_save)
                if slot is None:
                    names = [s.get("farmer_name") for s in slots]
                    self._failed("save", f"存档列表里没有 '{want}'（只有 {names}）")
                    raise StageFailure("save", f"存档列表里没有 '{want}'")

                # Click the slot ONCE and then give the game time to load it. Clicking every couple
                # of seconds restarts the load it is already doing, which is how a save can look
                # permanently unloadable while the clicks all "succeed".
                if time.monotonic() - slot_at > 15.0:
                    slot_at = time.monotonic()
                    result = self.client.action("menu_click", name=str(slot["index"]))
                    self._note("save", "clicked slot", index=slot["index"],
                               ok=bool(result.get("ok")), detail=str(result.get("detail", ""))[:120])

                time.sleep(2.0)
                continue

            if menu == "TitleMenu":
                if time.monotonic() - load_at > 10.0:
                    load_at = time.monotonic()
                    self.client.action("menu_click", name="Load")
                time.sleep(1.5)
                continue

            time.sleep(1.5)

        self._failed("save", f"{timeout_s:.0f}秒没进去；见过的界面 {seen_menus}",
                     cost_s=time.monotonic() - started)
        raise StageFailure("save", f"超时；见过的界面 {seen_menus}")

    # -- helper ------------------------------------------------------------

    def _match_slot(self, slots: list[dict], ai_save: dict) -> dict | None:
        """Find the AI's slot by name. Deliberately strict: matching the wrong save is fatal."""
        want = str(ai_save.get("character_name") or "").strip().lower()
        if not want:
            return None

        for slot in slots:
            names = [
                str(slot.get("farmer_name") or ""),
                str(slot.get("slot_name") or ""),
            ]
            if any(n.strip().lower() == want for n in names):
                return slot

        return None

    # -- whole run ---------------------------------------------------------

    def ensure(self, ai_save: dict | None) -> None:
        """Get from nothing to a playable day, or raise with the stage that stopped us."""
        self.stage_game()
        self.stage_api()
        self.stage_save(ai_save)
