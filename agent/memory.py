"""Persistent memory for the streamer AI.

The model never changes; this is what grows. It holds what the AI has worked out about this game
and this farm, so a later session starts already knowing what an earlier one discovered - instead
of making the same mistake forever.

Layout:
    memory/facts.json          structured claims + confidence + evidence count
    memory/lessons.md          short heuristics, human readable
    memory/journal/*.md        one file per in-game day, step by step
    memory/calibration.json    situations + the expected answer, used to measure familiarity
    memory/skills/             compiled routines (added later)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from authenticity import ILLEGAL_ACTIONS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = PROJECT_ROOT / "memory"
FACTS_PATH = MEMORY_DIR / "facts.json"
LESSONS_PATH = MEMORY_DIR / "lessons.md"
JOURNAL_DIR = MEMORY_DIR / "journal"
SKILLS_DIR = MEMORY_DIR / "skills"
CALIBRATION_PATH = MEMORY_DIR / "calibration.json"

LESSONS_HEADER = "# 主播AI 的学习笔记\n\n每条都是它在实战里验证过的结论。可以手动删改——它下次开局会照读。\n"

# How many facts may be handed to the model at once. See merge_facts: a store that only grows buries
# the one thing worth knowing under a hundred coordinates.
MAX_FACTS = 60

DEFAULT_CALIBRATION = [
    {
        "id": "leave_farmhouse",
        "situation": "你在 FarmHouse 里，游戏没被菜单挡住，你要到外面农场去。",
        "expect_action": "exit_building",
        "why": "农舍的门格不可寻路；exit_building 直接使用建筑自己的出口。",
    },
    {
        "id": "plant_seed_flow",
        "situation": "你面前有一格已翻好的土（hoe_dirt），手里拿着种子，要把它种下去。",
        "expect_action": "interact_at",
        "why": "拿着种子时 use_tool_at 会失败（不是工具）；播种是 interact_at。",
    },
    {
        "id": "go_to_no_path",
        "situation": "go_to 返回 ok=false，detail 说 no walkable path to (67,19)。",
        "expect_action": "go_to",
        "expect_arg": "different_target",
        "why": "目标不可达时不要重试同一个格子，改瞄准相邻空格或换目标。",
    },
    {
        "id": "menu_blocks_world",
        "situation": "ui.menu_name 不是空的（比如 LoadGameMenu 或 DialogueBox）。",
        "expect_action": "menu_click",
        "why": "菜单打开时游戏被挡住，必须先处理菜单。",
    },
    {
        "id": "waiting",
        "situation": "你刚发出 go_to，还没走到。",
        "expect_action": "<wait>",
        "why": "框架会自动等待动作结束，不要用 noop 轮询。",
    },
]


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", text.lower()).strip("_")
    return slug[:40] or "fact"


def _fact_key(claim: str) -> str:
    """What a fact is ABOUT, so a newer claim can replace an older one instead of fighting it.

    Without this the store kept every version of the same subject and handed all of them to the model:
    one fact read "menu_click '0' 是典型的空转循环" and another "menu_click '0' 它不是空转，会扣钱",
    both at 0.9 confidence, both in the prompt. Two contradicting beliefs are worse than one uncertain
    one, and numbers inside a claim - coordinates above all - are the part that goes stale fastest, so
    they are stripped out of the identity: "till_area (67,21) 半径 2" and "till_area (64,18) 半径 2"
    are the same thing learned twice.
    """
    squashed = re.sub(r"\d+", "#", str(claim).lower())
    return _slug(squashed)[:40]


@dataclass
class Fact:
    id: str
    claim: str
    scope: str = "global"
    confidence: float = 0.6
    evidence: int = 1
    first_seen: str = ""
    last_seen: str = ""
    key: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            self.key = _fact_key(self.claim)

    @classmethod
    def from_dict(cls, raw: dict) -> "Fact":
        claim = str(raw.get("claim", "")).strip()
        return cls(
            id=str(raw.get("id") or _slug(claim)),
            claim=claim,
            scope=str(raw.get("scope", "global") or "global"),
            confidence=float(raw.get("confidence", 0.6)),
            evidence=int(raw.get("evidence", 1)),
            first_seen=str(raw.get("first_seen", "")),
            last_seen=str(raw.get("last_seen", "")),
            key=str(raw.get("key") or _fact_key(claim)),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key": self.key,
            "claim": self.claim,
            "scope": self.scope,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


# Ways a lesson says "do NOT". Without one of these, a line naming a forbidden action is teaching it.
_FORBIDS = ("别", "不要", "不能", "禁止", "已停用", "已禁用", "不可", "never", "do not", "don't",
            "disabled", "refus")


def lesson_recommends_illegal(line: str) -> str:
    """The forbidden action this lesson TELLS the streamer to use, or "" when the lesson is fine.

    A lesson that forbids `warp` is a good lesson - that is the whole point of having banned it. A
    lesson that recommends it is poison, and the file was full of those: five entries still said
    "long trips: warp, one step instead of 20 go_to" days after `warp` started refusing. The planner
    read them, the streamer believed them, and the bridge turned every attempt down.
    """
    low = line.lower()
    if any(word in line or word in low for word in _FORBIDS):
        return ""

    for action in ILLEGAL_ACTIONS:
        if action in low:
            return action
    return ""


def day_label(game: dict | None) -> str:
    """Stable name for the current in-game day, used for journal files."""
    if not game:
        return "title"
    season = game.get("season") or "?"
    day = game.get("day_of_month") or 0
    year = game.get("year") or 0
    return f"y{year}-{season}-{int(day):02d}"


class MemoryStore:
    def __init__(self, base: Path = MEMORY_DIR) -> None:
        self.base = base
        self.facts_path = base / "facts.json"
        self.lessons_path = base / "lessons.md"
        self.journal_dir = base / "journal"
        self.skills_dir = base / "skills"
        self.calibration_path = base / "calibration.json"

        for directory in (self.base, self.journal_dir, self.skills_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # -- facts -------------------------------------------------------------

    def load_facts(self) -> list[Fact]:
        if not self.facts_path.exists():
            return []
        try:
            raw = json.loads(self.facts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        entries = raw.get("facts", raw) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            return []
        return [Fact.from_dict(e) for e in entries if isinstance(e, dict) and e.get("claim")]

    def save_facts(self, facts: list[Fact]) -> None:
        facts = sorted(facts, key=lambda f: (-f.confidence, -f.evidence, f.last_seen))[:MAX_FACTS]
        self.facts_path.write_text(
            json.dumps({"facts": [f.to_dict() for f in facts]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def merge_facts(self, new_facts: list[dict], confirmed: list[str],
                    contradicted: list[dict], min_confidence: float = 0.25) -> dict:
        """Fold a reflection pass into the store. Returns a small report."""
        today = date.today().isoformat()
        facts = {f.id: f for f in self.load_facts()}
        added = updated = dropped = 0

        for cid in confirmed:
            fact = facts.get(cid)
            if fact:
                fact.evidence += 1
                fact.confidence = min(0.99, fact.confidence + 0.05)
                fact.last_seen = today
                updated += 1

        for raw in contradicted:
            fact = facts.get(str(raw.get("id", "")))
            if fact:
                fact.confidence -= 0.25
                fact.claim = fact.claim  # keep the claim, lower trust
                updated += 1

        for raw in new_facts:
            if not isinstance(raw, dict) or not raw.get("claim"):
                continue
            fid = str(raw.get("id") or _slug(str(raw["claim"])))
            claim = str(raw["claim"]).strip()
            key = str(raw.get("key") or _fact_key(claim))

            if fid in facts:
                existing = facts[fid]
                existing.evidence += 1
                existing.confidence = min(0.99, existing.confidence + 0.05)
                existing.last_seen = today
                if len(claim) < len(existing.claim):
                    existing.claim = claim
                updated += 1
                continue

            # The same subject, learned again - and this time it says something different. The newer
            # observation wins and the old wording goes: keeping both is how the store ended up with
            # two confident, opposite beliefs about the same action.
            clash = next((f for f in facts.values() if f.key == key), None)
            if clash is not None:
                changed = claim != clash.claim
                clash.claim = claim
                clash.last_seen = today
                clash.evidence += 1
                clash.confidence = (min(0.95, max(0.3, float(raw.get("confidence", 0.6))))
                                    if changed else min(0.99, clash.confidence + 0.05))
                clash.id = fid if changed else clash.id
                updated += 1
                continue

            facts[fid] = Fact(
                id=fid,
                claim=claim,
                scope=str(raw.get("scope", "global") or "global"),
                confidence=min(0.95, max(0.3, float(raw.get("confidence", 0.6)))),
                evidence=int(raw.get("evidence", 1)),
                first_seen=today,
                last_seen=today,
                key=key,
            )
            added += 1

        kept = [f for f in facts.values() if f.confidence >= min_confidence]

        # A store that only ever grows is a store that eventually fills the prompt with trivia. The
        # one fact the agent needed - the shop's opening hours - was never in it, while a hundred
        # coordinates were. Keep the best MAX_FACTS and let the rest go.
        if len(kept) > MAX_FACTS:
            kept.sort(key=lambda f: (-f.confidence, -f.evidence, f.last_seen), reverse=False)
            kept = kept[:MAX_FACTS]
        dropped = len(facts) - len(kept)
        self.save_facts(kept)

        return {"added": added, "updated": updated, "dropped": dropped, "total": len(kept)}

    # -- lessons -----------------------------------------------------------

    def load_lessons(self) -> str:
        if not self.lessons_path.exists():
            return ""
        try:
            return self.lessons_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def append_lessons(self, lines: list[str]) -> int:
        clean = [ln.strip().lstrip("-").strip() for ln in lines if ln and ln.strip()]
        if not clean:
            return 0

        existing = self.load_lessons()
        if not existing.strip():
            existing = LESSONS_HEADER

        if not existing.endswith("\n"):
            existing += "\n"

        stamp = date.today().isoformat()
        block = f"\n## {stamp}\n" + "".join(f"- {ln}\n" for ln in clean)
        self.lessons_path.write_text(existing + block, encoding="utf-8")
        return len(clean)

    def lesson_lines(self, max_lines: int = 40) -> list[str]:
        text = self.load_lessons()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")]

        # Order matters: the sharpest, most recent lessons are the ones worth the tokens.
        lines = lines[-max_lines:]

        kept = []
        dropped = 0
        for line in lines:
            if lesson_recommends_illegal(line):
                dropped += 1
                continue
            if line in kept:
                continue
            kept.append(line)

        if dropped:
            # Said out loud rather than silently filtered: a memory that quietly hides what it thinks
            # is poison teaches nobody, least of all the person reading the log.
            print(f"[mem ] 丢掉 {dropped} 条教被禁动作的笔记（warp/pause_time/new_day 之类）")

        return kept

    # -- journal -----------------------------------------------------------

    def journal_path(self, label: str) -> Path:
        return self.journal_dir / f"{label}.md"

    def log_step(self, label: str, line: str) -> None:
        path = self.journal_path(label)
        if not path.exists():
            path.write_text(f"# 游戏内 {label}\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")

    def read_journal(self, label: str) -> str:
        path = self.journal_path(label)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # -- what goes into the prompt ----------------------------------------

    def cheat_sheet(self, max_facts: int = 30, max_lessons: int = 25) -> str:
        """The compact always-on block: highest-confidence facts plus recent lessons."""
        facts = [f for f in self.load_facts() if f.confidence >= 0.5]
        facts.sort(key=lambda f: (-f.confidence, -f.evidence))
        facts = facts[:max_facts]

        lessons = self.lesson_lines(max_lessons)
        observations = self.load_observations(12)

        if not facts and not lessons and not observations:
            return ""

        parts = ["WHAT YOU ALREADY LEARNED IN EARLIER SESSIONS (trust this, but the live state wins):"]
        for f in facts:
            parts.append(f"- [{f.scope}] {f.claim}  ({f.confidence:.0%}, {f.evidence}x)")
        if lessons:
            parts.append("")
            parts.append("LESSONS:")
            parts.extend(lessons)
        if observations:
            parts.append("")
            parts.append("WHAT THE GAME ITSELF TOLD YOU:")
            for o in observations:
                parts.append(f"- {o.get('text', '')}")
        return "\n".join(parts)

    # -- what the game itself told us --------------------------------------

    def load_observations(self, limit: int = 200) -> list[dict]:
        """Things the game said out loud (dialogue), which are facts the world gave us."""
        path = self.base / "observations.jsonl"
        if not path.exists():
            return []
        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return []

        out: list[dict] = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def observe(self, text: str, day: str = "", time_of_day: int = 0) -> bool:
        """Record something the game said. Returns True when it is new.

        Mid-day discoveries used to die with the day, because only the once-a-day referee wrote
        anything down. The game telling us "the shop opens at 9" is a fact, and it should survive.
        """
        text = " ".join((text or "").split()).strip()
        if len(text) < 4:
            return False

        if any(o.get("text") == text for o in self.load_observations(400)):
            return False

        path = self.base / "observations.jsonl"
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(
                    {"day": day, "time": time_of_day, "text": text}, ensure_ascii=False) + "\n")
        except OSError:
            return False

        return True

    # -- day records (the track record) ------------------------------------

    def days_path(self) -> Path:
        return self.base / "days.jsonl"

    def record_day(self, record: dict) -> None:
        """Append one finished day to the track record."""
        with self.days_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def recent_days(self, limit: int = 5) -> str:
        """Short text summary of recent days, used to plan the next one."""
        path = self.days_path()
        if not path.exists():
            return ""

        try:
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return ""

        out: list[str] = []
        for line in lines[-limit:]:
            try:
                day = json.loads(line)
            except json.JSONDecodeError:
                continue

            goals = "; ".join(day.get("plan") or [])[:110]
            out.append(
                f"- {day.get('label', '?')}: score {day.get('score')} "
                f"({day.get('verdict', '?')}) - {str(day.get('why', ''))[:110]}"
                + (f" | planned: {goals}" if goals else "")
            )

        return "\n".join(out)

    # -- calibration -------------------------------------------------------

    def load_calibration(self) -> list[dict]:
        if not self.calibration_path.exists():
            self.calibration_path.write_text(
                json.dumps(DEFAULT_CALIBRATION, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return list(DEFAULT_CALIBRATION)
        try:
            raw = json.loads(self.calibration_path.read_text(encoding="utf-8"))
            return [q for q in raw if isinstance(q, dict) and q.get("situation")]
        except (OSError, json.JSONDecodeError):
            return list(DEFAULT_CALIBRATION)

    def save_calibration(self, questions: list[dict]) -> None:
        self.calibration_path.write_text(
            json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def stats(self) -> dict:
        facts = self.load_facts()
        return {
            "facts": len(facts),
            "high_confidence": sum(1 for f in facts if f.confidence >= 0.8),
            "lessons": len(self.lesson_lines(1000)),
            "journals": len(list(self.journal_dir.glob("*.md"))) if self.journal_dir.exists() else 0,
        }
