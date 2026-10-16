"""Where the room's messages come from.

The commentary agent should not care whether a message came from Bilibili, from a test file, or
from a keyboard. It cares about three things and nothing else:

    who said it, what they said, and what kind of thing it was (a message, a join, a gift)

So every source produces the same event and publishes it on the bus:

    {"name": "chat", "chat_kind": "danmaku"|"enter"|"gift"|"guard"|"super_chat",
     "user": "...", "text": "..."}

That split is what makes the whole feature testable with no live room: run the local source, type
in a file, and the entire pipeline - reply, voice, overlay - behaves exactly as it will on stream.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path


class ChatSource:
    name = "chat"

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class LocalChatSource(ChatSource):
    """A text file you can type into. Stands in for a real room, and costs nothing to test.

    Lines are appended and picked up within a second:

        小明: 主播主播，你在干嘛呀          -> a normal message from 小明
        /enter 小美                        -> 小美 just walked into the room
        /gift 阿强 辣条                    -> 阿强 sent a gift
        /guard 老王 舰长                    -> 老王 bought a membership
        anything with no name prefix       -> a message from 观众
    """

    def __init__(self, path: Path, bus, log=print, poll_s: float = 1.0) -> None:
        self.path = Path(path)
        self.bus = bus
        self.log = log
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(
                "# 本地弹幕测试文件：在这里打字就会被当成观众说话\n"
                "# 格式   名字: 内容    /enter 名字    /gift 名字 礼物名    /guard 名字 舰长\n",
                encoding="utf-8",
            )
        self._offset = self.path.stat().st_size
        self._thread = threading.Thread(target=self._loop, name="chat-local", daemon=True)
        self._thread.start()
        self.log(f"[弹幕] 本地源已开启：往 {self.path.name} 里打字即可（文件里已写用法）")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._read_new()
            except OSError:
                pass
            self._stop.wait(self.poll_s)

    def _read_new(self) -> None:
        size = self.path.stat().st_size
        if size < self._offset:          # the file was truncated or replaced; start over
            self._offset = 0
        if size == self._offset:
            return

        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self._offset)
            chunk = handle.read()
            self._offset = handle.tell()

        for raw in chunk.splitlines():
            self._handle_line(raw.strip())

    def _handle_line(self, line: str) -> None:
        if not line or line.startswith("#"):
            return

        if line.startswith("/"):
            parts = line[1:].split(None, 2)
            if not parts:
                return
            verb = parts[0].lower()
            kind = {"enter": "enter", "gift": "gift", "guard": "guard",
                    "sc": "super_chat"}.get(verb, "danmaku")
            user = parts[1] if len(parts) > 1 else "观众"
            text = parts[2] if len(parts) > 2 else ""
            self._publish(kind, user, text)
            return

        if "：" in line:
            user, _, text = line.partition("：")
        elif ":" in line:
            user, _, text = line.partition(":")
        else:
            user, text = "观众", line

        self._publish("danmaku", user.strip(), text.strip())

    def _publish(self, kind: str, user: str, text: str) -> None:
        self.log(f"[弹幕] {user}: {text}" if text else f"[弹幕] {user} 进场")
        self.bus.publish({"name": "chat", "chat_kind": kind, "user": user, "text": text})
