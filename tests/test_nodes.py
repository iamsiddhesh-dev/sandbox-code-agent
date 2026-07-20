"""Per-node unit tests with the LLM and sandbox mocked out."""

import pytest

from agent.nodes import (
    LoopDeps,
    build_repair_message,
    classify,
    envelope_from_stdout,
    extract_single_block,
    make_evaluate,
    make_execute,
    make_generate,
    make_give_up,
    make_repair,
)
from agent.state import AgentState, Attempt, Budget
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


def deps_with(llm=None, backend=None) -> LoopDeps:
    return LoopDeps(
        llm=llm or ScriptedLLM([block("print(1)")]),
        backend=backend or ScriptedBackend([success_result()]),
    )


def fresh_state(**kwargs) -> AgentState:
    kwargs.setdefault("budget", Budget(model="llama-3.3-70b-versatile"))
    return AgentState(request="give me the answer", **kwargs)


# --- block extraction ---------------------------------------------------


def test_extract_single_block_returns_the_only_block():
    assert extract_single_block(block("print(1)")) == "print(1)"


@pytest.mark.parametrize(
    "text",
    ["no fences here at all", block("print(1)") + "\n" + block("print(2)")],
)
def test_zero_or_multiple_blocks_extract_to_none(text):
    assert extract_single_block(text) is None


# --- classification -----------------------------------------------------


@pytest.mark.parametrize(
    "result,expected",
    [
        (success_result(), "none"),
        (success_result(stdout="just some prose, no envelope"), "envelope"),
        (success_result(stdout='{"type": "wat", "data": 1}'), "envelope"),
        (success_result(stdout=""), "envelope"),
        (timeout_result(), "timeout"),
        (ExecResult(stdout="", stderr="SyntaxError: bad", exit_code=1), "syntax"),
        (runtime_error_result(), "runtime"),
        (
            ExecResult(
                stdout="",
                stderr="socket.gaierror: [Errno -3] Temporary failure in name resolution",
                exit_code=1,
            ),
            "security",
        ),
        (
            ExecResult(
                stdout="",
                stderr="OSError: [Errno 30] Read-only file system: '/etc/passwd'",
                exit_code=1,
            ),
            "security",
        ),
    ],
)
def test_classify(result, expected):
    assert classify(result) == expected


def test_envelope_is_read_from_the_last_stdout_line_only():
    stdout = f"debug chatter\n{OK_ENVELOPE}\n"
    envelope = envelope_from_stdout(stdout)

    assert envelope is not None and envelope.type == "text"
    assert envelope_from_stdout(f"{OK_ENVELOPE}\ntrailing noise") is None


# --- generate -----------------------------------------------------------


def test_generate_extracts_code_and_charges_the_budget():
    llm = ScriptedLLM([block("print(1)")], input_tokens=200, output_tokens=100)
    state = fresh_state()

    update = make_generate(deps_with(llm=llm))(state)

    assert update["code"] == "print(1)"
    assert update["budget"].input_tokens == 200
    assert update["pending_output_tokens"] == 100
    assert update["pending_cost_usd"] > 0


def test_generate_with_multiple_blocks_yields_no_code():
    llm = ScriptedLLM([block("print(1)") + block("print(2)")])

    update = make_generate(deps_with(llm=llm))(fresh_state())

    assert update["code"] is None


# --- execute ------------------------------------------------------------


def test_execute_runs_code_increments_attempt_and_records_history():
    backend = ScriptedBackend([success_result()])
    state = fresh_state(code="print(1)", pending_input_tokens=10, pending_output_tokens=5)

    update = make_execute(deps_with(backend=backend))(state)

    assert backend.runs == ["print(1)"]
    assert update["attempt"] == 1
    assert len(update["history"]) == 1
    assert update["history"][0].input_tokens == 10
    assert update["pending_input_tokens"] == 0
    assert update["exec_result"].exit_code == 0


def test_execute_burns_an_attempt_even_when_no_code_was_produced():
    backend = ScriptedBackend([success_result()])
    state = fresh_state(code=None)

    update = make_execute(deps_with(backend=backend))(state)

    assert backend.runs == []
    assert update["attempt"] == 1
    assert update["exec_result"].exit_code == 1


def test_execute_charges_sandbox_seconds():
    state = fresh_state(code="print(1)")

    update = make_execute(deps_with())(state)

    assert update["budget"].sandbox_seconds >= 0.0
    assert "sandbox_seconds" in update["budget"].model_dump()


