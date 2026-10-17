"""The streamer's voice out loud.

Text on screen becomes audio here. Three things matter and none of them is obvious:

  * The moment. A line about "I'm walking into town" is worth nothing once the farmer is in the
    mine. So the queue is bounded and the OLDEST is dropped, and anything that has been waiting too
    long is thrown away rather than spoken. A late line is not late, it is wrong.
  * The waiting. Synthesis and playback both take real time, so this runs on its own thread and
    the playing loop never hears about it.
  * The voice. Gentle, soft, Taiwanese-accented Mandarin, a touch slow - the register people mean
    when they say a voice is 温柔. Edge TTS gives that for free; a cloned voice can be dropped in
    later behind the same interface.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import queue
import threading
import time
from pathlib import Path

# The default register: Taiwanese Mandarin, gentle female, a little slow.
#
# Note what is NOT here: a pitch shift. Raising the pitch of the whole line with a resampler is what
# makes every sentence end on the same note - the model's own varied intonation gets flattened onto
# one contour. It is the single biggest source of "this is obviously a machine". Speed is kept mild
# for the same reason: dragging a neural voice out makes its syllables smear and land alike.
DEFAULT_VOICE = "zh-TW-HsiaoChenNeural"
DEFAULT_RATE = "-6%"
DEFAULT_PITCH = "+0Hz"

# Names that mean "this device is a pipe into OBS", so the voice can be mixed separately from the
# game instead of being drowned by it.
VIRTUAL_CABLE_HINTS = ("cable", "vb-audio", "voicemeeter", "virtual", "loopback")


class Voice:
    """Synthesises and plays the streamer's lines, dropping anything that has gone stale."""

    # A requested pause longer than this is a bug or a stalled talker, not a rhythm. Bounded so a
    # wrong number cannot park the mouth in silence for a minute with nothing in the log.
    MAX_PAUSE_S = 10.0

    def __init__(
        self,
        cache_dir: Path,
        log=print,
        enabled: bool = True,
        voice: str = DEFAULT_VOICE,
        rate: str = DEFAULT_RATE,
        pitch: str = DEFAULT_PITCH,
        volume: str = "+0%",
        device=None,
        max_queue: int = 1,
        stale_s: float = 25.0,
        pause_floor_s: float = 0.0,
        autoplay: bool = True,
        on_air=None,
        per_sentence: bool = True,
        jitter_rate_pct: float = 4.0,
        jitter_pitch_hz: float = 0.0,
        sentence_gap_ms: int = 90,
        gap_jitter_ms: int = 40,
        fade_ms: int = 28,
        normalize: bool = True,
        backend: str = "edge",
        tts_url: str = "http://127.0.0.1:8790/tts",
        tts_ref: str = "",
        emo: list | None = None,
        duration: float | None = None,
        speed: float | None = None,
        tts_timeout: float = 120.0,
        temperature: float | None = None,
        top_p: float | None = None,
        seed_mode: str = "fixed",
        seed_base: int = 1234,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.log = log
        self.enabled = enabled
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.volume = volume
        self.device = device
        self.stale_s = stale_s
        # The shortest gap the player will leave between two lines that asked for a gap. The talker
        # picks the rhythm; this stops it from ever picking "immediately", which reads as a rush.
        self.pause_floor_s = pause_floor_s
        self.autoplay = autoplay
        self.per_sentence = per_sentence
        self.jitter_rate_pct = jitter_rate_pct
        self.jitter_pitch_hz = jitter_pitch_hz
        self.sentence_gap_ms = sentence_gap_ms
        self.gap_jitter_ms = gap_jitter_ms
        self.fade_ms = fade_ms
        self.normalize = normalize

        # Where the audio actually comes from. "edge" is the built-in fallback and always works;
        # "http" points at a local voice service (tools/tts_server.py) which is a different, better
        # model. The fallback matters: a stream with a plainer voice beats a stream with none.
        self.backend = (backend or "edge").strip().lower()
        self.tts_url = tts_url
        self.tts_ref = tts_ref
        self.emo = emo
        # `speed` is the old, misleading name for this: it is a length multiplier, so BIGGER MEANS
        # SLOWER. Kept as an accepted alias because other callers were written against it, and a
        # rename that breaks a caller is worse than a name that is wrong.
        self.duration = duration if duration is not None else speed
        self.tts_timeout = tts_timeout

        # How the voice service samples this line. "temperature" is the big one: the engine's default
        # (0.8) keeps every reading safe and, over a hundred lines, the same shape - which on short
        # sentences reads as a newsreader. Measured on our own short line, 1.05 with top_p 0.9 nearly
        # doubles the pitch movement (13.6 -> 20.3 semitones) and it was picked by ear.
        self.temperature = temperature
        self.top_p = top_p
        # One seed for every line makes the whole stream one performance. A seed derived from the
        # text gives each line its own draw while keeping a given line reproducible - which is also
        # what keeps the cache honest.
        self.seed_mode = (seed_mode or "fixed").strip().lower()
        self.seed_base = int(seed_base or 1234)
        self._http_ok = self.backend == "edge"
        # How long to wait before asking a service that failed whether it is back. Short enough that
        # a restart is barely noticed, long enough not to hammer a service that is genuinely down.
        self._http_retry_s = 20.0
        self._retry_http_at = 0.0

        # Called with the text the moment it actually starts playing. This is how the subtitle on
        # screen is kept equal to the voice in the air: the line is announced by the player, not by
        # the generator, so a viewer never reads ahead of what they are hearing.
        self.on_air = on_air

        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._ready: queue.Queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._spoken = 0
        self._dropped = 0
        self._fell_back = False
        self._last_vram = ""
        self._speaking = False
        self._rendering = False
        # How long the last clip actually took to come out of the speakers. A line that takes about
        # twice its own length is being played twice, however healthy the rest of the logs look.
        self._last_play_s = 0.0
        self._fallback_plays = 0
        # When the last line actually stopped being audible. Pauses are measured from here rather
        # than from when the next line was decided, so a slow model eats into the silence instead of
        # adding to it - which is the whole difference between a streamer and a queue of jingles.
        self._last_end = 0.0

    # -- lifecycle ---------------------------------------------------------

    def start(self, bus=None) -> None:
        if not self.enabled:
            self.log("[配音] 已关闭（program.json: voice.enabled=false）")
            return

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if self.backend == "http":
            self._http_ok = self._probe_http()
            if self._http_ok:
                self.log(f"[配音] 后端=http {self.tts_url}（IndexTTS2 那类本地语音服务）")
            else:
                self.log(f"[配音] 后端=http 但 {self.tts_url} 连不上 —— 自动退回 edge-tts")

        if bus is not None:
            bus.subscribe(self._on_event, name="voice")

        # Rendering and playing run in parallel; see the note on _synth_loop.
        self._threads = [
            threading.Thread(target=self._synth_loop, name="voice-synth", daemon=True),
            threading.Thread(target=self._play_loop, name="voice-play", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

        where = self.device if self.device is not None else "系统默认"
        if self._http_ok and self.backend == "http":
            self.log(f"[配音] {self.tts_ref or '服务默认音色'} -> 输出设备 {where}")
        else:
            self.log(f"[配音] {self.voice} rate={self.rate} pitch={self.pitch} -> 输出设备 {where}")

    def _probe_http(self, quiet: bool = False) -> bool:
        """Ask the voice service whether it is up.

        `quiet` is for the periodic retry: a service that is down for a while should not fill the
        window's log with the same sentence every twenty seconds.
        """
        try:
            import httpx
            url = self.tts_url.rsplit("/", 1)[0] + "/health"
            with httpx.Client(timeout=8.0, trust_env=False) as client:
                data = client.get(url).json()
            if data.get("ok"):
                self.log(f"[配音] 语音服务就绪：{data.get('device')} | 显存 {data.get('vram')}")
                return True
            if not quiet:
                self.log(f"[配音] 语音服务在跑但模型没加载好：{data}")
        except Exception as exc:
            if not quiet:
                self.log(f"[配音] 语音服务探测失败：{type(exc).__name__}: {str(exc)[:80]}")
        return False

    def stop(self) -> None:
        self._stop.set()

    # -- in -----------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        # Anything the commentary agent said out loud is meant to be heard.
        if str(event.get("name") or "") != "commentary":
            return

        say = str(event.get("say") or "").strip()
        if say:
            self.say(say, _as_float(event.get("gap_s")))

    def say(self, text: str, gap_s: float | None = None) -> None:
        """Queue a line. Never blocks; if the queue is full the oldest line is sacrificed.

        `gap_s` is how long to wait after the *previous* line has finished before starting this one.
        It is a request for rhythm, and the player is what keeps it (see _play_loop): measuring the
        pause at the point of playback is what makes the beat survive a slow model or a slow
        synthesiser, since both of those hide inside the previous line's audio instead of showing up
        as dead air. None means "do not wait for a rhythm - say it now".
        """
        if not self.enabled or not text.strip():
            return

        item = (time.monotonic(), text.strip(), gap_s)

        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._dropped += 1
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self._dropped += 1

    # -- the workers --------------------------------------------------------
    #
    # Two threads, not one. Synthesising a line takes about as long as playing it, so a single
    # thread that renders and then plays can only ever be idle during playback and playing during
    # rendering - it doubles the wait for the listener. Split in two, the next line is rendered
    # while the current one is in the air, and the delay is paid once at the start instead of on
    # every sentence.

    def _synth_loop(self) -> None:
        loop = asyncio.new_event_loop()

        while not self._stop.is_set():
            try:
                queued_at, text, gap_s = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if time.monotonic() - queued_at > self.stale_s:
                self._dropped += 1
                continue

            self._rendering = True
            rendered_at = time.monotonic()
            try:
                data, samplerate = self._render_once(loop, text)
            except Exception as exc:
                self.log(f"[配音] 合成失败：{str(exc)[:110]}")
                continue
            finally:
                self._rendering = False
            render_s = time.monotonic() - rendered_at

            if data is None or len(data) == 0:
                continue

            # One line of look-ahead. Deeper would mean speaking about a moment that has passed.
            while not self._stop.is_set():
                try:
                    self._ready.put((queued_at, text, data, samplerate, gap_s, render_s), timeout=0.5)
                    break
                except queue.Full:
                    if time.monotonic() - queued_at > self.stale_s:
                        self._dropped += 1
                        break

        loop.close()

    def _play_loop(self) -> None:
        while not self._stop.is_set():
            try:
                queued_at, text, data, samplerate, gap_s, render_s = self._ready.get(timeout=0.5)
            except queue.Empty:
                continue

            if time.monotonic() - queued_at > self.stale_s:
                self._dropped += 1
                continue

            # The rhythm, kept where it can actually be kept. The talker asks for a pause between
            # lines; the pause is timed from the end of the previous line's AUDIO, so a model that
            # took four seconds to think, or a synthesiser that took six, costs the audience nothing
            # - those seconds were spent while the last line was still playing. This is the whole
            # reason the talker is allowed to run one line ahead.
            if gap_s is not None and self._last_end:
                pause = min(max(float(gap_s), self.pause_floor_s), self.MAX_PAUSE_S)
                wait = self._last_end + pause - time.monotonic()
                if wait > 0:
                    self._stop.wait(wait)

            if self._stop.is_set():
                break

            # Measured, not requested: the difference between the two is the whole point of putting
            # the beat here, and it is the number worth reading when the stream sounds wrong.
            actual_pause = max(0.0, time.monotonic() - self._last_end) if self._last_end else 0.0

            self._spoken += 1
            self._speaking = True

            # Announce it at the moment it is heard, not when it was decided.
            if self.on_air is not None:
                try:
                    self.on_air(text)
                except Exception:
                    pass

            if not self.autoplay:
                self.log(f"[配音] 已生成（未播放）：{text[:40]}")
                self._speaking = False
                continue

            waited = time.monotonic() - queued_at
            try:
                self._play_data(data, samplerate)
                seconds = len(data) / float(samplerate or 1)
                played = self._last_play_s
                marker = "  ⚠实播%.1fs(应%.1fs)" % (played, seconds) if played > seconds * 1.6 else ""
                self.log(f"[配音 {seconds:4.1f}s 实{played:4.1f}s 停{actual_pause:4.1f}s "
                         f"等{waited:4.1f}s 合成{render_s:4.1f}s]{marker} {text[:36]}")
            except Exception as exc:
                self.log(f"[配音] 播放失败：{str(exc)[:110]}")
            finally:
                self._last_end = time.monotonic()
                self._speaking = False

    @property
    def pending(self) -> int:
        """Lines that have not started playing yet: waiting to be rendered, or rendered and waiting.

        This is the number the talker gates on, and it is deliberately NOT `busy`. `busy` is true
        for the whole time a line is in the air, and a talker that waits for that can only ever
        start thinking about the next sentence after the current one has ended - which is exactly
        the silence this exists to remove. `pending` answers the question the talker actually has:
        is a next line already prepared? If not, make one now, while the current line is playing.
        """
        return self._queue.qsize() + self._ready.qsize()

    @property
    def busy(self) -> bool:
        """True while there is more to say than we are saying - used to stop the talker running ahead.

        Three states count as busy, and missing any one of them opens a hole the talker will walk
        straight through: queued (not started), rendering (taken out of the queue but not yet
        playable - it is in neither queue, which is why this flag exists), and playing.
        """
        return self._speaking or self._rendering or not self._ready.empty() or not self._queue.empty()

    def _render_once(self, loop, text: str):
        """Turn one line into (int16 samples, samplerate). No playing, no side effects."""
        # The local voice service first, if it is configured and reachable. A failure here is not
        # fatal: it falls through to the built-in voice rather than leaving the stream silent.
        if self.backend == "http":
            if not self._http_ok:
                # A restarted voice service has to be allowed back. Latching the fallback for the
                # rest of the stream means a three-second restart silently downgrades the voice for
                # hours - which is exactly what happened: the service died at 18:08 and the stream
                # kept speaking with the plainer voice until somebody restarted the window. The
                # fallback is a bridge, not a verdict.
                now = time.monotonic()
                if now >= self._retry_http_at:
                    self._retry_http_at = now + self._http_retry_s
                    if self._probe_http(quiet=True):
                        self._http_ok = True
                        self._fell_back = False
                        self.log("[配音] 语音服务回来了，切回本地音色。")

            if self._http_ok:
                rendered = self._render_http(text)
                if rendered is not None:
                    return rendered
                if not self._fell_back:
                    self._fell_back = True
                    self._http_ok = False
                    self._retry_http_at = time.monotonic() + self._http_retry_s
                    self.log(f"[配音] 语音服务暂时不可用，先用 edge-tts（每 "
                             f"{self._http_retry_s:.0f} 秒重试本地服务）")

        if self.per_sentence:
            return self._render_per_sentence(loop, text)

        path = self._synthesise(loop, text, self.rate, self.pitch)
        if path is None:
            return None, 0
        import soundfile as sf
        return sf.read(str(path), dtype="int16")

    def _speak_once(self, loop, text: str) -> None:
        """Render and play one line, in that order. Used by the preview CLI, not by the stream."""
        data, samplerate = self._render_once(loop, text)
        if data is None or len(data) == 0:
            return

        self._spoken += 1
        if not self.autoplay:
            self.log(f"[配音] 已生成（未播放）：{text[:40]}")
            return

        self._play_data(data, samplerate)
        self.log(f"[配音] {text[:48]}")

    def _render_http(self, text: str):
        """Ask the local voice service for one line. Cached by content, like the built-in voice.

        Returns (int16 array, samplerate) or None if the service cannot be reached.
        """
        import soundfile as sf

        seed = None
        if self.seed_mode == "per_line":
            seed = self.seed_base + int(
                hashlib.sha1(text.encode("utf-8")).hexdigest()[:6], 16) % 10000

        # Everything that changes the audio belongs in the key. The sampling parameters do change it,
        # so a cache hit from before they were tuned would quietly play the old, flatter reading.
        digest = hashlib.sha1(
            f"{self.tts_ref}|{self.emo}|{self.duration}|{self.temperature}|"
            f"{self.top_p}|{seed}|{text}".encode("utf-8")
        ).hexdigest()[:16]

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached = self.cache_dir / f"svc-{digest}.wav"

        if cached.exists() and cached.stat().st_size > 44:
            try:
                return sf.read(str(cached), dtype="int16")
            except Exception:
                pass

        import httpx

        payload: dict = {"text": text}
        if self.tts_ref:
            payload["ref"] = self.tts_ref
        if self.emo:
            payload["emo"] = self.emo
        if self.duration:
            payload["duration"] = self.duration
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.top_p is not None:
            payload["top_p"] = self.top_p
        if seed is not None:
            payload["seed"] = seed

        try:
            with httpx.Client(timeout=self.tts_timeout, trust_env=False) as client:
                resp = client.post(self.tts_url, json=payload)
        except Exception as exc:
            self.log(f"[配音] 语音服务请求失败：{type(exc).__name__}: {str(exc)[:90]}")
            return None

        if resp.status_code != 200:
            self.log(f"[配音] 语音服务返回 {resp.status_code}: {resp.text[:120]}")
            return None

        audio = resp.content
        if not audio:
            return None

        vram = resp.headers.get("X-Vram")
        if vram:
            self._last_vram = vram

        try:
            cached.write_bytes(audio)
        except OSError:
            pass

        try:
            return sf.read(io.BytesIO(audio), dtype="int16")
        except Exception as exc:
            self.log(f"[配音] 语音服务返回的音频解不开：{str(exc)[:90]}")
            return None

    def _render_per_sentence(self, loop, text: str):
        """Synthesise each sentence on its own, with its own small tempo variation.

        This is the fix for "every sentence ends the same". One long synthesis gives one reading of
        the whole paragraph, and any global processing then stamps the identical contour on every
        ending. Reading each sentence separately, at slightly different speeds, gives each one its
        own realisation - which is what a person does without thinking about it.

        Two things are deliberately NOT varied:
          * the pitch. Wobbling the pitch changes the TIMBRE, so the character appears to change
            mid-sentence. Tempo wobble reads as liveliness; pitch wobble reads as a fault.
          * the joins. Each sentence is level-matched and faded into the next, because two clips
            butted together with a hard cut and a silent gap is exactly what "choppy" means.
        """
        import numpy as np
        import soundfile as sf

        sentences = _split_sentences(text)
        if len(sentences) <= 1:
            path = self._synthesise(loop, text, self.rate, self.pitch)
            if path is None:
                return None, 0
            data, samplerate = sf.read(str(path), dtype="int16")
            return data, samplerate

        clips: list = []
        samplerate = 24000
        fade = 0
        rng_state = 12345

        for index, sentence in enumerate(sentences):
            rate, pitch = self._prosody(sentence, index)
            path = self._synthesise(loop, sentence, rate, pitch)
            if path is None:
                continue

            clip, samplerate = sf.read(str(path), dtype="int16")
            if len(clip) == 0:
                continue

            if self.normalize:
                clip = _level_match(clip)

            # A short, slightly uneven breath between sentences. Perfectly even gaps are what make
            # a stitched voice sound stitched.
            rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
            extra = (rng_state % (2 * max(1, self.gap_jitter_ms) + 1)) - self.gap_jitter_ms
            gap_ms = max(20, self.sentence_gap_ms + extra)
            gap = np.zeros(int(samplerate * gap_ms / 1000.0), dtype="int16")

            if clips:
                clips.append(gap)

            clips.append(clip)

        if not clips:
            return None, 0

        fade = int(samplerate * self.fade_ms / 1000.0)
        data = clips[0]
        for clip in clips[1:]:
            data = _join(data, clip, fade)

        return data, samplerate

    def _prosody(self, sentence: str, index: int) -> tuple[str, str]:
        """Base prosody plus a small, per-sentence, deterministic wobble."""
        import hashlib

        base_rate = _parse_percent(self.rate)
        base_pitch = _parse_hz(self.pitch)

        digest = hashlib.sha1(sentence.encode("utf-8")).digest()
        # Two independent-ish draws in [-1, 1], so the same sentence always renders the same way
        # (and therefore stays cached), while neighbouring sentences differ.
        wobble_a = (digest[0] / 255.0) * 2 - 1
        wobble_b = (digest[1] / 255.0) * 2 - 1

        rate = base_rate + wobble_a * self.jitter_rate_pct
        pitch = base_pitch + wobble_b * self.jitter_pitch_hz

        return f"{rate:+.0f}%", f"{pitch:+.0f}Hz"

    def _synthesise(self, loop, text: str, rate: str, pitch: str) -> Path | None:
        """Turn text into an mp3, cached by content so a repeated line costs nothing."""
        digest = hashlib.sha1(
            f"{self.voice}|{rate}|{pitch}|{self.volume}|{text}".encode("utf-8")
        ).hexdigest()[:16]

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{digest}.mp3"
        if path.exists() and path.stat().st_size > 0:
            return path

        import edge_tts

        async def run() -> None:
            communicate = edge_tts.Communicate(
                text, self.voice, rate=rate, pitch=pitch, volume=self.volume
            )
            await communicate.save(str(path))

        try:
            loop.run_until_complete(run())
        except Exception as exc:
            self.log(f"[配音] 合成失败：{str(exc)[:110]}")
            return None

        return path if path.exists() and path.stat().st_size > 0 else None

    def _play_data(self, data, samplerate: int) -> None:
        import sounddevice as sd

        if data is None or len(data) == 0:
            return

        device = self.device

        if getattr(data, "ndim", 1) == 1:
            try:
                info = sd.query_devices(device, "output") if device is not None \
                    else sd.query_devices(kind="output")
                max_out = int(info.get("max_output_channels") or 1)
            except Exception:
                max_out = 1

            if max_out >= 2:
                import numpy as np
                data = np.repeat(data[:, None], 2, axis=1)

        def abort() -> None:
            try:
                sd.stop()
            except Exception:
                pass

        # A watchdog, because `sd.wait()` has no timeout. If another application grabs the sound
        # card (a streaming tool taking it exclusively, say), that call simply never returns - and a
        # play loop parked inside it leaves `busy` true for ever, which silences the streamer
        # permanently with nothing in the log to explain it. Bounded by how long the audio could
        # possibly take, plus room to be wrong.
        duration = len(data) / float(samplerate or 1)
        watchdog = threading.Timer(duration + 5.0, abort)
        watchdog.daemon = True
        watchdog.start()

        started = time.monotonic()
        try:
            try:
                sd.play(data, samplerate, device=device)
                sd.wait()
            except Exception as exc:
                # A device that refuses the mapping still deserves a try at the default.
                #
                # This branch REPLAYS the clip, so it is only safe if nothing was audible yet. That
                # is not guaranteed: `sd.play()` can start the stream and `sd.wait()` can then fail
                # (the watchdog aborting it, or another stream taking the device), and in that case
                # this fallback says the sentence a second time - which is heard as a repeat, while
                # every log line and every subtitle shows it exactly once. Counted and reported
                # rather than left as a mystery in the audio.
                self._fallback_plays += 1
                self.log(f"[配音] 输出被中断，改用默认设备重播一次（第 {self._fallback_plays} 次）："
                         f"{type(exc).__name__}: {str(exc)[:70]}")
                sd.play(data, samplerate)
                sd.wait()
        finally:
            watchdog.cancel()
            self._last_play_s = time.monotonic() - started

    def _play(self, path: Path) -> None:
        import soundfile as sf

        data, samplerate = sf.read(str(path), dtype="int16")
        self._play_data(data, samplerate)

    # -- reporting ----------------------------------------------------------

    def stats(self) -> str:
        where = "本地语音服务" if (self.backend == "http" and self._http_ok) else "edge-tts"
        extra = f" | 显存 {self._last_vram}" if self._last_vram else ""
        return f"发声 {self._spoken} 次 / 丢弃 {self._dropped} 次（{where}）{extra}"


# ---------------------------------------------------------------------------
# Picking an output device that OBS can capture on its own
# ---------------------------------------------------------------------------

def _as_float(value) -> float | None:
    """A requested pause, or None. Anything unparseable means "no rhythm asked for"."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_percent(value: str) -> float:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0.0


def _parse_hz(value: str) -> float:
    try:
        return float(str(value).strip().rstrip("Hzhz"))
    except (TypeError, ValueError):
        return 0.0


def _level_match(clip, target_rms: float = 5200.0):
    """Bring one clip's loudness in line with the others.

    Sentences rendered at different speeds come back at slightly different levels. Left alone, the
    volume steps between sentences read as a stutter even when the words flow perfectly.
    """
    import numpy as np

    data = clip.astype(np.float32)
    rms = float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0
    if rms < 1.0:
        return clip

    gain = max(0.4, min(2.5, target_rms / rms))
    return np.clip(data * gain, -32768, 32767).astype(np.int16)


def _join(a, b, fade: int):
    """Butt two clips together with a short crossfade instead of a hard cut."""
    import numpy as np

    if fade <= 0 or len(a) < fade or len(b) < fade:
        return np.concatenate([a, b])

    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    head = b[:fade].astype(np.float32) * ramp + a[-fade:].astype(np.float32) * (1.0 - ramp)
    return np.concatenate([a[:-fade], head.astype(np.int16), b[fade:]])


def _split_sentences(text: str) -> list[str]:
    """Cut a line into speakable sentences, keeping the punctuation that shapes the ending."""
    text = " ".join(str(text or "").split())
    if not text:
        return []

    enders = "。！？!?…～~"
    parts: list[str] = []
    current = ""

    for char in text:
        current += char
        if char in enders:
            parts.append(current.strip())
            current = ""

    if current.strip():
        parts.append(current.strip())

    # Very short fragments sound worse on their own than glued to the neighbour.
    merged: list[str] = []
    for part in parts:
        if merged and len(part) < 4:
            merged[-1] += part
        else:
            merged.append(part)

    return [p for p in merged if p]


def find_virtual_cable() -> int | None:
    """Index of a virtual audio cable, if one is installed. None means 'use the system default'."""
    try:
        import sounddevice as sd
    except Exception:
        return None

    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_output_channels") or 0) <= 0:
            continue
        name = str(device.get("name") or "").lower()
        if any(hint in name for hint in VIRTUAL_CABLE_HINTS):
            return index
    return None


def list_devices() -> list[str]:
    import sounddevice as sd

    rows = []
    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_output_channels") or 0) <= 0:
            continue
        default = "  <= 默认" if index == sd.default.device[1] else ""
        rows.append(f"[{index}] {device['name']}{default}")
    return rows


if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))

    parser = argparse.ArgumentParser(description="试听直播配音")
    parser.add_argument("--list", action="store_true", help="列出音频输出设备")
    parser.add_argument("--say", default="", help="要试听的句子")
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--rate", default=DEFAULT_RATE)
    parser.add_argument("--pitch", default=DEFAULT_PITCH)
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--no-play", action="store_true", help="只生成不播放")
    parser.add_argument("--backend", default="edge", choices=["edge", "http"])
    parser.add_argument("--url", default="http://127.0.0.1:8790/tts")
    parser.add_argument("--ref", default="", help="参考音色 wav（http 后端用）")
    parser.add_argument("--emo", default="", help="8 个逗号分隔的情绪权重")
    parser.add_argument("--duration", "--speed", dest="duration", type=float, default=0.0,
                        help="时长倍率，越大越慢（--speed 是旧别名）")
    args = parser.parse_args()

    if args.list:
        for row in list_devices():
            print(row)
        cable = find_virtual_cable()
        print(f"\n虚拟声卡：{'未找到（会用系统默认）' if cable is None else f'设备 [{cable}]'}")
        raise SystemExit(0)

    text = args.say or "哈喽大家好，我是 Juniper，欢迎来到我的直播间哦～今天我们来种田吧。"
    emo = [float(x) for x in args.emo.split(",")] if args.emo else None

    voice = Voice(
        cache_dir=Path(__file__).resolve().parent.parent / "logs" / "voice-cache",
        voice=args.voice,
        rate=args.rate,
        pitch=args.pitch,
        device=args.device,
        autoplay=not args.no_play,
        backend=args.backend,
        tts_url=args.url,
        tts_ref=args.ref,
        emo=emo if emo and len(emo) == 8 else None,
        duration=args.duration or None,
    )
    voice.enabled = True
    v = voice._probe_http() if args.backend == "http" else True
    if args.backend == "http" and not v:
        print("语音服务连不上，先启动 tools/tts_server.py")
        raise SystemExit(2)

    print(f"synthesising via {args.backend} ({args.voice if args.backend == 'edge' else args.ref}) ...")
    voice._speak_once(asyncio.new_event_loop(), text)
    print(voice.stats())
