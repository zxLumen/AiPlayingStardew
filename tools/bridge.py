"""The bridge's own hand: read it, patch it, build it, deploy it - or put it back.

The agent plays through a bridge (the C# mod) that turns the game into a structured API. When the
agent keeps walking into the same wall and the wall turns out to be the bridge - a handle that names
two controls, a click that reports OK while landing somewhere else - the honest repair is to change
the bridge, not to write a rule around it.

This tool is that repair, with the invariants kept outside the thing being repaired:

  * a patch is applied to the working tree and then COMPILED. If it does not compile, it is rolled
    back and the compiler's words are handed back. A compile error costs nothing.
  * a patch is DEPLOYED only while the game is stopped, and only with a probe. If the probe does not
    pass, the previous DLL is restored and the game is brought back up on it. The stack always comes
    back.
  * the referee files cannot be patched at all. A repair that can edit its own judge is not a repair.
  * nobody is trusted: every patch and every probe result is appended to logs/bridge-patches.jsonl.

Usage (from the repo root):
    python tools/bridge.py status
    python tools/bridge.py read mod/Utils/UiStateBuilder.cs --grep FindComponent --around 12
    python tools/bridge.py patch --file mod/Utils/UiStateBuilder.cs --find "..." --replace "..." --why "..."
    python tools/bridge.py build
    python tools/bridge.py deploy --probe-cmd "python tools/probes/menu_handles_unique.py"
    python tools/bridge.py revert
    python tools/bridge.py tickets
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOD = ROOT / "mod"
GAME_DIR = Path(r"E:\Program Files (x86)\Steam\steamapps\common\Stardew Valley")
DLL = GAME_DIR / "Mods" / "StateBridge" / "StateBridge.dll"
BACKUP = ROOT / "logs" / "StateBridge.prev.dll"
TICKETS = ROOT / "logs" / "bridge-tickets.jsonl"
PATCHES = ROOT / "logs" / "bridge-patches.jsonl"
DOTNET = Path.home() / "AppData" / "Local" / "dotnet-sdk" / "dotnet.exe"

# Files that decide what the agent is allowed to do. A repair may change the hand; it may not change
# the rules the hand is held to, because then "repaired" would just mean "the test was edited".
OFF_LIMITS = (
    "tools/check_fidelity.py",
    "agent/authenticity.py",
    "tools/bridge.py",
)

sys.path.insert(0, str(ROOT / "agent"))


def out(text: str) -> None:
    print(text, flush=True)


def ledger(kind: str, **fields) -> None:
    PATCHES.parent.mkdir(parents=True, exist_ok=True)
    row = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
    row.update(fields)
    try:
        with PATCHES.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return (result.stdout or "") + (result.stderr or "")


def dirty_files() -> list[str]:
    lines = git("status", "--porcelain").splitlines()
    return [line[3:].strip() for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# build / deploy
# ---------------------------------------------------------------------------

def build() -> tuple[bool, str]:
    if not DOTNET.exists():
        return False, f"no dotnet at {DOTNET}"
    result = subprocess.run(
        [str(DOTNET), "build", str(MOD / "StateBridge.csproj"), "-c", "Release",
         f"-p:GamePath={GAME_DIR}"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = (result.stdout or "") + (result.stderr or "")
    errors = [line.strip() for line in text.splitlines() if "error CS" in line or "fidelity:" in line]
    # The deploy step fails with an IOException while the game is running. That is not a code error:
    # the compile already succeeded by then, and a game that is up must not make a patch look broken.
    locked = "StateBridge.dll' because it is being used by another process" in text
    ok = not errors and (result.returncode == 0 or locked)
    return ok, "\n".join(errors[:12]) if errors else ("compiled (deploy locked by the running game)"
                                                      if locked else "compiled")


def game():
    import game_process
    return game_process


def state_payload(timeout: float = 6.0) -> dict | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/state", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def wait_for_state(seconds: float = 150.0) -> dict | None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        payload = state_payload(3.0)
        if payload:
            return payload
        time.sleep(4)
    return None


def probe_state(path: str) -> tuple[bool, str]:
    payload = state_payload()
    if not payload:
        return False, "the bridge did not answer /state"
    node = payload
    for part in path.split("."):
        if isinstance(node, list):
            try:
                node = node[int(part)]
                continue
            except Exception:
                return False, f"{path}: list index {part} is not there"
        if not isinstance(node, dict) or part not in node:
            return False, f"{path}: '{part}' is not in the payload"
        node = node[part]
    return True, f"{path} = {json.dumps(node, ensure_ascii=False)[:120]}"


def probe_cmd(command: str) -> tuple[bool, str]:
    result = subprocess.run(command, cwd=str(ROOT), shell=True, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=240)
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    return result.returncode == 0, text[-600:] or f"exit {result.returncode}"


def cmd_status() -> int:
    payload = state_payload()
    out(f"repo:      {'dirty: ' + ', '.join(dirty_files()[:8]) if dirty_files() else 'clean'}")
    out(f"backup:    {'present' if BACKUP.exists() else 'none'}")
    out(f"deployed:  {DLL.stat().st_mtime if DLL.exists() else 'missing'}")
    out(f"built:     {(MOD / 'bin' / 'Release').exists()}")
    out(f"game:      {'running' if game().is_running() else 'stopped'}")
    out(f"bridge:    {'answering /state' if payload else 'not answering'}"
        + (f" tick={payload.get('tick')}" if payload else ""))
    open_tickets = [t for t in read_tickets() if not t.get("closed")]
    out(f"tickets:   {len(open_tickets)} open of {len(read_tickets())}")
    return 0


def cmd_read(path: str, pattern: str | None, around: int, lines: str | None) -> int:
    target = (ROOT / path).resolve()
    if not str(target).startswith(str(ROOT)) or not target.exists():
        out(f"cannot read {path}")
        return 1

    text = target.read_text(encoding="utf-8", errors="replace").splitlines()
    if lines:
        first, _, last = lines.partition("-")
        start = max(1, int(first or 1))
        stop = int(last) if last else start + 40
        for number in range(start, min(stop, len(text)) + 1):
            out(f"{number:>5}: {text[number - 1]}")
        return 0

    if pattern:
        hits = 0
        for number, line in enumerate(text, start=1):
            if pattern.lower() in line.lower():
                hits += 1
                start = max(1, number - around // 2)
                stop = min(len(text), number + around // 2)
                for n in range(start, stop + 1):
                    out(f"{n:>5}: {text[n - 1]}")
                out("")
                if hits >= 6:
                    break
        if not hits:
            out(f"no line contains {pattern!r}")
            return 1
        return 0

    out(f"{path}: {len(text)} lines")
    return 0


# ---------------------------------------------------------------------------
# tickets
# ---------------------------------------------------------------------------

def read_tickets() -> list[dict]:
    if not TICKETS.exists():
        return []
    rows = []
    for line in TICKETS.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def cmd_ticket(symptom: str, evidence: str, source: str) -> int:
    ticket = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "symptom": symptom,
        "evidence": evidence,
        "source": source,
        "closed": False,
    }
    TICKETS.parent.mkdir(parents=True, exist_ok=True)
    with TICKETS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ticket, ensure_ascii=False) + "\n")
    out(f"ticket filed: {symptom}")
    return 0


def cmd_tickets() -> int:
    rows = [t for t in read_tickets() if not t.get("closed")]
    if not rows:
        out("no open tickets")
        return 0
    for index, row in enumerate(rows, start=1):
        out(f"{index}. [{row.get('t')}] {row.get('symptom')}")
        if row.get("evidence"):
            out(f"   evidence: {row['evidence']}")
        if row.get("source"):
            out(f"   asked by: {row['source']}")
    return 0


# ---------------------------------------------------------------------------
# patch / deploy / revert
# ---------------------------------------------------------------------------

def cmd_patch(path: str, find: str, replace: str, why: str, evidence: str, source: str) -> int:
    if any(path.endswith(banned) or banned in path for banned in OFF_LIMITS):
        out(f"refused: {path} is the referee. A repair may not edit what judges it.")
        ledger("patch_refused", file=path, why=why, reason="referee")
        return 2

    target = (ROOT / path).resolve()
    if not str(target).startswith(str(ROOT)) or not target.exists():
        out(f"refused: cannot write {path}")
        return 2

    text = target.read_text(encoding="utf-8", errors="replace")
    if find not in text:
        out(f"refused: that exact text is not in {path} - read it first, then patch it")
        ledger("patch_refused", file=path, why=why, reason="find-not-found")
        return 2
    if text.count(find) > 1:
        out(f"refused: that text appears {text.count(find)} times in {path} - make it unique")
        ledger("patch_refused", file=path, why=why, reason="not-unique")
        return 2

    dirty = [f for f in dirty_files() if f != path]
    if dirty:
        out(f"refused: the tree already has other changes ({', '.join(dirty[:5])}). "
            f"One patch at a time, or revert first.")
        return 2

    target.write_text(text.replace(find, replace, 1), encoding="utf-8")
    ok, report = build()
    ledger("patch", file=path, why=why, evidence=evidence, source=source, compiled=ok, report=report)

    if not ok:
        git("checkout", "--", path)
        out(f"did not compile, rolled back:\n{report}")
        return 1

    out(f"compiled: {report}")
    out("now deploy it when the game is at a natural break:")
    out(f"    python tools/bridge.py deploy --probe-cmd \"python tools/probes/<your test>.py\"")
    return 0


def cmd_deploy(probe_state_path: str | None, probe: str | None, keep_on_failure: bool) -> int:
    ok, report = build()
    if not ok:
        out(f"refused: does not compile:\n{report}")
        ledger("deploy_refused", report=report)
        return 1

    # Stop the game: the DLL cannot be replaced while it is loaded, and a half-deployed bridge is
    # worse than an old one. The old DLL is kept, so the way back is a copy and not a rebuild.
    was_running = game().is_running()
    if was_running:
        game().stop()
        time.sleep(3)

    if DLL.exists():
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DLL, BACKUP)

    built = next((p for p in (MOD / "bin" / "Release").glob("**/StateBridge.dll")), None)
    if built is None:
        out("refused: nothing was built")
        return 1
    shutil.copy2(built, DLL)
    ledger("deploy", probe=probe or probe_state_path or "alive", backup=str(BACKUP))

    game().ensure_running()
    alive = wait_for_state()
    verdict, detail = (False, "the bridge never answered /state")
    if alive:
        if probe:
            verdict, detail = probe_cmd(probe)
        elif probe_state_path:
            verdict, detail = probe_state(probe_state_path)
        else:
            first = alive.get("tick")
            time.sleep(3)
            second = state_payload()
            verdict = bool(second) and second.get("tick") != first
            detail = f"tick {first} -> {(second or {}).get('tick')}"

    ledger("probe", probe=probe or probe_state_path or "alive", passed=verdict, detail=detail)

    if verdict:
        out(f"deployed and the probe passed: {detail}")
        return 0

    if keep_on_failure:
        out(f"probe FAILED but was told to keep it: {detail}")
        return 1

    out(f"probe FAILED, putting the old bridge back: {detail}")
    game().stop()
    time.sleep(3)
    if BACKUP.exists():
        shutil.copy2(BACKUP, DLL)
    game().ensure_running()
    wait_for_state()
    ledger("revert", reason="probe-failed", detail=detail)
    return 1


def cmd_revert() -> int:
    was_running = game().is_running()
    if was_running:
        game().stop()
        time.sleep(3)
    if BACKUP.exists():
        shutil.copy2(BACKUP, DLL)
    git("checkout", "--", "mod")
    game().ensure_running()
    wait_for_state()
    ledger("revert", reason="asked")
    out("the previous bridge is deployed and the mod/ tree is back to the last commit")
    return 0


def cmd_build() -> int:
    ok, report = build()
    out(report)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="read, patch, build, deploy or revert the bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=lambda _a: cmd_status())

    p_read = sub.add_parser("read")
    p_read.add_argument("path")
    p_read.add_argument("--grep", default=None)
    p_read.add_argument("--around", type=int, default=12)
    p_read.add_argument("--lines", default=None)
    p_read.set_defaults(func=lambda a: cmd_read(a.path, a.grep, a.around, a.lines))

    p_ticket = sub.add_parser("ticket")
    p_ticket.add_argument("--symptom", required=True)
    p_ticket.add_argument("--evidence", default="")
    p_ticket.add_argument("--source", default="agent")
    p_ticket.set_defaults(func=lambda a: cmd_ticket(a.symptom, a.evidence, a.source))

    p_tickets = sub.add_parser("tickets")
    p_tickets.set_defaults(func=lambda _a: cmd_tickets())

    p_patch = sub.add_parser("patch")
    p_patch.add_argument("--file", required=True)
    p_patch.add_argument("--find", required=True)
    p_patch.add_argument("--replace", required=True)
    p_patch.add_argument("--why", required=True)
    p_patch.add_argument("--evidence", default="")
    p_patch.add_argument("--source", default="agent")
    p_patch.set_defaults(func=lambda a: cmd_patch(a.file, a.find, a.replace, a.why, a.evidence, a.source))

    p_build = sub.add_parser("build")
    p_build.set_defaults(func=lambda _a: cmd_build())

    p_deploy = sub.add_parser("deploy")
    p_deploy.add_argument("--probe-state", dest="probe_state", default=None,
                          help="a dotted path that must exist in the live /state payload")
    p_deploy.add_argument("--probe-cmd", dest="probe", default=None,
                          help="a command that must exit 0 against the running game")
    p_deploy.add_argument("--keep-on-failure", action="store_true")
    p_deploy.set_defaults(func=lambda a: cmd_deploy(a.probe_state, a.probe, a.keep_on_failure))

    p_revert = sub.add_parser("revert")
    p_revert.set_defaults(func=lambda _a: cmd_revert())

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
