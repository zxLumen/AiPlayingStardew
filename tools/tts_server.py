"""The voice service: IndexTTS2 behind a tiny HTTP API.

Run this in the TTS environment (Python 3.11 + torch), NOT in the project venv:

    tts\\.venv\\Scripts\\python.exe tools\\tts_server.py

The project's agent talks to it over localhost and never imports torch. That split exists for a
plain reason: the agent environment is Python 3.15 and PyTorch has no 3.15 wheels, and the two
jobs - deciding what to say, and saying it - have nothing to do with each other.

Two things are deliberately turned off to keep the card free for the game:

  * the Qwen emotion model. It is loaded by default on a card with >= 10 GB, and it is a whole
    extra network. Emotion is asked for as an 8-number vector instead, which costs nothing.
  * long-text batching. Text is split and synthesised piece by piece, so the peak allocation is a
    sentence rather than a paragraph.

`INDEX_TTS_VRAM_GB` caps what the process is allowed to hold. It is a hard cap, not a saving: set
it below what the model actually needs and the first request dies. Measure first, then cap.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(os.environ.get("INDEX_TTS_DIR",
                           Path(__file__).resolve().parent.parent / "tts" / "index-tts-main"))

# The project directory the service was started from. Captured before the chdir below, because a
# relative --ref has to mean "relative to where you ran me", not "relative to the model repo".
ORIGINAL_CWD = Path.cwd()

# The model writes its HuggingFace cache next to itself; make that absolute before importing, so it
# does not depend on the working directory the service happened to be started from.
os.environ.setdefault("HF_HUB_CACHE", str(REPO / "checkpoints" / "hf_cache"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")          # weights are already local; never phone home
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

sys.path.insert(0, str(REPO))

# index-tts expects to be imported from its own directory.
os.chdir(REPO)

import numpy as np  # noqa: E402
import torch  # noqa: E402

RATE_LIMIT_LOCK = threading.Lock()
INFER_LOCK = threading.Lock()          # one synthesis at a time; the model is not re-entrant

STATE: dict = {
    "model": None,
    "samplerate": 22050,
    "ref": "",
    "emo": None,
    "duration": 1.0,
    "beams": 1,
    "seed": 1234,
    "load_s": 0.0,
    "calls": 0,
    "last_s": 0.0,
    "last_gen": {},
}


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def pcm_to_wav(pcm: "np.ndarray", rate: int) -> bytes:
    """Turn an (n,) or (n, channels) int16 array into a playable wav in memory."""
    data = np.asarray(pcm)
    if data.ndim == 2:
        # index-tts returns (samples, channels); wave wants the channels interleaved as (rows, ch)
        channels = data.shape[1]
    else:
        channels = 1

    if data.dtype != np.int16:
        data = np.clip(data, -32768, 32767).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())
    return buffer.getvalue()


def vram_report() -> str:
    if not torch.cuda.is_available():
        return "cpu"
    allocated = torch.cuda.memory_allocated() / 1024 / 1024
    reserved = torch.cuda.memory_reserved() / 1024 / 1024
    total = torch.cuda.get_device_properties(0).total_memory / 1024 / 1024
    return f"{allocated:.0f}MB in use / {reserved:.0f}MB reserved / {total:.0f}MB total"


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

def load(args) -> None:
    from indextts.infer_v2_5 import IndexTTS2

    started = time.perf_counter()

    model = IndexTTS2(
        cfg_path=str(REPO / args.model_dir / "config.yaml"),
        model_dir=str(REPO / args.model_dir),
        use_bf16=not args.fp32,
        use_cuda_kernel=False,          # no nvcc on most Windows boxes; the fused kernel needs it
        use_deepspeed=False,
        use_accel=False,
        use_torch_compile=False,
        use_qwen_emo=False,             # an entire extra network we never ask for
    )

    # index-tts only auto-enables chunking below 10 GB of TOTAL vram, which looks at the card and
    # not at what is left on it. Forcing it keeps the peak allocation to one sentence.
    model.low_vram = not args.no_low_vram

    STATE["model"] = model
    STATE["load_s"] = time.perf_counter() - started
    print(f">> model loaded in {STATE['load_s']:.1f}s | vram {vram_report()}", flush=True)


def resolve_ref(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)

    for base in (ORIGINAL_CWD, REPO):
        guess = base / candidate
        if guess.exists():
            return str(guess)

    return str(REPO / candidate)


def synth(text: str, ref: str | None, emo, duration: float | None, beams: int | None,
          temperature: float | None = None, top_p: float | None = None,
          seed: int | None = None, emo_ref: str | None = None,
          emo_alpha: float | None = None) -> bytes:
    model = STATE["model"]
    if model is None:
        raise RuntimeError("model not loaded")

    reference = resolve_ref(ref or STATE["ref"])
    if not Path(reference).exists():
        raise RuntimeError(f"reference audio not found: {reference}")

    vector = emo if emo else STATE["emo"]
    factor = float(duration) if duration else STATE["duration"]
    beam_count = int(beams) if beams else STATE["beams"]

    # How the autoregressive stage is sampled, which is what decides how a line is *performed*
    # rather than what it says. The engine's defaults (temperature 0.8, top_p 0.8) are conservative:
    # they keep every reading safe and, over a hundred lines, identical in shape. Raising them is
    # the cheapest way to get some life into short sentences. The seed is per-request so that each
    # line can draw its own realisation while the same line stays reproducible (and cacheable).
    gen: dict = {"num_beams": beam_count}
    if temperature is not None:
        gen["temperature"] = float(temperature)
    if top_p is not None:
        gen["top_p"] = float(top_p)

    out = REPO / "checkpoints" / "_tts_out.wav"

    with INFER_LOCK:
        # Sampling is random, so the same line came back a different length every time - roughly
        # 2-10% either way, which is the same size as the effect of the speed knob itself. Tuning
        # inside that noise is guesswork. Pinning the seed makes a given line reproducible, which
        # is what makes a setting adjustable at all.
        torch.manual_seed(int(seed) if seed is not None else STATE["seed"])

        started = time.perf_counter()
        result = model.infer(
            spk_audio_prompt=reference,
            text=text,
            output_path=str(out),
            lang="ZH",
            # Timbre comes from spk_audio_prompt. Emotion can come from a separate clip, blended by
            # emo_alpha: that is how a line gets a smile in it without changing who is speaking. We
            # never use the 8-number emotion vector (it was tried and sounded wrong) - the vector
            # path stays available but empty.
            emo_audio_prompt=resolve_ref(emo_ref) if emo_ref else None,
            emo_alpha=float(emo_alpha) if emo_alpha is not None else 1.0,
            emo_vector=vector,
            use_random=False,
            verbose=False,
            duration_factor=factor,
            interval_silence=120,
            # The shipped default is 3 beams, which runs the autoregressive stage three times over.
            **gen,
        )
        STATE["calls"] += 1
        STATE["last_s"] = time.perf_counter() - started
        STATE["last_gen"] = dict(gen)

    # infer() normally returns the path it wrote; be tolerant about the other shapes it can return.
    if isinstance(result, tuple) and len(result) == 2:
        rate, pcm = result
        return pcm_to_wav(pcm, int(rate))

    if not out.exists():
        raise RuntimeError(f"synthesis produced nothing (infer returned {type(result).__name__})")

    return out.read_bytes()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass                                    # the agent logs its own side; keep this quiet

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                   # noqa: N802
        if self.path.startswith("/health"):
            self._json({
                "ok": STATE["model"] is not None,
                "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
                "vram": vram_report(),
                "samplerate": STATE["samplerate"],
                "ref": STATE["ref"],
                "emo": STATE["emo"],
                "duration": STATE["duration"],
                "beams": STATE["beams"],
                "seed": STATE["seed"],
                "calls": STATE["calls"],
                "last_s": round(STATE["last_s"], 2),
                "gen": STATE["last_gen"],
                "load_s": round(STATE["load_s"], 1),
            })
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:                  # noqa: N802
        if not self.path.startswith("/tts"):
            self._json({"error": "not found"}, 404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:
            self._json({"error": f"bad request: {exc}"}, 400)
            return

        text = str(payload.get("text") or "").strip()
        if not text:
            self._json({"error": "empty text"}, 400)
            return

        try:
            audio = synth(
                text,
                payload.get("ref"),
                payload.get("emo"),
                # "duration" is the real name; it multiplies the predicted lengths, so bigger is
                # SLOWER. "speed" is accepted because the first client sent it that way.
                payload.get("duration", payload.get("speed")),
                payload.get("beams"),
                payload.get("temperature"),
                payload.get("top_p"),
                payload.get("seed"),
                payload.get("emo_ref"),
                payload.get("emo_alpha"),
            )
        except Exception as exc:
            print(f"!! synthesis failed: {type(exc).__name__}: {exc}", flush=True)
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("X-Vram", vram_report())
        self.end_headers()
        self.wfile.write(audio)


def warm_up() -> None:
    """Synthesise once at startup so the first real line is not the one that pays for loading."""
    try:
        started = time.perf_counter()
        synth("你好，我是主播。", None, None, None, None)
        print(f">> warm-up {time.perf_counter() - started:.1f}s | vram {vram_report()}", flush=True)
    except Exception as exc:
        print(f"!! warm-up failed: {type(exc).__name__}: {exc}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="IndexTTS2 voice service")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--model-dir", default="checkpoints", help="checkpoints | checkpoints_2")
    parser.add_argument("--ref", default="", help="reference voice wav")
    parser.add_argument("--emo", default="", help="8 comma-separated emotion weights")
    parser.add_argument("--duration", "--speed", dest="duration", type=float, default=1.0,
                        help="length multiplier; bigger = SLOWER speech (--speed kept as an alias)")
    parser.add_argument("--beams", type=int, default=1,
                        help="autoregressive beams; the shipped default of 3 costs ~3x the wall clock")
    parser.add_argument("--seed", type=int, default=1234,
                        help="sampling seed; pinned so the same line always sounds the same")
    parser.add_argument("--fp32", action="store_true", help="disable bf16 (more vram, no benefit here)")
    parser.add_argument("--no-low-vram", action="store_true")
    parser.add_argument("--vram-gb", type=float, default=0.0,
                        help="hard cap on this process's vram; 0 = uncapped")
    parser.add_argument("--no-warmup", action="store_true")
    args = parser.parse_args()

    if args.vram_gb > 0 and torch.cuda.is_available():
        total_gb = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
        fraction = min(0.95, args.vram_gb / total_gb)
        torch.cuda.set_per_process_memory_fraction(fraction, 0)
        print(f">> vram capped at {args.vram_gb:.1f}GB of {total_gb:.1f}GB", flush=True)

    torch.set_grad_enabled(False)

    STATE["ref"] = args.ref
    STATE["duration"] = args.duration
    STATE["beams"] = max(1, int(args.beams))
    STATE["seed"] = int(args.seed)
    if args.emo:
        values = [float(x) for x in args.emo.split(",") if x.strip()]
        STATE["emo"] = values if len(values) == 8 else None

    print(f">> loading from {REPO / args.model_dir} ...", flush=True)
    load(args)

    if not args.no_warmup:
        warm_up()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f">> voice service on http://127.0.0.1:{args.port}  (ref={STATE['ref'] or 'none'})",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
