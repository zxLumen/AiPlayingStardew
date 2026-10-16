"""The room, inside the game.

The danmaku is captured by the commentary process and appended to `logs/agents/chat.jsonl` - the
same shape as the other two pipes in this project (the playing process writes `bus.jsonl` for it to
read, it writes `commentary.jsonl` for the voice window to read). Nothing was reading `chat.jsonl`,
so the in-game panel sat on whatever it was last handed and looked frozen: the messages were being
written the whole time, one file away.

Two things this gets right by construction:

  * It binds to the FILE, not to a room. Switching the live room in the commentary window writes to
    the same file, so the game follows with nothing to switch. A binding to a room would have to be
    told about every reconnection; a binding to a file does not.
  * It starts at the end of the file and follows a byte offset, so a 150KB backlog of messages that
    nobody watched is not dumped into the panel on every restart.
"""

from __future__ import annotations

import json
import os
import threading
import time


class ChatFeed:
    """Follows the danmaku log and hands new messages to the game."""

    def __init__(self, path, push, log=print, max_batch: int = 5,
                 quiet_after_s: float = 20.0) -> None:
        self.path = path
        self.push = push
        self.log = log
        self.max_batch = max_batch
        self.quiet_after_s = quiet_after_s

        self.offset = 0
        self._pending: list[dict] = []
        self._last_push = 0.0
        self._last_real = 0.0                       # when somebody last actually said something
        self._last_enter = 0.0                      # and when we last welcomed anybody
        self.enter_every_s = 5.0
        self._announced: dict[str, float] = {}      # who we have already welcomed, and when
        self._first_poll = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Begin at the end of the file, then follow it on a thread of its own.

        Its own thread because the panel must never be a reason for the playing loop to wait - the
        same reason every other subscriber in this project has its own.
        """
        try:
            self.offset = os.path.getsize(self.path)
            self.log(f"[弹幕桥] 从 {self.path} 末尾开始跟（跳过 {self.offset} 字节历史）")
        except OSError:
            self.offset = 0

        def run() -> None:
            while not self._stop.is_set():
                try:
                    self.poll()
                except Exception:
                    pass
                self._stop.wait(1.0)

        self._thread = threading.Thread(target=run, name="chatfeed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def poll(self) -> None:
        """Read whatever has been appended since the last look. Cheap: a stat and a small read."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return                                  # not created yet - nothing to do

        if size < self.offset:
            # The file was rotated or truncated. Start over rather than reading garbage.
            self.offset = 0

        if size == self.offset:
            self._flush(force=False)
            return

        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
                self.offset = handle.tell()
        except OSError:
            return

        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue

            # A partial last line means the writer is mid-append; the next poll will bring the rest.
            if not line.endswith("}"):
                self.offset -= len(line) + 1
                break

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            self._accept(event)

        self._flush(force=False)

    # -- turning an event into a panel line --------------------------------

    def _accept(self, event: dict) -> None:
        if not isinstance(event, dict):
            return

        # Two shapes are in play: the raw bus event, and something already shaped for the panel.
        kind = str(event.get("chat_kind") or event.get("kind") or "danmaku")
        user = str(event.get("user") or event.get("name") or "")
        text = str(event.get("text") or event.get("message") or "").strip()

        if kind == "enter":
            self._accept_enter(user)
            return

        if not text:
            return

        self._last_real = time.monotonic()

        prefix = {"gift": "[礼物] ", "guard": "[上舰] ", "super_chat": "[醒目留言] "}.get(kind, "")
        self._pending.append({"user": user, "text": prefix + text})

    def _accept_enter(self, user: str) -> None:
        """Welcome somebody in - but only while the room is quiet.

        Join notices used to be dropped outright, which left the panel looking dead in exactly the
        moments a viewer is most likely to be watching it: a slow room with three people in it. So
        they are shown now, under three conditions that keep them from burying the conversation:

          * the room has been quiet for a while, so there is nothing to bury
          * and not more than one every few seconds, so a rush of arrivals is still readable
          * and the same person is not welcomed again every time they reconnect

        The moment somebody actually talks, the notices stop on their own. In a busy room this is
        exactly the old behaviour - and the numbers say busy is the normal case (1672 arrivals to
        614 messages), which is why they were dropped in the first place.
        """
        now = time.monotonic()

        if now - self._last_real < self.quiet_after_s:
            return                                  # there is real talk to protect

        if not user:
            return

        if now - self._last_enter < self.enter_every_s:
            return                                  # one arrival at a time

        if now - self._announced.get(user, -1e9) < 300:
            return                                  # already said hello to this one

        self._last_enter = now
        self._announced[user] = now
        self._pending.append({"user": user, "text": "进入了直播间"})

        # Don't let this dict grow without bound in a busy room.
        if len(self._announced) > 200:
            cutoff = now - 300
            self._announced = {u: t for u, t in self._announced.items() if t > cutoff}

    def _flush(self, force: bool) -> None:
        """Send at most a handful at a time - the room talks far faster than a panel can read."""
        if not self._pending:
            return

        now = time.monotonic()
        if not force and len(self._pending) < self.max_batch and now - self._last_push < 1.5:
            return

        batch, self._pending = self._pending[: self.max_batch], self._pending[self.max_batch :]
        self._last_push = now

        try:
            self.push(batch)
        except Exception:
            pass        # the panel is a nicety; it must never break the run
