"""Do the detectors actually detect?

Every rule in authenticity.py was written in response to a real defect. That is the only thing that
makes a rule worth having - and it is also how a rule quietly becomes decoration: it is written, it
reports nothing, and "reports nothing" cannot be told apart from "everything is fine".

That happened the day the fidelity rule was added. It was written SPECIFICALLY to catch planting that
does not charge for the seed, and it could not see the call it was written for - the scan only read
methods shaped like an action, and the call sat in a helper. The audit printed `findings: 0`, and that
zero meant nothing at all.

So: every rule gets fed the code it was written for, plus a case proving it is not a blanket ban.
A detector that cannot catch the defect that motivated it is not a detector.

Run:  .venv\\Scripts\\python.exe agent\\detector_tests.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import authenticity as A  # noqa: E402


def src(arms: str, methods: str) -> str:
    """A minimal ActionProcessor.cs: the dispatch switch, plus whatever methods we want scanned."""
    return (
        "public static class ActionProcessor\n"
        "{\n"
        "    public static void Execute(string body)\n"
        "    {\n"
        "        string action = body;\n"
        "        (bool ok, string detail) = action switch\n"
        "        {\n"
        f"{arms}"
        '            _ => (false, "unknown"),\n'
        "        };\n"
        "    }\n"
        f"{methods}"
        "}\n"
    )


ARM_PLANT = '            "plant" => Plant(root),\n'
ARM_GO_TO = '            "go_to" => GoTo(root),\n'
ARM_TILL = '            "till_area" => TillArea(root),\n'

PLANT_METHOD = (
    "    private static (bool, string) Plant(JsonElement root)\n"
    "    {\n"
    '        return PlantAt(loc, 1, 2, f) ? (true, "planted") : (false, "no");\n'
    "    }\n"
)

# The defect itself: the crop goes in and nothing is taken for it.
PLANT_AT_UNCHARGED = (
    "    private static bool PlantAt(GameLocation loc, int x, int y, Farmer f)\n"
    "    {\n"
    "        dirt.plant(held.QualifiedItemId, f, false);\n"
    "        return true;\n"
    "    }\n"
)

# The fix: same call, and the seed is taken in the same reachable chain.
PLANT_AT_CHARGED = (
    "    private static bool PlantAt(GameLocation loc, int x, int y, Farmer f)\n"
    "    {\n"
    "        if (dirt.plant(held.QualifiedItemId, f, false))\n"
    "        {\n"
    "            ChargeSeed(f, held);\n"
    "            return true;\n"
    "        }\n"
    "        return false;\n"
    "    }\n"
    "\n"
    "    private static void ChargeSeed(Farmer farmer, StardewValley.Object seed)\n"
    "    {\n"
    "        farmer.reduceActiveItemByOne();\n"
    "    }\n"
)

# A teleport, one call away from the action that is declared as a walk.
GO_TO_METHODS = (
    "    private static (bool, string) GoTo(JsonElement root)\n"
    "    {\n"
    '        return Walk(loc, 1, 2) ? (true, "walked") : (false, "no");\n'
    "    }\n"
    "\n"
    "    private static bool Walk(GameLocation loc, int x, int y)\n"
    "    {\n"
    "        Game1.warpFarmer(\"Farm\", x, y, 0);\n"
    "        return true;\n"
    "    }\n"
)

TILL_METHOD = (
    "    private static (bool, string) TillArea(JsonElement root)\n"
    "    {\n"
    '        return (true, "tilled");\n'
    "    }\n"
)

DECL = {
    "plant": {"kind": "action", "how": "put seeds into tilled soil"},
    "go_to": {"kind": "action", "how": "walk there with the direction keys"},
    "till_area": {"kind": "action", "how": "(reflex) the same hoeing over a patch"},
}

# name, source, declarations, expected verdict, expected action  (both None = must find nothing)
CASES = [
    ("planting that takes no seed is caught",
     src(ARM_PLANT, PLANT_METHOD + PLANT_AT_UNCHARGED), DECL, "unfaithful", "plant"),

    ("planting that DOES take a seed is not accused",
     src(ARM_PLANT, PLANT_METHOD + PLANT_AT_CHARGED), DECL, None, None),

    ("a teleport hidden one call away is still a cheat",
     src(ARM_GO_TO, GO_TO_METHODS), DECL, "contradicts", "go_to"),

    ("a declaration with nothing behind it is caught",
     src(ARM_PLANT, PLANT_METHOD + PLANT_AT_CHARGED),
     {**DECL, "map": {"kind": "read", "needs": "WorldMap", "how": "open the map (M)"}},
     "phantom", "map"),

    ("an action nobody declared is caught",
     src(ARM_TILL, TILL_METHOD), {}, "undeclared", "till_area"),
]


def audit_source(source: str, declarations: dict) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Utils").mkdir(parents=True, exist_ok=True)
        (root / "Utils" / "ActionProcessor.cs").write_text(source, encoding="utf-8")
        return A.audit(root, declarations, "")


def runtime_cases() -> list[tuple[str, bool]]:
    """The rules that run WHILE the game is being played, fed the defects that motivated them.

    Same question as above, same reason for asking. Two of these were written down as rules and could
    not survive their own case:

      * `is_loop` counted repeats and blocked `dialogue_advance` for the rest of the day while the
        scene it was advancing was turning pages - four calls, four lines, and the agent then spent
        that day pressing escape, space, enter and E at a dialogue box it was forbidden to advance.

      * the chore witness watched `till_area` against `empty_soil`, and hoeing only ever pushes that
        UP: it turns a TILLABLE tile into empty soil. 0 successes in 42 attempts, recorded as a broken
        verb, and the farmer told to stop tilling while the tilling was working.
    """
    import learning as L
    import witness as W

    cases: list[tuple[str, bool]] = []

    def loop(action: str, args: dict, repeats: int, worked: bool | None) -> bool:
        store = L.LearningStore(Path(tempfile.gettempdir()) / "aipst-rule-test-absent.json",
                                log=lambda _m: None)
        for _ in range(repeats):
            store.note_attempt_outcome(store.remember_attempt(action, args), worked)
        return store.is_loop(action, args)

    cases.append(("dialogue_advance four times, each one turning the page, is NOT a loop",
                  loop("dialogue_advance", {}, 4, True) is False))
    cases.append(("menu_click on the same shelf row four times is NOT a loop",
                  loop("menu_click", {"name": "forSaleButtons.0"}, 4, True) is False))
    cases.append(("shouldering the same locked shop four times, changing nothing, IS a loop",
                  loop("enter", {"target": "SeedShop"}, 4, False) is True))
    cases.append(("three repeats is still under the threshold",
                  loop("menu_click", {"name": "upperRightCloseButton"}, 3, False) is False))

    # The chore witness, fed a world where the work actually happened. Hoeing a patch moves
    # `tillable` DOWN and `empty_soil` UP - which is the reverse of planting, and the reason naming a
    # counter after the verb was not enough to get it right.
    hoed_before = {"location": {"work": {"tillable": 11, "empty_soil": 3}}}
    hoed_after = {"location": {"work": {"tillable": 9, "empty_soil": 5}}}
    worked, evidence = W.judge("till_area", {}, hoed_before, hoed_after)
    cases.append((f"a patch that got hoed reads as success ({evidence})", worked is True))
    cases.append(("a patch nobody touched is not a success",
                  W.judge("till_area", {}, hoed_before, hoed_before)[0] is False))

    return cases


def main() -> int:
    failures = 0

    for name, source, declarations, want_verdict, want_action in CASES:
        findings = audit_source(source, declarations)

        if want_verdict is None:
            hit = [f for f in findings if f.action in ("plant",)]
            ok = not hit
            got = "no finding" if ok else f"{hit[0].verdict} on {hit[0].action}"
        else:
            hit = [f for f in findings if f.verdict == want_verdict and f.action == want_action]
            ok = bool(hit)
            got = f"{want_verdict} on {want_action}" if ok else (
                ", ".join(f"{f.verdict}:{f.action}" for f in findings) or "no finding at all")

        print(f"{'PASS' if ok else 'FAIL':4}  {name}")
        if not ok:
            print(f"        wanted {want_verdict or 'nothing'} on {want_action or '-'}, got {got}")
            failures += 1

    print()
    total = len(CASES)
    for name, ok in runtime_cases():
        print(f"{'PASS' if ok else 'FAIL':4}  {name}")
        if not ok:
            failures += 1
        total += 1

    print()
    print(f"{total - failures}/{total} detectors behave as their own history says they should")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
