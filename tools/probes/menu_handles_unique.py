"""Probe: does the bridge hand out handles that cannot point at two things?

A behavioural test, not a compile test. It opens the pause menu (what pressing the key does), asks the
bridge for every control in it, and checks the three claims the fix makes:

  1. every control carries an id, and no two controls share one - a handle names exactly one thing;
  2. every control says which grid it is on (bag / container / shop / control);
  3. a handle that means nothing is REFUSED with a receipt, and a name that means two things is
     refused as ambiguous rather than quietly clicked on whichever came first.

The last one is the whole point: before this, "no match", "one match" and "two matches" all returned a
control and the first one won, so a click could land somewhere the agent never asked for and still
report OK.

Run by tools/bridge.py after a deploy, or by hand:
    python tools/probes/menu_handles_unique.py
Exit code 0 = the bridge behaves as this probe claims.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8787"
FAILURES: list[str] = []


def post(action: str, **args) -> dict:
    body = json.dumps({"action": action, **args}).encode("utf-8")
    request = urllib.request.Request(f"{BASE}/action", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"ok": False, "detail": raw[:300]}
    except Exception as exc:
        return {"ok": False, "detail": f"request failed: {exc}"}


def get_state() -> dict:
    try:
        with urllib.request.urlopen(f"{BASE}/state", timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def check(condition: bool, message: str) -> None:
    print(("  ok   " if condition else "  FAIL ") + message)
    if not condition:
        FAILURES.append(message)


def wait_for_save(seconds: float = 120.0) -> bool:
    """Wait for a save to actually be loaded - the agent may still be driving the title screen."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        state = get_state()
        player = state.get("player") or {}
        if player.get("name") and player.get("current_location"):
            return True
        time.sleep(4)
    return False


def main() -> int:
    print("probe: handles name exactly one control")
    if not wait_for_save():
        print("no save loaded within the time budget - nothing could be tested")
        return 1

    # The farmer has to be free: at the start of a day the agent may still be in a dialogue or a menu.
    # Waiting is honest - the point is to test the bridge, not to time the harness.
    opened: dict = {}
    deadline = time.time() + 90
    while time.time() < deadline:
        opened = post("open_screen", screen="bag")
        if opened.get("ok"):
            break
        time.sleep(4)

    check(bool(opened.get("ok")), f"open the bag: {str(opened.get('detail'))[:90]}")
    if not opened.get("ok"):
        return 1

    try:
        probe = post("menu_probe")
        check(bool(probe.get("ok")), f"menu_probe answers: {str(probe.get('detail'))[:90]}")
        controls = ((probe.get("data") or {}).get("buttons") or [])
        check(len(controls) > 0, f"the open menu reported {len(controls)} controls")

        ids = [c.get("id") or "" for c in controls]
        check(all(ids), "every control carries an id")
        check(len(set(ids)) == len(ids), f"ids are unique ({len(set(ids))} of {len(ids)})")
        check(all(c.get("grid") for c in controls), "every control says which grid it is on")

        # A handle that means nothing must be refused, and say why.
        unknown = post("menu_click", id="no-such-control-anywhere")
        receipt = unknown.get("data") or {}
        check(not unknown.get("ok"), "an unknown handle is refused")
        check(receipt.get("verdict") == "HANDLE_UNKNOWN",
              f"and the receipt says so: verdict={receipt.get('verdict')!r}")
        check("available" in str(unknown.get("detail")),
              "and it lists the handles that would have worked")

        # If this menu happens to have two controls sharing a name, the ambiguity path can be tested
        # for real. When it does not, say so rather than pretending it was checked.
        names: dict[str, str] = {}
        duplicated = None
        for control in controls:
            name = str(control.get("name") or "")
            if not name:
                continue
            if name in names:
                duplicated = name
                break
            names[name] = str(control.get("id") or "")

        if duplicated:
            ambiguous = post("menu_click", name=duplicated)
            receipt = ambiguous.get("data") or {}
            check(not ambiguous.get("ok"), f"the shared name {duplicated!r} is refused, not guessed")
            check(receipt.get("verdict") == "HANDLE_AMBIGUOUS",
                  f"and the receipt says why: verdict={receipt.get('verdict')!r}")
            check(bool(receipt.get("candidates")),
                  f"and names the candidates: {receipt.get('candidates')}")
        else:
            print("  note  this menu has no duplicated names")

        # A HANDLE THAT COVERS A WHOLE GRID is ambiguous too: "inventory" is not a control, it is a
        # row of them, and it used to be resolved to the first one. The two grids of an ItemGrabMenu
        # both answered to the same slot names for exactly this reason.
        sources: dict[str, int] = {}
        for control in controls:
            source = str(control.get("source") or "")
            if source:
                sources[source] = sources.get(source, 0) + 1
        grid_handle = next((s for s, count in sources.items() if count > 1), None)

        if grid_handle:
            ambiguous = post("menu_click", name=grid_handle)
            receipt = ambiguous.get("data") or {}
            check(not ambiguous.get("ok"), f"a whole grid ({grid_handle!r}) is refused, not guessed")
            check(receipt.get("verdict") == "HANDLE_AMBIGUOUS",
                  f"and the receipt says why: verdict={receipt.get('verdict')!r}")
            check(bool(receipt.get("candidates")),
                  f"and names the candidates: {receipt.get('candidates')}")
        else:
            print("  note  no handle here covers more than one control")
    finally:
        post("press_key", key="escape")

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed")
        return 1

    print("\nthe bridge names exactly one control per handle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
