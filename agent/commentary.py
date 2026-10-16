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
            self._beats.append((note, "event"))
            self._cond.notify_all()

    # -- events in ---------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        if not self.active:
            return

        name = str(event.get("name") or "")

        if name == "chat":
            self._on_chat(event)
            return

        self._remember_agent(name, event)

        beat = self._beat(event)
        if not beat:
            return

        with self._cond:
            self._beats.append(beat)
            self._cond.notify_all()

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
            self._agent_lines[name] = line[:120]

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
            return (f"今天的打算定下来了：{focus}", "event:plan") if focus else None

        if name == "referee":
            return (f"今天结束了，给自己打了 {event.get('score')} 分"
                    f"（{event.get('verdict')}）。{str(event.get('why') or '')[:60]}", "event:day")

        if name == "observation":
            text = str(event.get("text") or "").strip()
            return (f"游戏里有人对你说：{text[:70]}", "event:dialogue") if text else None

        if name == "watcher":
            if str(event.get("event")) in ("remedy", "stalled", "gave up"):
                return (f"卡了一下：{event.get('reason') or event.get('stopped') or ''}"
                        f"（处置：{event.get('remedy') or '无'}）", "moment")
            return None

        if name == "doctor":
            cause = str(event.get("cause") or "").strip()
            return (f"你搞明白了一件事：{cause[:70]}", "event:doctor") if cause else None

        if name == "gaps":
            return f"有个动作不太灵：{event.get('action')}", "event:gaps"

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

        beats = [text for text, _ in queued]
        topic = queued[-1][1] if queued else "event"

        if answer is not None:
            # The specific message is the whole context. Given the rest of the room as well, the
            # model thanks a gift it was not asked about, and then thanks it again when its own turn
            # comes round - and the room hears the same sentence twice.
            self._speak(beat="；".join(beats[:3]), answer=answer, include_chat=False, topic="chat")
            return

        with self._cond:
            welcomes, self._welcome = self._welcome, []
            follows, self._follows = self._follows, []
            anon_enter, self._anon_enter = self._anon_enter, 0
            anon_follow, self._anon_follow = self._anon_follow, 0

        if not (welcomes or follows or anon_enter or anon_follow):
            self._speak(beat="；".join(beats[:3]), answer=None, topic=topic)
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
                    must_greet=must)

    def _system_block(self) -> str:
        """What the machine itself is doing and stuck on - the material for a teaching stream."""
        parts: list[str] = []

        if self._agent_lines:
            parts.append("各个智能体刚才在做什么：")
            for name, line in self._agent_lines.items():
                parts.append(f"- {AGENT_LABELS.get(name, name)}：{line}")

        if self.context is not None:
            try:
                extra = str(self.context() or "")
            except Exception:
                extra = ""
            if extra:
                parts.append("现在还没解决的问题：")
                parts.append(extra)

        return "\n".join(parts)

    def _speak(self, beat: str, answer: dict | None = None, idle: bool = False,
               include_chat: bool = True, topic: str = "event",
               require_names: list[str] | None = None, prefix_if_missing: str = "",
               must_greet: bool | None = None) -> None:
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
                recent = list(self._chat)[-6:]
            if recent:
                chat_text = "\n".join(
                    f"{m['user']}：{m['text']}" if m["user"] else m["text"] for m in recent
                )

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
                    system=self._system_block(),
                    # No topic thread on a greeting: it is the one signal pulling the talker back to
                    # what it was already doing, and a hello that continues the previous subject is
                    # how a viewer who just walked in gets ignored.
                    thread="" if topic == "greet" else self._topic,
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
