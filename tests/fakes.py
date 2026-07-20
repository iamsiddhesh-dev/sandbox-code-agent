"""Scripted LLM and sandbox doubles so the loop can be tested without spending anything."""

from __future__ import annotations

from collections.abc import Callable

from agent.llm import LLMResponse
from sandbox.base import ExecResult

OK_ENVELOPE = '{"type": "text", "data": "42", "artifact_path": null, "note": null}'


def block(code: str) -> str:
    return f"```python\n{code}\n```"


class ScriptedLLM:
    """Replays a fixed list of raw responses; repeats the last one if exhausted."""

    def __init__(self, responses: list[str], model: str = "llama-3.3-70b-versatile",
                 input_tokens: int = 100, output_tokens: int = 50):
        self.responses = responses
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return LLMResponse(
            text=self.responses[index],
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


class ScriptedBackend:
    """Returns a fixed list of ExecResults, or defers to a code -> ExecResult function."""

    def __init__(
        self,
        results: list[ExecResult] | None = None,
        responder: Callable[[str], ExecResult] | None = None,
    ):
        self.results = results or []
        self.responder = responder
        self.runs: list[str] = []

    def run(self, code: str, lang: str = "python", timeout_s: int = 30) -> ExecResult:
        self.runs.append(code)
        if self.responder is not None:
            return self.responder(code)
        index = min(len(self.runs) - 1, len(self.results) - 1)
        return self.results[index]


def success_result(stdout: str = OK_ENVELOPE, files: dict[str, bytes] | None = None):
    return ExecResult(stdout=stdout, stderr="", exit_code=0, files=files or {})


def runtime_error_result(stderr: str = "Traceback...\nZeroDivisionError: division by zero"):
    return ExecResult(stdout="", stderr=stderr, exit_code=1)


def timeout_result():
    return ExecResult(stdout="", stderr="killed", exit_code=124, timed_out=True)
