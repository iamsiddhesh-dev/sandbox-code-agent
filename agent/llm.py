"""Thin Groq wrapper: system+user in, text and token usage out."""

from __future__ import annotations

import logging
import re
import time
from typing import Protocol

from pydantic import BaseModel

import config

log = logging.getLogger("agent.llm")

RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)(ms|s)", re.IGNORECASE)
MAX_BACKOFF_S = 15.0


class LLMResponse(BaseModel):
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class CodegenLLM(Protocol):
    model: str

    def complete(self, system: str, user: str) -> LLMResponse: ...


class GroqCodegen:
    """Calls the runtime code-gen model. Low temperature: this is a contract, not prose."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.2,
        # Repair prompts are long; a too-tight cap truncates the closing fence
        # and the answer reads as "no code block" instead of a fixable program.
        max_tokens: int = 2048,
        max_retries: int = 4,
    ):
        from groq import Groq

        self.model = model or config.CODEGEN_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._client = Groq(api_key=api_key or config.GROQ_API_KEY)

    def complete(self, system: str, user: str) -> LLMResponse:
        from groq import RateLimitError

        for attempt in range(self.max_retries + 1):
            try:
                completion = self._client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                break
            except RateLimitError as exc:
                # The per-minute token bucket refills fast (seconds); the daily
                # bucket does not — retrying that blindly would just hang, so
                # the backoff is capped and the caller sees the real error once
                # retries are exhausted rather than a silent multi-minute stall.
                if attempt == self.max_retries:
                    raise
                wait_s = min(_parse_retry_after(str(exc)), MAX_BACKOFF_S)
                log.warning(
                    "groq rate limit (attempt %d/%d), backing off %.1fs",
                    attempt + 1, self.max_retries, wait_s,
                )
                time.sleep(wait_s)

        usage = completion.usage
        return LLMResponse(
            text=completion.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


def _parse_retry_after(message: str) -> float:
    match = RETRY_AFTER_RE.search(message)
    if not match:
        return 2.0
    value, unit = match.groups()
    seconds = float(value) / 1000.0 if unit.lower() == "ms" else float(value)
    return max(seconds, 0.5)
