"""
Resolve LLM endpoint settings for the AI Stardew agent.

Priority (highest first):
  1. Environment variable   (OPENCODE_API_KEY for section "llm", DEEPSEEK_API_KEY for "fallback")
  2. config/secrets.json    (gitignored, local only)

Nothing else in the repo should ever read the key directly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRETS_PATH = PROJECT_ROOT / "config" / "secrets.json"

# section name -> environment variable that overrides its api_key
ENV_OVERRIDES: dict[str, str] = {
    "llm": "OPENCODE_API_KEY",
    "fallback": "DEEPSEEK_API_KEY",
}


@dataclass
class Endpoint:
    """One OpenAI-compatible chat-completions endpoint."""

    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.4
    max_tokens: int = 900
    timeout_s: int = 120
    reasoning_effort: str | None = None
    key_source: str = "unknown"
    headers: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    # Whether to let httpx pick up the machine's system proxy. Off by default, and that default is
    # load-bearing: a local proxy client that is restarting, switching nodes, or simply wedged turns
    # every model call into an SSL EOF - which looks exactly like the AI going silent for no reason.
    # The bridge client already refuses the system proxy for the same class of reason.
    trust_env: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def masked_key(self) -> str:
        k = self.api_key.strip()
        if not k:
            return "<empty>"
        if len(k) <= 10:
            return k[:2] + "***"
        return f"{k[:6]}...{k[-4:]} (len={len(k)})"

    def describe(self) -> str:
        return (
            f"provider={self.provider}\n"
            f"  base_url = {self.base_url}\n"
            f"  model    = {self.model}\n"
            f"  headers  = {self.headers}\n"
            f"  api_key  = {self.masked_key()}  [from {self.key_source}]"
        )


def _read_secrets() -> dict:
    if not SECRETS_PATH.exists():
        return {}
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not parse {SECRETS_PATH}: {exc}") from exc


def resolve(section: str = "llm") -> Endpoint:
    """Build an Endpoint for a section of secrets.json, applying env overrides."""
    raw = _read_secrets().get(section, {})

    env_name = ENV_OVERRIDES.get(section)
    env_value = os.environ.get(env_name, "").strip() if env_name else ""

    if env_value:
        api_key, source = env_value, f"env:{env_name}"
    else:
        api_key, source = str(raw.get("api_key", "")).strip(), str(SECRETS_PATH)

    # A section with no key of its own borrows the main one: one key covers the whole model family.
    if not api_key and section != "llm":
        try:
            fallback_key = str(json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
                               .get("llm", {}).get("api_key", "")).strip()
            if fallback_key:
                api_key, source = fallback_key, f"{SECRETS_PATH} [llm]"
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    known = {
        "provider", "base_url", "api_key", "model", "headers",
        "temperature", "max_tokens", "timeout_s", "reasoning_effort", "trust_env",
    }
    extra = {k: v for k, v in raw.items() if k not in known and not k.startswith("_")}

    return Endpoint(
        provider=str(raw.get("provider", "unknown")),
        base_url=str(raw.get("base_url", "")),
        api_key=api_key,
        model=str(raw.get("model", "")),
        temperature=float(raw.get("temperature", 0.4)),
        max_tokens=int(raw.get("max_tokens", 900)),
        timeout_s=int(raw.get("timeout_s", 120)),
        reasoning_effort=raw.get("reasoning_effort"),
        key_source=source,
        headers=dict(raw.get("headers", {})),
        extra=extra,
        trust_env=bool(raw.get("trust_env", False)),
    )


if __name__ == "__main__":
    for name in ENV_OVERRIDES:
        print(f"--- section: {name} ---")
        print(resolve(name).describe())
        print()
