"""How the stream actually sounds, in numbers.

Rhythm is the one thing that cannot be settled by reading code. "It feels too slow" is a true
statement and a useless one: what is needed is the distance between the end of one sentence and the
start of the next, and how much of the last hour the streamer spent with its mouth open.

The window logs both, per line:

    [配音  4.2s 停2.1s] 种子买到手啦，今天总算没白忙哦～

      4.2s  how long the line took to say
      停     how long the silence before it lasted, measured from the end of the previous line

so this reads that file and reports the distribution, plus what the talker asked for - the two
numbers disagreeing is a signal in itself, and it is the one that says "the mouth cannot keep up
with the mind" rather than "the mind is slow".

    .venv\\Scripts\\python.exe tools\\pacing.py
    .venv\\Scripts\\python.exe tools\\pacing.py --last 60
    .venv\\Scripts\\python.exe tools\\pacing.py --watch 30        (refresh every 30s)
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOW_LOG = ROOT / "logs" / "voice-window.log"
SAID_LOG = ROOT / "logs" / "agents" / "commentary.jsonl"

# [配音 4.2s 实4.3s 停2.1s 等6.0s 合成4.4s] the line
# Older shapes are accepted too: 实/等/合成 were each added over time.
SPOKEN = re.compile(
    r"^(\d\d):(\d\d):(\d\d)\s+\[配音\s+([\d.]+)s"
    r"(?:\s+实\s*([\d.]+)s)?"
    r"\s+停\s*([\d.]+)s"
    r"(?:\s+等\s*([\d.]+)s)?"
    r"(?:\s+合成\s*([\d.]+)s)?\]\s*(.*)$")


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _clock_seconds(hh: str, mm: str, ss: str) -> int:
    return int(hh) * 3600 + int(mm) * 60 + int(ss)


def read_spoken(limit: int) -> list[dict]:
    """The lines actually heard, newest last: when, how long, and what silence came before."""
    if not WINDOW_LOG.exists():
        return []

    rows: list[dict] = []
    for line in WINDOW_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        match = SPOKEN.match(line.strip())
        if not match:
            continue
        rows.append({
            "t": _clock_seconds(match.group(1), match.group(2), match.group(3)),
            "seconds": float(match.group(4)),
            "played": float(match.group(5)) if match.group(5) else 0.0,
            "pause": float(match.group(6)),
            "waited": float(match.group(7)) if match.group(7) else 0.0,
            "render": float(match.group(8)) if match.group(8) else 0.0,
            "text": match.group(9).strip(),
        })

    return rows[-limit:] if limit else rows


def read_said(limit: int) -> list[dict]:
    """What the talker decided: the text, the pause it asked for, and how long it took to think."""
    if not SAID_LOG.exists():
        return []

    rows: list[dict] = []
    for line in SAID_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not data.get("say"):
            continue
        rows.append(data)

    return rows[-limit:] if limit else rows


def report(limit: int, show_lines: int) -> str:
    spoken = read_spoken(limit)
    said = read_said(limit * 2)
    out: list[str] = []

    out.append("=" * 68)
    out.append(f"节奏报告  {time.strftime('%H:%M:%S')}   （最近 {len(spoken)} 句已播出的台词）")
    out.append("=" * 68)

    if not spoken:
        out.append("还没有新格式的配音记录。窗口重启一次，播几句再看。")
        out.append("（老日志里只有 [配音] 没有秒数，量不出停顿。）")
        return "\n".join(out)

    pauses = [row["pause"] for row in spoken]
    lengths = [row["seconds"] for row in spoken]
    periods = [a["t"] - b["t"] for a, b in zip(spoken[1:], spoken[:-1]) if a["t"] >= b["t"]]

    span = max(1, spoken[-1]["t"] - spoken[0]["t"])
    speaking = sum(lengths)
    duty = speaking / span if span else 0.0

    out.append("")
    out.append("停顿（上一句说完 → 这一句开口）：")
    out.append("   中位 %4.1fs   平均 %4.1fs   最短 %4.1fs   最长 %4.1fs"
               % (_percentile(pauses, 0.5), sum(pauses) / len(pauses), min(pauses), max(pauses)))
    out.append("   90 分位 %4.1fs   >5s 的 %d 句（%.0f%%）   >8s 的 %d 句（%.0f%%）"
               % (_percentile(pauses, 0.9),
                  sum(1 for p in pauses if p > 5), 100 * sum(1 for p in pauses if p > 5) / len(pauses),
                  sum(1 for p in pauses if p > 8), 100 * sum(1 for p in pauses if p > 8) / len(pauses)))

    out.append("")
    out.append("单句时长 / 句子周期（开口 → 下次开口）：")
    out.append("   句子 %4.1fs（中位）   周期 %4.1fs（中位）   期间 %d 分钟"
               % (_percentile(lengths, 0.5), _percentile([float(p) for p in periods], 0.5), span // 60))

    out.append("")
    out.append("说得多密：")
    out.append("   每分钟 %.1f 句   嘴巴张开的时间占 %.0f%%（主播通常 50~70%%）"
               % (60.0 * len(spoken) / span, 100 * duty))

    if said:
        asked = [float(row.get("gap_s") or 0.0) for row in said if row.get("gap_s") is not None]
        latencies = [float(row.get("latency_s") or 0.0) for row in said if row.get("latency_s")]
        chars = [len(str(row.get("say") or "")) for row in said]
        out.append("")
        out.append("解说脑那一侧（决定用了多久 + 它要的停顿）：")
        if chars:
            out.append("   单句 %d 字（平均）   最长 %d 字   条数 %d" % (
                sum(chars) // len(chars), max(chars), len(chars)))
        if latencies:
            out.append("   思考 %4.1fs（平均，最长 %4.1fs）" % (
                sum(latencies) / len(latencies), max(latencies)))
        if asked:
            out.append("   要求的停顿 %4.1fs（中位）  —— 和上面的实际值差得多，说明管线跟不上"
                       % _percentile(asked, 0.5))

    phases = [row for row in spoken if row.get("waited")]
    if phases:
        out.append("")
        out.append("时间都去哪儿了（每句，中位）：")
        out.append("   合成 %4.1fs   从说完到开口等 %4.1fs   开口后停顿 %4.1fs"
                   % (_percentile([r["render"] for r in phases], 0.5),
                      _percentile([r["waited"] for r in phases], 0.5),
                      _percentile([r["pause"] for r in phases], 0.5)))

    if show_lines:
        out.append("")
        out.append(f"最近 {show_lines} 句：")
        for row in spoken[-show_lines:]:
            played = f" 实{row['played']:4.1f}s" if row.get("played") else ""
            out.append("   %5.1fs%s 停%4.1fs ｜ %s"
                       % (row["seconds"], played, row["pause"], row["text"][:40]))

    doubled = [row for row in spoken if row.get("played") and row["played"] > row["seconds"] * 1.6]
    if doubled:
        out.append("")
        out.append("⚠ 播得比音频长得多（可能同一句播了两遍）：")
        for row in doubled[-5:]:
            out.append("   音频 %4.1fs 实播 %4.1fs ｜ %s"
                       % (row["seconds"], row["played"], row["text"][:36]))

    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="解说节奏报告（停顿 / 密度 / 句长）")
    parser.add_argument("--last", type=int, default=60, help="看最近多少句（默认 60）")
    parser.add_argument("--lines", type=int, default=8, help="列最后几句的明细（默认 8，0 = 不列）")
    parser.add_argument("--watch", type=float, default=0.0, help="每隔几秒刷新一次（0 = 只看一次）")
    args = parser.parse_args()

    while True:
        text = report(args.last, args.lines)
        print(text, flush=True)
        if args.watch <= 0:
            return 0
        try:
            time.sleep(args.watch)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
