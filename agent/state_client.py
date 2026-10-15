"""HTTP client for the StateBridge mod.

The mod exposes three endpoints on localhost:
    GET  /health  -> liveness + whether a save is loaded
    GET  /state   -> full structured game state (JSON)
    POST /action  -> execute one action on the game thread
"""

from __future__ import annotations

import time

import httpx


class StateClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8787", timeout_s: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        # trust_env=False: never route a localhost request through a system proxy. A proxy in the
        # middle answers 502 for a target it cannot reach, which looks exactly like the game died.
        self._client = httpx.Client(timeout=timeout_s, trust_env=False)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "StateClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- endpoints ---------------------------------------------------------

    def health(self) -> dict:
        resp = self._client.get(f"{self.base_url}/health")
        resp.raise_for_status()
        return resp.json()

    def state(self, retries: int = 3) -> dict:
        """Read the state, tolerating a brief blip so one dropped request cannot kill a day."""
        last: Exception | None = None

        for attempt in range(retries + 1):
            try:
                resp = self._client.get(f"{self.base_url}/state")
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last = exc
                time.sleep(0.4 * (attempt + 1))

        raise RuntimeError(f"could not read /state after {retries + 1} tries: {last}")

    def action(self, action: str, **args: object) -> dict:
        """POST one action. Returns the mod's result envelope (ok/detail)."""
        payload = {"action": action, **args}
        last: Exception | None = None

        for attempt in range(3):
            try:
                resp = self._client.post(f"{self.base_url}/action", json=payload)
                break
            except Exception as exc:
                last = exc
                time.sleep(0.4 * (attempt + 1))
        else:
            return {"ok": False, "action": action, "detail": f"transport error: {last}"}

        # A rejected action comes back as 400 with a JSON body, so parse either way.
        try:
            data = resp.json()
        except ValueError:
            return {"ok": False, "action": action, "detail": resp.text}

        if not isinstance(data, dict):
            return {"ok": False, "action": action, "detail": str(data)}
        return data

    # -- helpers -----------------------------------------------------------

    def wait_until_up(self, timeout_s: float = 240.0, interval_s: float = 3.0) -> bool:
        """Block until /health answers, or give up. Returns True when it is up."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if self.health().get("ok"):
                    return True
            except Exception:
                pass
            time.sleep(interval_s)
        return False

    def wait_until_loaded(self, timeout_s: float = 240.0, interval_s: float = 2.0) -> bool:
        """Block until a save is in game. Returns True when has_save is set."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if self.state().get("game", {}).get("has_save"):
                    return True
            except Exception:
                pass
            time.sleep(interval_s)
        return False

    def wait_until_chore_done(self, timeout_s: float = 300.0, poll_s: float = 1.0) -> dict:
        """Wait for a reflex routine (clear_area / water_all / harvest_all) to finish.

        The routine runs inside the mod at game speed, so this is a few seconds per tile and costs
        nothing but HTTP polling - no model calls while the farmer works.
        """
        deadline = time.monotonic() + timeout_s
        state = self.state()

        while time.monotonic() < deadline:
            if (state.get("chore") or "idle") == "idle":
                break
            time.sleep(poll_s)
            state = self.state()

        return state

    def wait_until_settled(self, timeout_s: float = 12.0, poll_s: float = 0.2,
                           grace_s: float = 0.15) -> dict:
        deadline = time.monotonic() + timeout_s
        state = self.state()

        while time.monotonic() < deadline:
            player = state.get("player") or {}
            if not player.get("is_auto_moving"):
                if grace_s:
                    time.sleep(grace_s)
                    state = self.state()
                return state

            time.sleep(poll_s)
            state = self.state()

        return state
