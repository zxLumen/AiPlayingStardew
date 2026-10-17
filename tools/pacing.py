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


CHAT_LOG = ROOT / "logs" / "agents" / "chat.jsonl"


def read_chat(limit: int) -> list[dict]:
    """The room as the daemon heard it: who arrived, who spoke, who followed, who sent a gift."""
    if not CHAT_LOG.exists():
        return []

    rows: list[dict] = []
    for line in CHAT_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or not data.get("t"):
            continue
        rows.append(data)

    return rows[-limit:] if limit else rows


def _clock_of(stamp: str) -> int:
    try:
        hh, mm, ss = str(stamp).split(":")[:3]
        return int(hh) * 3600 + int(mm) * 60 + int(float(ss))
    except (TypeError, ValueError):
        return -1


def reply_gaps(chat: list[dict], said: list[dict], window_s: float = 90.0) -> list[str]:
    """Who spoke, arrived or followed and then heard nothing back.

    Three questions, and only one of them has an exact answer:

      * the greetings. Every line the talker wrote to greet somebody records whether it had to be a
        guaranteed one and whether the name had to be forced in, so this is measurable exactly.
      * the questions. "Asked something and nobody answered" is counted as a danmaku containing a
        question mark with no line addressing that person within the next window. A heuristic, but a
        fair one: it under-counts rhetorical questions and over-counts nothing much.
      * the follows. Same shape, with the name looked for in the line instead of the "to" field.

    The point is not a perfect number. It is that "the streamer ignored me" stops being something
    only the audience can see.
    """
    out: list[str] = []

    greet_lines = [row for row in said if str(row.get("beat") or "").startswith("刚进直播间的人：")
                   or "刚关注直播间的人：" in str(row.get("beat") or "")]
    if greet_lines:
        forced = sum(1 for row in greet_lines if row.get("guard"))
        musts = [row for row in greet_lines if row.get("must_greet")]
        named = 0
        for row in musts:
            names = re.findall(r"刚(?:进|关注)直播间的人：(.+)", str(row.get("beat") or ""))
            people = [n.strip() for group in names for n in group.split("、")]
            if any(p and p in str(row.get("say") or "") for p in people):
                named += 1
        out.append("  欢迎轮 %d 次（其中「人少必须欢迎」%d 次，念到名字 %d 次%s，兜底补名字 %d 次）"
                   % (len(greet_lines), len(musts), named,
                      "  ← 人少那档成功率 %d%%" % (100 * named // max(1, len(musts))) if musts else "",
                      forced))

    # Who spoke, and whether anything answered them.
    if chat and said:
        stamps = [(str(row.get("t") or ""), str(row.get("to") or ""), str(row.get("say") or ""))
                  for row in said]
        missed_ask, asked = [], 0
        missed_follow, followed = [], 0

        for event in chat[-400:]:
            kind = str(event.get("chat_kind") or "")
            user = str(event.get("user") or "").strip()
            text = str(event.get("text") or "").strip()
            at = _clock_of(event.get("t"))
            if at < 0 or not user:
                continue

            if kind == "danmaku" and any(mark in text for mark in "?？"):
                asked += 1
                if not any(user == to or user in say
                           for stamp, to, say in stamps if 0 <= _clock_of(stamp[11:]) - at <= window_s):
                    missed_ask.append((event.get("t"), user, text))

            elif kind == "follow":
                followed += 1
                if not any(user in say or "关注" in say
                           for stamp, to, say in stamps if 0 <= _clock_of(stamp[11:]) - at <= window_s):
                    missed_follow.append((event.get("t"), user, ""))

        out.append("  弹幕提问 %d 条，其中 %d 条 %ds 内没人回应%s"
                   % (asked, len(missed_ask), int(window_s),
                      "（例：%s）" % missed_ask[-1][2][:18] if missed_ask else ""))
        out.append("  新增关注 %d 个，其中 %d 个没被提到%s"
                   % (followed, len(missed_follow),
                      "（例：%s）" % missed_follow[-1][1] if missed_follow else ""))

    return out


def _bigrams(text: str) -> set:
    clean = "".join(ch for ch in str(text or "") if ch.strip())
    return {clean[i:i + 2] for i in range(len(clean) - 1)} or ({clean} if clean else set())


def beat_alignment(said: list[dict], worst: int = 5) -> list[str]:
    """Does the line have anything to do with the event that triggered it?

    A rough instrument, and it has to be described honestly: a good line paraphrases its event
    rather than quoting it ("收了一轮作物" becomes "终于收完啦"), so a LOW overlap is not proof of a
    mismatch. What it does catch is the failure this exists for - a line that shares no vocabulary
    at all with its own trigger, which in practice means the talker wandered off to an older subject
    or to the machine's own problems. The worst few examples are printed so they can be read.
    """
    rows = []
    for row in said:
        beat = str(row.get("beat") or "")
        say = str(row.get("say") or "")
        # Idle turns have no event to match, and greetings carry a name that is not the subject.
        if not beat or beat.startswith("（") or "刚进直播间的人" in beat or "刚关注直播间的人" in beat:
            continue
        overlap = len(_bigrams(beat) & _bigrams(say)) / max(1, len(_bigrams(beat) | _bigrams(say)))
        rows.append((overlap, str(row.get("t") or "")[11:], beat, say))

    if not rows:
        return []

    overlaps = sorted(r[0] for r in rows)
    zero = sum(1 for o in overlaps if o < 0.06)

    out = ["  最不像的几句（供人眼看。台词本来就该换词重讲，所以低分**不代表**错）："]
    for overlap, when, beat, say in sorted(rows)[:worst]:
        out.append("   %.2f  %s  事件=%-22s 台词=%s"
                   % (overlap, when, beat[:22], say[:26]))
    return out


PLAN_WORDS = ("为了", "计划", "说好", "本来", "目标", "打算")
PLAN_TERMS = ("打招呼", "介绍", "种子", "浇水", "种下", "清", "收", "睡觉", "钓鱼", "矿")


def plan_engagement(said: list[dict], hours: float = 0.0) -> list[str]:
    """Does the talker actually talk about the day's plan, and how often does it re-announce it?

    Two cheap questions with very different answers expected:

      * how many lines hang what they are doing onto a goal ("为了买种子先回镇上"), counted by the
        vocabulary of intent plus the plan's own words. A low number means the commentary is
        narrating movements rather than a person with a plan - which is what it sounded like.
      * how many lines *announce* the plan. Before this was fixed the planner rewrote itself every
        ~3 minutes and every rewrite was announced, so the streamer restated the day's plan around
        twelve times an hour, each time in different words.
    """
    rows = [row for row in said if row.get("say")]
    if not rows:
        return []

    engaged = [row for row in rows
               if any(word in str(row["say"]) for word in PLAN_WORDS)
               or any(term in str(row["say"]) for term in PLAN_TERMS)]
    announced = [row for row in rows
                 if "计划有了变化" in str(row.get("beat") or "")
                 or "今天的打算定下来了" in str(row.get("beat") or "")]

    out = ["  台词里带上「计划/为了/目标」或计划里的事：%d/%d 句（%.0f%%）"
           % (len(engaged), len(rows), 100 * len(engaged) / len(rows)),
           "  重新宣布计划的次数：%d 次（计划每 3 分钟重写一次；改前每重写一次就播报一次，"
           "现在只该在真转向时发生）" % len(announced)]
    for row in engaged[-4:]:
        out.append("    %s  %s" % (str(row.get("t"))[11:], str(row["say"])[:26]))
    return out


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

    gaps = reply_gaps(read_chat(400), read_said(400))
    if gaps:
        out.append("")
        out.append("该回没回（观众的视角）：")
        out.extend(gaps)

    alignment = beat_alignment(read_said(200))
    if alignment:
        out.append("")
        out.append("台词 vs 事件（对不对得上）：")
        out.extend(alignment)

    ages = [float(row["beat_age_s"]) for row in read_said(300) if row.get("beat_age_s") is not None]
    if ages:
        ordered = sorted(ages)
        out.append("")
        out.append("我们讲的事有多旧（写这句话时，事件已经过去多久）：")
        out.append("   中位 %.1fs   90 分位 %.1fs   最长 %.1fs   >10s 的 %d/%d 句"
                   % (ordered[len(ordered) // 2], _percentile(ages, 0.9), max(ages),
                      sum(1 for a in ages if a > 10), len(ages)))
        out.append("   （再加上管线那约 7 秒，才是观众听到时事件真正过去的时长）")

    engagement = plan_engagement(read_said(300))
    if engagement:
        out.append("")
        out.append("和「今天的目标」的关系：")
        out.extend(engagement)

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
