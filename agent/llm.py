"""Thin Groq wrapper: system+user in, text and token usage out."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

import config


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
    ):
        from groq import Groq

        self.model = model or config.CODEGEN_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = Groq(api_key=api_key or config.GROQ_API_KEY)

    def complete(self, system: str, user: str) -> LLMResponse:
        completion = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        usage = completion.usage
        return LLMResponse(
            text=completion.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )
