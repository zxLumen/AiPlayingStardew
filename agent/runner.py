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

    def __init__(self, client: StateClient, ledger: DefectLedger, runlog=None, log=print,
                 design_character=None) -> None:
        self.client = client
        self.ledger = ledger
        self.runlog = runlog
        self.log = log

        # Given a list of farm types, returns the character design to create. Supplied by the agent
        # side, because designing a character is a judgement, not a stage.
        self.design_character = design_character
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

    def stage_save(self, ai_save: dict | None, create=None, timeout_s: float = 240.0) -> dict | None:
        """Get into the agent's own save - creating one if there is not one yet.

        "No save on record" is not a failure: on a first run it is the normal state, and the job is
        then to make one. Reporting it as a blocker is how an unattended run spends the night parked
        at the title screen.

        Never clicks a slot that is not the agent's own.
        """
        if ai_save is None:
            ai_save = self._create(create)

        started = time.monotonic()
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
                return ai_save

            menu = ui.get("menu_name") or ""
            slots = ui.get("save_slots") or []

            # NOBODY'S FOCUS IS STOLEN HERE EITHER. This used to re-assert the foreground every ten
            # seconds "in case the desktop stole it", which is the opposite of what is wanted: the
            # person at the keyboard is working on the other screen, and the game now keeps running
            # while it is behind them (see the mod: pause-when-unfocused is held off). Asking for the
            # foreground is not needed to receive a click any more, and doing it every ten seconds
            # makes the machine unusable to work next to.

            if menu and menu not in seen_menus:
                seen_menus.append(menu)

            if slots:
                slot = self._match_slot(slots, ai_save)

                if slot is None:
                    # The recorded save is gone - deleted, renamed, or a different machine. The
                    # honest move is to make a new one rather than sit here failing.
                    names = [s.get("farmer_name") for s in slots]
                    self.log(f"[流水线] 存档列表里没有 '{want}'（只有 {names}）—— 改为新建一局")
                    self._note("save", "record missing, creating instead", saw=names)
                    ai_save = self._create(create)
                    want = str(ai_save.get("character_name") or "")
                    started = time.monotonic()          # a fresh clock for the load itself
                    continue

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
                    handle = self._control_id(ui, "Load")
                    if not handle:
                        self.log(f"[流水线] 标题界面里没有 Load 这个控件："
                                 f"{[b.get('name') for b in (ui.get('buttons') or [])][:8]}")
                        time.sleep(1.5)
                        continue

                    result = self.client.action("menu_click", id=handle)
                    receipt = result.get("data") if isinstance(result.get("data"), dict) else {}
                    # SAY WHAT CAME BACK. This click used to be thrown away, so a refused or unheard
                    # click on the title screen looked exactly like a game that was still loading - and
                    # the loop sat here for a minute at a time with nothing on the console. What it was
                    # actually doing: clicking `{'name': 'Load'}` every ten seconds, being told "that
                    # name matches two controls - use buttons#1", and clicking it again.
                    self.log(f"[流水线] 点 Load -> id={handle} ok={result.get('ok')} "
                             f"{receipt.get('verdict') or ''}"
                             f"{'' if receipt.get('active', True) else ' [窗口不是活动的]'}"
                             f" | {str(result.get('detail'))[:110]}")
                    self._note("save", "clicked Load", ok=bool(result.get("ok")), id=handle,
                               verdict=str(receipt.get("verdict") or ""),
                               detail=str(result.get("detail", ""))[:120])
                time.sleep(1.5)
                continue

            time.sleep(1.5)

        self._failed("save", f"{timeout_s:.0f}秒没进去；见过的界面 {seen_menus}",
                     cost_s=time.monotonic() - started)
        raise StageFailure("save", f"超时；见过的界面 {seen_menus}")

    @staticmethod
    def _control_id(ui: dict, wanted: str) -> str:
        """The handle of a control, found by name or label.

        The label alone is not a handle. "Load" matched TWO controls on the title screen - the button
        and something else carrying the same word - so the bridge refused it as ambiguous, correctly,
        and this loop re-sent that same refusal every ten seconds without reading a word of it. The
        receipt had the answer in it the whole time: use buttons#1.
        """
        buttons = ui.get("buttons") or []
        want = str(wanted).lower()

        for button in buttons:
            for field in ("name", "label", "id"):
                if str(button.get(field) or "").lower() == want:
                    return str(button.get("id") or button.get("name") or "")

        for button in buttons:
            if str(button.get("name") or "").lower().startswith(want):
                return str(button.get("id") or button.get("name") or "")

        return ""


    def _create(self, create) -> dict:
        """Have the agent make itself a save. `create` returns the record of what it made."""
        if create is None:
            self._failed("save", "没有存档，也没有创建存档的能力")
            raise StageFailure("save", "没有存档且无法创建")

        self.log("[流水线] 还没有属于自己的存档 —— 让主播自己捏一个")
        self._note("save", "creating")

        try:
            fresh = create()
        except Exception as exc:
            self._failed("save", f"创建存档失败: {type(exc).__name__}: {exc}")
            raise StageFailure("save", f"创建存档失败: {exc}")

        if not fresh:
            self._failed("save", "创建流程跑完了，但拿不到存档记录")
            raise StageFailure("save", "创建存档失败")

        self._note("save", "created", farmer=fresh.get("character_name"))
        return fresh

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

    def ensure(self, ai_save: dict | None, create=None) -> dict | None:
        """Get from nothing to a playable day, or raise with the stage that stopped us.

        `create` is how the AI comes to have a save of its own in the first place. Without it, a
        missing config/ai_save.json is not a problem to be solved but a dead end - and a dead end
        before the play loop is exactly how an unattended run spends the whole night parked at the
        title screen, retrying every minute, achieving nothing and saying nothing about it.
        """
        self.stage_game()
        self.stage_api()
        return self.stage_save(ai_save, create=create)
