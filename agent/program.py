"""The show layer, in one place.

Assembles the pieces that make this a stream rather than a bot: an event bus, the commentary brain,
the voice, and wherever the room's messages come from. The playing loop knows none of this exists -
it publishes events and forgets them.

Everything is configured in config/program.json, which is written with working defaults the first
time it runs, so there is always a file to edit rather than a mystery to guess at.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audience import Audience  # noqa: E402
from bilibili import BilibiliDanmaku  # noqa: E402
from chat_source import LocalChatSource  # noqa: E402
from commentary import Commentator  # noqa: E402
from events import EventBus, JsonlSink, JsonlTail  # noqa: E402
from voice import Voice, find_virtual_cable  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "program.json"

DEFAULTS: dict = {
    "_readme": [
        "直播节目层配置。改完重启 agent.py 生效。",
        "commentary = 解说脑；voice = 配音；chat = 弹幕来源。",
        "chat.source: local（读 logs/viewer-input.txt，用来测试）| bilibili（真直播间）",
    ],
    "commentary": {
        "enabled": True,
        "model_section": "commentary",
        "min_gap_s": 2.5,
        "_min_gap_note": [
            "硬下限：两句话至少隔这么久（秒）。只用来防止抢话，不决定节奏 —— 真正的节奏由",
            "下面的 pause_* 和声音本身的长度决定。设大了就只是白放着不说话（设 14 时每句要 36 秒）。",
        ],
        "max_chars": 24,
        "_max_chars_note": [
            "单句台词的最大字数，超了按标点收口截断（提示词里要求的是 25 字）。",
            "短句是节奏的根本：24 字约 5 秒语音，观众听得完，也才插得进下一句。",
        ],
        "idle_gap_s": 15.0,
        "_idle_gap_note": [
            "实在没事发生时，安静这么久就自己找话说（秒）。主播不会静 38 秒。",
            "注意：连着几次没事，间隔会自动变长（15 → 24 → 33 → 42，有事了立刻复位），",
            "而且每次换一个角度讲（心情 / 打算 / 请观众出主意 / 昨天的事），",
            "不会把同一件事换个说法再讲一遍。",
        ],
        "pause_continue": [1.0, 2.5],
        "pause_new_topic": [2.5, 4.5],
        "pause_moment": [0.8, 1.8],
        "_pause_note": [
            "每句之前的停顿区间（秒），每次在这个区间里随机取，所以节奏是弹性的、不是节拍器：",
            "  pause_continue  接着上一句讲同一件事（最短）",
            "  pause_moment    翻车、惊喜、当下正在发生的事（贴身，最短）",
            "  pause_new_topic 换了话题、换了件事（最长）",
            "真值 = 随机值 × 密度系数：最近说太多就整体拉长，安静久了就整体缩短。",
            "注意：这是**下限**。实时模式下（look_ahead=0）光思考+合成就要约 7 秒，",
            "停顿时长实际由管线决定，这几个数字只在管线够快时才起作用。",
        ],
        "density_target_s": 7.0,
        "_density_note": [
            "自适应目标：平均几秒说一句（秒）。这是弹性来源 —— 说密了就自动放慢，",
            "冷场了就自动加快。想要 5~10 秒一句的感觉，7 左右最合适。",
        ],
        "beat_cooldown_s": 25.0,
        "_beat_cooldown_note": [
            "同一个动作多久之内只念一次（秒）。点菜单这种会连点十几次，没有冷却就像卡带。",
        ],
        "look_ahead": 0,
        "_look_ahead_note": [
            "0 = 实时（默认）：上一句播完才开始想下一句。每句都在讲**当下**正在发生的事，",
            "    代价是思考和合成这段时间观众听得到 —— 实测间隔约 7 秒（思考约 2.5s + 合成约 4.2s）。",
            "1 = 预生成一句：上一句还在播就先想好下一句，间隔能压到 1~2 秒，",
            "    但每句要提前 5~10 秒决定，听起来像慢半拍在描述已经过去的画面。",
            "※ 这一项改完 5 秒自动生效，不用重启。",
        ],
        "auto_tune": True,
        "_auto_tune_note": [
            "true = 改上面任何一个数字都不用重启，解说脑每 5 秒自己读一次。",
        ],
        "quiet_room_max": 6.0,
        "_quiet_room_note": [
            "判断「人少 / 人多」的阈值：最近 60 秒里「进场 + 弹幕」条数。",
            "  ≤ 这个数 = 人少 → **保证欢迎**：名字一定会被念出来（模型忘了就在句首补上）。",
            "  > 这个数 = 人多 → 尽力而为：做同样的提示，但漏了就漏了，不为欢迎花额外时间；",
            "     并且回答弹幕优先于欢迎，人一多欢迎自动让路。",
            "人少人多都只看最近一分钟，所以开播没人时一定热情，热闹时不会卡在打招呼上。",
        ],
        "welcome_backlog_max": 3,
        "_welcome_backlog_note": [
            "还有多少个名字在排队等着被欢迎，就算「人多」（人）。达到这个数就不再保证欢迎了 ——",
            "这一条比人数更能反映「欢迎不过来」。",
        ],
    },
    "voice": {
        "enabled": True,
        "backend": "http",
        "_backend_note": [
            "backend = http：用本地语音服务 tools/tts_server.py（IndexTTS2，音色与自然度都好得多）",
            "  启动服务：tts\\.venv\\Scripts\\python.exe tools\\tts_server.py --duration 1.0",
            "backend = edge：退回 edge-tts（零显存，但机器感重）",
            "服务连不上会自动退回 edge，直播不会变成哑巴。",
        ],
        "max_queue": 2,
        "_max_queue_note": [
            "同时排队的台词条数上限。真正的深度由 commentary.look_ahead 决定，这里只是天花板；",
            "留一个空位是为了手工插一句（POST /say）时不会把已经在排的那句挤掉。",
        ],
        "stale_s": 25.0,
        "_stale_s_note": [
            "一句台词最多等多久还没轮到它就丢掉（秒）。画面变得比这句话快的时候，迟到的话不是",
            "迟到，是错的。实时模式下正常年龄只有合成那几秒；若开了 look_ahead=1 会有十秒左右，",
            "所以这个值必须明显大于正常年龄，否则合成一慢就变成静默地删句子、留下空洞。",
        ],
        "pause_floor_s": 0.8,
        "_pause_floor_note": [
            "两句之间的最短停顿（秒）。防止连成一串听不清。",
        ],
        "tts_url": "http://127.0.0.1:8790/tts",
        "tts_ref": "tts/voices/demo/voice_09.wav",
        "emo": [],
        "duration": 1.0,
        "_duration_note": "时长倍率：越大越慢。1.0 = 参考音频原速（当前选定），0.9 更快、1.1 更慢",
        "voice": "zh-CN-XiaoyiNeural",
        "rate": "-6%",
        "pitch": "+8Hz",
        "volume": "+0%",
        "device": None,
        "autoplay": True,
        "per_sentence": True,
        "jitter_rate_pct": 4.0,
        "jitter_pitch_hz": 5.0,
        "sentence_gap_ms": 90,
        "gap_jitter_ms": 40,
        "fade_ms": 28,
        "normalize": True,
    },
    "chat": {
        "source": "local",
        "bilibili_room": "",
        "_sessdata_note": [
            "可选：填上你自己的 B站 SESSDATA，进场/关注的观众名字才看得见。",
            "不填也能收弹幕，但 B站 会把进场/关注的名字打码成「你***」。",
            "SESSDATA 等同登录凭证，属于机密，只写在本机 config/program.json，不要外传。",
        ],
        "bilibili_sessdata": "",
        "local_file": "logs/viewer-input.txt",
    },
}


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Read the config, filling in anything missing so an old file keeps working."""
    config = json.loads(json.dumps(DEFAULTS))     # deep copy

    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            for section, values in saved.items():
                if isinstance(values, dict):
                    config.setdefault(section, {}).update(values)
                else:
                    config[section] = values
        except (OSError, json.JSONDecodeError):
            pass
    else:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    return config


