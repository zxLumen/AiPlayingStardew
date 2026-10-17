"""What the bridge DID, as opposed to what it was asked to do.

Every menu click now comes back with a receipt: the handle that was requested, the control it actually
resolved to, whether that handle named exactly one control, and what changed. Before this, a click that
landed on a different element than the one asked for was indistinguishable from a click that landed
correctly - both said OK, both left the world unchanged - so the only account of what happened was the
agent's own guess, and it wrote that guess down as a rule about the game. That is how a bug in the hand
turns into a superstition in the head.

The split this file exists to make:

  * a handle that names two controls is NOT a lesson about Stardew Valley - it is a defect in the hand,
    so it must never be learned as a rule about the game;
  * it is still worth surviving: the same defect will keep happening until it is repaired, so it is
    counted, it is said out loud, and the way around it is remembered separately.

Nothing here is a threshold and nothing here decides anything. It is a count of what the bridge did
wrong, so that both the agent and whoever repairs the bridge can see it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "memory" / "bridge.json"

# Verdicts that are the bridge's fault rather than the game's answer. A game refusing a click (a shop
# with nowhere to put a purchase) is a correct answer and must not be counted here - only a click that
# did not go where it was told, or a handle that does not identify anything.
BRIDGE_FAULTS = {
    "HANDLE_AMBIGUOUS": "这个名字对应菜单里不止一个控件 - 桥挑了一个，没说话",
    "HANDLE_UNKNOWN": "这个名字在菜单里一个控件都不对应",
    "NO_CURSOR": "鼠标没能移过去，点击是旧路径凑合做的",
}

# Not defects and not answers: this is the instrument changing to suit a window that is in the
# background. It says "the press is in the window's queue, the game reads it next frame" - which is
# neither a success nor a failure, so counting it would put noise in front of the model.
NOT_A_DEFECT = ("POSTED", "MENU_CALL")

# A click that was delivered, was aimed at exactly one control, and changed nothing. That is NOT
# automatically a fault - the game is entitled to refuse - so it is recorded but never called a fault.
SUSPECT = {"SILENT_NOOP": "点到了唯一的目标，但世界什么都没变（可能是游戏有权拒绝）"}


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"verdicts": {}, "routes": {}}


# --- one measured fact about the window, remembered ---------------------------------------------
#
# A posted click is the instrument that needs neither the cursor nor the focus - and this game window
# does not read its message queue: the press goes in, nothing happens, and the next frame still shows
# the title screen. That was measured four times in a row before the escalation to the game's own menu
# handler got the farmer in. There is no reason to pay for the discovery again every single click
# (the check costs three seconds), so once the queue has been caught not being read, menu clicks go
# straight to the handler. Facts about the instrument, not about the game - hence here.
def queue_is_read() -> bool:
    return bool((_load().get("input") or {}).get("queue_read", True))


def note_queue(result: str) -> None:
    """result: "unread" when a posted click had to be escalated, "read" when posting worked."""
    data = _load()
    input_state = data.setdefault("input", {})
    wanted = result == "read"
    if input_state.get("queue_read", True) != wanted:
        input_state["queue_read"] = wanted
        input_state["noted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(data)


def _save(data: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def record(action: str, args: dict, receipt: dict) -> str:
    """Count one receipt. Returns the verdict, for the caller to log or act on."""
    verdict = str(receipt.get("verdict") or "")
    if not verdict or verdict in ("OK", "OK_OLD_PATH"):
        return verdict

    data = _load()
    key = f"{action}|{verdict}"
    entry = data["verdicts"].get(key) or {"verdict": verdict, "action": action, "count": 0}
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    entry["requested"] = receipt.get("requested") or ""
    entry["resolved"] = receipt.get("resolved") or ""
    entry["unique"] = bool(receipt.get("unique"))
    entry["matches"] = int(receipt.get("matches") or 0)
    entry["candidates"] = (receipt.get("candidates") or [])[:4]
    entry["args"] = args
    entry["changed"] = (receipt.get("changed") or [])[:4]
    entry["fault"] = verdict in BRIDGE_FAULTS
    data["verdicts"][key] = entry

    # When the bridge hands back a handle that works, that is the route to remember: the agent should
    # stop using the ambiguous name, not stop reading the menu.
    resolved = str(receipt.get("resolved") or "")
    if resolved and verdict in BRIDGE_FAULTS:
        data.setdefault("routes", {})[f"{action}|{receipt.get('requested')}"] = resolved

    _save(data)
    return verdict


def fault(action: str, args: dict, receipt: dict) -> bool:
    """True when this receipt is a defect in the bridge, not an answer from the game."""
    return str(receipt.get("verdict") or "") in BRIDGE_FAULTS


def route_for(action: str, requested: str) -> str:
    """A handle that DID work for the same request, if one was recorded."""
    return str((_load().get("routes") or {}).get(f"{action}|{requested}") or "")


def brief(limit: int = 4) -> str:
    """The bridge's own scoreboard, for the prompt. Facts and counts, no advice."""
    data = _load()
    rows = sorted((data.get("verdicts") or {}).values(),
                  key=lambda r: -int(r.get("count", 0)))
    rows = [r for r in rows if int(r.get("count", 0)) > 0][:limit]
    if not rows:
        return ""

    lines = ["桥自己的记录（这是桥的问题，不是这个游戏的规律 - 别把它当成游戏知识）："]
    for row in rows:
        what = BRIDGE_FAULTS.get(row["verdict"]) or SUSPECT.get(row["verdict"]) or row["verdict"]
        detail = f"要的是 {row.get('requested')!r}"
        if row.get("resolved"):
            detail += f"，实际派发到 {row.get('resolved')!r}"
        if row.get("matches", 0) > 1:
            detail += f"，这个名字对应 {row['matches']} 个控件：{row.get('candidates')}"
        worked = route_for(row.get("action", ""), str(row.get("requested") or ""))
        if worked:
            detail += f"；能用的是 {worked!r}"
        lines.append(f"- {row['verdict']} x{row['count']}（{what}）：{detail}")

    return "\n".join(lines)


def open_faults() -> list[dict]:
    """Every counted bridge fault, worst first - the repair queue."""
    data = _load()
    rows = [r for r in (data.get("verdicts") or {}).values() if r.get("fault")]
    return sorted(rows, key=lambda r: -int(r.get("count", 0)))
