"""What the player has worked out about the game, by playing it.

Not prompt text written by a person, and not a doctor's prescription. Every entry here comes from
something that happened in the game: this action, on this kind of thing, went this way, and the game
said this. The agent reads it back the next time it is about to do something similar, and it is what
tells it "a shut shop does not open by knocking" - because it knocked once and the door answered.

Why it exists: on day twelve the streamer knocked on Pierre's shut door seventeen times in one
afternoon. The door printed the answer on the screen - 上锁了……（营业时间：9:00~21:00） - and the
witness recorded it, and the transcript carried it, and nothing learned it. The rule that was
supposed to cover it was written by hand into the prompt, which is the difference between a player
who has tried a door and a script that was told about doors.

The shape is small on purpose: a subject (the KIND of thing, not a coordinate, because coordinates
are this morning's and the rules are forever) and a verb, with what happened and how often.

    memory/world.json
    {
      "door:SeedShop|enter": {
        "works": 2, "fails": 3,
        "says": "上锁了……（营业时间：9:00~21:00）",
        "note": "shut before 9:00 and after 21:00",
        "last": "y1-spring-18"
      }
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORLD_PATH = PROJECT_ROOT / "memory" / "world.json"

MAX_ENTRIES = 80
MAX_SHOWN = 25


def _slug(text: str) -> str:
    clean = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", str(text).strip().lower()).strip("_")
    return clean[:40] or "thing"


def subject_for(action: str, args: dict, state: dict) -> str:
    """What kind of thing this action was aimed at.

    Deliberately a KIND and not a tile. "till_area (67,21) worked" is this morning; "hoeing works on
    the tilled-able ground" is a rule of the game, and it is the second one that is worth carrying to
    tomorrow.
    """
    player = state.get("player") or {}
    ui = state.get("ui") or {}
    location = (state.get("location") or {}).get("name") or player.get("current_location") or "?"

    if action == "enter":
        return f"door:{args.get('target') or '?'}"

    if action in ("interact_at", "use_tool_at", "work"):
        front = player.get("tile_in_front") or {}
        kind = front.get("object") or front.get("crop") or front.get("ground") or front.get("character")
        if not kind:
            kind = "empty" if front.get("passable") else "something"
        return f"tile:{str(kind)[:18]}"

    if action == "menu_click":
        menu = ui.get("menu_name") or "menu"
        name = str(args.get("name") or "?")
        # A shelf row, a bag slot, a close button: the KIND of control, in the kind of menu.
        if name.startswith("forSaleButtons"):
            name = "shelf row"
        elif re.fullmatch(r"\d+|inventory\.\d+", name):
            name = "bag slot"
        return f"{menu}:{name}"

    if action in ("plant_all", "water_all", "clear_area", "till_area", "harvest_all", "refill"):
        return f"{location}:patch"

    if action in ("exit_building", "sleep"):
        return f"{location}:{action}"

    return f"{action}@{location}"


@dataclass
class Entry:
    subject: str
    action: str
    works: int = 0
    fails: int = 0
    says: str = ""          # the game's own words, verbatim - the most valuable thing here
    note: str = ""          # what the player concluded, in one line
    last: str = ""

    @property
    def key(self) -> str:
        return f"{self.subject}|{self.action}"

    def to_dict(self) -> dict:
        return {"works": self.works, "fails": self.fails, "says": self.says,
                "note": self.note, "last": self.last}


class World:
    """The player's own notes on how this game behaves."""

    def __init__(self, path: Path = WORLD_PATH, log=print) -> None:
        self.path = path
        self.log = log
        self.entries: dict[str, Entry] = {}
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        for key, value in (raw.get("entries") or {}).items():
            if not isinstance(value, dict):
                continue
            subject, _, action = str(key).partition("|")
            self.entries[key] = Entry(
                subject=subject, action=action,
                works=int(value.get("works", 0)), fails=int(value.get("fails", 0)),
                says=str(value.get("says", "")), note=str(value.get("note", "")),
                last=str(value.get("last", "")),
            )

    def save(self) -> None:
        ordered = sorted(self.entries.values(), key=lambda e: (-(e.works + e.fails), e.key))
        del ordered[MAX_ENTRIES:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"entries": {e.key: e.to_dict() for e in ordered}},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            self.log(f"[世界] 存不下来：{exc}")

    # -- learning ----------------------------------------------------------

    def observe(self, action: str, args: dict, state: dict, worked: bool | None,
                says: str = "") -> None:
        """An attempt happened. Count it, and keep the game's own words if it said anything."""
        if worked is None:
            return

        subject = subject_for(action, args, state)
        key = f"{subject}|{action}"
        entry = self.entries.get(key) or Entry(subject=subject, action=action)

        if worked:
            entry.works += 1
        else:
            entry.fails += 1

        if says:
            entry.says = says.strip()[:160]
        entry.last = date.today().isoformat()

        self.entries[key] = entry
        self.save()

    def learn(self, subject: str, action: str, result: str, note: str = "") -> None:
        """What the model worked out from what it saw. The player's own conclusion, in its words."""
        if not subject or not action:
            return

        key = f"{_slug(subject)}|{_slug(action)}"
        entry = self.entries.get(key) or Entry(subject=_slug(subject), action=_slug(action))
        if result:
            entry.says = str(result).strip()[:160]
        if note:
            entry.note = str(note).strip()[:160]
        entry.last = date.today().isoformat()

        self.entries[key] = entry
        self.save()
        self.log(f"[世界] 学会了：{entry.subject} + {entry.action} -> {entry.note or entry.says}")

    # -- reading back ------------------------------------------------------

    def brief(self, max_lines: int = MAX_SHOWN) -> str:
        """What to put in front of the player. Failures first: those are the ones that cost days."""
        if not self.entries:
            return ""

        ordered = sorted(self.entries.values(), key=lambda e: (-e.fails, -(e.works + e.fails)))
        lines = []
        for entry in ordered[:max_lines]:
            if entry.note:
                lines.append(f"- {entry.subject} + {entry.action}: {entry.note}"
                             + (f" (the game said: {entry.says})" if entry.says else ""))
            elif entry.says:
                lines.append(f"- {entry.subject} + {entry.action}: the game said: {entry.says}"
                             f" ({entry.works} worked / {entry.fails} did not)")
            elif entry.works or entry.fails:
                lines.append(f"- {entry.subject} + {entry.action}: {entry.works} worked / "
                             f"{entry.fails} did not")

        return "\n".join(lines)

    def knows_about(self, fragment: str) -> list[str]:
        """For a test, or for the log: does it actually hold anything about this?"""
        needle = fragment.lower()
        return [f"{e.subject}|{e.action} -> {e.note or e.says}"
                for e in self.entries.values()
                if needle in f"{e.subject} {e.note} {e.says}".lower()]


_SHARED: World | None = None


def current(log=print) -> World:
    """The one world model for this process.

    Shared on purpose: the loop writes what happened, and the streamer reads it back before the next
    decision. Two instances would mean writing into one file and reading from another, which is how
    you get a player that "learns" and then plays as if it had not.
    """
    global _SHARED
    if _SHARED is None:
        _SHARED = World(log=log)
    return _SHARED
