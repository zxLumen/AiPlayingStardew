"""A tiny fan-out bus, so the show can watch the game without ever slowing it down.

The playing loop is the one thing that must not wait for anything. The commentary, the voice and the
chat all want to see what is happening, and any of them might be slow - an LLM call, an audio
synthesis, a network read. So none of them is called from the playing loop: each gets its own thread
and its own bounded queue, and when a queue is full the OLDEST event is dropped.

Dropping is the point. A commentary line that arrives two minutes late is not "late commentary", it
is a lie about what the streamer is doing now. Losing it is strictly better than delivering it.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path


class EventBus:
    """Publish/subscribe with per-subscriber threads, bounded queues, and no back-pressure."""

    def __init__(self, log=print, queue_size: int = 256) -> None:
        self.log = log
        self.queue_size = queue_size

        self._subscribers: list[tuple[deque, threading.Event, threading.Event]] = []
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._stopped = False

    def subscribe(self, callback, name: str = "subscriber") -> None:
        """Add a callback. It runs on its own thread and may block for as long as it likes."""
        queue: deque = deque(maxlen=self.queue_size)
        wake = threading.Event()
        stop = threading.Event()

        def run() -> None:
            while not stop.is_set():
                if not self._drain(queue, callback):
                    wake.wait(0.5)
                    wake.clear()

        thread = threading.Thread(target=run, name=f"bus-{name}", daemon=True)

        with self._lock:
            self._subscribers.append((queue, wake, stop))
            self._threads.append(thread)

        thread.start()

    def publish(self, event: dict) -> None:
        """Never blocks, never raises. A full queue loses its oldest event, not the newest."""
        if self._stopped:
            return

        event = dict(event)
        event.setdefault("t", time.strftime("%H:%M:%S"))
        event.setdefault("ts", time.time())

        with self._lock:
            subscribers = list(self._subscribers)

        for queue, wake, _ in subscribers:
            try:
                queue.append(event)      # deque(maxlen=...) drops the oldest on overflow
            except Exception:
                pass
            wake.set()

    def close(self) -> None:
        self._stopped = True
        with self._lock:
            subscribers = list(self._subscribers)
        for _, wake, stop in subscribers:
            stop.set()
            wake.set()

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _drain(queue: deque, callback) -> bool:
        worked = False
        while queue:
            try:
                event = queue.popleft()
            except IndexError:
                break

            worked = True
            try:
                callback(event)
            except Exception:
                # One bad event or one bad subscriber must not stop the bus.
                pass
        return worked


class JsonlTail:
    """Follow a JSON-lines file and publish every new line onto a bus.

    This is the seam that lets a process watch the game without being part of it. The playing loop
    appends events to the file and immediately forgets them; anyone who wants to react reads them
    from here, and can therefore be edited, restarted, or left dead without the game noticing.
    """

    def __init__(self, path, bus, log=print, poll_s: float = 0.4, name: str = "stream") -> None:
        self.path = Path(path)
        self.bus = bus
        self.log = log
        self.poll_s = poll_s
        self.name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0
        self._seen = 0

    def start(self, from_start: bool = False) -> None:
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            size = 0

        # Start at the end by default: yesterday's events are not this process's business, and
        # replaying them would have the commentary narrate a farm that has already been harvested.
        self._offset = 0 if from_start else size
        self._thread = threading.Thread(target=self._loop, name=f"tail-{self.name}", daemon=True)
        self._thread.start()
        self.log(f"[总线] 正在跟随 {self.path.name}"
                 + ("（从头读）" if from_start else "（只读新的）"))

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
        if not self.path.exists():
            return

        size = self.path.stat().st_size
        if size < self._offset:
            self._offset = 0                 # truncated or rotated underneath us
        if size == self._offset:
            return

        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self._offset)
            chunk = handle.read()
            self._offset = handle.tell()

        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                self._seen += 1
                self.bus.publish(event)

    @property
    def seen(self) -> int:
        return self._seen


class JsonlSink:
    """Copy every event on a bus into a JSON-lines file.

    The mirror of JsonlTail, and together they are how one process hands its view to another: the
    commentary daemon hears the room and writes it here, the window reads it there. One connection,
    two screens, and no way for them to disagree about which room they are watching.
    """

    MAX_BYTES = 8 * 1024 * 1024
    KEEP_LINES = 2000

    def __init__(self, path, bus, log=print, only: str | None = None,
                 name: str = "sink") -> None:
        self.path = Path(path)
        self.bus = bus
        self.log = log
        self.only = only              # keep only events with this "name", or None for everything
        self.name = name
        self._lock = threading.Lock()
        self.written = 0

    def start(self) -> None:
        self.bus.subscribe(self._write, name=self.name)
        self.log(f"[总线] 正在写入 {self.path.name}")

    def _write(self, event: dict) -> None:
        if self.only is not None and str(event.get("name") or "") != self.only:
            return

        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)

                # A busy room is thousands of lines a night, so the file is trimmed rather than
                # left to grow until someone notices the disk.
                if self.path.exists() and self.path.stat().st_size > self.MAX_BYTES:
                    kept = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
                    self.path.write_text("\n".join(kept[-self.KEEP_LINES:]) + "\n",
                                         encoding="utf-8")

                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
                self.written += 1
            except OSError:
                pass
