"""Streamlit UI: streamlit run demo/app.py"""

from __future__ import annotations

import time

import streamlit as st

import config
from agent.graph import build_graph, recursion_limit_for
from agent.llm import GroqCodegen
from agent.nodes import LoopDeps
from agent.state import AgentState, Budget
from renderers.dispatch import dispatch
from sandbox import get_backend

st.set_page_config(page_title="Sandbox Code Agent", layout="centered")


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
    backend = get_backend()
    deps = LoopDeps(llm=llm, backend=backend)

    state = AgentState(
        request=request, lang=lang, max_attempts=max_attempts, budget=Budget(model=llm.model)
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

badge_col = st.columns([3, 1])[1]
with badge_col:
    st.markdown(
        f"<div style='text-align:right'><code>{config.SANDBOX_BACKEND}</code> · "
        f"<code>{config.CODEGEN_MODEL}</code></div>",
        unsafe_allow_html=True,
    )

request = st.text_area(
    "Request",
    height=100,
    placeholder="e.g. Plot the distribution of ages in the attached CSV",
)

running = st.session_state.get("running", False)
run_clicked = st.button("Run", disabled=running, type="primary")

if run_clicked and request.strip():
    st.session_state["running"] = True
    started = time.perf_counter()

    with st.status("Running…", expanded=True) as status_box:
        final_state = run_with_status(request, "python", 3, status_box)
        status_box.update(
            label="Done" if final_state.final_output and final_state.final_output.success else "Failed",
            state="complete" if final_state.final_output and final_state.final_output.success else "error",
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
