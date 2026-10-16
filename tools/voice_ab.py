"""A/B the voice service: same line, different reference voices and emotions, one after another.

    .venv\\Scripts\\python.exe tools\\voice_ab.py                 # play every candidate
    .venv\\Scripts\\python.exe tools\\voice_ab.py --no-play       # just make the files
    .venv\\Scripts\\python.exe tools\\voice_ab.py --text "随便一句话"

Every candidate is measured for time and the card's memory is sampled between them, because "how
does it sound" and "what does it cost" are the same question here.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

LINE = ("哎唷～大家早啊，我是 Juniper。今天水壶好像是空的，我先去打个水好不好？"
        "等我回来就把萝卜全部收起来哦。")

# 8 emotion weights: [高兴, 生气, 难过, 害怕, 厌恶, 低落, 惊讶, 平静] (order per IndexTTS docs)
EMO_WARM = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
EMO_HAPPY = [0.8, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

REF_DIR = ROOT / "tts" / "voices"


def gpu_used_mb() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip().splitlines()[0].strip()
    except Exception:
        return "?"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8790")
    parser.add_argument("--text", default=LINE)
    parser.add_argument("--no-play", action="store_true")
    args = parser.parse_args()

    out_dir = ROOT / "logs" / "voice-ab"
    out_dir.mkdir(parents=True, exist_ok=True)

    refs = sorted(p.name for p in REF_DIR.glob("*.wav"))
    if not refs:
        print("没有参考音频：tts/voices/*.wav")
        return 2

    candidates: list[tuple[str, str | None, list[float] | None]] = []
    for name in refs:
        candidates.append((name, str(REF_DIR / name), None))
    # The same reference, once with a happy push, to hear what the emotion vector actually does.
    candidates.append((refs[0] + " +高兴", str(REF_DIR / refs[0]), EMO_HAPPY))

    with httpx.Client(timeout=300.0, trust_env=False) as client:
        try:
            health = client.get(args.url + "/health").json()
        except Exception as exc:
            print(f"语音服务连不上（{args.url}）：{exc}")
            return 2

        print(f"服务: {health.get('device')} | 显存 {health.get('vram')} | 加载 {health.get('load_s')}s")
        print(f"文本: {args.text[:60]}...")
        print()

        results = []
        for label, ref, emo in candidates:
            payload: dict = {"text": args.text, "ref": ref}
            if emo:
                payload["emo"] = emo

            before = gpu_used_mb()
            started = time.perf_counter()
            try:
                resp = client.post(args.url + "/tts", json=payload)
            except Exception as exc:
                print(f"  {label:<28} 请求失败 {exc}")
                continue
            elapsed = time.perf_counter() - started

            if resp.status_code != 200:
                print(f"  {label:<28} HTTP {resp.status_code} {resp.text[:100]}")
                continue

            path = out_dir / (label.replace(" ", "_").replace("+", "plus") + ".wav")
            path.write_bytes(resp.content)
            after = gpu_used_mb()

            seconds = 0.0
            try:
                import soundfile as sf
                info = sf.info(str(path))
                seconds = info.frames / info.samplerate
            except Exception:
                pass

            rtf = elapsed / seconds if seconds else 0.0
            results.append((label, elapsed, seconds, rtf, after))
            print(f"  {label:<28} {elapsed:5.2f}s 音频 {seconds:4.1f}s "
                  f"RTF {rtf:4.2f}  显存 {before}->{after}MB")

        print()
        if results:
            best = min(results, key=lambda r: r[3])
            print(f"最快: {best[0]}  RTF {best[3]:.2f}")

    if args.no_play:
        print(f"\n文件在 {out_dir}")
        return 0

    # Play them in order, with a spoken label between tracks so it is obvious which is which.
    import sounddevice as sd
    import soundfile as sf

    print(f"\n依次播放 {len(candidates)} 段（文件在 {out_dir}）：")
    for label, *_ in candidates:
        path = out_dir / (label.replace(" ", "_").replace("+", "plus") + ".wav")
        if not path.exists():
            continue
        print(f"  ▶ {label}")
        data, rate = sf.read(str(path), dtype="int16")
        sd.play(data, rate)
        sd.wait()
        time.sleep(0.5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
