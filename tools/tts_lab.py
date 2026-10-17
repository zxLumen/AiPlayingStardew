"""Listening lab for the voice.

"Lacks a bit of human" is a true complaint and an unhelpful instruction. What it decomposes into
here is measurable, at least in part:

  * how far the pitch travels within one line (a flat reading barely moves; a person swings),
  * how fast it is spoken, and how many breaths are inside it,
  * how clean the reference clip is (a noisy reference gives a muddy timbre, and no amount of
    tuning downstream fixes that).

So this synthesises the same short line several ways, prints those numbers next to each take, and
plays them in order. The ear decides; the numbers only stop the ear being fooled by loudness or by
one lucky take.

    .venv\\Scripts\\python.exe tools\\tts_lab.py --round timbre
    .venv\\Scripts\\python.exe tools\\tts_lab.py --round human --ref tts/voices/demo/voice_05.wav
    .venv\\Scripts\\python.exe tools\\tts_lab.py --round seed  --ref tts/voices/demo/voice_05.wav
    ... --no-play        (only write the files and the table)

Round 1 answers "whose voice". Round 2 answers "how alive is that voice". Round 3 answers "does
every line come out the same shape" - the machine tell that survives a good voice.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "logs" / "tts-lab"
TTS_URL = "http://127.0.0.1:8790/tts"
REF = "tts/voices/demo/voice_09.wav"

# Short on purpose: the real lines are 13-17 characters, and prosody is exactly what a short line
# has least of. Tuning on longer sentences would flatter the voice and lie about the stream.
LINE = "欸，种子买好啦，回家种去咯～"
LINE_B = "这个门我点四次了还没开。"


# ---------------------------------------------------------------------------
# Measuring what the ear is about to judge
# ---------------------------------------------------------------------------

def _mono(data: np.ndarray) -> np.ndarray:
    return data.mean(axis=1) if data.ndim > 1 else data


def _envelope(data: np.ndarray, rate: int, win_ms: float = 30.0) -> np.ndarray:
    win = max(1, int(rate * win_ms / 1000))
    return np.sqrt(np.convolve(data ** 2, np.ones(win) / win, mode="same"))


def _trim(data: np.ndarray, rate: int):
    """The audible span, so trailing silence does not slow the speech rate down."""
    env = _envelope(data, rate)
    if env.max() <= 0:
        return data, 0.0
    voiced = np.where(env > env.max() * 0.06)[0]
    if len(voiced) == 0:
        return data, 0.0
    start, end = int(voiced[0]), int(voiced[-1])
    return data[start:end], (end - start) / rate


def f0_track(data: np.ndarray, rate: int) -> list[float]:
    """Crude autocorrelation pitch, good enough to compare one take against another."""
    frame = int(0.04 * rate)
    hop = int(0.02 * rate)
    low_lag, high_lag = int(rate / 400), int(rate / 70)
    values: list[float] = []

    for start in range(0, max(1, len(data) - frame), hop):
        chunk = data[start:start + frame].astype(np.float64)
        if np.sqrt(np.mean(chunk ** 2)) < 0.01:
            continue
        chunk = chunk - chunk.mean()
        corr = np.correlate(chunk, chunk, mode="full")[frame - 1:]
        if high_lag >= len(corr) or low_lag >= high_lag:
            continue
        window = corr[low_lag:high_lag]
        if window.size == 0:
            continue
        lag = int(np.argmax(window)) + low_lag
        if lag <= 0:
            continue
        f0 = rate / lag
        if 70.0 <= f0 <= 400.0:
            values.append(f0)

    return values


def measure(audio: np.ndarray, rate: int, text: str) -> dict:
    mono = _mono(audio.astype(np.float32)) / 32768.0
    trimmed, seconds = _trim(mono, rate)
    f0 = f0_track(trimmed, rate)

    if len(f0) >= 6:
        lo, hi = np.percentile(f0, 5), np.percentile(f0, 95)
        semitones = 12.0 * math.log2(hi / lo) if lo > 0 else 0.0
    else:
        semitones = 0.0

    # Pauses inside the line: runs of quiet longer than 120ms within the audible span.
    env = _envelope(trimmed, rate)
    quiet = env < (env.max() * 0.06 if env.size and env.max() > 0 else 0)
    pauses, run = [], 0
    for flag in quiet:
        if flag:
            run += 1
            continue
        if run >= int(rate * 0.12):
            pauses.append(run / rate)
        run = 0

    return {
        "seconds": seconds,
        "rate": len(text) / seconds if seconds > 0 else 0.0,
        "semitones": semitones,
        "pauses": len(pauses),
    }


def reference_quality(path: Path) -> dict:
    """Length and how much noise the reference carries. A dirty reference = a muddy voice."""
    data, rate = sf.read(str(path), dtype="float32")
    mono = _mono(data)
    env = _envelope(mono, rate)
    if env.size == 0 or env.max() <= 0:
        return {"seconds": 0.0, "snr": 0.0}

    speech = float(np.percentile(env, 90))
    noise = float(np.percentile(env, 5)) or 1e-9
    return {"seconds": len(mono) / rate, "snr": 20.0 * math.log10(max(speech, 1e-9) / noise)}


# ---------------------------------------------------------------------------
# Synthesis + playback
# ---------------------------------------------------------------------------

def synth(text: str, ref: str, *, temperature=None, top_p=None, seed=None,
          emo_ref=None, emo_alpha=None, duration=None) -> tuple[np.ndarray, int]:
    import io

    import httpx

    payload: dict = {"text": text, "ref": ref, "duration": duration if duration is not None else 1.0,
                     "beams": 1}
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    if seed is not None:
        payload["seed"] = seed
    if emo_ref is not None:
        payload["emo_ref"] = emo_ref
        payload["emo_alpha"] = emo_alpha if emo_alpha is not None else 1.0

    with httpx.Client(timeout=300.0, trust_env=False) as client:
        resp = client.post(TTS_URL, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"TTS {resp.status_code}: {resp.text[:200]}")

    return sf.read(io.BytesIO(resp.content), dtype="int16")


def play(data: np.ndarray, rate: int) -> None:
    import sounddevice as sd

    sd.play(data, rate)
    sd.wait()


def text_seed(text: str, base: int = 1234) -> int:
    """A seed that differs per line but is stable for one line - variety without losing the cache."""
    return base + int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:6], 16) % 10000


# ---------------------------------------------------------------------------
# The rounds
# ---------------------------------------------------------------------------

def round_timbre(args) -> None:
    refs = ["tts/voices/demo/voice_09.wav", "tts/voices/demo/voice_05.wav",
            "tts/voices/demo/voice_06.wav", "tts/voices/demo/voice_11.wav",
            "tts/voices/demo/voice_12.wav"]

    print()
    print("=" * 78)
    print("第一轮 · 音色：同一句『%s』，5 个参考音色" % LINE)
    print("=" * 78)
    print("  %-2s %-24s %7s %8s %8s %6s  参考" % ("#", "音色", "时长", "语速", "音高起伏", "停顿"))
    print("  " + "-" * 74)
    takes = []
    for index, ref in enumerate(refs, 1):
        path = ROOT / ref
        quality = reference_quality(path)
        data, rate = synth(LINE, ref)
        stats = measure(data, rate, LINE)
        takes.append((index, path.stem, data, rate, stats))
        print("  %-2d %-24s %6.1fs %6.1f字/s %6.1f半音 %6d  %.1fs/%.0fdB"
              % (index, path.stem, stats["seconds"], stats["rate"], stats["semitones"],
                 stats["pauses"], quality["seconds"], quality["snr"]))
        sf.write(str(OUT_DIR / ("timbre-%02d-%s.wav" % (index, path.stem))), data, rate)

    play_all(takes, args)


def round_human(args) -> None:
    ref = args.ref
    variants = [
        ("1 基线（温度0.8）", dict(temperature=0.8)),
        ("2 温度0.95", dict(temperature=0.95)),
        ("3 温度1.05+top_p0.9", dict(temperature=1.05, top_p=0.9)),
        ("4 温度0.95+情绪克隆0.35", dict(temperature=0.95,
                                        emo_ref="tts/voices/demo/voice_05.wav", emo_alpha=0.35)),
        ("5 温度0.95+语速慢6%", dict(temperature=0.95, duration=1.06)),
    ]

    print()
    print("=" * 78)
    print("第二轮 · 活人参数：音色 %s，同一句『%s』" % (Path(ref).stem, LINE))
    print("=" * 78)
    print("  %-2s %-26s %8s %8s %8s %6s" % ("#", "变体", "时长", "语速", "音高起伏", "停顿"))
    print("  " + "-" * 74)

    takes = []
    for index, (label, kwargs) in enumerate(variants, 1):
        data, rate = synth(LINE, ref, **kwargs)
        stats = measure(data, rate, LINE)
        takes.append((index, label, data, rate, stats))
        print("  %-2d %-26s %6.1fs %6.1f字/s %6.1f半音 %6d"
              % (index, label, stats["seconds"], stats["rate"], stats["semitones"], stats["pauses"]))
        sf.write(str(OUT_DIR / ("human-%02d.wav" % index)), data, rate)

    play_all(takes, args)


def round_seed(args) -> None:
    """Two lines, twice: fixed seed versus one seed per line. Same voice, same settings."""
    ref = args.ref
    print()
    print("=" * 78)
    print("第三轮 · 每句是不是一个味：固定种子 vs 按文本派生种子")
    print("=" * 78)
    print("  先听 A 组（固定种子 1234 连读两句），再听 B 组（每句自己的种子）")
    print("  问自己：哪一组更像『同一个人在两个时刻说话』？")
    print()

    takes = []
    index = 1
    for group, seeder in (("A 固定种子", lambda t: 1234), ("B 每句种子", text_seed)):
        for text in (LINE, LINE_B):
            data, rate = synth(text, ref, temperature=args.temperature, seed=seeder(text))
            stats = measure(data, rate, text)
            takes.append((index, "%s『%s』" % (group, text[:8]), data, rate, stats))
            sf.write(str(OUT_DIR / ("seed-%02d.wav" % index)), data, rate)
            print("  %-2d %-30s %6.1fs %6.1f半音" % (index, takes[-1][1], stats["seconds"],
                                                    stats["semitones"]))
            index += 1

    play_all(takes, args)


def play_all(takes, args) -> None:
    if args.no_play:
        print("\n  （--no-play：只写文件，没播。文件在 logs/tts-lab/）")
        return

    print("\n  3 秒后开始播放，每个之间停 1.5 秒 —— 听的时候记住编号。")
    time.sleep(3)
    for index, label, data, rate, stats in takes:
        print("  >> %d 号：%s" % (index, label))
        play(data, rate)
        time.sleep(1.5)
    print("  播完了。")


def main() -> int:
    parser = argparse.ArgumentParser(description="语音试听台：音色 / 活人参数 / 每句变化")
    parser.add_argument("--round", default="timbre", choices=("timbre", "human", "seed"))
    parser.add_argument("--ref", default=REF, help="音色参考（第二轮/第三轮用）")
    parser.add_argument("--temperature", type=float, default=0.95, help="第三轮用的温度")
    parser.add_argument("--no-play", action="store_true", help="只生成不播放")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        import httpx
        with httpx.Client(timeout=8.0, trust_env=False) as client:
            health = client.get(TTS_URL.rsplit("/", 1)[0] + "/health").json()
        if not health.get("ok"):
            print("语音服务在跑但模型没加载好：", health)
            return 2
        print("语音服务：%s | %s" % (health.get("device"), health.get("vram")))
    except Exception as exc:
        print("语音服务连不上（%s）。先启动 start-tts.bat。" % type(exc).__name__)
        return 2

    if args.round == "timbre":
        round_timbre(args)
    elif args.round == "human":
        round_human(args)
    else:
        round_seed(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