# --- evaluate -----------------------------------------------------------


def test_evaluate_success_builds_the_final_output():
    state = fresh_state(
        code="print(1)",
        attempt=1,
        exec_result=success_result(files={"chart.png": b"\x89PNG"}),
        history=[Attempt(attempt=1, code_sha256="abc", failure_class="none")],
    )

    update = make_evaluate()(state)

    assert update["failure_class"] == "none"
    assert update["final_output"].success is True
    assert update["final_output"].envelope.type == "text"
    assert update["final_output"].files == {"chart.png": b"\x89PNG"}


def test_evaluate_backfills_the_failure_class_onto_the_last_attempt():
    state = fresh_state(
        code="print(1)",
        attempt=1,
        exec_result=runtime_error_result(),
        history=[Attempt(attempt=1, code_sha256="abc", failure_class="none")],
    )

    update = make_evaluate()(state)

    assert update["failure_class"] == "runtime"
    assert update["history"][-1].failure_class == "runtime"
    assert "ZeroDivisionError" in update["history"][-1].stderr_excerpt
    assert "final_output" not in update


def test_evaluate_before_execute_is_a_programming_error():
    with pytest.raises(RuntimeError):
        make_evaluate()(fresh_state())


# --- repair -------------------------------------------------------------


def test_repair_prompt_carries_request_code_and_stderr():
    deps = deps_with()
    state = fresh_state(
        code="x = 1/0",
        failure_class="runtime",
        exec_result=runtime_error_result(),
    )

    message = build_repair_message(state, deps.repair_prompt)

    assert "give me the answer" in message
    assert "x = 1/0" in message
    assert "ZeroDivisionError" in message
    assert "Fix ONLY what broke" in message


def test_repair_prompt_branches_on_envelope_failure():
    deps = deps_with()
    state = fresh_state(
        code="print('hi')",
        failure_class="envelope",
        exec_result=success_result(stdout="hi"),
    )

    message = build_repair_message(state, deps.repair_prompt)

    assert "final stdout line was not a valid output envelope" in message
    assert "'hi'" in message


def test_repair_prompt_branches_when_no_block_was_emitted():
    deps = deps_with()
    state = fresh_state(
        code=None,
        failure_class="envelope",
        exec_result=ExecResult(stdout="", stderr="no block", exit_code=1),
    )

    message = build_repair_message(state, deps.repair_prompt)

    assert "exactly one fenced" in message


def test_repair_returns_new_code_and_charges_the_budget():
    llm = ScriptedLLM([block("x = 1")])
    state = fresh_state(code="x = 1/0", failure_class="runtime", exec_result=runtime_error_result())

    update = make_repair(deps_with(llm=llm))(state)

    assert update["code"] == "x = 1"
    assert update["budget"].total_tokens > 0


# --- give_up ------------------------------------------------------------


def test_give_up_message_names_every_attempt_and_the_spend():
    state = fresh_state(
        attempt=3,
        failure_class="runtime",
        exec_result=runtime_error_result(),
        history=[
            Attempt(attempt=1, code_sha256="a", failure_class="syntax", stderr_excerpt="SyntaxError: bad"),
            Attempt(attempt=2, code_sha256="b", failure_class="runtime", stderr_excerpt="KeyError: 'x'"),
            Attempt(attempt=3, code_sha256="c", failure_class="runtime", stderr_excerpt="KeyError: 'x'"),
        ],
    )

    update = make_give_up()(state)

    message = update["final_output"].message
    assert update["gave_up"] is True
    assert update["give_up_reason"] == "max_attempts"
    assert "3 of 3 attempts" in message
    assert "SyntaxError: bad" in message
    assert "KeyError" in message
    assert "tokens" in message


def test_give_up_reason_is_terminal_for_a_timeout():
    state = fresh_state(attempt=1, failure_class="timeout", exec_result=timeout_result())

    update = make_give_up()(state)

    assert update["give_up_reason"] == "terminal_failure"
    assert "timed out" in update["final_output"].message


def test_give_up_reason_is_budget_when_a_ceiling_was_hit():
    state = fresh_state(
        attempt=1,
        failure_class="runtime",
        exec_result=runtime_error_result(),
        budget=Budget(model="llama-3.3-70b-versatile", max_total_tokens=10),
    )
    state.budget.charge_tokens(20, 0)

    update = make_give_up()(state)

    assert update["give_up_reason"] == "budget"
    assert "token ceiling" in update["final_output"].message
