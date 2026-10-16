"""The room's suggestions, and the rule that stops them from steering the farm.

A viewer saying "go plant potatoes" is a nice thing to hear and a terrible thing to obey: the room
is anonymous, unaccountable, and cannot see the plan. So a suggestion is never an instruction. It is
written down, carried into the next day's planning, and the planner - the one that actually knows
the season, the money and the quest - decides. That is the same shape as a real streamer reading
chat and then doing whatever they were already going to do, but politely.

Keeping them in a file rather than in memory matters: an overnight run must still know in the
morning what the room asked for, and a crash must not erase a promise.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class Audience:
    def __init__(self, path: Path, log=print, max_pending: int = 8) -> None:
        self.path = Path(path)
        self.log = log
        self.max_pending = max_pending
        self._pending: list[dict] = []
        self._load()

    # -- in -----------------------------------------------------------------

    def offer(self, text: str, user: str = "观众") -> None:
        """A suggestion. It is only ever advice; nothing here can touch the game."""
        text = (text or "").strip()
        if not text or len(text) > 200:
            return

        item = {"user": user or "观众", "text": text, "at": time.strftime("%m-%d %H:%M")}
        self._pending.append(item)

        if len(self._pending) > self.max_pending:
            self._pending = self._pending[-self.max_pending:]

        self._save()
        self.log(f"[观众] {item['user']} 建议：{text}")

    def pending(self) -> list[dict]:
        self._reload()
        return list(self._pending)

    def take(self) -> list[dict]:
        """Hand the suggestions over, once, at the moment they can actually be used."""
        self._reload()
        items, self._pending = self._pending, []
        self._save()
        return items

    def prompt_block(self) -> str:
        """The suggestions, written for the day planner."""
        self._reload()
        if not self._pending:
            return ""

        lines = ["以下是观众提的建议。它们只是建议：你可以采纳，也可以不采纳，"
                 "但如果你不采纳，说明理由。"]
        for item in self._pending[-6:]:
            lines.append(f"- {item['user']} 说：{item['text']}")
        return "\n".join(lines)

    def _reload(self) -> None:
        """Re-read before answering, because the writer is a different process.

        The commentary daemon files the room's wishes; the playing process decides on them. Two
        processes, one file, and an in-memory copy on each side means whichever one saves last wins -
        so the file is the truth and both sides look at it fresh.
        """
        self._pending = []
        self._load()

    def note_decision(self, accepted: list[str], declined: list[str]) -> None:
        """Optional: remember what the planner decided, so the story is auditable."""
        if not accepted and not declined:
            return
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(
                    {"at": time.strftime("%Y-%m-%d %H:%M"), "accepted": accepted,
                     "declined": declined},
                    ensure_ascii=False) + "\n")
        except OSError:
            pass

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                item = json.loads(line)
                if isinstance(item, dict) and item.get("text") and "accepted" not in item:
                    self._pending.append(item)
        except (OSError, json.JSONDecodeError):
            return

        self._pending = self._pending[-self.max_pending:]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as handle:
                handle.write("# 观众建议队列（只有未处理的部分在这里；plan 一次就清空）\n")
                for item in self._pending:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError:
            pass
