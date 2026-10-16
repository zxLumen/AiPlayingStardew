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


class Prescriber:
    """Turns the doctor's prescription into a change, and checks whether it worked."""

    def __init__(self, client, ledger, learning, store, log=print) -> None:
        self.client = client
        self.ledger = ledger
        self.learning = learning
        self.store = store
        self.log = log
        self.preconditions: dict[str, dict] = {}
        self.attempts: dict[str, int] = {}

    # -- what the play loop asks -------------------------------------------

    def precondition_for(self, action: str) -> dict | None:
        return self.preconditions.get(action)

    def unmet(self, action: str, state: dict) -> bool:
        """Whether this action's precondition is known to be unmet right now."""
        rule = self.preconditions.get(action)
        if not rule:
            return False

        requires = rule.get("requires") or {}
        verdict = _compare(_resolve(state, requires.get("fact")),
                           str(requires.get("op") or ">"),
                           requires.get("value"))
        return verdict is False        # unknown means "try anyway", not "block"

    def satisfy(self, action: str, state: dict) -> str | None:
        """Run whatever satisfies the precondition first. Returns what it ran."""
        rule = self.preconditions.get(action)
        if not rule:
            return None

        helper = str(rule.get("satisfy_with") or "").strip()
        if not helper:
            return None

        self.log(f"[处方] {action} 的前置不满足，先执行 {helper}（{rule.get('why', '')}）")
        result = self.client.action(helper)
        self.log(f"[处方]   {helper} -> ok={result.get('ok')} {str(result.get('detail'))[:80]}")
        return helper

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
