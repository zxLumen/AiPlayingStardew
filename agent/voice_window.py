"""The voice and the room, as a window.

Streaming tools - Bilibili's 直播姬 in particular - enumerate *windows*, not console processes. Run
the narration inside a headless `python.exe` and it is simply not in the list: there is nothing to
pick, and no audio session to attach to. So the voice gets a window of its own.

The window is deliberately self-contained. It plays the audio (so the audio session belongs to it,
which is the whole point), it shows what the streamer is saying, and it reads the live room itself -
danmaku, joins, follows, gifts, memberships - so it looks like a real streaming overlay rather than
a debug panel. It keeps working while the agent restarts, which on this project happens often.

Two kinds of input:

    the streamer's lines   read from logs/agents/commentary.jsonl as they are written
    the room               connected to directly, from the room id in config/program.json

and one output: audio, rendered with the same `Voice` the agent uses, so whichever backend the config
names - edge-tts, or the local IndexTTS2 service - is what you hear here.

    .venv\\Scripts\\pythonw.exe agent\\voice_window.py
    .venv\\Scripts\\pythonw.exe agent\\voice_window.py --room 1735947454
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

from events import EventBus, JsonlTail  # noqa: E402
from voice import Voice, list_devices  # noqa: E402

TITLE = "AiPlaying 解说"
DEFAULT_PORT = 8792          # deliberately not 8790: that port belongs to the TTS service

BG = "#10141c"
BG_SOFT = "#181e2a"
BG_CHAT = "#12161f"
INK = "#e8e8ee"
INK_DIM = "#8b93a7"
INK_FAINT = "#5d6473"
ACCENT = "#ffaad0"
GOLD = "#ffd479"
CYAN = "#7fd6ff"
GOOD = "#96e6aa"
WARN = "#ffd27e"
VIOLET = "#c9a6ff"

# One colour per viewer, so a regular is recognisable at a glance - the same idea the in-game panel
# uses, for the same reason.
USER_INKS = [CYAN, GOLD, GOOD, ACCENT, VIOLET, "#ffb08a", "#9fe8e8", "#e8e8a0"]

KIND_STYLE = {
    "danmaku": ("弹幕", INK_DIM),
    "enter": ("进场", INK_FAINT),
    "follow": ("关注", CYAN),
    "share": ("分享", INK_FAINT),
    "gift": ("礼物", GOLD),
    "guard": ("上舰", VIOLET),
    "super_chat": ("醒目留言", ACCENT),
}


def _ink_for(name: str) -> str:
    if not name:
        return INK
    return USER_INKS[sum(ord(c) for c in name) % len(USER_INKS)]


def _gap_s(event: dict) -> float | None:
    """The pause the commentary asked for before this line, if it asked for one.

    It rides in the log line rather than being recomputed here: the rhythm is the talker's opinion
    about the moment, and two places guessing at it would drift apart. A window reading an older
    line (written before this existed) simply gets None, which means "no wait" - the behaviour it
    had before.
    """
    value = event.get("gap_s")
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def save_room(room: str) -> None:
    """Write the room into config/program.json, leaving everything else in the file alone.

    Written to `chat.bilibili_room`, the key the agent reads, so the whole system ends up pointing
    at one room instead of the overlay watching one and the commentary answering another. Any
    per-window override is cleared for the same reason: two keys, one truth.
    """
    path = ROOT / "config" / "program.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, json.JSONDecodeError):
        raw = {}

    raw.setdefault("chat", {})["bilibili_room"] = str(room)
    if isinstance(raw.get("window"), dict):
        raw["window"]["bilibili_room"] = ""

    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def another_instance_running(port: int, timeout: float = 1.5) -> bool:
    """Is a second解说窗口 already answering on this port?

    Ask by connecting, not by binding. After a restart the port can still be in TIME_WAIT with
    nobody listening, and a bind probe would then refuse a perfectly good start - a guard that
    blocks the restart it is supposed to protect is worse than no guard.

    A second window is not a harmless duplicate: it is a second mouth playing the same lines a beat
    apart, and the talker reads `busy` from whichever one owns the port while the other one keeps
    talking over it.
    """
    import json as _json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8", "replace"))
        return bool(data.get("window"))
    except urllib.error.HTTPError:
        return True                     # something is answering, and it is not this instance
    except Exception:
        return False                    # nothing there


def warn(title: str, message: str, log=print, auto_close_s: float = 10.0) -> None:
    """Say it in the log, and on screen if we can - a pythonw app has no console to print to.

    The dialog closes itself. A second instance launched by a script, with nobody sitting in front
    of it, must not hang for ever on a box that needs a click.
    """
    log(message)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.after(int(auto_close_s * 1000), root.destroy)
        messagebox.showwarning(title, message)
        root.destroy()
    except Exception:
        pass


class VoiceWindow:
    def __init__(self, voice: Voice, *, port: int, title: str, log_path: Path, room: str = "",
                 speaker: str = "", sessdata: str = "", always_on_top: bool = True,
                 autoplay: bool = True, with_chat: bool = True, log=print) -> None:
        import tkinter as tk

        self.tk = tk
        self.voice = voice
        self.port = port
        self.title = title
        self.log_path = Path(log_path)
        self.room = room
        self.speaker = speaker
        self.sessdata = sessdata
        self.chat_disabled = not with_chat
        self.log = log
        self.muted = not autoplay

        self.said = 0
        self.chat_seen = 0
        self.chat_bus = 0
        self.chat_at = 0.0
        self.link = "未连接"
        self._stop = threading.Event()
        self.server: ThreadingHTTPServer | None = None
        self.fatal = False              # set when another instance already owns the port
        self.bus: EventBus | None = None
        self.chat_source: JsonlTail | None = None

        self.root = tk.Tk()
        self.root.title(title)
        self.root.configure(bg=BG)
        self.root.geometry("1000x640")
        self.root.minsize(640, 420)
        if always_on_top:
            self.root.attributes("-topmost", True)

        self._build()
        self._install_crash_logging(ROOT / "logs" / "voice-window.log")
        self._start_server()
        self._start_tailer()
        self._start_chat()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(500, self._tick)

    # ---------------------------------------------------------------- layout

    def _build(self) -> None:
        tk = self.tk

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 6))
        tk.Label(head, text="AiPlaying", bg=BG, fg=ACCENT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        tk.Label(head, text="  教会 AI 玩星露谷物语", bg=BG, fg=INK_DIM,
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        self.state = tk.Label(head, text="", bg=BG, fg=INK_DIM,
                              font=("Microsoft YaHei UI", 10))
        self.state.pack(side="right")

        # The footer is packed BEFORE the body and anchored to the bottom. Packed after an
        # `expand=True` widget it gets whatever slice of the window is left - which, with a body that
        # wants all of it, is nothing, and the buttons silently vanish below the edge.
        foot = tk.Frame(self.root, bg=BG)
        foot.pack(side="bottom", fill="x", padx=18, pady=(0, 14))

        mid = tk.Frame(self.root, bg=BG)
        mid.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        mid.columnconfigure(0, weight=3, uniform="cols")
        mid.columnconfigure(1, weight=2, uniform="cols")
        mid.rowconfigure(0, weight=1)

        self._build_stage(mid)
        self._build_room(mid)

        self.entry = tk.Entry(foot, bg=BG_SOFT, fg=INK, insertbackground=INK, bd=0,
                              font=("Microsoft YaHei UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self._say_typed())

        tk.Button(foot, text="试说一句", command=self._say_typed, bg=ACCENT, fg="#20101a",
                  activebackground=ACCENT, bd=0, padx=16, pady=5,
                  font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")

        self.mute_button = tk.Button(foot, text="已静音" if self.muted else "有声",
                                     command=self._toggle_mute, bg=BG_SOFT, fg=INK,
                                     activebackground=BG_SOFT, bd=0, padx=14, pady=5,
                                     font=("Microsoft YaHei UI", 10))
        self.mute_button.pack(side="left", padx=(8, 0))

    def _build_stage(self, parent) -> None:
        tk = self.tk
        left = tk.Frame(parent, bg=BG_SOFT)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        tk.Label(left, text="现在说的话", bg=BG_SOFT, fg=ACCENT, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=14, pady=(12, 0))

        self.speaker_label = tk.Label(left, text=self.speaker or " ", bg=BG_SOFT, fg=ACCENT,
                                      anchor="w", font=("Microsoft YaHei UI", 14, "bold"))
        self.speaker_label.pack(fill="x", padx=14, pady=(6, 2))

        self.line_label = tk.Label(left, text="（等着第一句话）", bg=BG_SOFT, fg=INK, anchor="nw",
                                   justify="left", wraplength=520,
                                   font=("Microsoft YaHei UI", 22))
        self.line_label.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        tk.Label(left, text="说过的话", bg=BG_SOFT, fg=INK_FAINT, anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=14)

        self.history_box = tk.Text(left, height=8, bg=BG_SOFT, fg=INK_DIM, bd=0,
                                   font=("Microsoft YaHei UI", 10), wrap="word",
                                   highlightthickness=0, spacing1=2)
        self.history_box.pack(fill="both", expand=True, padx=14, pady=(2, 12))
        self.history_box.configure(state="disabled")
        self.history_box.tag_configure("stamp", foreground=INK_FAINT)

    def _build_room(self, parent) -> None:
        tk = self.tk
        right = tk.Frame(parent, bg=BG_CHAT)
        right.grid(row=0, column=1, sticky="nsew")

        head = tk.Frame(right, bg=BG_CHAT)
        head.pack(fill="x", padx=12, pady=(12, 2))
        tk.Label(head, text="直播间", bg=BG_CHAT, fg=ACCENT, anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        self.link_label = tk.Label(head, text="未连接", bg=BG_CHAT, fg=INK_FAINT,
                                   font=("Microsoft YaHei UI", 9))
        self.link_label.pack(side="right")

        # The room is editable right here, because the moment you want to change it is the moment
        # you are looking at this window wondering why nothing is arriving.
        row = tk.Frame(right, bg=BG_CHAT)
        row.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(row, text="房间号", bg=BG_CHAT, fg=INK_DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.room_entry = tk.Entry(row, bg=BG_SOFT, fg=INK, insertbackground=INK, bd=0,
                                   width=14, font=("Microsoft YaHei UI", 10))
        self.room_entry.pack(side="left", padx=6, ipady=3)
        self.room_entry.insert(0, self.room)
        self.room_entry.bind("<Return>", lambda _e: self._apply_room())
        tk.Button(row, text="连接", command=self._apply_room, bg=BG_SOFT, fg=INK,
                  activebackground=BG_SOFT, bd=0, padx=10, pady=2,
                  font=("Microsoft YaHei UI", 9)).pack(side="left")

        self.chat_box = tk.Text(right, bg=BG_CHAT, fg=INK, bd=0, wrap="word",
                                font=("Microsoft YaHei UI", 10), highlightthickness=0,
                                spacing1=3, state="disabled")
        self.chat_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        for kind, (_, colour) in KIND_STYLE.items():
            self.chat_box.tag_configure(f"kind_{kind}", foreground=colour)
        for ink in set(USER_INKS) | {INK}:
            self.chat_box.tag_configure(f"ink_{ink}", foreground=ink)
        self.chat_box.tag_configure("body", foreground=INK)

    # ------------------------------------------------------- streamer's lines

    def _install_crash_logging(self, path: Path) -> None:
        """A pythonw app has no console, so without this a crash is a window that vanishes.

        That is the worst possible failure for a stream overlay: no error, no message, just silence,
        and nothing to read afterwards. Every unhandled exception - in a Tk callback or on the main
        thread - is written to the same log the rest of the app uses.
        """

        def write(kind: str, exc: BaseException, tb) -> None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n{time.strftime('%H:%M:%S')} [窗口] {kind} "
                                 f"{type(exc).__name__}: {exc}\n")
                    traceback.print_exception(type(exc), exc, tb, file=handle)
            except Exception:
                pass

        def hook(exc_type, exc, tb):
            write("崩溃", exc, tb)

        sys.excepthook = hook
        self.root.report_callback_exception = lambda exc_type, exc, tb: write("界面回调崩溃", exc, tb)

    def _start_tailer(self) -> None:
        threading.Thread(target=self._tail_loop, name="voice-window-tail", daemon=True).start()

    def _tail_loop(self) -> None:
        """Follow the commentary log. Start at the end: old lines are not this window's business."""
        offset = self.log_path.stat().st_size if self.log_path.exists() else 0

        while not self._stop.is_set():
            try:
                if not self.log_path.exists():
                    self._stop.wait(1.0)
                    continue

                size = self.log_path.stat().st_size
                if size < offset:
                    offset = 0

                if size == offset:
                    self._stop.wait(0.4)
                    continue

                with self.log_path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()

                # Read bytes, never text. The log is written with CRLF on Windows, and a text
                # wrapper hides the \r: counting the bytes of the decoded text drifts one byte per
                # line behind the real file. Small drifts are harmless, but a drift that grows long
                # enough re-reads a whole line - and the window says that line twice.
                last_newline = chunk.rfind(b"\n")
                if last_newline < 0:
                    self._stop.wait(0.3)
                    continue

                whole = chunk[: last_newline + 1]
                offset += len(whole)

                for raw in whole.decode("utf-8", errors="replace").splitlines():
                    self._handle_log_line(raw)

            except OSError:
                self._stop.wait(1.0)
            except Exception as exc:
                self.log(f"[窗口] 读日志出错：{type(exc).__name__}: {exc}")
                self._stop.wait(1.5)

    def _handle_log_line(self, raw: str) -> None:
        raw = raw.strip()
        if not raw.startswith("{"):
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return

        say = str(event.get("say") or "").strip()
        if say:
            self.receive(say, self.speaker, _gap_s(event))

    # --------------------------------------------------------------- the room

    def _start_chat(self) -> None:
        """Show the room by reading the copy the commentary daemon hears.

        This window used to open its own danmaku connection. Two connections meant two rooms and two
        moments of truth: the overlay could be showing a room the commentary was not answering, and
        changing rooms in one place left the other behind. The daemon is now the only listener; this
        reads its copy, so they cannot disagree about which room they are in.
        """
        if self.chat_disabled:
            self.link = "已关闭"
            return

        self.bus = EventBus(log=self.log)
        self.bus.subscribe(self._on_event, name="window-chat")

        self.chat_source = JsonlTail(ROOT / "logs" / "agents" / "chat.jsonl", self.bus,
                                     log=self._chat_log, name="window-chat")
        self.chat_source.start()

        self.link = "跟随解说脑"
        self._note("弹幕来自解说脑 —— 全系统只有一条 B站 连接")

    def _apply_room(self) -> None:
        """Take the room from the box: save it, and let the commentary daemon follow.

        Nothing is reconnected here. The connection belongs to the daemon, which watches this key, so
        the switch happens there in about a second - the window and the game are not disturbed.
        """
        try:
            self._apply_room_unsafe()
        except Exception as exc:
            # A button that kills the window is worse than a button that fails.
            self.log(f"[窗口] 切换房间失败：{type(exc).__name__}: {exc}")
            self._note(f"切换失败：{type(exc).__name__}: {exc}")

    def _apply_room_unsafe(self) -> None:
        value = self.room_entry.get().strip()
        if not value.isdigit():
            self._note("房间号只能是数字")
            return

        if value == self.room:
            self._note(f"已经在直播间 {value}")
            return

        note = ""
        try:
            save_room(value)
            note = "已写入 config/program.json"
        except Exception as exc:
            note = f"写配置失败：{type(exc).__name__}: {exc}"

        self.room = value
        self.link = "跟随解说脑"
        self._note(f"切到直播间 {value}（{note}）")
        self._note("解说脑会在几秒内跟上，不用重启任何东西")

    # There is deliberately no _restart_chat here any more. The room is no longer this window's to
    # connect to: it reads the commentary daemon's copy, and the daemon follows the config key on its
    # own. A "reconnect" button here would only restart a file tailer and pretend to be one.

    def _chat_log(self, message: str) -> None:
        """The danmaku client's log, filtered.

        A busy room produces dozens of messages a second. Writing every one of them to the log file
        buries the two lines that matter - "connected" and "connection lost" - under thousands of
        danmaku, which is exactly backwards: the window shows the chat, the log is for problems.
        """
        if any(word in message for word in ("连接", "鉴权", "中断", "失败", "错误", "拒绝")):
            self.log(message)

        if "已连接" in message or "鉴权成功" in message:
            self.link = "已连接"
        elif "连接中断" in message:
            self.link = "重连中…"

    def _on_event(self, event: dict) -> None:
        if str(event.get("name") or "") != "chat":
            return
        self.chat_bus += 1
        self.root.after(0, lambda: self._show_chat(event))

    def _show_chat(self, event: dict) -> None:
        kind = str(event.get("chat_kind") or "danmaku")
        user = str(event.get("user") or "").strip()
        text = str(event.get("text") or "").strip()

        self.chat_seen += 1
        self.chat_at = time.monotonic()

        box = self.chat_box
        box.configure(state="normal")

        tag, _ = KIND_STYLE.get(kind, KIND_STYLE["danmaku"])
        box.insert("end", f"[{tag}] ", f"kind_{kind}")
        if user:
            box.insert("end", user, f"ink_{_ink_for(user)}")
            box.insert("end", "  ", "body")
        if text:
            box.insert("end", text + "\n", "body")
        else:
            box.insert("end", "\n", "body")

        # A busy room must not eat memory, and nobody scrolls back in a live overlay anyway.
        if int(box.index("end-1c").split(".")[0]) > 400:
            box.delete("1.0", "60.0")

        box.see("end")
        box.configure(state="disabled")

    # --------------------------------------------------------------- in

    def receive(self, text: str, speaker: str = "", gap_s: float | None = None) -> None:
        """Safe from any thread: the actual work is handed to the UI thread."""
        self.root.after(0, lambda: self._show_line(text, speaker, gap_s))

    def receive_chat(self, kind: str, user: str, text: str) -> None:
        self.root.after(0, lambda: self._show_chat(
            {"name": "chat", "chat_kind": kind, "user": user, "text": text}))

    def _show_line(self, text: str, speaker: str, gap_s: float | None = None) -> None:
        self.said += 1
        stamp = time.strftime("%H:%M:%S")

        self.speaker_label.configure(text=speaker or " ")
        self.line_label.configure(text=text)

        box = self.history_box
        box.configure(state="normal")
        box.insert("end", f"{stamp}  ", "stamp")
        box.insert("end", text + "\n")
        if int(box.index("end-1c").split(".")[0]) > 300:
            box.delete("1.0", "60.0")
        box.see("end")
        box.configure(state="disabled")

        if not self.muted:
            try:
                self.voice.say(text, gap_s)
            except Exception as exc:
                self.log(f"[窗口] 播放失败：{type(exc).__name__}: {exc}")

    def _note(self, text: str) -> None:
        box = self.chat_box
        box.configure(state="normal")
        box.insert("end", f"· {text}\n", "kind_enter")
        box.see("end")
        box.configure(state="disabled")

    def _say_typed(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._show_line(text, self.speaker or "试听")

    def _toggle_mute(self) -> None:
        self.muted = not self.muted
        self.mute_button.configure(text="已静音" if self.muted else "有声")

    # --------------------------------------------------------------- local API

    def _start_server(self) -> None:
        window = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                pass

            def _send(self, code: int, payload: dict) -> None:
                blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)

            def do_GET(self) -> None:
                if self.path.startswith("/health"):
                    self._send(200, {
                        "ok": True, "window": window.title, "said": window.said,
                        "muted": window.muted, "backend": window.voice.backend,
                        "vram": window.voice._last_vram, "room": window.room,
                        "link": window.link, "chat_seen": window.chat_seen,
                        "chat_bus": window.chat_bus,
                        # Is something still being said? The commentary asks before writing another
                        # line, so the talker never runs ahead of the voice.
                        "busy": bool(window.voice.busy),
                        # How many lines are prepared but not yet playing. This is the number the
                        # commentary actually gates on: it may think ahead while a line is on air,
                        # but only one line ahead, so it never builds a queue of stale sentences.
                        "pending": int(window.voice.pending),
                    })
                else:
                    self._send(404, {"ok": False, "detail": "POST /say"})

            def do_POST(self) -> None:
                if self.path.startswith("/say"):
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    except Exception as exc:
                        self._send(400, {"ok": False, "detail": f"bad body: {exc}"})
                        return

                    text = str(body.get("text") or "").strip()
                    if not text:
                        self._send(400, {"ok": False, "detail": "no text"})
                        return

                    window.receive(text, str(body.get("speaker") or window.speaker))
                    self._send(200, {"ok": True, "said": window.said})
                    return

                if self.path.startswith("/chat"):
                    # Inject a room event. Rooms are quiet exactly when you want to check how a gift
                    # looks, so the styling has to be testable without waiting for one.
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    except Exception as exc:
                        self._send(400, {"ok": False, "detail": f"bad body: {exc}"})
                        return

                    window.receive_chat(str(body.get("chat_kind") or "danmaku"),
                                        str(body.get("user") or ""), str(body.get("text") or ""))
                    self._send(200, {"ok": True, "chat_seen": window.chat_seen})
                    return

                self._send(404, {"ok": False, "detail": "POST /say | POST /chat"})

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as exc:
            # Someone already holds the port. Do NOT carry on: without the API this instance has no
            # interface, but it still connects, still renders audio, and still talks - a second mouth
            # nobody can see or query. That is strictly worse than not starting.
            self.server = None
            self.fatal = True
            self.log(f"[窗口] 端口 {self.port} 被占用：{exc}")
            return

        threading.Thread(target=self.server.serve_forever, name="voice-window-http",
                         daemon=True).start()

    # --------------------------------------------------------------- status

    def _status(self) -> str:
        device = self.voice.device if self.voice.device is not None else "系统默认"
        bits = [f"输出 {device}"]
        if self.voice._last_vram:
            bits.append(f"显存 {self.voice._last_vram}")
        if not self.chat_disabled:
            bits.append(f"弹幕 {self.link} · {self.chat_seen}")
        if self.muted:
            bits.append("已静音")
        if self.server is None:
            bits.append(f"端口 {self.port} 不可用")
        return " · ".join(bits)

    def _tick(self) -> None:
        stale = False
        if not self.chat_disabled:
            if self.chat_at:
                age = time.monotonic() - self.chat_at
                stale = age > 90
                hint = f"{age:.0f}s 前" if age < 120 else f"⚠ {age / 60:.0f} 分钟没消息"
            else:
                hint = "等待消息"
            # "已连接" on its own is a lie when the link is half-open: no error, no close, no chat.
            # Showing how long ago the last message arrived is what makes that visible.
            self.link_label.configure(
                text=f"{self.link} · 收到 {self.chat_seen} · {hint}",
                fg=WARN if stale else INK_FAINT,
            )

        warn = (self.server is None) or stale or (not self.chat_disabled
                                                  and self.link == "重连中…")
        self.state.configure(text=self._status(), fg=WARN if warn else INK_DIM)
        self.root.after(1000, self._tick)

    # --------------------------------------------------------------- lifecycle

    def _on_close(self) -> None:
        self._stop.set()
        for part in (self.chat_source, self.bus):
            try:
                if part is not None:
                    part.stop() if part is self.chat_source else part.close()
            except Exception:
                pass
        try:
            if self.server is not None:
                self.server.shutdown()
        except Exception:
            pass
        try:
            self.voice.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self) -> None:
        if self.fatal:
            self._on_close()
            return
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="解说的独立窗口（直播姬采集它；顺带显示弹幕）")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--title", default="")
    parser.add_argument("--room", default="", help="直播间号；留空读 config/program.json")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--no-audio", action="store_true", help="只看字不出声")
    parser.add_argument("--no-chat", action="store_true", help="不连弹幕")
    parser.add_argument("--no-top", action="store_true", help="不要窗口置顶")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--allow-second", action="store_true",
                        help="跳过单实例检查（调试用；两个窗口会重复出声）")
    args = parser.parse_args()

    if args.list_devices:
        for row in list_devices():
            print(row)
        return 0

    from program import load_config

    config = load_config()
    voice_cfg = config.get("voice") or {}
    window_cfg = config.get("window") or {}
    chat_cfg = config.get("chat") or {}

    # One source of truth for the room: whatever the agent is watching. The window section can
    # override it, which is only useful when you want the overlay to show a different room.
    room = (args.room
            or str(window_cfg.get("bilibili_room") or "").strip()
            or str(chat_cfg.get("bilibili_room") or "").strip())

    port = args.port or int(window_cfg.get("port") or DEFAULT_PORT)
    title = args.title or str(window_cfg.get("title") or TITLE)
    log_path = ROOT / str(window_cfg.get("log_path") or "logs/agents/commentary.jsonl")
    # Where the game's bridge lives. Configurable because it used to be hard-coded, and a subtitle
    # that stops appearing because a port moved is a silent failure with a one-word cause.
    bridge = str(window_cfg.get("bridge_url") or "http://127.0.0.1:8787").rstrip("/")

    speaker = ""
    try:
        saved = json.loads((ROOT / "config" / "ai_save.json").read_text(encoding="utf-8"))
        speaker = str(saved.get("character_name") or "")
    except Exception:
        pass

    log_file = ROOT / "logs" / "voice-window.log"

    def log(message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {message}"
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    # The moment a line starts playing, put it on screen. This window is the one making sound, so
    # the subtitle has to be sent from here - the agent's own voice is switched off and its on_air
    # would never fire. Without this the in-game subtitle simply stops appearing.
    def announce(text: str) -> None:
        try:
            import httpx
            with httpx.Client(timeout=2.0, trust_env=False) as client:
                client.post(f"{bridge}/action",
                            json={"action": "narrate", "text": text, "speaker": speaker})
        except Exception:
            pass        # a missing subtitle is a small loss; a broken window is a big one

    # Same backend as the agent, so switching to the local IndexTTS2 service switches here too.
    voice = Voice(
        cache_dir=ROOT / "logs" / "voice-cache",
        log=log,
        enabled=True,
        backend=str(voice_cfg.get("backend") or "edge"),
        tts_url=str(voice_cfg.get("tts_url") or "http://127.0.0.1:8790/tts"),
        tts_ref=str(voice_cfg.get("tts_ref") or ""),
        emo=(voice_cfg.get("emo") or None),
        duration=voice_cfg.get("duration") or voice_cfg.get("speed"),
        # Two slots, not one: the talker is allowed to prepare the next line while the current one
        # is still playing, and with a single slot that prepared line would arrive by evicting the
        # line being spoken.
        max_queue=int(voice_cfg.get("max_queue") or 2),
        stale_s=float(voice_cfg.get("stale_s") or 25.0),
        pause_floor_s=float(voice_cfg.get("pause_floor_s") or 0.8),
        voice=str(voice_cfg.get("voice") or ""),
        rate=str(voice_cfg.get("rate") or ""),
        pitch=str(voice_cfg.get("pitch") or ""),
        volume=str(voice_cfg.get("volume") or ""),
        device=args.device if args.device is not None else voice_cfg.get("device"),
        per_sentence=bool(voice_cfg.get("per_sentence", True)),
        jitter_rate_pct=float(voice_cfg.get("jitter_rate_pct") or 0.0),
        jitter_pitch_hz=float(voice_cfg.get("jitter_pitch_hz") or 0.0),
        sentence_gap_ms=int(voice_cfg.get("sentence_gap_ms") or 90),
        gap_jitter_ms=int(voice_cfg.get("gap_jitter_ms") or 40),
        fade_ms=int(voice_cfg.get("fade_ms") or 28),
        normalize=bool(voice_cfg.get("normalize", True)),
        on_air=announce,
        temperature=voice_cfg.get("temperature"),
        top_p=voice_cfg.get("top_p"),
        seed_mode=str(voice_cfg.get("seed_mode") or "fixed"),
        seed_base=int(voice_cfg.get("seed_base") or 1234),
    )
    voice.start()

    log(f"启动：title={title} port={port} room={room or '(无)'} backend={voice.backend}")

    # Refuse to be the second mouth. Checked before the window, the voice or the danmaku client is
    # created, so a double-click on the launcher exits with a message instead of quietly doubling
    # every sentence and confusing the talker's `busy` handshake.
    if not args.allow_second and another_instance_running(port):
        warn(title,
             f"已经有一个解说窗口在跑了（端口 {port} 有响应）。\n\n"
             f"本实例退出。否则会有两张嘴同时说话，而且弹幕互动会错位。\n"
             f"确实要再开一个，请加 --allow-second。",
             log=log)
        return 1

    VoiceWindow(
        voice, port=port, title=title, log_path=log_path, room=room, speaker=speaker,
        sessdata=str(chat_cfg.get("bilibili_sessdata") or ""),
        # The config key has to be read here, not just the CLI flag. It was defined in program.json and
    # then never consulted, so the window stayed pinned above everything no matter what the file
    # said - and a window you cannot push down is a window you cannot watch the game behind.
    always_on_top=(not args.no_top) and bool(window_cfg.get("always_on_top", True)),
    autoplay=not args.no_audio,
        with_chat=not args.no_chat, log=log,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
