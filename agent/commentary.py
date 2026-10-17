"""The streamer's voice: what it says out loud while it plays.

This is deliberately a different brain from the one that plays. Playing is a strict, terse,
JSON-only loop with a hard latency budget; talking is slow, warm, and has to sound like a person.
Those two do not belong in the same prompt - mixing them makes the play worse and the talk wooden.

So this module watches the same event stream the other agents write, decides when something is
worth saying, and says it. It never decides what to do in the game, and it never makes the playing
loop wait: it runs on its own thread, and every call it makes goes out on a queue that is allowed
to drop.

Three jobs, one voice:
    * commentary   - what just happened, in the character's own words
    * recaps       - where the farm stands, for someone who just walked in
    * the room     - welcoming people in, answering what they say, thanking for gifts
"""

from __future__ import annotations

import random
import re
import threading
import time
from collections import deque

import prompts

SEASON_ZH = {"spring": "春", "summer": "夏", "fall": "秋", "winter": "冬"}

# Things to talk about when nothing is happening. Rotated one at a time, because the one thing a
# line in a quiet moment must NOT do is describe the situation again: the audience has just heard
# that, and a streamer who re-narrates the same minute in different words sounds broken rather than
# quiet. Each angle is a different kind of thought, so consecutive lulls cannot rhyme.
IDLE_ANGLES = [
    "说说你今天最想做成的一件事，以及为什么想做成。",
    "说说你这个 AI 现在卡在哪、你希望有人帮你改什么（用你自己的话说，别念术语）。",
    "向直播间的观众提一个问题，或者请他们帮你想个办法。",
    "说说今天学到的一件事，或者你以前弄错过、今天才想明白的地方。",
    "讲讲你现在的心情：累不累、饿不饿、有没有一点着急。",
    "说说昨天过得怎么样，今天想比昨天好在哪里。",
]

# The day's plan, reduced to the few words the audience actually cares about. The planner rewrites
# its focus every few minutes in different sentences - measured, the wording changes almost
# completely each time (0.09 similarity) while the *substance* stays put (greet people, buy seeds,
# water, clear a patch, sleep). Comparing whole sentences therefore finds no pattern to use; mapping
# both onto this small vocabulary does, and it is what lets the talker hang every action onto a goal
# ("为了买种子，我先去镇上") instead of narrating disconnected movements.
PLAN_LABELS = [
    ("打招呼", "和镇上的人打招呼"),
    ("介绍", "和镇上的人打招呼"),
    ("送礼", "给村民送礼"),
    ("买种子", "去种子店买种子"),
    ("种子店", "去种子店买种子"),
    ("SeedShop", "去种子店买种子"),
    # The planner says "去皮埃尔商店买 4 个防风草种子" as often as it says "买种子", so matching on
    # the phrase alone quietly dropped the day's most important goal from the talker's view.
    ("皮埃尔", "去种子店买种子"),
    ("forSaleButtons", "去种子店买种子"),
    ("Seeder", "去种子店买种子"),
    ("浇水", "给作物浇水"),
    ("water_all", "给作物浇水"),
    ("种下去", "把种子种下去"),
    ("plant_all", "把种子种下去"),
    ("收", "收成熟的作物"),
    ("harvest", "收成熟的作物"),
    # One label for the whole clearing family. Matching them separately produced three different
    # labels for one activity ("清杂草" / "锄地清地" / "清出一片地"), which made the plan look like it
    # changed every time the planner rephrased itself.
    ("杂草", "清出一片地"),
    ("锄", "清出一片地"),
    ("清", "清出一片地"),
    ("睡觉", "天黑前回家睡觉"),
    ("上床", "天黑前回家睡觉"),
    ("钓鱼", "去钓鱼"),
    ("矿", "去矿洞"),
]

# Actions that a viewer can see happen, and would want the streamer to talk about. Talking about
# every step is noise; NOT talking about these is a silent stream. Anything not here is bookkeeping.
#
# No placeholders in this table. One used to read "走进了 {target}" and was never formatted, so the
# model received that literal text, could not tell where the farmer had gone, and filled the gap
# with a place it invented - which is how the commentary ends up describing a walk that never
# happened. Anything that needs a field from the event is built in _beat, where the event is.
NOTABLE = {
    "exit_building": "走出了建筑",
    "harvest_all": "收了一轮作物",
    "plant_all": "把种子都种下去了",
    "water_all": "给作物浇了水",
    "till_area": "翻好了一片地",
    "clear_area": "清理了一片地",
    "refill": "把水壶打满了",
    "use_tool_at": "用了一次工具",
    "use_tool": "用了一次手上的工具",
    "interact_at": "互动了一下",
    "interact": "和人/东西互动了",
    "dialogue_advance": "和人说话",
    "dialogue_choose": "在对话里做了选择",
    "menu_click": "点了菜单",
    "sleep": "去睡觉了",
    "new_day": "开始了新的一天",
    "go_to": "在路上",
}

# A failure repeated this many times becomes something worth complaining about out loud.
FAILURE_PATIENCE = 3

CHAT_KINDS = {
    "danmaku": "弹幕",
    "enter": "进场",
    "follow": "关注",
    "share": "分享",
    "gift": "礼物",
    "super_chat": "醒目留言",
    "guard": "上舰",
}

# How each agent is named when the streamer talks about the system out loud. Viewers are told what
# an agent is doing, not what its identifier is.
AGENT_LABELS = {
    "planner": "负责定今天计划的",
    "streamer": "正在玩的",
    "supervisor": "在旁边盯着别跑偏的",
    "referee": "每天结束打分的",
    "watcher": "盯着每一步有没有卡住的",
    "doctor": "负责查「为什么会卡」的",
    "witness": "判断这一步世界有没有真的变化的",
    "gatekeeper": "拦住重复犯错的",
    "pipeline": "负责把游戏开起来、把存档读进来的",
    "outcome": "看整体有没有在进步的",
    "gaps": "检查哪个动作不灵了的",
}


