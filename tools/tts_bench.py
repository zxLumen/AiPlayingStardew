"""Measure the local voice service: how long a line takes, and what it costs in VRAM.

Latency is the thing that decides whether a better voice is usable on a live stream, and it cannot
be guessed from the model size. Run this before and after any change to the service.

    .venv\\Scripts\\python.exe tools\\tts_bench.py
    .venv\\Scripts\\python.exe tools\\tts_bench.py --url http://127.0.0.1:8790/tts --rounds 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LINES = [
    "谢谢你哦，我先把水壶打满，再去田里浇水好不好？",
    "哎唷，大家早啊～我是Juniper，今天要多认识几个镇上的人哦！",
    "欸，我刚刚发现一个好玩的事。",
    "今天秋天的第十九天，我打算把农场整理一下，然后进城里买种子。",
]


def health(base: str) -> dict:
    url = base.rsplit("/", 1)[0] + "/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def synth(url: str, text: str, timeout: float, ref: str = "",
          emo: list | None = None, speed: float | None = None) -> tuple[float, int]:
    payload: dict = {"text": text}
    if ref:
        payload["ref"] = ref
    if emo:
        payload["emo"] = emo
    if speed is not None:
        payload["speed"] = speed

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None
    return time.perf_counter() - started, len(audio)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8790/tts")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--ref", default="", help="参考音色 wav（留空用服务的默认）")
    parser.add_argument("--emo", default="", help="8 个逗号分隔的情绪权重")
    parser.add_argument("--speed", type=float, default=None)
    args = parser.parse_args()

    emo = None
    if args.emo:
        values = [float(x) for x in args.emo.split(",") if x.strip()]
        emo = values if len(values) == 8 else None

    info = health(args.url)
    print("=== 服务 ===")
    for key in ("device", "vram", "samplerate", "ref", "beams", "load_s"):
        if key in info:
            print(f"  {key:11} = {info[key]}")
    if info.get("error"):
        print(f"  连不上：{info['error']}")
        return 1

    print(f"\n=== 逐句合成（{args.rounds} 轮） ===")
    print(f"  {'字数':>4} {'耗时':>8} {'音频':>9}   句子")

    times: list[float] = []

    for round_index in range(args.rounds):
        if round_index:
            print("  " + "-" * 56)
        for text in LINES:
            try:
                seconds, size = synth(args.url, text, args.timeout, ref=args.ref,
                                      emo=emo, speed=args.speed)
            except Exception as exc:
                print(f"  {len(text):>4} {'FAIL':>8} {'-':>9}   {str(exc)[:120]}")
                continue
            times.append(seconds)
            print(f"  {len(text):>4} {seconds:>7.1f}s {size / 1024:>7.0f}KB   {text[:24]}")

    if times:
        print("\n=== 汇总 ===")
        print(f"  样本 {len(times)} 条 | 中位 {statistics.median(times):.1f}s | "
              f"最快 {min(times):.1f}s | 最慢 {max(times):.1f}s")
        after = health(args.url)
        if "vram" in after:
            print(f"  显存 {after['vram']}")
        print(f"  参考：解说平均 9 秒才说一句（commentary.min_gap_s），"
              f"所以合成超过 9 秒就会开始落后于字幕。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
