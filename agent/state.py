"""Graph state for the generate → execute → evaluate → repair loop."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from renderers.envelope import Envelope
from sandbox.base import ExecResult

FailureClass = Literal["syntax", "runtime", "timeout", "security", "envelope", "none"]
GiveUpReason = Literal["max_attempts", "budget", "terminal_failure"]

RETRYABLE: frozenset[str] = frozenset({"syntax", "runtime", "envelope"})
TERMINAL: frozenset[str] = frozenset({"timeout", "security"})

# Groq list price per million tokens. Unknown models fall back to the most
# expensive entry so an unpriced model can never look free to the ceiling check.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
}


def price_for(model: str) -> tuple[float, float]:
    if model in PRICING_USD_PER_MTOK:
        return PRICING_USD_PER_MTOK[model]
    return max(PRICING_USD_PER_MTOK.values(), key=lambda p: p[0] + p[1])


class Attempt(BaseModel):
    """One trip through execute — the per-attempt observability record."""

    attempt: int
    code_sha256: str
    failure_class: FailureClass
    stderr_excerpt: str = ""
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Budget(BaseModel):
    """Cumulative spend for one request, plus the ceilings that abort it."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    sandbox_seconds: float = 0.0

    max_total_tokens: int = 24_000
    max_cost_usd: float = 0.05
    max_sandbox_seconds: float = 180.0
    per_run_timeout_s: int = 30

    def charge_tokens(self, input_tokens: int, output_tokens: int) -> float:
        in_price, out_price = price_for(self.model)
        cost = (input_tokens * in_price + output_tokens * out_price) / 1_000_000
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += cost
        return cost

    def charge_sandbox(self, seconds: float) -> None:
        self.sandbox_seconds += seconds

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def exhausted(self) -> str | None:
        """Name the ceiling that has been hit, or None if there is room left."""
        if self.total_tokens >= self.max_total_tokens:
            return f"token ceiling reached ({self.total_tokens}/{self.max_total_tokens})"
        if self.cost_usd >= self.max_cost_usd:
            return f"cost ceiling reached (${self.cost_usd:.4f}/${self.max_cost_usd:.4f})"
        if self.sandbox_seconds >= self.max_sandbox_seconds:
            return (
                f"sandbox time ceiling reached "
                f"({self.sandbox_seconds:.1f}s/{self.max_sandbox_seconds:.1f}s)"
            )
        return None


class RenderedResult(BaseModel):
    """What the loop hands to the render layer, success or failure."""

    success: bool
    envelope: Envelope | None = None
    files: dict[str, bytes] = Field(default_factory=dict)
    message: str = ""
    raw_stdout: str = ""


class AgentState(BaseModel):
    request: str
    lang: Literal["python", "js"] = "python"
    code: str | None = None
    exec_result: ExecResult | None = None
    attempt: int = 0
    max_attempts: int = 3
    history: list[Attempt] = Field(default_factory=list)
    failure_class: FailureClass | None = None
    final_output: RenderedResult | None = None
    gave_up: bool = False
    give_up_reason: GiveUpReason | None = None
    budget: Budget = Field(default_factory=lambda: Budget(model="unknown"))

    # Usage of the LLM call that produced the *current* `code`, parked here by
    # generate/repair so execute can fold it into that attempt's record.
    pending_input_tokens: int = 0
    pending_output_tokens: int = 0
    pending_cost_usd: float = 0.0

    def to_log_dict(self) -> dict:
        """Serializable snapshot — artifact bytes dropped, they are not loggable."""
        payload = self.model_dump(
            mode="json",
            exclude={
                "exec_result": {"files"},
                "final_output": {"files"},
            },
        )
        return payload