def _zh(mapping: dict, key) -> str:
    """A bridge code turned into the words the audience uses. Unknown codes pass through."""
    text = str(key or "")
    return mapping.get(text, text)


def _clamp(text: str, limit: int) -> str:
    """Keep one line inside its time budget.

    The prompt asks for short; this enforces it. The talking model is a small cheap one and it will
    overshoot, and an overshoot is not a style problem - it is fifteen seconds of audio the audience
    sits through while the farm moves on without them.

    Cut where a sentence ends if there is one, then at a pause, and only then mid-word, so a clamped
    line still sounds like a person stopping rather than a tape being cut.
    """
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text

    window = text[:limit]

    for stop in "。！？!?～~…":
        cut = window.rfind(stop)
        if cut >= limit // 2:
            return window[: cut + 1]

    for pause in "，,、；;：:":
        cut = window.rfind(pause)
        if cut >= limit // 2:
            return window[:cut] + "。"

    return window.rstrip() + "…"


class Commentator:
    """Watches, decides when to speak, and speaks. Never blocks the game."""

    def __init__(
        self,
        llm,
        client,
        runlog=None,
        persona: str = "",
        speaker: str = "",
        log=print,
        active: bool = True,
        min_gap_s: float = 2.5,
        idle_gap_s: float = 15.0,
        max_chars: int = 24,
        density_target_s: float = 7.0,
        beat_cooldown_s: float = 25.0,
        look_ahead: int = 0,
        quiet_room_max: float = 6.0,
        welcome_backlog_max: int = 3,
        beat_fresh_s: float = 10.0,
        agent_line_fresh_s: float = 120.0,
        name_cooldown_s: float = 90.0,
        pause_continue: tuple[float, float] = (1.0, 2.5),
        pause_new_topic: tuple[float, float] = (2.5, 4.5),
        pause_moment: tuple[float, float] = (0.8, 1.8),
        on_suggestion=None,
        memory=None,
        context=None,
        pending_check=None,
        busy_check=None,
    ) -> None:
        self.llm = llm
        self.client = client
        self.runlog = runlog
        self.persona = persona
        self.speaker = speaker
        self.log = log
        self.active = active
        self.min_gap_s = min_gap_s
        self.idle_gap_s = idle_gap_s
        self.max_chars = max(12, int(max_chars or 0))
        self.on_suggestion = on_suggestion
        self.memory = memory          # MemoryStore, for recaps and for "what has been said"
        self.context = context        # callable() -> str: what the AI system itself is stuck on

        # The rhythm. All of it is live-tunable (Program.apply_pacing) because these numbers are
        # judgements about how the stream sounds, and that is not something anyone can settle at a
        # desk. See _pause_for for how they combine.
        self.density_target_s = density_target_s    # target seconds per line, averaged
        self.beat_cooldown_s = beat_cooldown_s      # how long the same action stays un-newsworthy
        self.pause_continue = pause_continue
        self.pause_new_topic = pause_new_topic
        self.pause_moment = pause_moment

        # How many lines the talker is allowed to prepare before the ones in front of them have been
        # spoken. 0 = speak about the moment, and only about the moment: nothing is written until the
        # previous line has finished, so a sentence is never about something that already moved on.
        # 1 = keep one line ready while the current one is in the air, which closes the silence
        # between sentences but decides each line five to ten seconds before it is heard - the
        # audience hears the game described a moment late. That trade is the operator's to make.
        self.look_ahead = max(0, int(look_ahead or 0))

        # What counts as "a quiet room". With a handful of people watching, a new arrival is the
        # whole audience and missing the hello is rude; with a crowd, greeting everybody properly
        # costs more airtime than the greeting is worth, and the stream should answer questions
        # instead. Two ways of being busy, both checked: people arriving/spoke in the last minute,
        # and names already queued up waiting for their hello.
        self.quiet_room_max = float(quiet_room_max)              # 人/分钟 以下算人少
        self.welcome_backlog_max = max(1, int(welcome_backlog_max))
        self._room_events: deque = deque(maxlen=300)             # when somebody arrived or spoke

        # How old a fact may be and still be worth saying. The game moves several times while one
        # sentence is written, synthesised and spoken, so a queue of events is not a script to read
        # out - it is a stack of old news. Anything older than this is dropped rather than narrated:
        # describing something two steps back is worse than saying nothing, because the audience is
        # watching the screen and can see that it is wrong.
        self.beat_fresh_s = float(beat_fresh_s)
        # Same idea for the agents' own one-liners, which are shown to the talker as "what they are
        # doing". A defect report from an hour ago, presented as current, is how a streamer ends up
        # explaining a problem it solved long ago.
        self.agent_line_fresh_s = float(agent_line_fresh_s)

        # callable() -> bool: is the mouth still working (queued, rendering, or speaking)? The gate
        # in the no-look-ahead mode.
        self.busy_check = busy_check

        # callable() -> int: how many lines the mouth has prepared but not yet started speaking.
        # Only consulted when look_ahead is on; see the gate in _turn.
        self.pending_check = pending_check

        self._beats: list[tuple[str, str]] = []
        self._chat: deque = deque(maxlen=12)
        self._pending_chat: deque = deque(maxlen=6)
        self._welcome: list[str] = []
        self._follows: list[str] = []
        self._anon_enter = 0
        self._anon_follow = 0
        self._said: deque = deque(maxlen=10)
        self._failures: dict[str, int] = {}
        self._notable_at: dict[str, float] = {}     # action -> last time it was worth mentioning
        self._spoken_at: deque = deque(maxlen=80)   # when lines went out, for the density nudge

        # What the streamer is currently talking about, in its own words. Carried across turns so
        # that a new event does not silently change the subject: a person playing a game narrates a
        # thread ("I'm trying to buy seeds... it won't click... oh, the coordinates"), and it is the
        # thread, not the individual event, that makes it sound like somebody is actually there.
        self._topic = ""
        self._last_topic = ""
        # The day's plan, kept apart from the agents' perishable one-liners: a plan is valid all day,
        # and a talker that forgets it after two minutes can only narrate disconnected movements.
        self._plan: dict | None = None
        self._plan_changed = False
        # Every kind of goal today has had, so a re-worded plan is not mistaken for a new direction.
        self._plan_seen: set = set()

        # Chat messages that have already been answered, and names that have just been addressed.
        # The room is small, so one unanswered question sits in "recent chat" for a dozen turns and
        # the talker thanks the same person again and again: measured, one question was mentioned ten
        # times over thirteen minutes. Remembering what has been dealt with is what stops that, and
        # it is a bookkeeping job - the model cannot be trusted to remember that it already replied.
        self._spent: deque = deque(maxlen=40)
        self._named_at: dict[str, float] = {}
        self.name_cooldown_s = float(name_cooldown_s)

        # How many lines in a row have been about the same subject. The continuity rule ("carry on
        # with what you were saying") is what makes the streamer sound like a person, and it is also
        # what lets one topic run for ten sentences if nothing stops it.
        self._subject = ""
        self._streak = 0
        # Consecutive quiet-moment lines, and which angle the next one should take. See IDLE_ANGLES.
        self._idle_count = 0
        self._idle_angle = 0

        # The latest line from every agent, kept here rather than asked for: the streamer is being
        # watched by the audience to explain how the machine works, so it has to know what the
        # machine is doing right now - and it already sees every event on the bus.
        self._agent_lines: dict[str, str] = {}

        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._bus = None
        self._last_spoke = 0.0
        self._last_progress = ""
        self._lines = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self, bus) -> None:
        # The voice listens for the streamer's own lines. Publishing straight onto the bus, rather
        # than relying on the log to echo them, means the audio does not depend on a logger being
        # present - which is exactly the kind of invisible coupling that leaves a stream silent with
        # no error anywhere.
        self._bus = bus

        if bus is not None:
            bus.subscribe(self._on_event, name="commentary")

        self._thread = threading.Thread(target=self._loop, name="commentary", daemon=True)
        self._thread.start()
        self.log("[解说] 已上线（说话间隔 ≥ %.0fs，安静 %.0fs 就会找话说）"
                 % (self.min_gap_s, self.idle_gap_s))

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def nudge(self, note: str) -> None:
        """Ask for a line out of band - used for the opening greeting, where no event happened."""
        if not note:
            return
        with self._cond:
            self._beats.append((note, "event", time.monotonic()))
            self._cond.notify_all()

    # -- events in ---------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        if not self.active:
            return

        name = str(event.get("name") or "")

        if name == "chat":
            self._on_chat(event)
            return

        # A new day wipes the plan; the planner will set the next one. Done before the beat is built,
        # so the day's first plan is treated as new (and therefore announced) rather than compared
        # against yesterday's goals.
        if name == "pipeline" and str(event.get("event")) == "成功":
            self._plan = None
            self._plan_changed = False
            self._plan_seen = set()

        if name == "planner":
            self._remember_plan(event)
            if not self._plan_changed:
                # Same day, same things to do, only the sentences were rewritten. Update silently:
                # telling the audience the plan again every three minutes reads as no memory at all.
                return

        self._remember_agent(name, event)

        beat = self._beat(event)
        if not beat:
            return

        with self._cond:
            self._beats.append((beat[0], beat[1], time.monotonic()))
            self._cond.notify_all()

    def _remember_plan(self, event: dict) -> None:
        """Keep today's goal, and say whether it has actually changed.

        Returns nothing; the caller reads `self._plan_changed`. The plan is stored as the small set
        of things to do plus the very next step, because that is all a one-sentence line can use -
        the planner's own paragraph is sixty characters of good advice the talker cannot fit.
        """
        focus = str(event.get("focus") or "")
        goals = [str(g) for g in (event.get("goals") or [])]
        text = focus + " " + " ".join(goals)

        # Canonical order, so the same day's goals read the same way in the prompt no matter how the
        # planner ordered its sentences this time.
        labels: list[str] = []
        for key, label in PLAN_LABELS:
            if label not in labels and key in text:
                labels.append(label)
        labels = labels[:4]

        next_step = ""
        if goals:
            head = re.split(r"[：:（(，,。]", goals[0])[0].strip()
            next_step = head[:24]

        previous = set((self._plan or {}).get("labels") or [])
        # "New" means a kind of task the day has not contained at all yet - not merely a goal that
        # was missing from the last wording. The planner rewrites itself every three minutes and the
        # set of labels wobbles by one every time; announcing on that basis is the same as announcing
        # every three minutes. A goal it has never had today (fishing, a gift, the mine) is a real
        # change of direction and worth telling the audience about.
        added = [label for label in labels if label not in self._plan_seen]

        self._plan = {"labels": labels, "next": next_step}
        self._plan_seen = self._plan_seen | set(labels)
        self._plan_changed = bool(added) or not previous

    def _plan_text(self) -> str:
        """The durable block for the prompt: what today is for, and what comes next."""
        if not self._plan:
            return ""
        labels = self._plan.get("labels") or []
        if not labels:
            return ""
        lines = ["今天的目标（整天有效）：" + "、".join(labels)]
        if self._plan.get("next"):
            lines.append("眼下这一步：" + str(self._plan["next"]))
        return "\n".join(lines)

    def _remember_agent(self, name: str, event: dict) -> None:
        """Keep the newest line from each agent, so the streamer can explain the machine itself."""
        if not name:
            return

        try:
            from runlog import summarize
            line = summarize(name, {k: v for k, v in event.items() if k != "name"})
        except Exception:
            line = ""

        if line:
            self._agent_lines[name] = (line[:120], time.monotonic())

    def _on_chat(self, event: dict) -> None:
        kind = str(event.get("chat_kind") or "danmaku")
        user = str(event.get("user") or "").strip()
        text = str(event.get("text") or "").strip()

        with self._cond:
            self._room_events.append(time.monotonic())
            if kind == "enter":
                if user:
                    if user not in self._welcome:
                        self._welcome.append(user)
                else:
                    self._anon_enter += 1
            elif kind == "follow":
                # A follow is not a message. Thanking it and answering it are different jobs.
                if user:
                    if user not in self._follows:
                        self._follows.append(user)
                else:
                    self._anon_follow += 1
            elif kind == "share":
                pass                       # worth knowing, not worth a sentence of its own
            else:
                self._chat.append({"user": user, "text": text, "kind": kind})
                self._pending_chat.append({"user": user, "text": text, "kind": kind})
            self._cond.notify_all()

    # -- turning an event into something worth saying -----------------------

    def _beat(self, event: dict) -> tuple[str, str] | None:
        """Turn one event into something worth saying: the line, and what it is *about*.

        The topic travels with the line because pacing depends on it - continuing a sentence about
        the same thing should follow quickly, changing the subject should not. Deriving the topic
        here, where the event still has its structured fields, is the only place it is knowable.
        """
        name = str(event.get("name") or "")

        if name == "pipeline":
            if str(event.get("event")) == "成功":
                return "新的一天开始了，你刚走进自己的农场。", "event:day"
            return None

        if name == "planner":
            focus = str(event.get("focus") or "").strip()
            if not focus:
                return None
            # Not the plan itself - that lives in the durable block (see _plan_text). This only says
            # what the line is FOR: a sixty-character paragraph squeezed into eighteen characters is
            # where "answers the plan with 'let me try a different variable'" comes from.
            goals = [str(g) for g in (event.get("goals") or [])]
            first = ""
            if goals:
                first = re.split(r"[：:（(，,。]", goals[0])[0].strip()[:20]
            text = ("计划有了变化。这一句用一句话告诉观众：今天接下来先干什么、为什么。"
                    + (f"\n（先做的这件事是：{first}）" if first else ""))
            return text, "event:plan"

        if name == "referee":
            return (f"今天结束了，给自己打了 {event.get('score')} 分"
                    f"（{event.get('verdict')}）。{str(event.get('why') or '')[:60]}", "event:day")

        if name == "observation":
            text = str(event.get("text") or "").strip()
            return (f"游戏里有人对你说：{text[:70]}", "event:dialogue") if text else None

        if name == "watcher":
            if str(event.get("event")) in ("remedy", "stalled", "gave up"):
                # The bridge speaks in codes ("dialogue is open", "dialogue_advance"). Read out
                # uncut they are noise the model then paraphrases into something stranger; the maps
                # already exist for the on-screen panel, so the talker gets the same words.
                from runlog import ACTIONS, BLOCKED, EVENTS, STOPPED

                held = str(event.get("reason") or event.get("stopped") or "")
                reason = _zh(STOPPED, held)
                if reason == held and held:
                    reason = _zh(BLOCKED, held)
                remedy = _zh(ACTIONS, event.get("remedy")) if event.get("remedy") else ""
                what = _zh(EVENTS, event.get("event"))
                return (f"{what}：{reason}（处置：{remedy or '无'}）", "moment")
            return None

        if name == "doctor":
            cause = str(event.get("cause") or "").strip()
            return (f"你搞明白了一件事：{cause[:70]}", "event:doctor") if cause else None

        if name == "gaps":
            from runlog import ACTIONS

            return f"有个动作不太灵：{_zh(ACTIONS, event.get('action'))}", "event:gaps"

        if name == "streamer":
            action = str(event.get("action") or "")
            ok = bool(event.get("ok"))

            if not ok:
                self._failures[action] = self._failures.get(action, 0) + 1
                if self._failures[action] == FAILURE_PATIENCE:
                    return f"同一件事失败好几次了（{action}），有点烦欸。", "moment"
                return None

            self._failures.pop(action, None)

            if action == "enter":
                # The whole point of this event is *where*. Built here, from the event's own args,
                # rather than left as a placeholder in a table that is never asked to format one.
                now = time.monotonic()
                if now - self._notable_at.get(action, 0.0) < self.beat_cooldown_s:
                    return None
                self._notable_at[action] = now

                where = str((event.get("args") or {}).get("target") or "").strip()
                line = f"走进了{where}" if where else "走进了建筑"
                return line, f"action:{action}"

            if action in NOTABLE:
                # Twelve menu clicks are one piece of news, not twelve. Without this the streamer
                # narrates the same trivial action until it sounds like a tape loop - which is the
                # fastest way to stop sounding like a person.
                now = time.monotonic()
                if now - self._notable_at.get(action, 0.0) < self.beat_cooldown_s:
                    return None
                self._notable_at[action] = now
                return NOTABLE[action], f"action:{action}"
            return None

        return None

    # -- the speaking loop -------------------------------------------------

    def _loop(self) -> None:
        """Talk, forever - and never let one bad sentence end the day.

        This runs on a background thread, and a background thread that raises does not crash the
        program: it just stops. The stream goes quiet, and the only clue is a traceback in a log
        nobody is watching while they watch the stream. So every turn is guarded instead of trusted,
        because the failure mode is silence and silence is the one thing nobody can debug by ear.
        """
        while not self._stop.is_set():
            try:
                self._turn()
            except Exception as exc:
                self.log(f"[解说] 这一句没说出来，跳过：{type(exc).__name__}: {str(exc)[:120]}")
                self._stop.wait(1.0)

    def _turn(self) -> None:
        with self._cond:
            if not self._work_pending():
                self._cond.wait(timeout=2.0)

        if self._stop.is_set():
            return

        gap = self.min_gap_s - (time.monotonic() - self._last_spoke)
        if gap > 0:
            self._stop.wait(min(gap, 2.0))
            return

        # The gate on when the next sentence may be written.
        #
        # Real-time (look_ahead 0): nothing is written while the mouth is still working. The line
        # therefore describes what is happening NOW - it is decided after the last one has finished,
        # so it cannot be about a moment that has already passed. The price is paid in silence: the
        # thinking and the synthesis both happen after the previous sentence ended, and that is
        # heard as a longer gap between sentences.
        #
        # Prepared (look_ahead 1): the next line is written while the current one is in the air, so
        # that work hides inside the previous sentence and the gaps collapse. The price is that a
        # line is fixed five to ten seconds before it is heard, and the audience hears the game
        # described slightly after the fact.
        if self._work_pending() and self._must_wait():
            self._stop.wait(0.25)
            return

        if self._work_pending():
            self._speak_queued()
        else:
            # Nothing is happening. On a teaching stream that is not a reason to stay quiet - but a
            # quiet moment is also the easiest place to sound broken: with no new fact to report,
            # the easy thing to do is describe the situation again in slightly different words, and
            # with nothing to describe at all the easy thing is to drift onto a topic with no
            # relation to what is on screen. Both are worse than silence.
            #
            # So the order is: the machine's own state first (that is what this channel is about,
            # and it is always current), and the rotating angles only once that is exhausted.
            idle_wait = self.idle_gap_s * (1.0 + 0.6 * min(3, self._idle_count))
            if time.monotonic() - self._last_spoke >= idle_wait:
                angle = IDLE_ANGLES[self._idle_angle % len(IDLE_ANGLES)]
                self._idle_angle += 1
                self._speak(beat="（现在没有新的进展。这一句请先看「系统现状」：有没有还没讲过的 —— "
                                 "卡在哪、刚才试了什么、学到了什么、下一步打算怎么办。"
                                 "有就如实讲出来，用你自己的话，别念术语。\n"
                                 "实在没有可讲的了，再按这个角度聊：\n" + angle +
                                 "\n不管是哪一种，都别把刚才那句话换个说法再讲一遍。）",
                            topic="idle")

    def _work_pending(self) -> bool:
        return bool(self._beats or self._pending_chat or self._welcome or self._follows
                    or self._anon_enter or self._anon_follow)

    def _must_wait(self) -> bool:
        """Is the mouth still busy enough that writing the next line now would run ahead?

        The two answers are the two modes, and the difference is the whole rhythm of the stream:
        one waits for silence so every sentence is about this moment, the other waits only for the
        queue so the pauses disappear and the sentences lag a little.
        """
        if self.look_ahead <= 0:
            return self._busy()

        return self._waiting() >= self.look_ahead

    def _busy(self) -> bool:
        """True while the voice has anything left to do: queued, rendering, or speaking."""
        if self.busy_check is None:
            return False
        try:
            return bool(self.busy_check())
        except Exception:
            return False

    def _waiting(self) -> int:
        """Lines the mouth has prepared but not started. Never raises, never blocks long."""
        if self.pending_check is None:
            return 0
        try:
            return int(self.pending_check())
        except Exception:
            return 0

    def _room_traffic(self) -> int:
        """How many people arrived or spoke in the last minute."""
        now = time.monotonic()
        return sum(1 for t in self._room_events if now - t <= 60.0)

    def _must_greet(self) -> bool:
        """Is the room quiet enough that a missed welcome would be noticed?

        The answer decides between the two ways of treating a greeting, and the difference is real:
        when it is quiet the welcome is guaranteed (the name is forced into the sentence if the
        model forgets it), and when it is busy the welcome is best-effort and may simply not happen.
        Spending retries on a room with a queue of twenty people would trade the questions of the
        many for the hello of the one.
        """
        if len(self._welcome) + len(self._follows) >= self.welcome_backlog_max:
            return False
        return self._room_traffic() <= self.quiet_room_max

    @staticmethod
    def _chat_key(message: dict) -> str:
        """How one chat message is remembered. Name plus text, because the same person says the same
        thing more than once and only the pair identifies it."""
        return f"{message.get('user') or ''}|{message.get('text') or ''}"

    @staticmethod
    def _similar(a: str, b: str, threshold: float = 0.5) -> bool:
        """Are these two short descriptions of a subject the same subject?"""
        da = {a[i:i + 2] for i in range(len(a) - 1)} or {a}
        db = {b[i:i + 2] for i in range(len(b) - 1)} or {b}
        return len(da & db) / max(1, len(da | db)) >= threshold

    def _pause_for(self, topic: str) -> float:
        """How long to wait before this line, in seconds.

        Three things decide it, and none of them is a constant, because a fixed interval does not
        sound like a person - it sounds like a metronome, and a metronome with a person's voice is
        worse than a slow streamer:

          * what this line is about, compared with the line before it. Carrying on about the same
            thing follows quickly; changing the subject waits.
          * a draw inside that range, so two consecutive pauses are never identical.
          * how much has been said lately. Ahead of the target density the pauses stretch, behind it
            they shorten - so the stream drifts towards a natural rate instead of being pinned to one.

        The player keeps the beat (Voice._play_loop): this is a request, and a request is what it
        should be, because the honest measure of a pause is the end of the previous line's audio -
        something only the player knows.
        """
        if topic == "moment":
            low, high = self.pause_moment
        elif topic and topic == self._last_topic:
            low, high = self.pause_continue
        else:
            low, high = self.pause_new_topic

        pause = random.uniform(min(low, high), max(low, high))

        now = time.monotonic()
        recent = [t for t in self._spoken_at if now - t <= 60.0]
        expected = 60.0 / max(1.0, self.density_target_s)

        if expected > 0:
            pressure = len(recent) / expected
            if pressure > 1.0:
                pause *= min(2.0, pressure)          # said a lot lately - let it breathe
            elif pressure < 1.0:
                pause *= max(0.5, pressure)          # gone quiet - pick it up

        return max(0.0, round(pause, 2))

    def forget_chat(self) -> None:
        """Throw away the room's history.

        Called when the room changes. Everything queued here belongs to people who are no longer
        watching: greeting them in the new room is confusing for them and for everyone else, and the
        answers would be to conversations the current audience never had.
        """
        with self._cond:
            self._chat.clear()
            self._pending_chat.clear()
            self._welcome.clear()
            self._follows.clear()
            self._anon_enter = 0
            self._anon_follow = 0

    def _speak_queued(self) -> None:
        """One turn, one subject: a question from the room first, then the greetings, then events.

        The priority here is load-bearing. The earlier order gave a greeting the first turn whenever
        fewer than three questions were queued, and handed the question back to the queue to be
        answered "next time". In a busy room somebody joins every few seconds, so the greeting always
        won and the question kept being deferred to itself - the streamer welcomed the room forever
        and never actually answered anybody.
        """
        with self._cond:
            answer = self._pending_chat.popleft() if self._pending_chat else None
            if answer is not None:
                # Drop it from the "recent chat" list too: a message that has been answered must not
                # still be sitting in the context to be thanked a second time on the next turn.
                try:
                    self._chat.remove(answer)
                except ValueError:
                    pass
            queued, self._beats = self._beats, []

        # Only what is still happening. A beat that has been waiting longer than beat_fresh_s is
        # about a moment the audience has already watched pass, and narrating it is what makes the
        # commentary feel like a recap of the last minute instead of a reaction to this second.
        now = time.monotonic()
        fresh = [item for item in queued if now - item[2] <= self.beat_fresh_s]
        if len(fresh) < len(queued):
            self.log(f"[解说] 丢掉 {len(queued) - len(fresh)} 条过期事件"
                     f"（超过 {self.beat_fresh_s:.0f} 秒才轮到讲）")

        # The newest, not the oldest. `_beats` is in arrival order, and taking the first three meant
        # reading out the backlog from the beginning - always describing the oldest thing still
        # waiting. If only three can be mentioned, they should be the three that just happened.
        chosen = fresh[-3:]
        beats = [text for text, _topic, _when in chosen]
        topic = chosen[-1][1] if chosen else "event"
        beat_age = (now - chosen[-1][2]) if chosen else None

        if answer is not None:
            # The specific message is the whole context. Given the rest of the room as well, the
            # model thanks a gift it was not asked about, and then thanks it again when its own turn
            # comes round - and the room hears the same sentence twice.
            self._spent.append(self._chat_key(answer))
            if answer.get("user"):
                self._named_at[str(answer["user"])] = time.monotonic()
            self._speak(beat="；".join(beats[:3]), answer=answer, include_chat=False, topic="chat",
                        beat_age_s=beat_age)
            return

        with self._cond:
            welcomes, self._welcome = self._welcome, []
            follows, self._follows = self._follows, []
            anon_enter, self._anon_enter = self._anon_enter, 0
            anon_follow, self._anon_follow = self._anon_follow, 0

        if not (welcomes or follows or anon_enter or anon_follow):
            if not beats:
                # Everything queued was already old news. Saying nothing is the honest answer; the
                # quiet-moment path will find something to talk about if it stays quiet.
                return
            self._speak(beat="；".join(beats[:3]), answer=None, topic=topic, beat_age_s=beat_age)
            return

        # Two ways of treating a hello, chosen by how busy the room is. See _must_greet.
        must = self._must_greet()
        limit = 5 if must else 3          # a crowd does not get every name spelled out

        greetings: list[str] = []

        # Only facts go in here - never an explanation, and never an instruction. Anything phrased
        # as "you cannot see the name, so say X" gets read out loud as "I cannot see your name",
        # which is exactly the sentence a viewer should never hear.
        if welcomes:
            who = "、".join(welcomes[:limit])
            if len(welcomes) > limit:
                who += f" 等 {len(welcomes)} 位"
            greetings.append(f"刚进直播间的人：{who}")

        if follows:
            who = "、".join(follows[:limit])
            greetings.append(f"刚关注直播间的人：{who}")

        if anon_enter:
            greetings.append(f"又有 {anon_enter} 位观众进了直播间")

        if anon_follow:
            greetings.append(f"又有 {anon_follow} 位观众关注了直播间")

        beat = "\n".join(greetings)
        if beats:
            beat += "\n" + "；".join(beats[:2])

        names = (welcomes + follows)[:5]
        prefix = ""

        if must and names:
            # An imperative, not a fact. The rules for this live in the system prompt, several
            # paragraphs up, and a small talking model follows the last thing it was told far more
            # reliably than a rule stated earlier - especially when the rest of its context is
            # inviting it to carry on with whatever it was already doing.
            beat += ("\n现在需要你做的：这一句先欢迎 " + "、".join(names) +
                     "，把名字念出来；念完可以再补半句别的，但这一句不要只讲你手上正在做的事。")
            if welcomes:
                prefix += "欢迎 " + "、".join(welcomes[:3]) + " 进来～"
            if follows:
                prefix += "谢谢 " + "、".join(follows[:3]) + " 关注～"

        self._speak(beat=beat, answer=None, include_chat=False, topic="greet",
                    require_names=names if must else None, prefix_if_missing=prefix,
                    must_greet=must, beat_age_s=beat_age, named=names)

    def _system_block(self, with_stuck: bool = True) -> str:
        """What the machine itself is doing and stuck on - the material for a teaching stream.

        Two kinds of material, and they are not equally relevant at any given moment. The agents'
        own one-liners are facts about *now* and are only worth showing while they are actually
        recent - each one carries its age, and the ancient ones are dropped, because "the agents
        are doing X" a hour after X ended is a lie the audience can see through. The open defects
        are a different animal: they are what the channel is *about*, but they are also the fastest
        way to end up describing last hour's problem while the farmer walks across the screen, so
        they are only offered when there is nothing fresh to talk about (`with_stuck`).
        """
        parts: list[str] = []

        if self._agent_lines:
            now = time.monotonic()
            fresh = [(name, line, now - when) for name, (line, when) in self._agent_lines.items()
                     if now - when <= self.agent_line_fresh_s]
            if fresh:
                parts.append("各个智能体刚才在做什么（括号里是多久以前，只说最新鲜的）：")
                for name, line, age in fresh:
                    if age < 20:
                        age_text = "刚刚"
                    elif age < 90:
                        age_text = f"{int(age)} 秒前"
                    else:
                        age_text = f"{int(age / 60)} 分钟前"
                    parts.append(f"- {AGENT_LABELS.get(name, name)}（{age_text}）：{line}")

        if with_stuck and self.context is not None:
            try:
                extra = str(self.context() or "")
            except Exception:
                extra = ""
            if extra:
                parts.append("现在还没解决的问题（这些是长期的问题，不是刚发生的）：")
                parts.append(extra)

        return "\n".join(parts)

    def _speak(self, beat: str, answer: dict | None = None, idle: bool = False,
               include_chat: bool = True, topic: str = "event",
               require_names: list[str] | None = None, prefix_if_missing: str = "",
               must_greet: bool | None = None, beat_age_s: float | None = None,
               named: list[str] | None = None) -> None:
        self._last_spoke = time.monotonic()
        self._spoken_at.append(self._last_spoke)

        # Worked out before the call, not after: the pause belongs to this line, and the moment the
        # model is asked is the moment the line starts costing time.
        gap_s = self._pause_for(topic)

        try:
            state = self.client.state(retries=0)
        except Exception:
            state = {}

        now = now_line(state)
        progress = progress_line(state)
        self._push_progress(progress)

        chat_text = ""
        if include_chat:
            with self._cond:
                recent = list(self._chat)[-8:]
            # Already answered: the room is quiet enough that a single question otherwise sits in the
            # prompt for a dozen turns, and the talker thanks the same person over and over.
            recent = [m for m in recent if self._chat_key(m) not in self._spent]
            if recent:
                chat_text = "\n".join(
                    f"{m['user']}：{m['text']}" if m["user"] else m["text"] for m in recent[-6:]
                )

        # Someone who was just addressed should not be addressed again this minute; a streamer who
        # says a name every sentence sounds like it has a list, not a conversation.
        hot_names = [name for name, when in self._named_at.items()
                     if time.monotonic() - when < self.name_cooldown_s]
        avoid = ("、" .join(hot_names[:3]) + " 刚刚已经被回应/欢迎过了，这一句别再点他们的名字。"
                 if hot_names else "")

        # Two lines on one subject is a conversation; three is a loop. The continuity rule above is
        # what makes the talker sound like a person and also what lets one topic run for ten
        # sentences, so the stop condition has to be explicit rather than hoped for.
        streak_hint = ""
        if self._streak >= 1:
            streak_hint = ("同一件事你已经连着讲了两句了。这一句必须换话题 —— 换个完全不同的角度"
                           "（心情、打算、问观众），哪怕没什么新事可讲。")

        pending_text = ""
        if answer:
            kind = CHAT_KINDS.get(answer.get("kind") or "", "")
            who = answer.get("user") or "观众"
            pending_text = f"{who}（{kind}）：{answer.get('text') or '（进场）'}"

        # The last few lines verbatim, newest last, with the instruction to keep going rather than
        # to avoid repeating. Framing them as "do not repeat" is what made every sentence start a
        # new subject: told to avoid the past, a talker will not build on it either.
        said_lines = list(self._said)[-3:]
        already = "\n".join(f"- {line}" for line in said_lines)

        # The thread, but only while it is still the subject. Passing it every turn is what made the
        # streamer keep explaining the previous minute: a small model told both "here is what just
        # happened" and "here is what you were talking about" will usually continue its own subject,
        # and then the line matches neither the screen nor the event that triggered it. The topic
        # tag that came with the beat already says whether this is a continuation.
        continuing = bool(topic) and topic == self._last_topic
        thread_text = self._topic if continuing and topic not in ("greet", "idle") else ""

        # The machine's own state, offered only when there is no fresh event to talk about. The
        # channel is about teaching an AI to play, so defects are the material - but they are also
        # last hour's problem, and reading them out while the farmer walks across the screen is
        # exactly the sort of mismatch the audience notices immediately.
        system_text = self._system_block(with_stuck=(topic in ("idle", "greet")))

        messages = [
            {"role": "system", "content": prompts.COMMENTARY_SYSTEM},
            {
                "role": "user",
                "content": prompts.commentary_user(
                    persona=self.persona,
                    now_line=now,
                    beat=beat,
                    chat=chat_text,
                    memory=already,
                    pending=pending_text,
                    system=system_text,
                    thread=thread_text,
                    plan=self._plan_text(),
                    avoid=avoid,
                    streak_hint=streak_hint,
                ),
            },
        ]

        started = time.perf_counter()
        try:
            reply, _ = self.llm.chat_json(messages, max_tokens=400, temperature=0.85)
        except Exception as exc:
            self.log(f"[解说] 说话失败：{str(exc)[:90]}")
            return

        latency = time.perf_counter() - started

        say = _clamp(str(reply.get("say") or "").strip(), self.max_chars)
        if not say:
            return

        # The welcome, guaranteed when the room is quiet. The model forgets the name about one time
        # in ten - it fills the "to" field correctly and then talks about its own business - and a
        # personal hello is exactly the thing that must not be missed when the audience is small.
        # Prepending costs no extra thinking time (a re-ask would cost the audience three seconds of
        # silence in real-time mode); the price is that the sentence reads as a person would say it,
        # which is what it is.
        guard_fired = False
        if require_names and prefix_if_missing:
            if not any(name and name in say for name in require_names):
                say = prefix_if_missing + " " + say
                guard_fired = True
                self.log(f"[解说] 模型没念名字，已补上：{prefix_if_missing}")

        to = str(reply.get("to") or "").strip()
        suggestion = str(reply.get("suggestion") or "").strip()

        # The thread, in the streamer's own words. Kept across turns so the next line - which may be
        # about something else entirely - still knows what it was in the middle of saying.
        thread = str(reply.get("thread") or "").strip()
        if thread:
            self._topic = _clamp(thread, 40)
        self._last_topic = topic

        # Who was just spoken to, so the next line does not say the same name again.
        for name in (named or []):
            self._named_at[str(name)] = time.monotonic()
        if to:
            self._named_at[to] = time.monotonic()

        # How long the current subject has been running. Compared on the model's own one-line summary
        # of what it is talking about, because that is the only description of the subject that
        # survives between turns.
        subject = str(thread or self._topic or "").strip()
        if subject and self._subject and self._similar(subject, self._subject):
            self._streak += 1
        else:
            self._streak = 0
        if subject:
            self._subject = subject

        # A real event resets the quiet-moment backoff: the game moved, so the next lull is a fresh
        # lull and may be filled sooner.
        if topic == "idle":
            self._idle_count += 1
        else:
            self._idle_count = 0

        shown = f"【{self.speaker}】{say}" if self.speaker else say
        self.log(f"[解说 {latency:4.1f}s 停{gap_s:3.1f}s] {shown}")

        # NOT pushed to the screen here. A subtitle that appears when the line is *decided* runs
        # ahead of the voice, which then has to catch up: the viewer reads one sentence while
        # hearing the previous one, and with any queue at all the gap is measured in tens of
        # seconds. The line is announced by whoever plays it (see Voice.on_air), so the screen and
        # the speaker never disagree.
        if self._bus is not None:
            try:
                self._bus.publish({"name": "commentary", "say": say, "speaker": self.speaker,
                                   "to": to, "suggestion": suggestion, "gap_s": gap_s})
            except Exception:
                pass

        self._said.append(say)
        self._lines += 1

        if self.runlog is not None:
            # via_bus=False: this event has already been published above, and saying the same
            # sentence twice is worse than not saying it. `gap_s` travels with the line because the
            # mouth may well be in another process - the window reads this file and speaks it, and
            # the pause is part of the sentence. The two greeting fields are recorded so the welcome
            # rate can be measured instead of guessed: they are what "_reply_gaps" reads back.
            extra = {"must_greet": bool(must_greet), "guard": guard_fired} if topic == "greet" else {}
            if beat_age_s is not None:
                # How old the event behind this sentence was when the sentence was written. It is the
                # honest measure of "does the commentary follow the screen": the pipeline adds a few
                # more seconds before it is heard, so this number is a floor, not the whole story.
                extra["beat_age_s"] = round(float(beat_age_s), 1)
            self.runlog.agent("commentary", via_bus=False, say=say, to=to, beat=beat[:120],
                              suggestion=suggestion, latency_s=round(latency, 2), gap_s=gap_s,
                              thread=self._topic[:60], **extra)

        if suggestion and self.on_suggestion is not None:
            self.on_suggestion(suggestion, to or "观众")

    def _push_progress(self, text: str) -> None:
        if not text or text == self._last_progress:
            return
        self._last_progress = text
        self.client.action("progress", text=text)


