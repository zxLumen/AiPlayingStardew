"""Applying a diagnosis: the hand.

A diagnosis that changes nothing is just a longer log line. This is where a cause turns into a
change of behaviour, and where the change is checked:

    precondition    enforced before the action is dispatched, and satisfied automatically when the
                    doctor named something that satisfies it ("water_all needs a full can: refill")
    plan_constraint written into memory, where the planner reads it
    capability      a defect on the developer queue, with the evidence attached
    ban             the only case where an action is actually taken away

And then it verifies. A fix that is not verified is a guess, and the difference between this and
what came before is exactly that: the old loop's last step was blacklisting, and nothing ever asked
whether the blacklist was the right answer.
"""

from __future__ import annotations

import time

import authenticity
from defects import PROJECT_ROOT
from witness import ACTION_NAMES, resolve_action


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(text).lower()).strip("-")[:48] or "x"


def _resolve(state: dict, path: str):
    """Read one value out of the live state by a dotted name ("held.charges")."""
    if not path:
        return None

    player = state.get("player") or {}
    roots = {
        "held": player.get("current_item"),
        "player": player,
        "game": state.get("game") or {},
        "ui": state.get("ui") or {},
        "work": (state.get("location") or {}).get("work") or {},
        "money": player.get("money"),
        "stamina": player.get("stamina"),
    }

    parts = str(path).split(".")
    node = roots.get(parts[0], state.get(parts[0]))
    for part in parts[1:]:
        if isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def _compare(fact, op: str, want) -> bool | None:
    """True / False, or None when this cannot be judged from the numbers available."""
    if fact is None:
        return None
    try:
        if op == ">":
            return float(fact) > float(want)
        if op == ">=":
            return float(fact) >= float(want)
        if op == "<":
            return float(fact) < float(want)
        if op == "<=":
            return float(fact) <= float(want)
        if op == "==":
            return fact == want
        if op == "!=":
            return fact != want
        if op == "contains":
            return str(want) in str(fact)
    except (TypeError, ValueError):
        return None
    return None


# Is the hand gate allowed to BLOCK anything?
#
# It was on for one afternoon and turned a bad situation into a deadlock. The gate blocks every action
# while a hand is full, and the thing that satisfies it - clicking a free bag slot - does nothing in the
# real game: measured, `menu_click 12` on an empty slot, and the held stack did not move off the menu's
# hand. A gate whose remedy does not exist is worse than the bug it was written for, because the original
# bug at least let the day continue.
#
# Detection stays ON: the witness says the hand is not empty, the doctor can see both hands, and every
# journal line carries `[hand: ...]`. Blocking comes back when putting an item down has been PROVEN -
# not when it has been written.
HAND_GATE = False


