"""Live loop tests: real code-gen model, real sandbox. Metered — marked slow."""

import pytest

import config
from agent.graph import run_request
from agent.llm import GroqCodegen
from agent.nodes import LoopDeps, build_repair_message, extract_single_block
from agent.state import AgentState, Budget
from sandbox import get_backend
from sandbox.base import ExecResult
from sandbox.docker_backend import docker_available

pytestmark = pytest.mark.slow

# An off-by-one that only bites at runtime: the loop over indices walks one past
# the end, so the model gets a genuine IndexError traceback to repair from.
SEEDED_BUG = """\
import json

values = [3, 1, 4, 1, 5]
total = 0
for i in range(len(values) + 1):
    total += values[i]

envelope = {"type": "text", "data": str(total), "artifact_path": None, "note": None}
print(json.dumps(envelope, default=str))
"""


@pytest.fixture(scope="module")
def backend():
    if docker_available():
        return get_backend("docker")
    if config.E2B_API_KEY:
        return get_backend("e2b")
    pytest.skip("no sandbox backend available")


@pytest.fixture(scope="module")
def llm():
    if not config.GROQ_API_KEY:
        pytest.skip("GROQ_API_KEY not set")
    return GroqCodegen()


def test_seeded_off_by_one_is_repaired_from_its_traceback(llm, backend):
    broken = backend.run(SEEDED_BUG)
    assert broken.exit_code != 0 and "IndexError" in broken.stderr

    deps = LoopDeps(llm=llm, backend=backend)
    state = AgentState(
        request="Sum this list of values and report the total.",
        code=SEEDED_BUG,
        exec_result=broken,
        failure_class="runtime",
        budget=Budget(model=llm.model),
    )

    response = llm.complete(deps.codegen_prompt, build_repair_message(state, deps.repair_prompt))
    repaired = extract_single_block(response.text)
    assert repaired is not None, response.text

    result: ExecResult = backend.run(repaired)

    assert result.exit_code == 0, result.stderr
    assert "14" in result.stdout


def test_end_to_end_request_succeeds_within_the_cap(llm, backend):
    state = run_request(
        "Given the list [12.5, 45.0, 8.75], compute the count, mean, min and max.",
        llm=llm,
        backend=backend,
        budget=Budget(model=llm.model),
    )

    assert state.gave_up is False, state.final_output.message
    assert state.attempt <= state.max_attempts
    assert state.final_output.success is True
    assert state.final_output.envelope is not None
    assert state.budget.cost_usd > 0


def test_impossible_request_stops_cleanly_at_the_cap(llm, backend):
    state = run_request(
        "Write a program whose last stdout line is the SHA-256 of the current "
        "Bitcoin block hash fetched live from blockchain.info.",
        llm=llm,
        backend=backend,
        budget=Budget(model=llm.model),
    )

    assert state.attempt <= state.max_attempts
    assert state.final_output is not None
    if state.gave_up:
        assert state.final_output.message
        assert state.give_up_reason in ("max_attempts", "terminal_failure", "budget")
