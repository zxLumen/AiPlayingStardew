"""Bilibili danmaku.

Reads a live room's messages without logging in, which is all a commentary feed needs. The protocol
is the same one every third-party client speaks: ask the room API for a danmaku token, open the
websocket, authenticate, then heartbeat. Packets arrive deflated (zlib) or brotli-compressed
depending on the version you asked for.

Everything that is not a chat message - a join, a gift, a membership, a paid message - is reported
too, because for a streamer those are exactly the moments worth reacting to.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import threading
import time
import zlib

import httpx

ROOM_INIT = "https://api.live.bilibili.com/room/v1/Room/room_init"
GETCONF = "https://api.live.bilibili.com/room/v1/Danmu/getConf"
DANMU_INFO = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://live.bilibili.com/",
    "Origin": "https://live.bilibili.com",
}

HEADER_LEN = 16
OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_MESSAGE = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8

# Bilibili closes a connection that goes quiet. Twenty seconds leaves margin under the ~30s the
# server allows, because a heartbeat that lands at 29s is a heartbeat that sometimes does not land.
HEARTBEAT_S = 20.0

# How long with no frame at all before the link is presumed dead.
#
# This exists because a half-open connection is invisible: the server is gone, no FIN ever arrives,
# `recv()` blocks forever, and everything looks healthy - "connected", no errors, no messages. On a
# busy room that silence is obvious, but on a quiet one it is indistinguishable from a quiet room,
# so the check is on frames of ANY kind (including heartbeat replies), not on chat.
#
# The library's own keepalive cannot be used for this: this server ignores protocol pings, so
# enabling it kills healthy connections instead (see the note where the socket is opened).
STALE_S = 75.0


def _brotli_decompress(data: bytes) -> bytes:
    try:
        import brotli
        return brotli.decompress(data)
    except ImportError:
        pass
    import brotlicffi
    return brotlicffi.decompress(data)


# ---------------------------------------------------------------------------
# A protobuf reader without a schema
#
# Some events (INTERACT_WORD_V2, the online-rank lists) ship their payload as a protobuf blob
# instead of JSON. The schema is not published, but the message shape is stable and simple, so it
# can be read by walking the fields. Without this, a real room produced nothing but the empty line
# ": 关注了直播间", and nobody was ever welcomed.
# ---------------------------------------------------------------------------

def _read_varint(data: bytes, index: int) -> tuple[int, int]:
    value = 0
    shift = 0
    size = len(data)

    while index < size:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break

    return value, index


def _pb_fields(data: bytes) -> list[tuple[int, int, object]]:
    """Walk one protobuf message: (field_number, wire_type, value). Stops at the first bad byte."""
    out: list[tuple[int, int, object]] = []
    index, size = 0, len(data)

    while index < size:
        key, index = _read_varint(data, index)
        field, wire = key >> 3, key & 7

        if wire == 0:
            value, index = _read_varint(data, index)
            out.append((field, wire, value))
        elif wire == 1:
            out.append((field, wire, data[index:index + 8]))
            index += 8
        elif wire == 2:
            length, index = _read_varint(data, index)
            out.append((field, wire, data[index:index + length]))
            index += length
        elif wire == 5:
            out.append((field, wire, data[index:index + 4]))
            index += 4
        else:
            break

    return out


def pb_text(data: bytes, field: int) -> str:
    for number, wire, value in _pb_fields(data):
        if number == field and wire == 2 and isinstance(value, (bytes, bytearray)):
            try:
                return bytes(value).decode("utf-8")
            except UnicodeDecodeError:
                return ""
    return ""


def pb_int(data: bytes, field: int) -> int:
    for number, wire, value in _pb_fields(data):
        if number == field and wire == 0 and isinstance(value, int):
            return value
    return 0


def looks_masked(name: str) -> bool:
    """Bilibili hides nicknames from clients that are not logged in: 'somebody' arrives as '你***'."""
    return "*" in name or "＊" in name


def _decode_json_messages(body: bytes) -> list:
    """Every JSON object in one payload, including concatenated ones.

    A batch of messages can arrive back to back with no separator at all. `json.loads` gives up on
    the whole buffer when that happens, which would silently drop every message in the frame - the
    exact shape of "the chat is mostly missing but nothing looks broken".
    """
    if not body:
        return []

    text = body.decode("utf-8", "replace").strip()
    if not text:
        return []

    try:
        payload = json.loads(text)
        return payload if isinstance(payload, list) else [payload]
    except json.JSONDecodeError:
        pass

    out: list = []
    decoder = json.JSONDecoder()
    index = 0

    while index < len(text):
        while index < len(text) and text[index] in " \r\n\t,":
            index += 1
        if index >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        out.append(obj)
        index = end

    return out


def _pack(operation: int, body: bytes = b"") -> bytes:
    return struct.pack(">IHHII", HEADER_LEN + len(body), HEADER_LEN, 1, operation, 1) + body


def _unpack(blob: bytes) -> list[tuple[int, bytes]]:
    """Split a websocket frame into (operation, payload) packets, decompressing as needed."""
    out: list[tuple[int, bytes]] = []
    offset = 0
    total = len(blob)

    while offset + HEADER_LEN <= total:
        packet_len, header_len, protover, operation, _ = struct.unpack(
            ">IHHII", blob[offset:offset + HEADER_LEN]
        )
        if packet_len < header_len or offset + packet_len > total:
            break

        body = blob[offset + header_len: offset + packet_len]

        if protover == 2:
            try:
                body = zlib.decompress(body)
            except zlib.error:
                body = b""
            out.extend(_unpack_nested(body))
        elif protover == 3:
            try:
                body = _brotli_decompress(body)
            except Exception:
                body = b""
            out.extend(_unpack_nested(body))
        else:
            out.append((operation, body))

        offset += packet_len

    return out


def _unpack_nested(blob: bytes) -> list[tuple[int, bytes]]:
    """A decompressed body is itself a full packet stream, so unpack it again."""
    return _unpack(blob)


class BilibiliDanmaku:
    """Connects to one live room and publishes what happens there.

    Never raises into the caller and reconnects on its own: a stream that dies at 3am because the
    room hiccupped is exactly the failure this project has been trying to stop repeating.
    """

    def __init__(self, room_id, bus, log=print, retry_s: float = 8.0, on_raw=None,
                 sessdata: str = "") -> None:
        self.room_id_in = str(room_id).strip()
        self.bus = bus
        self.log = log
        self.retry_s = retry_s
        self.on_raw = on_raw          # debugging hook: the undecoded message dict
        # A logged-in cookie is the only way to see real nicknames on join/follow events. Without
        # it Bilibili masks them, and the streamer would be reading out "你***".
        self.sessdata = (sessdata or "").strip()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.real_room_id: int | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_forever, name="chat-bilibili", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._session())
            except Exception as exc:
                self.log(f"[弹幕] 连接中断：{str(exc)[:120]}")

            if self._stop.is_set():
                break
            self.log(f"[弹幕] {self.retry_s:.0f} 秒后重连…")
            self._stop.wait(self.retry_s)

    # -- one connection ----------------------------------------------------

    def _uid_from_cookie(self) -> int:
        """Whose login this SESSDATA is.

        A logged-in connection has to declare its own uid in the auth packet. Sending a real cookie
        with `uid: 0` looks like a forged session, and the server does not argue about it - it just
        closes the socket right after it opens, again and again, which reads as a network fault.
        """
        if not self.sessdata:
            return 0

        try:
            with self._http() as client:
                payload = client.get("https://api.bilibili.com/x/web-interface/nav").json()
        except Exception as exc:
            self.log(f"[弹幕] 取 uid 失败，退回匿名：{type(exc).__name__}: {str(exc)[:60]}")
            return 0

        uid = int(((payload.get("data") or {}).get("mid")) or 0)
        if uid:
            self.log(f"[弹幕] 以登录身份连接（uid {uid}）")
        else:
            self.log("[弹幕] SESSDATA 没换来 uid（可能已过期），退回匿名")
        return uid

    async def _session(self) -> None:
        import websockets

        room = self._resolve_room()
        token, host, port = self._danmu_info(room)
        self.real_room_id = room
        uid = self._uid_from_cookie()

        url = f"wss://{host}:{port}/sub"
        self.log(f"[弹幕] 连接直播间 {room} -> {url}")

        async with websockets.connect(url, additional_headers=self._ws_headers(), max_size=None,
                                      # The library's own ping is ignored by this server, so it
                                      # closes a perfectly healthy connection after ~20s of silence.
                                      # Bilibili has its own heartbeat (op 2/3) and answers it.
                                      ping_interval=None, close_timeout=5) as socket:
            auth = {
                "uid": uid,
                "roomid": room,
                "protover": 3,
                "platform": "web",
                "type": 2,
                "key": token,
            }
            await socket.send(_pack(OP_AUTH, json.dumps(auth).encode("utf-8")))
            self.log("[弹幕] 已连接，开始接收")

            last_beat = time.monotonic()
            last_frame = time.monotonic()

            while not self._stop.is_set():
                try:
                    frame = await asyncio.wait_for(socket.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    frame = None

                if time.monotonic() - last_beat > HEARTBEAT_S:
                    await socket.send(_pack(OP_HEARTBEAT, b"[object Object]"))
                    last_beat = time.monotonic()

                if frame is not None:
                    last_frame = time.monotonic()
                elif time.monotonic() - last_frame > STALE_S:
                    # Nothing at all - not even a heartbeat reply - for over a minute. Assume the
                    # link is half-open and start again, rather than sitting here looking connected.
                    self.log(f"[弹幕] {STALE_S:.0f} 秒没有任何数据，判定链路已死，重连")
                    break

                if frame is None:
                    continue

                for operation, body in _unpack(frame):
                    if operation == OP_AUTH_REPLY:
                        self.log("[弹幕] 鉴权成功")
                    elif operation in (OP_MESSAGE, OP_HEARTBEAT_REPLY):
                        self._handle_messages(body)

    # -- protocol ----------------------------------------------------------

    def _http(self) -> httpx.Client:
        """A client with a browser's headers. `trust_env=False` so a system proxy cannot shadow it."""
        headers = dict(HEADERS)
        if self.sessdata:
            headers["Cookie"] = f"SESSDATA={self.sessdata}"
        return httpx.Client(timeout=15, trust_env=False, headers=headers, follow_redirects=True)

    def _ws_headers(self) -> dict:
        headers = dict(HEADERS)
        if self.sessdata:
            headers["Cookie"] = f"SESSDATA={self.sessdata}"
        return headers

    def _resolve_room(self) -> int:
        if str(self.room_id_in).isdigit() and len(str(self.room_id_in)) > 6:
            return int(self.room_id_in)        # already a real room id

        with self._http() as client:
            resp = client.get(ROOM_INIT, params={"id": self.room_id_in})
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"room_init failed: {data.get('code')} {data.get('message')}")

        return int(data["data"]["room_id"])

    def _danmu_info(self, room: int) -> tuple[str, str, int]:
        """The token and server for the danmaku socket.

        Two endpoints offer this, and only one of them answers a client that has not logged in.
        The newer one (`getDanmuInfo`) sits behind Bilibili's risk control and returns -352 even
        with a device cookie, because it wants a signed request. Reading a public room's chat is
        not worth signing for, so we ask the older `getConf`, which still answers anonymously.

        A quick visit to the site first is not superstition: an endpoint called cold, with no
        cookie jar at all, is exactly the shape of traffic that gets a -352.
        """
        with self._http() as client:
            try:
                client.get("https://www.bilibili.com/")
            except Exception:
                pass

            resp = client.get(GETCONF, params={"room_id": room, "platform": "pc", "player": "web"})
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"getConf failed: {data.get('code')} {data.get('message')} "
                f"(B站风控；换个网络或稍后再试)"
            )

        payload = data["data"]
        hosts = payload.get("host_server_list") or []
        if not hosts:
            raise RuntimeError("no danmaku host offered")

        host = hosts[0]
        return (str(payload.get("token") or ""), str(host["host"]),
                int(host.get("wss_port") or 443))

    def _handle_messages(self, body: bytes) -> None:
        for payload in _decode_json_messages(body):
            if isinstance(payload, list):
                for item in payload:
                    self._handle_one(item)
            else:
                self._handle_one(payload)

    def _handle_one(self, message: dict) -> None:
        if not isinstance(message, dict):
            return

        if self.on_raw is not None:
            try:
                self.on_raw(message)
            except Exception:
                pass

        cmd = str(message.get("cmd") or "")
        data = message.get("data") if isinstance(message.get("data"), dict) else {}

        if cmd.startswith("DANMU_MSG"):
            info = message.get("info") or []
            text = str(info[1]) if len(info) > 1 else ""
            user = ""
            if len(info) > 2 and isinstance(info[2], list) and len(info[2]) > 1:
                user = str(info[2][1])
            self._publish("danmaku", user, text)
            return

        if cmd.startswith("INTERACT_WORD_V2"):
            self._publish_v2(data)
            return

        if cmd.startswith("INTERACT_WORD") or cmd.startswith("ENTRY_EFFECT"):
            self._publish_interact(data)
            return

        if cmd.startswith("SEND_GIFT"):
            user = str(data.get("uname") or "")
            gift = str(data.get("giftName") or "礼物")
            count = data.get("num") or 1
            self._publish("gift", user, f"{gift} x{count}")
            return

        if cmd.startswith("GUARD_BUY"):
            user = str(data.get("username") or "")
            gift = str(data.get("gift_name") or "舰长")
            self._publish("guard", user, gift)
            return

        if cmd.startswith("SUPER_CHAT_MESSAGE"):
            info = data.get("user_info") or {}
            user = str(info.get("uname") or "")
            self._publish("super_chat", user, str(data.get("message") or ""))

    def _publish_v2(self, data: dict) -> None:
        """The v2 form keeps the facts in a protobuf blob rather than readable JSON.

        Field 2 is the name and field 5 is the kind. Read without a schema on purpose: the schema
        is not published, and a best-effort read that degrades to "somebody arrived" beats dropping
        the event - which is what the old parser effectively did, by looking for a `uname` key that
        is not there.
        """
        raw = str(data.get("pb") or "")
        if not raw:
            return

        try:
            blob = base64.b64decode(raw)
        except Exception:
            return

        self._announce(pb_text(blob, 2), pb_int(blob, 5))

    def _publish_interact(self, data: dict) -> None:
        user = str(data.get("uname") or data.get("username") or "")
        if not user:
            user = str((data.get("user_info") or {}).get("uname") or "")
        self._announce(user, int(data.get("msg_type") or 0))

    def _announce(self, user: str, msg_type: int) -> None:
        """A join, a follow or a share - three different things, and none of them a chat message."""
        user = (user or "").strip()

        # 1 walked in, 2 followed, 3 shared. The mapped value is a guess for events where the kind
        # cannot be read; "someone arrived" is the harmless default.
        kind = {1: "enter", 2: "follow", 3: "share"}.get(msg_type, "enter")
        label = {"enter": "", "follow": "关注了直播间", "share": "分享了直播间"}.get(kind, "")

        if user and looks_masked(user):
            # Logged out, Bilibili answers with '你***'. Passing that through would have the
            # streamer read a redaction out loud as if it were somebody's name.
            self._publish(kind, "", label)
            return

        self._publish(kind, user, label)

    def _publish(self, kind: str, user: str, text: str) -> None:
        if kind == "danmaku":
            self.log(f"[弹幕] {user}: {text}")
        elif user:
            self.log(f"[弹幕] {user} {kind} {text}".rstrip())
        else:
            self.log(f"[弹幕] （匿名）{kind}")
        self.bus.publish({"name": "chat", "chat_kind": kind, "user": user, "text": text})


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from events import EventBus

    parser = argparse.ArgumentParser(description="只读测试 B站直播间弹幕")
    parser.add_argument("room", help="直播间号（短号或真实房间号都行）")
    args = parser.parse_args()

    bus = EventBus()
    bus.subscribe(lambda e: print("bus:", e), name="echo")

    source = BilibiliDanmaku(args.room, bus)
    source.start()
    print("按 Ctrl+C 结束")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        source.stop()