class Prescriber:
    """Turns the doctor's prescription into a change, and checks whether it worked."""

    def __init__(self, client, ledger, learning, store, log=print) -> None:
        self.client = client
        self.ledger = ledger
        self.learning = learning
        self.store = store
        self.log = log
        self.root = PROJECT_ROOT
        self.preconditions: dict[str, dict] = {}
        self.attempts: dict[str, int] = {}

    # -- what the play loop asks -------------------------------------------

    def precondition_for(self, action: str) -> dict | None:
        return self.preconditions.get(action)

    def unmet(self, action: str, state: dict, args: dict | None = None) -> bool:
        """Whether this action must not be dispatched as asked, right now."""
        # The hand gate, first and unconditional. This is not a prescription anybody had to discover -
        # it is a fact about the world: while something is held, a shop will not close and its clicks
        # are swallowed, so every other action is guaranteed to fail. The model HAD the fact
        # (ui.held_item is in its payload) and asked for something else anyway, which is exactly why
        # this is enforced by the harness rather than requested in a prompt - the same reason a
        # watering can gets filled before watering.
        if HAND_GATE and self._hand_full(state) and not self._is_put_down(action, args):
            return True

        rule = self.preconditions.get(action)
        if not rule:
            return False

        requires = rule.get("requires") or {}
        verdict = _compare(_resolve(state, requires.get("fact")),
                           str(requires.get("op") or ">"),
                           requires.get("value"))
        return verdict is False        # unknown means "try anyway", not "block"

    def satisfy(self, action: str, state: dict, args: dict | None = None) -> str | None:
        """Run whatever satisfies the precondition first. Returns what it ran."""
        if HAND_GATE and self._hand_full(state) and not self._is_put_down(action, args):
            return self._put_down(state)

        rule = self.preconditions.get(action)
        if not rule:
            return None

        helper = str(rule.get("satisfy_with") or "").strip()
        if not helper:
            return None

        # A prescription may name an action the model invented. Match it against what exists, or
        # refuse: running a name the bridge does not know is not a repair, it is a typo.
        resolved = resolve_action(helper)
        if resolved is None:
            self.log(f"[处方] 开出的 '{helper}' 不是桥里存在的动作，跳过（可用：{', '.join(ACTION_NAMES[:8])}…）")
            return None

        helper = resolved
        self.log(f"[处方] {action} 的前置不满足，先执行 {helper}（{rule.get('why', '')}）")
        result = self.client.action(helper)
        self.log(f"[处方]   {helper} -> ok={result.get('ok')} {str(result.get('detail'))[:80]}")
        return helper

    # ------------------------------------------------------------------- the hand
    #
    # There are two places a stack can be carried and only one of them used to be watched: the player's
    # cursor, and the open menu's own held item. A shop purchase lands on the MENU's hand. While it is
    # there the shop will not close (Stardew's own readyToClose() says no) and every later click is
    # swallowed - which the agents spent a whole day reading as "the bridge is lying about the shop".

    def _hand_full(self, state: dict) -> str:
        """What is being carried, wherever it is being carried, or ""."""
        menu_hand = str((state.get("ui") or {}).get("held_item") or "").strip()
        cursor = str(((state.get("player") or {}).get("cursor_item") or {}).get("name") or "").strip()
        return menu_hand or cursor

    def _is_put_down(self, action: str, args: dict | None) -> bool:
        """Whether this action is itself a way of putting the carried stack down."""
        args = args or {}

        if action == "menu_click":
            # A shop's bag slots are named `inventory...` in ui.buttons; clicking one places the stack.
            return str(args.get("name") or "").startswith("inventory")

        if action == "press_key":
            return str(args.get("key") or "").lower() == "escape"

        return action in ("noop", "stop")

    def _put_down(self, state: dict) -> str | None:
        """Put the carried stack down. Returns what it ran, or None when there is no way to."""
        ui = state.get("ui") or {}

        occupied = set()
        for slot in (state.get("inventory") or []):
            try:
                occupied.add(int(slot.get("slot")))
            except (TypeError, ValueError):
                pass

        def bag_index(name: str) -> int:
            digits = "".join(c for c in name.split(".")[-1] if c.isdigit())
            return int(digits) if digits else -1

        # EVERY bag slot, not only the ones whose name starts with "inventory".
        #
        # A shop's panel is 36 slots. The few that collide with a shelf row get prefixed
        # ("inventory.0"); the other thirty-two keep their plain number ("12"). Filtering on the prefix
        # therefore found four slots - the tool row, which is always full - concluded "no free slot",
        # and clicked the axe. That is the difference between putting the hand down and not being able
        # to act at all, and it is why this gate looked like a trap instead of a repair.
        bag = []
        for button in (ui.get("buttons") or []):
            name = str(button.get("name") or "")
            if not name:
                continue
            tail = name.split(".")[-1]
            if name.startswith("inventory") or tail.isdigit():
                bag.append(name)

        if not bag:
            # Either the hand is the player's cursor (not the menu's), or the panel is not addressable.
            self.log(f"[处方] 手上有东西（{self._hand_full(state)}），界面里没有可点的背包格 → 用 escape 放回")
            result = self.client.action("press_key", key="escape")
            self.log(f"[处方]   press_key escape -> ok={result.get('ok')} {str(result.get('detail'))[:80]}")
            return "press_key escape"

        free = [n for n in bag if bag_index(n) not in occupied]

        if not free:
            # Do NOT click a full slot "anyway". Placing onto an occupied slot can swap that item onto
            # the hand and leave the farmer worse off than before. A satisfier that cannot satisfy has
            # to SAY so - the gate it feeds is blocking everything else, so silence is a deadlock.
            self.log(f"[处方] 手上有东西（{self._hand_full(state)}），但背包没有空闲格 - 不冒险点已占用的格子")
            return None

        target = free[0]
        self.log(f"[处方] 手上有东西（{self._hand_full(state)}），先放进背包：menu_click {target}")
        result = self.client.action("menu_click", name=target)
        self.log(f"[处方]   menu_click {target} -> ok={result.get('ok')} {str(result.get('detail'))[:90]}")
        return f"menu_click {target}"

    def note_outcome(self, action: str, worked: bool | None) -> None:
        """The verification half: did the prescribed fix actually fix it?"""
        rule = self.preconditions.get(action)
        if not rule or worked is None:
            return

        key = rule.get("defect_key", "")
        if worked:
            self.attempts[action] = self.attempts.get(action, 0) + 1
            if self.attempts[action] >= 2:
                self.log(f"[处方] ✓ {action} 在加了前置条件后连续成功 —— 缺陷关闭")
                if key:
                    self.ledger.set_status(key, "fixed")
                self.learning.unblock(action)
                self.preconditions.pop(action, None)
            return

        # The fix did not take: the diagnosis was wrong, so say so rather than pretend.
        self.attempts[action] = 0
        if key:
            self.ledger.set_status(key, "open")

    # -- applying one diagnosis --------------------------------------------

    def apply(self, defect, verdict: dict, state: dict) -> str:
        """Make a diagnosis real. Returns a one-line account of what was done."""
        key = getattr(defect, "key", "")
        cause = str(verdict.get("cause") or "").strip()
        kind = str(verdict.get("class") or "").strip()
        prescription = verdict.get("prescription") or {}

        if not isinstance(prescription, dict) or not prescription:
            msg = f"医生给出结论但没开方子（{cause}）"
            self._record(defect, msg, owner="agent", status="open")
            return msg

        pkind = str(prescription.get("kind") or "").strip()

        if pkind == "precondition":
            action = str(prescription.get("action") or "").strip()
            if not action:
                return self._record(defect, "前置条件没写动作名，无法执行", "agent", "open")

            self.preconditions[action] = {
                "requires": prescription.get("requires") or {},
                "satisfy_with": str(prescription.get("satisfy_with") or ""),
                "why": str(prescription.get("why") or cause),
                "defect_key": key,
            }
            self.attempts[action] = 0
            self.learning.unblock(action)      # a precondition makes the action usable again

            msg = (f"给 {action} 加了前置条件：{prescription.get('requires')}"
                   f"，不足时先 {prescription.get('satisfy_with') or '(无)'}。原因：{cause}")
            self._record(defect, msg, owner="watcher", status="mitigated")
            return msg

        if pkind == "plan_constraint":
            text = str(prescription.get("text") or "").strip()
            if not text:
                return self._record(defect, "计划约束是空的", "agent", "open")

            try:
                self.store.append_lessons([f"[医生开的约束] {text}"])
            except Exception as exc:
                return self._record(defect, f"约束写不进记忆：{exc}", "agent", "open")

            msg = f"写成计划约束：{text}"
            self._record(defect, msg, owner="agent", status="mitigated")
            self.log(f"[处方] {msg}")
            return msg

        if pkind == "capability":
            title = str(prescription.get("title") or cause or "缺失的能力").strip()
            detail = str(prescription.get("detail") or "").strip()

            self.ledger.raise_defect(
                f"cap:{_slug(title)}", title, "developer",
                detail=f"{detail}（原缺陷：{cause}）",
                evidence=str(getattr(defect, "detail", ""))[:200],
            )
            msg = f"桥缺能力：{title}"
            self._record(defect, msg, owner="developer", status="mitigated")
            self.log(f"[处方] !!! {msg} —— 已进开发者队列")
            return msg

        if pkind == "declare":
            full = str(prescription.get("action") or "").strip()
            cls = str(prescription.get("class") or "").strip()
            how = str(prescription.get("how") or cause).strip()

            action = resolve_action(full) or full
            if not action:
                return self._record(defect, "声明没写动作名，无法执行", "agent", "open")

            # The declaration is a sentence; the audit still checks it against the code, and a
            # teleport declared as "action" is caught regardless of what anybody wrote.
            if not authenticity.save_declaration(self.root, action, cls, how=how):
                return self._record(defect, f"声明类别 '{cls}' 不合法（只能是 "
                                            f"{' / '.join(authenticity.KINDS)}）", "agent", "open")

            msg = f"声明 {action} 属于 {cls}：{how}"
            self._record(defect, msg, owner="agent", status="fixed")
            return msg

        if pkind == "ban":
            action = str(prescription.get("action") or "").strip()
            why = str(prescription.get("why") or cause).strip()
            if not action:
                return self._record(defect, "要禁用的动作没写名字", "agent", "open")

            self.learning.blacklist(action, f"医生判定：{why}")
            msg = f"禁用 {action}：{why}"
            self._record(defect, msg, owner="agent", status="fixed")
            return msg

        return self._record(defect, f"看不懂的处方类型 '{pkind}'", "agent", "open")

    # -- bookkeeping -------------------------------------------------------

    def _record(self, defect, message: str, owner: str, status: str) -> str:
        self.log(f"[处方] {message}")

        if self.ledger is not None:
            key = getattr(defect, "key", "")
            if key:
                self.ledger.raise_defect(key, getattr(defect, "title", key), owner,
                                         detail=message, evidence=message)
                if status in ("fixed", "mitigated"):
                    self.ledger.set_status(key, status)

        if self.store is not None:
            try:
                self.store.append_lessons([f"[处方] {message}"])
            except Exception:
                pass

        return message
