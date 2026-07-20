"""Loop-level guarantees: it repairs, it stops, and it cannot exceed its ceilings."""

import random

import pytest

from agent.graph import build_graph, recursion_limit_for, route_after_evaluate
from agent.nodes import LoopDeps
from agent.state import AgentState, Budget
from sandbox.base import ExecResult
from tests.fakes import (
    OK_ENVELOPE,
    ScriptedBackend,
    ScriptedLLM,
    block,
    runtime_error_result,
    success_result,
    timeout_result,
)

BROKEN = block("x = 1 / 0")
FIXED = block(f"print({OK_ENVELOPE!r})")


def run(llm, backend, *, max_attempts=3, budget=None) -> AgentState:
    deps = LoopDeps(llm=llm, backend=backend)
    state = AgentState(
        request="give me the answer",
        max_attempts=max_attempts,
        budget=budget or Budget(model="llama-3.3-70b-versatile"),
    )
    final = build_graph(deps).invoke(
        state, config={"recursion_limit": recursion_limit_for(max_attempts)}
    )
    return AgentState.model_validate(final)


def fails_then_succeeds(fail_count: int):
    """Backend that fails the first `fail_count` runs, then succeeds."""
    calls = {"n": 0}

    def responder(code: str) -> ExecResult:
        calls["n"] += 1
        if calls["n"] <= fail_count:
            return runtime_error_result()
        return success_result()

    return ScriptedBackend(responder=responder)


def test_first_attempt_failure_is_repaired_and_succeeds():
    llm = ScriptedLLM([BROKEN, FIXED])
    backend = fails_then_succeeds(1)

    state = run(llm, backend)

    assert state.gave_up is False
    assert state.attempt == 2
    assert state.failure_class == "none"
    assert state.final_output.success is True
    assert state.final_output.envelope.type == "text"
    assert [a.failure_class for a in state.history] == ["runtime", "none"]
    assert len(llm.calls) == 2


def test_unfixable_code_stops_at_the_cap_with_a_useful_message():
    llm = ScriptedLLM([BROKEN])
    backend = ScriptedBackend([runtime_error_result()])

    state = run(llm, backend)

    assert state.gave_up is True
    assert state.give_up_reason == "max_attempts"
    assert state.attempt == 3
    assert len(backend.runs) == 3
    assert state.final_output.success is False
    assert "3 of 3 attempts" in state.final_output.message
    assert "ZeroDivisionError" in state.final_output.message


@pytest.mark.parametrize("max_attempts", [1, 2, 3, 5])
def test_attempt_cap_holds_for_any_configured_ceiling(max_attempts):
    state = run(
        ScriptedLLM([BROKEN]),
        ScriptedBackend([runtime_error_result()]),
        max_attempts=max_attempts,
    )

    assert state.attempt == max_attempts
    assert len(state.history) == max_attempts
    assert state.gave_up is True


def test_ten_randomised_runs_never_exceed_the_cap():
    """Property test: whatever the failure mix, execute runs at most max_attempts times."""
    rng = random.Random(20260720)
    outcomes = [
        success_result(),
        runtime_error_result(),
        ExecResult(stdout="", stderr="SyntaxError: bad", exit_code=1),
        success_result(stdout="not an envelope"),
        timeout_result(),
        ExecResult(stdout="", stderr="socket.gaierror: name resolution", exit_code=1),
    ]

    for _ in range(10):
        max_attempts = rng.randint(1, 4)
        backend = ScriptedBackend(responder=lambda code: rng.choice(outcomes))
        llm = ScriptedLLM([BROKEN, FIXED, BROKEN])

        state = run(llm, backend, max_attempts=max_attempts)

        assert state.attempt <= max_attempts
        assert len(backend.runs) <= max_attempts
        assert len(state.history) == state.attempt
        assert state.final_output is not None


@pytest.mark.parametrize("failure_class", ["timeout", "security"])
def test_terminal_failures_skip_repair_entirely(failure_class):
    result = (
        timeout_result()
        if failure_class == "timeout"
        else ExecResult(
            stdout="", stderr="OSError: Network is unreachable", exit_code=1
        )
    )
    llm = ScriptedLLM([BROKEN])
    backend = ScriptedBackend([result])

    state = run(llm, backend)

    assert state.attempt == 1
    assert len(llm.calls) == 1
    assert state.gave_up is True
    assert state.give_up_reason == "terminal_failure"
    assert state.failure_class == failure_class


def test_budget_ceiling_aborts_before_the_attempt_cap_is_reached():
    llm = ScriptedLLM([BROKEN], input_tokens=400, output_tokens=200)
    backend = ScriptedBackend([runtime_error_result()])

    state = run(
        llm,
        backend,
        max_attempts=5,
        budget=Budget(model="llama-3.3-70b-versatile", max_total_tokens=1000),
    )

    assert state.gave_up is True
    assert state.give_up_reason == "budget"
    assert state.attempt < 5
    assert "token ceiling" in state.final_output.message


def test_cost_ceiling_aborts_and_the_run_cost_is_recorded():
    llm = ScriptedLLM([BROKEN], input_tokens=100_000, output_tokens=50_000)
    backend = ScriptedBackend([runtime_error_result()])

    state = run(
        llm,
        backend,
        max_attempts=5,
        budget=Budget(
            model="llama-3.3-70b-versatile",
            max_total_tokens=10_000_000,
            max_cost_usd=0.05,
        ),
    )

    assert state.gave_up is True
    assert state.give_up_reason == "budget"
    assert state.budget.cost_usd > 0
    assert "cost ceiling" in state.final_output.message


def test_sandbox_time_ceiling_aborts_the_run():
    def slow(code: str) -> ExecResult:
        return runtime_error_result()

    backend = ScriptedBackend(responder=slow)
    budget = Budget(model="llama-3.3-70b-versatile", max_sandbox_seconds=0.0)

    state = run(ScriptedLLM([BROKEN]), backend, max_attempts=5, budget=budget)

    assert state.gave_up is True
    assert state.give_up_reason == "budget"
    assert "sandbox time ceiling" in state.final_output.message


def test_a_model_that_never_emits_a_block_still_terminates():
    llm = ScriptedLLM(["I'm sorry, I can't help with that."])
    backend = ScriptedBackend([success_result()])

    state = run(llm, backend)

    assert state.attempt == 3
    assert backend.runs == []
    assert state.gave_up is True
    assert state.failure_class == "envelope"


def test_envelope_violation_is_retryable_and_repairs():
    calls = {"n": 0}

    def responder(code: str) -> ExecResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return success_result(stdout="here is your answer: 42")
        return success_result()

    llm = ScriptedLLM([BROKEN, FIXED])
    state = run(llm, ScriptedBackend(responder=responder))

    assert state.history[0].failure_class == "envelope"
    assert state.gave_up is False
    assert state.final_output.success is True


def test_router_never_routes_to_repair_at_the_cap():
    at_cap = AgentState(request="x", attempt=3, max_attempts=3, failure_class="runtime")
    below_cap = AgentState(request="x", attempt=2, max_attempts=3, failure_class="runtime")

    assert route_after_evaluate(at_cap) == "give_up"
    assert route_after_evaluate(below_cap) == "repair"
    assert route_after_evaluate(AgentState(request="x", failure_class="none")) == "success"
