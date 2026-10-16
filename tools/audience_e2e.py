"""End-to-end check of the audience loop, without touching the game.

Feeds the real Bilibili room (and a few synthetic viewers) through the real commentary model and
the real voice, with the game stubbed out. Answers the only question that matters before a stream:
does a message from the room come back out of the streamer's mouth, in the right voice?

    .venv\\Scripts\\python.exe tools\\audience_e2e.py --seconds 60
    .venv\\Scripts\\python.exe tools\\audience_e2e.py --no-voice      # text only, quieter
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.stdout.reconfigure(encoding="utf-8")

from bilibili import BilibiliDanmaku  # noqa: E402
from commentary import Commentator  # noqa: E402
from events import EventBus  # noqa: E402
from program import load_config, make_commentary_llm  # noqa: E402
from voice import Voice  # noqa: E402

STATE = {
    "game": {"season": "fall", "day_of_month": 19, "year": 1, "time_of_day": 610, "has_save": True},
    "player": {"name": "Juniper", "money": 500, "current_location_display": "农场",
               "current_item": {"name": "喷壶", "charges": 0}},
    "quests": [{"is_completed": False, "is_primary": True}],
}


class StubClient:
    """Stands in for the bridge, so this can run while the game is busy with something else."""

    def state(self, retries: int = 3) -> dict:
        return STATE

    def action(self, name: str, **kwargs):
        if name == "narrate":
            print(f"   >> 字幕   {kwargs.get('speaker')}: {kwargs.get('text')}", flush=True)
        elif name == "progress":
            print(f"   >> 进度条 {kwargs.get('text')}", flush=True)
        elif name == "chat":
            print(f"   >> 弹幕上屏 +{len(kwargs.get('lines') or [])}", flush=True)
        return {"ok": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--room", default="")
    parser.add_argument("--no-voice", action="store_true")
    parser.add_argument("--no-room", action="store_true", help="只用模拟观众，不连真房间")
    parser.add_argument("--scenario", default="both", choices=["both", "names", "anon", "none"],
                        help="注入哪种观众事件：有名字 / 匿名（B站打码）/ 两种都来 / 都不来")
    args = parser.parse_args()

    config = load_config()
    room = args.room or str(config["chat"].get("bilibili_room") or "")

    sections = [k for k, v in json.loads(
        (ROOT / "config" / "secrets.json").read_text(encoding="utf-8")).items()
        if isinstance(v, dict)]
    print(f"secrets 配置段: {sections}")

    llm = make_commentary_llm()
    if llm is None:
        print("没有可用的解说模型，退出")
        return 1

    bus = EventBus(log=lambda m: None)

    # Record the beat each line was generated from - without it, "why did it say that" is guesswork.
    from runlog import RunLog
    runlog = RunLog(ROOT, log=lambda m: None)
    runlog.attach_bus(bus)

    voice = Voice(
        cache_dir=ROOT / "logs" / "voice-cache",
        voice=str(config["voice"]["voice"]),
        rate=str(config["voice"]["rate"]),
        pitch=str(config["voice"]["pitch"]),
        jitter_rate_pct=float(config["voice"]["jitter_rate_pct"]),
        jitter_pitch_hz=float(config["voice"]["jitter_pitch_hz"]),
        per_sentence=bool(config["voice"].get("per_sentence", True)),
        enabled=not args.no_voice,
        log=print,
    )
    voice.start(bus)

    commentary = Commentator(
        llm=llm,
        client=StubClient(),
        runlog=runlog,
        persona="温柔台湾腔的农场女孩，说话慢、爱笑、有礼貌",
        speaker="Juniper",
        min_gap_s=6.0,
        idle_gap_s=30.0,
    )
    commentary.start(bus)

    print("--- 打招呼 ---")
    commentary.nudge("直播刚开始，跟刚进直播间的观众打个招呼。")

    room_source = None
    if room and not args.no_room:
        print(f"--- 连真实直播间 {room} ---")
        room_source = BilibiliDanmaku(room, bus)
        room_source.start()

    time.sleep(8)

    named = [{"name": "chat", "chat_kind": "enter", "user": "小明", "text": ""},
             {"name": "chat", "chat_kind": "enter", "user": "阿露", "text": ""},
             {"name": "chat", "chat_kind": "follow", "user": "老王", "text": ""},
             {"name": "chat", "chat_kind": "danmaku", "user": "阿露",
              "text": "你今天准备种什么呀？"},
             {"name": "chat", "chat_kind": "gift", "user": "老王", "text": "辣条 x1"}]

    # The masked form, exactly as Bilibili delivers it when nobody is logged in.
    anon = [{"name": "chat", "chat_kind": "enter", "user": "", "text": ""},
            {"name": "chat", "chat_kind": "enter", "user": "", "text": ""},
            {"name": "chat", "chat_kind": "follow", "user": "", "text": ""}]

    print(f"--- 注入模拟观众（scenario={args.scenario}）---")
    if args.scenario in ("both", "names"):
        for event in named:
            bus.publish(event)
        time.sleep(9)
    if args.scenario in ("both", "anon"):
        for event in anon:
            bus.publish(event)
        time.sleep(9)

    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        time.sleep(0.5)

    if room_source is not None:
        room_source.stop()
    commentary.stop()
    voice.stop()
    bus.close()

    print()
    print(f"解说共 {commentary._lines} 句 | {voice.stats()}")
    return 0 if commentary._lines > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
