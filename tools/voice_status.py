"""One-shot health check for the TTS/voice chain, and a list of what changed.

    .venv\\Scripts\\python.exe tools\\voice_status.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str], timeout: float = 25.0) -> str:
    """Run a console tool and get its text back.

    The output is decoded leniently: this is a Chinese Windows, `netstat` and `wmic` answer in GBK,
    and a strict utf-8 decode raises instead of returning the ASCII ports we actually want.
    """
    try:
        blob = subprocess.run(args, capture_output=True, timeout=timeout).stdout
    except Exception as exc:
        return f"(failed: {exc})"
    for encoding in ("utf-8", "gbk"):
        try:
            return blob.decode(encoding)
        except UnicodeDecodeError:
            continue
    return blob.decode("utf-8", "replace")


def ports() -> dict:
    out = {}
    text = _run(["netstat", "-ano", "-p", "TCP"])
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3] == "LISTENING":
            local = parts[1]
            if local.endswith((":8790", ":8792", ":8787")):
                out[local.split(":")[-1]] = parts[-1]
    return out


def http(url: str, timeout: float = 6.0):
    import httpx
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as c:
            r = c.get(url)
        return r.status_code, r.text[:400]
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    print("=== 端口监听 ===")
    for port, pid in ports().items():
        print(f"  {port} -> PID {pid}")
    for port in ("8787", "8790", "8792"):
        if port not in ports():
            print(f"  {port} -> 没人在听")

    print()
    print("=== 语音服务 8790 ===")
    code, body = http("http://127.0.0.1:8790/health")
    print(f"  HTTP {code}  {body[:240]}")

    print()
    print("=== 解说窗口 8792 ===")
    code, body = http("http://127.0.0.1:8792/health")
    print(f"  HTTP {code}  {body[:400]}")

    print()
    print("=== 游戏桥 8787 ===")
    code, body = http("http://127.0.0.1:8787/health", 4)
    print(f"  HTTP {code}  {body[:160]}")

    print()
    print("=== config/program.json 的 voice 段 ===")
    try:
        cfg = json.loads((ROOT / "config" / "program.json").read_text(encoding="utf-8"))
        for key in ("enabled", "backend", "tts_url", "tts_ref", "duration", "speed", "device"):
            print(f"  {key:9} = {cfg['voice'].get(key)!r}")
    except Exception as exc:
        print(f"  读不到: {exc}")

    print()
    print("=== 相关的 python/pythonw 进程 ===")
    text = _run(["wmic", "process", "where",
                 "name='python.exe' or name='pythonw.exe'",
                 "get", "ProcessId,ParentProcessId,CommandLine", "/format:list"])

    current: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if current:
                cmd = current.get("CommandLine", "")
                tag = ("tts_server" if "tts_server" in cmd else
                       "voice_window" if "voice_window" in cmd else
                       "commentary_daemon" if "commentary_daemon" in cmd else
                       "agent" if "agent.py" in cmd else "other")
                print(f"  PID {current.get('ProcessId','?'):>7} 父={current.get('ParentProcessId','?'):>7}  [{tag}]")
                current = {}
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()

    print()
    print("=== voice.py 里他们那套东西还在不在 ===")
    src = (ROOT / "agent" / "voice.py").read_text(encoding="utf-8")
    for token in ("_render_http", "duration", "on_air", "def busy", "_synth_loop", "_play_loop",
                  "set_per_process_memory_fraction"):
        print(f"  {'OK ' if token in src else 'MISSING'}  {token}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
