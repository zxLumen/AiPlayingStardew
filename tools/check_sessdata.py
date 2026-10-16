"""Check whether a Bilibili SESSDATA is valid - without ever printing it.

A wrong or expired SESSDATA does not fail politely: the danmaku socket connects and is then closed
immediately, over and over, which looks exactly like a broken network. This tells you which of the
two it is in a couple of seconds.

It also checks the two encodings, because a SESSDATA pasted out of a browser is percent-encoded
(`%2C` for a comma) and it is not always obvious which form the server wants.

    .venv\\Scripts\\python.exe tools\\check_sessdata.py
    .venv\\Scripts\\python.exe tools\\check_sessdata.py --sessdata "xxxx%2Cyyyy"
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

NAV = "https://api.bilibili.com/x/web-interface/nav"


def mask(value: str) -> str:
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}…{value[-4:]} (len={len(value)})"


def probe(label: str, value: str) -> bool:
    try:
        with httpx.Client(timeout=15, trust_env=False, headers=HEADERS,
                          cookies={"SESSDATA": value}) as client:
            payload = client.get(NAV).json()
    except Exception as exc:
        print(f"  {label:14} 请求失败 {type(exc).__name__}: {str(exc)[:60]}")
        return False

    data = payload.get("data") or {}
    logged_in = bool(data.get("isLogin"))
    mark = "✅ 有效" if logged_in else "❌ 无效"
    print(f"  {label:14} {mark}  code={payload.get('code')}  "
          f"isLogin={data.get('isLogin')}  uname={data.get('uname')!r}")
    return logged_in


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessdata", default="", help="留空则读 config/program.json")
    args = parser.parse_args()

    value = args.sessdata.strip()
    if not value:
        try:
            value = str(json.loads((ROOT / "config" / "program.json")
                                   .read_text(encoding="utf-8"))["chat"]["bilibili_sessdata"]).strip()
        except Exception as exc:
            print(f"读不到 config/program.json 里的 SESSDATA: {exc}")
            return 1

    if not value:
        print("没有配 SESSDATA（匿名模式：能收弹幕，但进场/关注的名字会被打码）")
        return 0

    print(f"待查凭证：{mask(value)}")
    print()

    ok_raw = probe("原样", value)
    decoded = urllib.parse.unquote(value)
    ok_decoded = (decoded != value) and probe("URL 解码后", decoded)

    print()
    if ok_raw:
        print("结论：原样可用 —— 直接填进 config/program.json 的 chat.bilibili_sessdata")
    elif ok_decoded:
        print("结论：解码后可用 —— 请填解码后的那一串（逗号是真实字符）")
    else:
        print("结论：两种形式都无效。多半是已过期。重新登录 B站 后从浏览器 cookie 里复制")
        print("      新的 SESSDATA；过期的凭证会让弹幕连接连上就被踢掉。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