# ---------------------------------------------------------------------------
# Reading the game the way a viewer would
# ---------------------------------------------------------------------------

def now_line(state: dict) -> str:
    """A short, human answer to 'where are we right now?'."""
    game = state.get("game") or {}
    player = state.get("player") or {}

    season = SEASON_ZH.get(str(game.get("season") or ""), str(game.get("season") or ""))
    day = game.get("day_of_month")
    year = game.get("year")
    clock = clock_text(game.get("time_of_day"))
    where = player.get("current_location_display") or player.get("current_location") or "？"

    bits = []
    if season and day:
        bits.append(f"{season}{day}日 第{year}年")
    if clock:
        bits.append(clock)
    bits.append(f"在{where}")

    return " ".join(bits)


def progress_line(state: dict) -> str:
    """The strip across the top: day, clock, quest, money, and today's job."""
    game = state.get("game") or {}
    player = state.get("player") or {}
    quests = state.get("quests") or []

    season = SEASON_ZH.get(str(game.get("season") or ""), str(game.get("season") or ""))
    day = game.get("day_of_month")
    year = game.get("year")
    clock = clock_text(game.get("time_of_day"))

    money = player.get("money")
    money_text = f"{money}g" if money is not None else ""

    primary = next((q for q in quests if q.get("is_primary")), None) or next(
        (q for q in quests if not q.get("is_completed")), None)

    quest_text = ""
    if primary:
        done = sum(1 for q in quests if q.get("is_completed"))
        quest_text = f"任务 {done}/{len(quests)}"

    where = player.get("current_location_display") or player.get("current_location") or ""

    segments = [f"{season}{day}日 第{year}年" if season and day else "",
                clock, where, quest_text, money_text]
    return "  ·  ".join(s for s in segments if s)


def clock_text(time_of_day) -> str:
    """Stardew stores 8:30am as 830. Show it the way a player reads it."""
    try:
        raw = int(time_of_day)
    except (TypeError, ValueError):
        return ""

    if raw <= 0:
        return ""

    hour, minute = divmod(raw, 100)
    suffix = "am" if hour < 12 else "pm"
    display = hour if 1 <= hour <= 12 else (hour - 12 if hour > 12 else 12)
    return f"{display}:{minute:02d}{suffix}"
