"""Chat client for the configured OpenAI-compatible endpoint.

Reads its endpoint from agent/llm_config.py (config/secrets.json + env overrides).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from llm_config import Endpoint, resolve

_DEFAULT = "__default__"
_FENCE_OPEN = re.compile(r"^```[a-zA-Z0-9_-]*\s*")
_FENCE_CLOSE = re.compile(r"\s*```$")


class LlmError(RuntimeError):
    pass


@dataclass
class LlmReply:
    content: str = ""
    reasoning: str = ""
    latency_s: float = 0.0
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return int(self.usage.get("total_tokens", 0) or 0)

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


class LlmClient:
    def __init__(self, section: str = "llm") -> None:
        self.endpoint: Endpoint = resolve(section)
        if not self.endpoint.is_configured:
            raise LlmError(
                "no API key configured. Set OPENCODE_API_KEY, or fill llm.api_key "
                "in config/secrets.json"
            )
        self._client = httpx.Client(timeout=self.endpoint.timeout_s)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LlmClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = _DEFAULT,  # type: ignore[assignment]
    ) -> LlmReply:
        body: dict = {
            "model": model or self.endpoint.model,
            "messages": messages,
            "temperature": self.endpoint.temperature if temperature is None else temperature,
            "max_tokens": self.endpoint.max_tokens if max_tokens is None else max_tokens,
        }

        effort = self.endpoint.reasoning_effort if reasoning_effort == _DEFAULT else reasoning_effort
        if effort:
            body["reasoning_effort"] = effort

        started = time.perf_counter()
        resp = self._client.post(
            self.endpoint.chat_url,
            headers={
                "Authorization": f"Bearer {self.endpoint.api_key}",
                "Content-Type": "application/json",
                **self.endpoint.headers,
            },
            json=body,
        )
        elapsed = time.perf_counter() - started

        if resp.status_code != 200:
            raise LlmError(f"HTTP {resp.status_code} from {self.endpoint.chat_url}: {resp.text[:400]}")

        data = resp.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise LlmError(f"unexpected response shape: {json.dumps(data)[:400]}") from exc

        return LlmReply(
            content=message.get("content") or "",
            reasoning=message.get("reasoning_content") or "",
            latency_s=elapsed,
            finish_reason=str(data["choices"][0].get("finish_reason") or ""),
            usage=data.get("usage", {}) or {},
            raw=data,
        )

    def chat_json(self, messages: list[dict], *, retries: int = 2, **kwargs: object) -> tuple[dict, LlmReply]:
        """Chat and parse the reply as JSON, retrying when the model rambles or gets truncated."""
        attempt_messages = list(messages)
        attempt_kwargs = dict(kwargs)
        last_error: Exception | None = None

        for _ in range(retries + 1):
            reply = self.chat(attempt_messages, **attempt_kwargs)  # type: ignore[arg-type]

            try:
                return extract_json(reply.content), reply
            except LlmError as exc:
                last_error = exc

                # A reasoning model can burn the whole output budget thinking and return no
                # content at all. Give the retry more room instead of repeating the same failure.
                current = attempt_kwargs.get("max_tokens")
                if not isinstance(current, int):
                    current = self.endpoint.max_tokens
                attempt_kwargs["max_tokens"] = min(4000, current * 2 + 200)

                if reply.truncated:
                    complaint = "Your previous reply was CUT OFF before the JSON finished. "
                elif not (reply.content or "").strip():
                    complaint = "Your previous reply was empty - you spent the budget thinking. "
                else:
                    complaint = "Your previous reply was not valid JSON. "

                attempt_messages = list(messages) + [
                    {"role": "assistant", "content": reply.content or "(empty)"},
                    {
                        "role": "user",
                        "content": complaint
                        + "Reply with ONE complete JSON object and nothing else - no prose, no code fences.",
                    },
                ]

        raise last_error if last_error else LlmError("JSON parsing failed")


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a reply, tolerating markdown fences and stray prose."""
    if not text or not text.strip():
        raise LlmError("model returned an empty reply")

    cleaned = _FENCE_CLOSE.sub("", _FENCE_OPEN.sub("", text.strip())).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = next((i for i, ch in enumerate(cleaned) if ch == "{"), -1)
    if start < 0:
        raise LlmError(f"no JSON object found in reply: {text[:200]!r}")

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(cleaned)):
        ch = cleaned[i]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise LlmError(f"malformed JSON in reply: {exc}") from exc

    raise LlmError(f"unterminated JSON object in reply: {text[:200]!r}")
