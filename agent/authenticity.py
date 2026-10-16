"""Could a player do this?

The witness asks "did the world change?". That catches an action which reports success and does
nothing. It cannot catch a `warp` that teleports perfectly, a clock that stops on request, or a
`sleep` that puts you in your kitchen - those all change the world exactly as designed, so from
inside the loop nothing is broken.

What is wrong with them lives in the action set, not in the execution: they are things a person at
the keyboard cannot do. That question can be asked without the game running, because the answer is
in the code.

Every exposed action falls into one of five kinds:

    action     a player does it - "walk there with the direction keys"
    knowledge  a player can look it up - a guide, a wiki, or from memory. Knowing is not cheating;
               cheating is DOING something the player cannot do.
    read       looking at something - legal only while the matching screen is open
    recovery   the harness un-wedging itself; the agent must never choose it as play
    illegal    a player cannot do it at all

The declaration is a sentence a person wrote. It is NOT the evidence. The evidence is the code: if a
method teleports, stops the clock, rolls the day over or writes the farmer's position, then it is
illegal no matter what anybody declared, and this says so. Without that, an agent could launder a
cheat by writing "action" next to it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

KINDS = ("action", "knowledge", "read", "recovery", "illegal")

# What "a player cannot do this" looks like in the code. These are unarguable: they are not the
# action's description, they are the action.
CHEAT_PRIMITIVES = {
    "Game1.warpFarmer": "teleports the farmer",
    "Game1.isTimePaused": "stops or starts the clock",
    "Game1.NewDay": "rolls the day over without sleeping",
    "Game1.player.Position": "moves the farmer directly",
    "player.Position": "moves the farmer directly",
}

# The starting declarations. Kept deliberately complete, so "undeclared" means someone added an
# action and did not say what it is - which is the thing worth reporting.
DEFAULT_DECLARATIONS: dict[str, dict] = {
    # -- a player action -------------------------------------------------
    "go_to": {"kind": "action", "how": "walk there with the direction keys"},
    "move": {"kind": "action", "how": "hold a direction key"},
    "enter": {"kind": "action", "how": "walk onto the door tile"},
    "exit_building": {"kind": "action", "how": "walk onto the door tile"},
    "sleep": {"kind": "action", "how": "walk to the bed, interact, confirm"},
    "work": {"kind": "action", "how": "face a tile and swing the right tool"},
    "use_tool": {"kind": "action", "how": "swing the held tool"},
    "use_tool_at": {"kind": "action", "how": "face a tile and swing"},
    "interact": {"kind": "action", "how": "interact with what is in front"},
    "interact_at": {"kind": "action", "how": "face a tile and interact"},
    "select_slot": {"kind": "action", "how": "number key or mouse wheel"},
    "plant": {"kind": "action", "how": "put seeds into tilled soil"},
    "refill": {"kind": "action", "how": "use the can on water"},
    "clear_area": {"kind": "action", "how": "(reflex) the same swings over a patch, without a decision between each"},
    "till_area": {"kind": "action", "how": "(reflex) the same hoeing over a patch"},
    "plant_all": {"kind": "action", "how": "(reflex) the same sowing over a patch"},
    "water_all": {"kind": "action", "how": "(reflex) the same watering over a patch"},
    "harvest_all": {"kind": "action", "how": "(reflex) the same harvesting over a patch"},
    "menu_click": {"kind": "action", "how": "click a button on the screen"},
    "dialogue_advance": {"kind": "action", "how": "click to continue a dialogue"},
    "dialogue_choose": {"kind": "action", "how": "click a dialogue option"},
    "title_button": {"kind": "action", "how": "click a title-screen button"},
    "skip_intro": {"kind": "action", "how": "press the skip key"},
    "new_game": {"kind": "action", "how": "start a new file from the title screen"},
    "create_character": {"kind": "action", "how": "confirm in the character creator"},
    "set_appearance": {"kind": "action", "how": "pick an appearance in the character creator"},
    "set_text": {"kind": "action", "how": "type into a text field"},
    "press_key": {"kind": "action", "how": "press a key"},
    "stop": {"kind": "action", "how": "let go of the keys"},
    "noop": {"kind": "action", "how": "stand still"},
    "open_screen": {"kind": "action", "how": "press M for the map, E for the bag"},

    # -- looking, which is free ------------------------------------------
    "scan": {"kind": "read", "needs": "", "how": "look around - only what is on screen"},
    "observe": {"kind": "read", "needs": "", "how": "look at one nearby tile"},

    # -- knowledge: what a player can look up ----------------------------
    #
    # These two were filed as `read` with a `needs` screen, and the audit dutifully asked for that
    # screen to be opened first. That premise was wrong and it cost weeks: to make `game_data` legal
    # the bridge would have had to open the map - and there was no way to open the map, so the finding
    # could never be closed. Nothing re-read the premise; it just printed every cycle.
    #
    # The premise is the user's rule, and it is simple: cheating means doing what a player cannot DO.
    # Knowing is different. A player looks up Pierre's opening hours, which crops grow in spring, what
    # an item is worth - in a guide, a wiki, or from memory. Reading the game's own data tables yields
    # exactly that class of fact. No in-game operation happens and no game state changes, so it is
    # knowledge - not a shortcut, and not a reason to lock it behind an interface.
    "game_data": {"kind": "knowledge",
                  "how": "read the game's own data tables - the same facts a player looks up in a wiki"},
    "inspect": {"kind": "knowledge",
                "how": "ask a fact about the running game - a player looks at the screen, or looks it up",
                "boundary": "facts a player could come by: what is on the shelf, what is in hand, what "
                            "a field is called. NOT hidden state (rng, unrevealed outcomes) - that "
                            "would be knowing something no player can know."},

    # -- recovery: the harness may, the agent may not --------------------
    "cancel_chore": {"kind": "recovery", "why": "the reflex layer is ours; a player has no such thing"},
    "clear_event": {"kind": "recovery", "why": "un-wedging an abandoned cutscene"},
    "focus_window": {"kind": "recovery", "why": "the bridge needs the window in front to inject input"},
    "chore": {"kind": "recovery", "why": "status query"},
    "actions": {"kind": "recovery", "why": "status query - what the bridge can do at all"},

    # -- illegal ---------------------------------------------------------
    "warp": {"kind": "illegal", "why": "a player cannot teleport"},
    "pause_time": {"kind": "illegal", "why": "a player cannot stop the clock and keep playing"},
    "new_day": {"kind": "illegal", "why": "a player cannot skip a day"},
    "set_display": {"kind": "illegal", "why": "a player changes options, not the running game"},

    # -- audience only ---------------------------------------------------
    "overlay": {"kind": "recovery", "why": "draws the audience panel; not a game action"},
}

SWITCH_ENTRY = re.compile(r'"([a-z_]+)"\s*=>\s*(\w+)\(')
# A switch arm that is not a plain method call - `"noop" => (true, "noop")` - is still an action the
# bridge accepts. Looking only for calls made `noop` look like a phantom, which is a false alarm of
# exactly the kind this audit exists to avoid.
SWITCH_ARM = re.compile(r'"([a-z_]+)"\s*=>')
# `game_data` and `overlay` are answered before the switch, so they are not switch entries and were
# being skipped entirely by the first version of this audit.
EARLY_RETURN = re.compile(r'action\s*==\s*"([a-z_]+)"\s*\)\s*\n\s*return\s+(\w+)\(')
METHOD_START = re.compile(r'(?:private|internal|public)\s+static\s+\(bool,\s*string\)\s+(\w+)\s*\(')

DECLARATIONS_PATH = "memory/authenticity.json"


@dataclass
class Finding:
    action: str
    verdict: str          # undeclared | phantom | illegal-exposed | contradicts | leaked | ungated | ok
    owner: str            # agent | developer
    detail: str


# ---------------------------------------------------------------- declarations

def load_declarations(root: Path) -> dict[str, dict]:
    """The starting declarations, overridden by whatever has been learned since."""
    merged = {k: dict(v) for k, v in DEFAULT_DECLARATIONS.items()}

    path = root / DECLARATIONS_PATH
    if path.exists():
        try:
            learned = json.loads(path.read_text(encoding="utf-8"))
            for action, entry in (learned.get("actions") or {}).items():
                if isinstance(entry, dict) and entry.get("kind") in KINDS:
                    merged[action] = entry
        except (OSError, json.JSONDecodeError):
            pass

    return merged


def save_declaration(root: Path, action: str, kind: str, how: str = "", why: str = "") -> bool:
    """Record what an action is. The audit still checks it against the code."""
    if kind not in KINDS:
        return False

    path = root / DECLARATIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {"actions": {}}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"actions": {}}

    entry = {"kind": kind, "how": how or why, "by": "agent"}
    data.setdefault("actions", {})[action] = entry
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


# ----------------------------------------------------------------- code reading

def _method_bodies(source: str) -> dict[str, str]:
    bodies: dict[str, str] = {}

    for match in METHOD_START.finditer(source):
        name = match.group(1)
        start = source.find("{", match.end())
        if start < 0:
            continue

        depth = 0
        for i in range(start, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    bodies[name] = source[start:i + 1]
                    break

    return bodies


def _switch_block(source: str) -> str:
    """The text of the `action switch { ... }` expression.

    Scoped deliberately: a pattern like `"up" =>` also occurs in direction and field maps elsewhere in
    the file, and treating those as actions produced half a dozen false accusations - the same failure
    the audit exists to prevent, just turned around.
    """
    match = re.search(r'action\s+switch\s*\{', source)
    if not match:
        return ""

    start = match.end() - 1
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]

    return source[start:]


def exposed_actions(source: str) -> dict[str, str]:
    """Every action the bridge accepts, and the method each one runs."""
    block = _switch_block(source) or source

    found = {action: method for action, method in SWITCH_ENTRY.findall(block)}
    found.update({action: method for action, method in EARLY_RETURN.findall(source)})
    for action in SWITCH_ARM.findall(block):
        found.setdefault(action, "")
    return found


def audit(mod_dir: Path, decls: dict[str, dict], catalogue: str = "") -> list[Finding]:
    """Check every exposed action against what a player can do."""
    path = mod_dir / "Utils" / "ActionProcessor.cs"
    if not path.exists():
        return [Finding("?", "undeclared", "developer", f"could not read {path}")]

    source = path.read_text(encoding="utf-8", errors="replace")
    bodies = _method_bodies(source)
    exposed = exposed_actions(source)
    findings: list[Finding] = []

    for action, method in sorted(exposed.items()):
        body = bodies.get(method, "")
        declared = decls.get(action)
        cheats = [what for primitive, what in CHEAT_PRIMITIVES.items() if primitive in body]

        if declared is None:
            findings.append(Finding(
                action, "undeclared", "agent",
                f"'{action}' is exposed and nothing says what a player does to trigger it"))
            continue

        kind = str(declared.get("kind") or "")

        # The evidence wins. A declaration cannot make a teleport legal.
        if cheats:
            findings.append(Finding(
                action, "contradicts", "developer",
                f"declared '{kind}' ({declared.get('how') or declared.get('why') or ''}) but "
                f"{', '.join(cheats)}"))
            continue

        if kind == "illegal":
            findings.append(Finding(
                action, "illegal-exposed", "developer",
                f"'{action}' is still offered although a player cannot do it ({declared.get('why')})"))
            continue

        if kind == "recovery" and f"- {action} " in catalogue:
            findings.append(Finding(
                action, "leaked", "developer",
                f"'{action}' is a recovery action but it is in the list the model reads, "
                "so the agent can choose it as play"))
            continue

        if kind == "read":
            needs = str(declared.get("needs") or "")
            if needs:
                findings.append(Finding(
                    action, "ungated", "developer",
                    f"'{action}' reads the game without requiring '{needs}' to be open "
                    f"(a player would have to open it) - see whether the bridge can gate this"))

    # The other direction, and this is the one that rotted.
    #
    # Everything above asks "is every exposed action declared?" - it is blind to the reverse. The
    # declarations claimed `open_screen`, `map` and `inventory` were things a player can do, none of
    # them existed in the bridge, and nothing said a word. One of those phantom declarations was the
    # entire reason a real finding could never be closed: the fix it prescribed - "open the map
    # first" - named an action that had never been written, so the finding was unsatisfiable and
    # printed every cycle for weeks.
    #
    # A declaration is a claim. A claim about an action that does not exist is a defect of its own,
    # and the only way it stays hidden is if nobody ever looks in this direction.
    for action in sorted(decls):
        if action in exposed:
            continue

        # A refusal is not a phantom. Declaring `warp` illegal while offering no `warp` is the
        # promise being kept - the whole point of the entry is that this action must never appear.
        if str(decls[action].get("kind") or "") == "illegal":
            continue

        findings.append(Finding(
            action, "phantom", "developer",
            f"'{action}' is declared as '{decls[action].get('kind')}' but the bridge has no such "
            "action - a claim with nothing behind it"))

    return findings


def report(findings: list[Finding], decls: dict[str, dict] | None = None) -> str:
    bad = [f for f in findings if f.verdict != "ok"]

    lines = [
        "# 合法性审计（玩家做得到吗）",
        "",
        "判据：**一个坐在键盘前的人能不能做出这个动作。**",
        "声明只是人写的一句话；**证据是代码** —— 方法体里出现瞬移/停钟/跳天/直接写位置，",
        "无论声明成什么类别，一律算违法。",
        "",
        "五类：`action` 玩家能做 / `knowledge` 玩家能查（攻略、wiki、记忆）/ "
        "`read` 要看必须先开界面 / `recovery` 只有 harness 能用 / `illegal` 玩家做不到",
        "",
    ]

    if not bad:
        lines.append("✅ 暴露的每个动作都说得清是什么。")
        return "\n".join(lines) + "\n"

    lines += ["| 动作 | 判定 | 该谁修 | 说明 |", "|---|---|---|---|"]
    for f in bad:
        lines.append(f"| `{f.action}` | **{f.verdict}** | {f.owner} | {f.detail} |")

    return "\n".join(lines) + "\n"


def as_defects(findings: list[Finding]):
    """(key, title, owner, detail) for the ledger."""
    for f in findings:
        if f.verdict == "ok":
            continue
        yield (f"cheat:{f.action}", f"`{f.action}` 不是玩家能做的动作", f.owner, f.detail)