def _pause_pair(cfg: dict, key: str, low: float, high: float) -> tuple[float, float]:
    """Read a [min, max] pause from config, tolerating a hand-edited file.

    Every knob here is meant to be edited by whoever is listening, not by whoever wrote the code, so
    a typo has to be survivable: a bad pair falls back to the default rather than taking the stream
    off the air. A reversed pair is sorted rather than refused - the intent is obvious.
    """
    value = cfg.get(key)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            first, second = float(value[0]), float(value[1])
            return (min(first, second), max(first, second))
        except (TypeError, ValueError):
            pass
    return (low, high)


def make_commentary_llm(section: str = "commentary", log=print):
    """The talking model, deliberately separate from the playing model.

    Talking happens far more often than deciding, so it belongs on something cheap. If the named
    section is not configured at all it falls back to the fast brain rather than refusing to run -
    a stream with a slightly wrong voice is better than a stream with no voice.
    """
    from llm import LlmClient
    from llm_config import SECRETS_PATH

    section = (section or "commentary").strip() or "commentary"

    try:
        saved = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved.get(section), dict):
            log(f"[解说] config/secrets.json 里没有 [{section}] 段，改用 fast")
            section = "fast"
    except (OSError, json.JSONDecodeError):
        section = "fast"

    try:
        client = LlmClient(section)
        log(f"[解说] 用 [{section}] 段的模型：{client.endpoint.model}")
        return client
    except Exception as exc:
        log(f"[解说] 没有可用的模型（{str(exc)[:80]}）——解说关闭")
        return None


