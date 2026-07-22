"""Streamlit UI: streamlit run demo/app.py"""

from __future__ import annotations

import time
import uuid

import streamlit as st

import config
from agent.graph import build_graph, recursion_limit_for
from agent.llm import GroqCodegen
from agent.nodes import LoopDeps
from agent.state import AgentState, Budget
from hosting import (
    RateLimiter,
    SpendLedger,
    check_passphrase,
    hosted_budget,
    load_hosted_config,
    resolve_hosted_backend,
)
from renderers.dispatch import dispatch
from sandbox import get_backend

st.set_page_config(page_title="Sandbox Code Agent", layout="centered")

HOSTED = load_hosted_config()


def status_line(node_name: str, state: AgentState) -> str:
    if node_name == "generate":
        return f"Attempt {state.attempt + 1} — generating…"
    if node_name == "execute":
        return f"Attempt {state.attempt} — executing…"
    if node_name == "evaluate":
        if state.failure_class == "none":
            return f"Attempt {state.attempt} — succeeded."
        return f"Attempt {state.attempt} — failed: {state.failure_class}"
    if node_name == "repair":
        return f"Attempt {state.attempt} — repairing…"
    if node_name == "give_up":
        return f"Gave up: {state.give_up_reason}"
    return node_name


def run_with_status(request: str, lang: str, max_attempts: int, status_box) -> AgentState:
    llm = GroqCodegen()
    backend = get_backend(BACKEND_NAME)
    deps = LoopDeps(llm=llm, backend=backend)

    budget = (
        hosted_budget(HOSTED, llm.model) if HOSTED.enabled else Budget(model=llm.model)
    )
    state = AgentState(
        request=request, lang=lang, max_attempts=max_attempts, budget=budget
    )
    graph = build_graph(deps)

    for step in graph.stream(
        state, config={"recursion_limit": recursion_limit_for(max_attempts)}
    ):
        for node_name, update in step.items():
            state = state.model_copy(update=update)
            status_box.write(status_line(node_name, state))

    return state


st.title("Sandbox Code Agent")
st.caption("natural language → code → sandboxed execution → result")

# A public code-execution endpoint that is misconfigured must not start at all —
# the failure mode of "quietly ran without a passphrase" is the whole risk.
problems = HOSTED.misconfigurations()
if problems:
    st.error("Refusing to start in public-demo mode:")
    for problem in problems:
        st.markdown(f"- {problem}")
    st.stop()

try:
    BACKEND_NAME = (
        resolve_hosted_backend(config.SANDBOX_BACKEND)
        if HOSTED.enabled
        else config.SANDBOX_BACKEND
    )
except RuntimeError as exc:
    st.error(f"Refusing to start in public-demo mode: {exc}")
    st.stop()

ledger = (
    SpendLedger(HOSTED.state_path, HOSTED.daily_spend_cap_usd) if HOSTED.enabled else None
)
limiter = (
    RateLimiter(HOSTED.state_path, HOSTED.rate_limit_runs, HOSTED.rate_limit_window_s)
    if HOSTED.enabled
    else None
)

badge_col = st.columns([3, 1])[1]
with badge_col:
    st.markdown(
        f"<div style='text-align:right'><code>{BACKEND_NAME}</code> · "
        f"<code>{config.CODEGEN_MODEL}</code></div>",
        unsafe_allow_html=True,
    )

if HOSTED.enabled:
    if "session_id" not in st.session_state:
        st.session_state["session_id"] = uuid.uuid4().hex

    if not st.session_state.get("authed", False):
        # A plain text_input only commits its value on Enter/blur, so clicking a
        # separate button submits an empty string. A form commits both together.
        with st.form("gate"):
            supplied = st.text_input("Demo passphrase", type="password")
            if st.form_submit_button("Enter"):
                if check_passphrase(supplied, HOSTED.passphrase):
                    st.session_state["authed"] = True
                    st.rerun()
                else:
                    st.error("Incorrect passphrase.")
        st.stop()

    if ledger.exhausted():
        st.error(
            "The demo's daily spend cap has been reached, so runs are paused until "
            "tomorrow. The code, benchmarks, and results are all in the repo."
        )
        st.stop()

    # Runs cost fractions of a cent, so a 2-decimal remaining balance would sit
    # at the cap all day and read as if nothing were being metered.
    st.caption(
        f"Public demo · ${ledger.remaining():.4f} of "
        f"${HOSTED.daily_spend_cap_usd:.2f} daily budget left · "
        f"{HOSTED.rate_limit_runs} runs per "
        f"{HOSTED.rate_limit_window_s // 60} min · "
        f"{HOSTED.max_attempts} attempts max"
    )

