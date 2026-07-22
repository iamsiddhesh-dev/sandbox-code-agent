"""AgentState / Budget: validation, log serialization, and ceiling arithmetic."""

import json

import pytest
from pydantic import ValidationError

from agent.state import AgentState, Attempt, Budget, RenderedResult, price_for
from renderers.envelope import TextEnvelope
from sandbox.base import ExecResult


def test_defaults_are_a_valid_fresh_run():
    state = AgentState(request="plot something")

    assert state.attempt == 0
    assert state.max_attempts == 3
    assert state.history == []
    assert state.code is None
    assert state.gave_up is False


def test_failure_class_enum_is_enforced():
    with pytest.raises(ValidationError):
        AgentState(request="x", failure_class="explosion")


def test_to_log_dict_is_json_serializable_and_drops_artifact_bytes():
    state = AgentState(
        request="chart it",
        code="print(1)",
        exec_result=ExecResult(
            stdout="ok", stderr="", exit_code=0, files={"chart.png": b"\x89PNG\r\n"}
        ),
        history=[Attempt(attempt=1, code_sha256="abc123", failure_class="runtime")],
        final_output=RenderedResult(
            success=True,
            envelope=TextEnvelope(type="text", data="42"),
            files={"chart.png": b"\x89PNG\r\n"},
        ),
    )

    payload = state.to_log_dict()
    encoded = json.dumps(payload)

    assert "files" not in payload["exec_result"]
    assert "files" not in payload["final_output"]
    assert "chart.png" not in encoded
    assert payload["history"][0]["failure_class"] == "runtime"


def test_budget_charges_tokens_at_model_price():
    budget = Budget(model="llama-3.3-70b-versatile")

    cost = budget.charge_tokens(1_000_000, 1_000_000)

    assert budget.total_tokens == 2_000_000
    assert cost == pytest.approx(0.59 + 0.79)
    assert budget.cost_usd == pytest.approx(1.38)


def test_unknown_model_prices_at_the_most_expensive_known_rate():
    assert price_for("some-future-model") == max(
        price_for("llama-3.3-70b-versatile"), price_for("llama-3.1-8b-instant")
    )


def test_exhausted_reports_which_ceiling_was_hit():
    budget = Budget(model="llama-3.3-70b-versatile", max_total_tokens=100)
    assert budget.exhausted() is None

    budget.charge_tokens(60, 40)
    assert "token ceiling" in budget.exhausted()

    cost_bound = Budget(model="llama-3.3-70b-versatile", max_cost_usd=0.000_001)
    cost_bound.charge_tokens(1000, 1000)
    assert "cost ceiling" in cost_bound.exhausted()

    time_bound = Budget(model="llama-3.3-70b-versatile", max_sandbox_seconds=5)
    time_bound.charge_sandbox(5.1)
    assert "sandbox time ceiling" in time_bound.exhausted()


def test_default_cost_ceiling_is_reachable_within_the_token_ceiling():
    """A ceiling that another cap makes unreachable is not a guardrail."""
    budget = Budget(model="llama-3.3-70b-versatile")
    _, out_price = price_for(budget.model)
    max_spend_at_token_ceiling = budget.max_total_tokens * out_price / 1_000_000

    assert max_spend_at_token_ceiling >= budget.max_cost_usd


def test_default_sandbox_ceiling_is_reachable_within_the_attempt_cap():
    budget = Budget(model="llama-3.3-70b-versatile")
    structural_max = AgentState(request="x").max_attempts * budget.per_run_timeout_s

    assert structural_max >= budget.max_sandbox_seconds


def test_default_ceilings_clear_worst_measured_gauntlet_run():
    """Guardrails must backstop runaway loops, not abort the known-good worst case."""
    budget = Budget(model="llama-3.3-70b-versatile")

    assert budget.max_total_tokens > 11_329
    assert budget.max_cost_usd > 0.00729
    assert budget.max_sandbox_seconds > 34.3