class Program:
    """Everything the audience sees and hears. Safe to start when half of it is switched off."""

    def __init__(self, client, runlog=None, store=None, persona: str = "", speaker: str = "",
                 log=print, config: dict | None = None, context=None, role: str = "agent") -> None:
        self.client = client
        self.runlog = runlog
        self.store = store
        self.persona = persona
        self.speaker = speaker
        self.log = log
        self.config = config or load_config()
        self.context = context        # callable() -> str: what the AI system is stuck on
        # "agent"       - the playing process. Owes the show nothing but a file of wishes.
        # "commentary"  - this process IS the show: follows the event stream and talks.
        self.role = role

        self.bus = EventBus(log=log)
        self.commentator: Commentator | None = None
        self.voice: Voice | None = None
        self.chat = None
        self.tail = None
        self.chat_sink = None
        self.audience: Audience | None = None
        self._started = False
        self._voice_missing_at = 0.0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True

        self.audience = Audience(PROJECT_ROOT / "memory" / "viewer_suggestions.jsonl", log=self.log)

        # The playing process deliberately starts none of the show. It used to own the commentary,
        # and that made every change to a sentence cost a restart of the whole game loop - the day
        # in progress died so that a subtitle could be reworded. Now the two are joined by a file.
        if self.role != "commentary":
            self.log("[节目] 本进程只玩游戏；解说/配音在独立进程里（见 start-commentary.bat）")
            return

        self._start_stream()
        self._start_commentary()
        self._start_chat()
        self._welcome()

    def _start_stream(self) -> None:
        """Follow bus.jsonl, which is how this process hears about a game it is not running."""
        path = PROJECT_ROOT / "logs" / "agents" / "bus.jsonl"
        self.tail = JsonlTail(path, self.bus, log=self.log, name="commentary")
        self.tail.start()

    def _start_commentary(self) -> None:
        voice_cfg = self.config.get("voice") or {}
        if voice_cfg.get("enabled") and self._window_is_up():
            # Two mouths, one script. Refusing to be the second one is the only safe answer: the
            # alternative is every sentence said twice with nothing in any log to show for it.
            self.log("[配音] voice.enabled=true，但「解说窗口」正在出声 —— 两个嘴会把每句话说两遍，"
                     "所以本进程不出声。要本进程出声，先关掉窗口；否则把 config/program.json 里"
                     "的 voice.enabled 改成 false。")
        elif voice_cfg.get("enabled"):
            device = voice_cfg.get("device")
            if device is None:
                device = find_virtual_cable()
            self.voice = Voice(
                cache_dir=PROJECT_ROOT / "logs" / "voice-cache",
                log=self.log,
                enabled=True,
                backend=str(voice_cfg.get("backend") or "edge"),
                tts_url=str(voice_cfg.get("tts_url") or "http://127.0.0.1:8790/tts"),
                tts_ref=str(voice_cfg.get("tts_ref") or ""),
                emo=(voice_cfg.get("emo") or None),
                duration=voice_cfg.get("duration") or voice_cfg.get("speed"),
                voice=str(voice_cfg.get("voice") or ""),
                rate=str(voice_cfg.get("rate") or ""),
                pitch=str(voice_cfg.get("pitch") or ""),
                volume=str(voice_cfg.get("volume") or ""),
                device=device,
                # Two slots so the talker can prepare the next line while this one is still in the
                # air. 25s rather than 30s: a prepared line is normally spoken within ten seconds or
                # so (the pause it waits for is part of that), and anything much older is a line
                # about a moment that has moved on - but the threshold has to sit ABOVE that normal
                # age, or a slow synthesis merely deletes sentences and leaves silent holes.
                max_queue=int(voice_cfg.get("max_queue") or 2),
                stale_s=float(voice_cfg.get("stale_s") or 25.0),
                pause_floor_s=float(voice_cfg.get("pause_floor_s") or 0.8),
                autoplay=bool(voice_cfg.get("autoplay", True)),
                per_sentence=bool(voice_cfg.get("per_sentence", True)),
                jitter_rate_pct=float(voice_cfg.get("jitter_rate_pct") or 0.0),
                jitter_pitch_hz=float(voice_cfg.get("jitter_pitch_hz") or 0.0),
                sentence_gap_ms=int(voice_cfg.get("sentence_gap_ms") or 90),
                gap_jitter_ms=int(voice_cfg.get("gap_jitter_ms") or 40),
                fade_ms=int(voice_cfg.get("fade_ms") or 28),
                normalize=bool(voice_cfg.get("normalize", True)),
                on_air=self._announce,
            )
            self.voice.start(self.bus)
        else:
            # Say it out loud in the log. "This process is quiet" is a legitimate configuration, but
            # it is also exactly what a broken one looks like, and the difference has to be visible
            # without opening the config file. Being silent about silence is how two mouths went
            # unnoticed for half an hour: every line was spoken twice and nothing said so.
            self.log("[配音] 本进程不出声（config: voice.enabled=false）—— 由「解说窗口」出声，"
                     "字幕也由窗口发给游戏。")

        commentary_cfg = self.config.get("commentary") or {}
        if commentary_cfg.get("enabled"):
            llm = make_commentary_llm(str(commentary_cfg.get("model_section") or "commentary"),
                                      log=self.log)
            if llm is not None:
                self.commentator = Commentator(
                    llm=llm,
                    client=self.client,
                    runlog=self.runlog,
                    persona=self.persona,
                    speaker=self.speaker,
                    log=self.log,
                    active=True,
                    min_gap_s=float(commentary_cfg.get("min_gap_s") or 2.5),
                    idle_gap_s=float(commentary_cfg.get("idle_gap_s") or 15.0),
                    max_chars=int(commentary_cfg.get("max_chars") or 24),
                    density_target_s=float(commentary_cfg.get("density_target_s") or 7.0),
                    beat_cooldown_s=float(commentary_cfg.get("beat_cooldown_s") or 25.0),
                    look_ahead=int(commentary_cfg.get("look_ahead") or 0),
                    quiet_room_max=float(commentary_cfg.get("quiet_room_max") or 6.0),
                    welcome_backlog_max=int(commentary_cfg.get("welcome_backlog_max") or 3),
                    pause_continue=_pause_pair(commentary_cfg, "pause_continue", 1.0, 2.5),
                    pause_new_topic=_pause_pair(commentary_cfg, "pause_new_topic", 2.5, 4.5),
                    pause_moment=_pause_pair(commentary_cfg, "pause_moment", 0.8, 1.8),
                    on_suggestion=(self.audience.offer if self.audience else None),
                    context=self.context,
                    pending_check=self._voice_waiting,
                    busy_check=self._voice_busy,
                )
                self.commentator.start(self.bus)

    def stop(self) -> None:
        for part in (self.chat, self.commentator, self.voice, self.tail):
            try:
                if part is not None:
                    part.stop()
            except Exception:
                pass
        self.bus.close()
        self.chat_sink = None

    # -- chat source -------------------------------------------------------

    CHAT_FEED = "logs/agents/chat.jsonl"

    def _start_chat(self) -> None:
        chat_cfg = self.config.get("chat") or {}

        # One listener, one room. Whatever the window wants to display it reads from this copy, so
        # there is never a second connection that could be watching a different room than the one
        # the commentary is answering.
        self.chat_sink = JsonlSink(PROJECT_ROOT / self.CHAT_FEED, self.bus, log=self.log,
                                   only="chat", name="chat-feed")
        self.chat_sink.start()

        source = str(chat_cfg.get("source") or "local").lower()

        if source == "bilibili":
            room = str(chat_cfg.get("bilibili_room") or "").strip()
            if not room:
                self.log("[弹幕] 配置里没填 bilibili_room，退回本地文件源")
                source = "local"
            else:
                self._open_room(room)

        if source == "local":
            path = PROJECT_ROOT / str(chat_cfg.get("local_file") or "logs/viewer-input.txt")
            self.chat = LocalChatSource(path, self.bus, log=self.log)
            self.chat.start()

    def _open_room(self, room: str) -> None:
        chat_cfg = self.config.get("chat") or {}
        self.chat = BilibiliDanmaku(room, self.bus, log=self.log,
                                    sessdata=str(chat_cfg.get("bilibili_sessdata") or ""))
        self.chat.start()

    def switch_room(self, room: str) -> None:
        """Follow the room the operator picked, without touching anything else.

        The window's room box writes the number into config/program.json. Reading that file at
        startup was enough while the commentary lived in the playing process and got restarted
        together with it; on its own it means the commentary keeps answering a room that no longer
        exists. So the room is watched, not read once.
        """
        room = str(room).strip()
        if not room:
            return

        try:
            if self.chat is not None:
                self.chat.stop()
        except Exception:
            pass
        self.chat = None

        self._open_room(room)

        # Remember it, so the caller's "has the room changed?" test has something current to compare
        # against. Without this the config still says the old room and the watcher reconnects every
        # few seconds, forever.
        self.config.setdefault("chat", {})["bilibili_room"] = room

        # The queue is still full of people from the previous room, and answering them in a new one
        # confuses everybody - not least the audience that just walked in.
        if self.commentator is not None:
            self.commentator.forget_chat()

        self.log(f"[弹幕] 已跟随到直播间 {room}（未重启其它任何东西）")

    def current_room(self) -> str:
        return str((self.config.get("chat") or {}).get("bilibili_room") or "").strip()

    def _welcome(self) -> None:
        """A first line, so nobody has to wonder whether the voice is working."""
        if self.commentator is not None:
            self.commentator.nudge("直播刚开始，跟刚进直播间的观众打个招呼。")

    # -- what the audience sees and hears ----------------------------------

    def _announce(self, text: str) -> None:
        """Put the line on screen the moment it is spoken, not when it was written.

        Voice calls this the instant playback starts, which is the entire reason the subtitle and
        the audio agree: one is derived from the other instead of both being derived from a clock.
        """
        try:
            self.client.action("narrate", text=text, speaker=self.speaker)
        except Exception:
            pass

    def _voice_waiting(self) -> int:
        """How many lines the mouth already has ready and has not started speaking yet.

        Zero means "go ahead and think about the next sentence NOW" - not "the mouth is idle". That
        distinction is the whole point of this method: a voice that is `busy` because a line is in
        the air is exactly the moment the talker should be preparing the next one, and a talker that
        waits for `busy` to clear can only start thinking after the sentence has ended. The audience
        hears that as a gap of however long thinking and synthesis take.

        If this process owns the voice, ask it directly. Otherwise the voice lives in the separate
        window (so 直播姬 can capture it by window), which publishes the same number on /health. A
        window too old to publish it falls back to `busy`, which is the previous, slower behaviour.

        An unreachable window means zero, which reads backwards until you say it out loud: the
        window is where the sound comes from, so if it cannot be reached, nothing is being said.
        The opposite answer is how this once went permanently silent.
        """
        if self.voice is not None and getattr(self.voice, "enabled", False):
            return int(self.voice.pending)

        port = int((self.config.get("window") or {}).get("port") or 8792)
        try:
            import httpx
            with httpx.Client(timeout=2.0, trust_env=False) as client:
                data = client.get(f"http://127.0.0.1:{port}/health").json()
            if "pending" in data:
                return int(data.get("pending") or 0)
            return 1 if data.get("busy") else 0
        except Exception as exc:
            now = time.monotonic()
            if now - self._voice_missing_at > 60.0:
                self._voice_missing_at = now
                self.log(f"[解说] 语音窗口没响应（127.0.0.1:{port}）——{type(exc).__name__}。"
                         f"字幕和声音可能不会出现；先启动 start-voice-window.bat。"
                         f"（本进程不会因此停止说话）")
            return 0

    def _voice_busy(self) -> bool:
        """Is the mouth still working - queued, rendering, or speaking?

        This is the gate for the real-time mode: the talker writes nothing until the previous line
        has finished, which is what keeps a sentence about the moment it describes.
        """
        if self.voice is not None and getattr(self.voice, "enabled", False):
            return self.voice.busy

        port = int((self.config.get("window") or {}).get("port") or 8792)
        try:
            import httpx
            with httpx.Client(timeout=2.0, trust_env=False) as client:
                data = client.get(f"http://127.0.0.1:{port}/health").json()
            return bool(data.get("busy"))
        except Exception:
            return False

    def apply_pacing(self, config: dict | None = None) -> list[str]:
        """Re-read the pacing numbers and hand them to the talker, without a restart.

        Rhythm is the one thing that cannot be tuned from a desk: it is a judgement about how the
        stream *sounds*, and it changes with the room, the day, and the mood of whoever is watching.
        Restarting the commentary to change a number costs nothing in the game, but it does cost the
        thread of the conversation, so the numbers are read live here.
        """
        cfg = (config or {}).get("commentary") or self.config.get("commentary") or {}
        if self.commentator is None:
            return []

        changes: list[str] = []

        def put(name: str, value) -> None:
            old = getattr(self.commentator, name, None)
            if isinstance(value, float) and isinstance(old, float) and abs(old - value) < 1e-9:
                return
            if old == value:
                return
            setattr(self.commentator, name, value)
            changes.append(f"{name}:{old}->{value}")

        try:
            put("min_gap_s", float(cfg.get("min_gap_s") or 2.5))
            put("idle_gap_s", float(cfg.get("idle_gap_s") or 15.0))
            put("max_chars", max(12, int(cfg.get("max_chars") or 24)))
            put("density_target_s", float(cfg.get("density_target_s") or 7.0))
            put("beat_cooldown_s", float(cfg.get("beat_cooldown_s") or 25.0))
            put("look_ahead", max(0, int(cfg.get("look_ahead") if cfg.get("look_ahead") is not None else 0)))
            put("quiet_room_max", float(cfg.get("quiet_room_max") or 6.0))
            put("welcome_backlog_max", max(1, int(cfg.get("welcome_backlog_max") or 3)))
            for key in ("pause_continue", "pause_new_topic", "pause_moment"):
                value = cfg.get(key)
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    put("pause_" + key.split("_", 1)[1], (float(value[0]), float(value[1])))
        except (TypeError, ValueError) as exc:
            self.log(f"[解说] 节奏参数有误，忽略：{type(exc).__name__}: {exc}")
            return []

        self.config.setdefault("commentary", {}).update(cfg)
        return changes

    def _window_is_up(self, timeout: float = 1.5) -> bool:
        """Is the separate voice window running?

        There are two possible mouths in this project - this process's own `Voice`, and the window -
        and exactly one of them may be open. Both being open is not a small mistake: every line is
        said twice, once as the talker finishes it and again a few seconds later when the window gets
        to it, which is heard as a stutter. Worse, it is invisible everywhere else: the log records
        one line, the subtitle shows one line, and the only symptom is the sound.
        """
        port = int((self.config.get("window") or {}).get("port") or 8792)
        try:
            import httpx
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                return bool(client.get(f"http://127.0.0.1:{port}/health").json().get("ok"))
        except Exception:
            return False

    def enforce_one_mouth(self) -> None:
        """Give up this process's voice if the window has appeared since we started.

        The window can be launched at any time, so checking only at startup is not enough: the pair
        would overlap for the rest of the run. Cheap to check, and it costs one log line to make the
        difference between one streamer and two talking over each other.
        """
        if self.voice is None or not getattr(self.voice, "enabled", False):
            return
        if not self._window_is_up():
            return

        self.voice.enabled = False
        self.log("[配音] 解说窗口起来了 —— 本进程让出声音，免得每句话说两遍。")

    def refresh_persona(self, persona: str, speaker: str = "") -> None:
        """The character can be designed mid-run (first start), so the voice has to be told."""
        self.persona = persona
        if speaker:
            self.speaker = speaker
        if self.commentator is not None:
            self.commentator.persona = persona
            if speaker:
                self.commentator.speaker = speaker

    # -- suggestions -------------------------------------------------------

    def viewer_block(self) -> str:
        return self.audience.prompt_block() if self.audience else ""

    def take_suggestions(self) -> list[dict]:
        return self.audience.take() if self.audience else []

    def stats(self) -> str:
        bits = []
        if self.voice is not None:
            bits.append(self.voice.stats())
        if self.commentator is not None:
            bits.append(f"解说 {self.commentator._lines} 句")
        return " | ".join(bits)


def main() -> int:
    parser_args = sys.argv[1:]
    if parser_args and parser_args[0] == "devices":
        from voice import list_devices
        for row in list_devices():
            print(row)
        return 0

    if parser_args and parser_args[0] == "config":
        print(json.dumps(load_config(), ensure_ascii=False, indent=2))
        return 0

    print(__doc__)
    print("用法：python agent/program.py [devices|config]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