request = st.text_area(
    "Request",
    height=100,
    placeholder="e.g. Plot the distribution of ages in the attached CSV",
)

running = st.session_state.get("running", False)
run_clicked = st.button("Run", disabled=running, type="primary")

def admitted() -> bool:
    """Rate limit, then reserve budget. Both must pass before any tokens are spent."""
    if not HOSTED.enabled:
        return True

    allowed, retry_after = limiter.check(st.session_state["session_id"])
    if not allowed:
        st.warning(f"Rate limit reached — try again in {retry_after:.0f}s.")
        return False

    if not ledger.reserve(HOSTED.max_cost_usd):
        st.error("The demo's daily spend cap has been reached. Runs are paused until tomorrow.")
        return False

    return True


if run_clicked and request.strip() and admitted():
    st.session_state["running"] = True
    started = time.perf_counter()
    max_attempts = HOSTED.max_attempts if HOSTED.enabled else 3
    final_state = None

    try:
        with st.status("Running…", expanded=True) as status_box:
            final_state = run_with_status(request, "python", max_attempts, status_box)
            status_box.update(
                label="Done" if final_state.final_output and final_state.final_output.success else "Failed",
                state="complete" if final_state.final_output and final_state.final_output.success else "error",
            )
    finally:
        # Settle even if the run raised: an un-released reservation would leak
        # budget out of the daily cap until midnight.
        if HOSTED.enabled:
            ledger.settle(
                reserved=HOSTED.max_cost_usd,
                actual=(
                    final_state.budget.cost_usd
                    if final_state is not None
                    else HOSTED.max_cost_usd
                ),
            )

    wall_time = time.perf_counter() - started
    st.session_state["running"] = False
    st.session_state["last_state"] = final_state
    st.session_state["last_wall_time"] = wall_time

final_state: AgentState | None = st.session_state.get("last_state")

if final_state is not None and final_state.final_output is not None:
    st.divider()
    outcome = dispatch(final_state.final_output)

    if outcome.redactions:
        st.warning(
            f"Redacted {outcome.redactions} secret-shaped string(s) from the output "
            "before rendering."
        )

    if outcome.kind == "table" and outcome.table_rows is not None:
        st.dataframe(outcome.table_rows)
        if outcome.note:
            st.caption(outcome.note)
    elif outcome.kind == "chart" and outcome.image_bytes is not None:
        st.image(outcome.image_bytes)
        if outcome.note:
            st.caption(outcome.note)
    elif outcome.kind == "file" and outcome.file_bytes is not None:
        st.download_button(
            "Download file",
            data=outcome.file_bytes,
            file_name=outcome.saved_path.name if outcome.saved_path else "output",
        )
        if outcome.note:
            st.caption(outcome.note)
    elif outcome.kind == "malformed":
        st.warning("Structured output unavailable — showing raw stdout.")
        st.code(outcome.raw_stdout)
    elif outcome.kind == "failure":
        st.error(outcome.summary)
    else:
        st.markdown(outcome.summary)

    if final_state.code:
        with st.expander("Show generated code"):
            st.code(final_state.code, language=final_state.lang)

    st.divider()
    wall_time = st.session_state.get("last_wall_time", 0.0)
    st.caption(
        f"{final_state.attempt} attempt(s) · {wall_time:.1f}s wall time · "
        f"${final_state.budget.cost_usd:.5f} · sandbox torn down"
    )
