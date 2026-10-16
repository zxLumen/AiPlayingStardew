"""Probe a Bilibili live room's danmaku, without touching the running agent.

    .venv\\Scripts\\python.exe tools\\danmaku_probe.py 25819332
    .venv\\Scripts\\python.exe tools\\danmaku_probe.py 25819332 --seconds 60

Reads only. Prints who is in the room and what they say, so a room can be checked before it is
pointed at the stream - and so a silent chat can be told apart from a broken connection.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

import httpx  # noqa: E402

from bilibili import GETCONF, HEADERS, ROOM_INIT, BilibiliDanmaku  # noqa: E402
from events import EventBus  # noqa: E402

ANCHOR = "https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room"


def describe(room_input: str) -> int | None:
    with httpx.Client(timeout=12, trust_env=False, headers=HEADERS) as client:
        try:
            init = client.get(ROOM_INIT, params={"id": room_input}).json()
        except Exception as exc:
            print("room_init failed:", exc)
            return None

    if init.get("code") != 0:
        print(f"房间打不开: code={init.get('code')} {init.get('message')}")
        return None

    data = init["data"]
    room = int(data["room_id"])
    live = data.get("live_status")

    print(f"真实房间号 : {room}")
    print(f"直播状态   : {'直播中' if live == 1 else f'未开播 (live_status={live})'}")

    with httpx.Client(timeout=12, trust_env=False, headers=HEADERS) as client:
        try:
            anchor = client.get(ANCHOR, params={"roomid": room}).json()
            if anchor.get("code") == 0:
                info = (anchor.get("data") or {}).get("info") or {}
                print(f"主播       : {info.get('uname')}")
        except Exception:
            pass

        try:
            info = client.get(GETCONF, params={"room_id": room, "platform": "pc",
                                               "player": "web"}).json()
            hosts = (info.get("data") or {}).get("host_server_list") or []
            print(f"弹幕服务器 : {len(hosts)} 个（{hosts[0]['host'] if hosts else '无'}）")
            if info.get("code") != 0:
                print(f"            getConf 返回 {info.get('code')} —— 多半是 B站风控")
        except Exception as exc:
            print("getConf failed:", exc)

    return room


def main() -> int:
    parser = argparse.ArgumentParser(description="只读探测 B站直播间弹幕")
    parser.add_argument("room", help="直播间号（短号或真实房间号）")
    parser.add_argument("--seconds", type=float, default=45.0, help="听多久")
    parser.add_argument("--raw", action="store_true", help="把非弹幕的原始报文打出来（调试字段用）")
    args = parser.parse_args()

    print(f"=== 房间 {args.room} ===")
    if describe(args.room) is None:
        return 1

    print(f"\n=== 连弹幕 {args.seconds:.0f} 秒（只读） ===")

    bus = EventBus()
    seen = {"n": 0}
    kinds: dict[str, int] = {}

    def on_event(event: dict) -> None:
        if event.get("name") != "chat":
            return
        seen["n"] += 1
        kind = str(event.get("chat_kind") or "?")
        kinds[kind] = kinds.get(kind, 0) + 1
        user = event.get("user") or "?"
        text = event.get("text") or ""
        print(f"  [{kind}] {user}: {text}"[:160], flush=True)

    bus.subscribe(on_event, name="probe")

    on_raw = None
    if args.raw:
        import json as _json
        seen_cmds: dict[str, int] = {}

        def on_raw(message: dict) -> None:
            cmd = str(message.get("cmd") or "?")
            key = cmd.split(":")[0]
            seen_cmds[key] = seen_cmds.get(key, 0) + 1
            if key in ("DANMU_MSG",):
                return
            if seen_cmds[key] > 2:
                return                       # two samples of a shape is enough to read the fields
            print(f"  RAW {cmd}: "
                  + _json.dumps(message, ensure_ascii=False)[:700], flush=True)

    source = BilibiliDanmaku(args.room, bus, on_raw=on_raw)
    source.start()

    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        time.sleep(0.5)

    source.stop()
    bus.close()

    print(f"\n--- 共 {seen['n']} 条 ---")
    if seen["n"] == 0:
        print("一条都没收到。可能：房间没人说话 / 房间未开播 / 网络到 B站 不通。")
    else:
        print("分类:", kinds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
