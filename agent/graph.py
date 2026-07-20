"""LangGraph wiring for the loop, plus the termination guarantees around it."""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

import config
from agent.llm import CodegenLLM, GroqCodegen
from agent.nodes import (
    LoopDeps,
    make_evaluate,
    make_execute,
    make_generate,
    make_give_up,
    make_repair,
)
from agent.state import TERMINAL, AgentState, Budget
from sandbox import get_backend
from sandbox.base import SandboxBackend

log = logging.getLogger("agent.loop")

# Per loop iteration the graph visits execute → evaluate → repair, plus the one
# generate at the head and give_up at the tail. This is a backstop below
# LangGraph's default of 25, not the real cap: the real cap is that no edge
# leads to repair once `attempt` reaches `max_attempts`.
def recursion_limit_for(max_attempts: int) -> int:
    return 3 * max_attempts + 4


def route_after_evaluate(state: AgentState) -> str:
    """The only place a retry can be authorised — and it checks every ceiling."""
    if state.failure_class == "none":
        return "success"
    if state.failure_class in TERMINAL:
        return "give_up"
    if state.attempt >= state.max_attempts:
        return "give_up"
    if state.budget.exhausted():
        return "give_up"
    return "repair"


def build_graph(deps: LoopDeps):
    builder = StateGraph(AgentState)

    builder.add_node("generate", make_generate(deps))
    builder.add_node("execute", make_execute(deps))
    builder.add_node("evaluate", make_evaluate(deps))
    builder.add_node("repair", make_repair(deps))
    builder.add_node("give_up", make_give_up(deps))

    builder.add_edge(START, "generate")
    builder.add_edge("generate", "execute")
    builder.add_edge("execute", "evaluate")
    builder.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {"success": END, "repair": "repair", "give_up": "give_up"},
    )
    builder.add_edge("repair", "execute")
    builder.add_edge("give_up", END)

    return builder.compile()


def run_request(
    request: str,
    *,
    lang: str = "python",
    llm: CodegenLLM | None = None,
    backend: SandboxBackend | None = None,
    max_attempts: int = 3,
    budget: Budget | None = None,
) -> AgentState:
    """Run one request through the loop and return the final state."""
    llm = llm or GroqCodegen()
    deps = LoopDeps(llm=llm, backend=backend or get_backend())

    state = AgentState(
        request=request,
        lang=lang,
        max_attempts=max_attempts,
        budget=budget or Budget(model=getattr(llm, "model", config.CODEGEN_MODEL)),
    )

    graph = build_graph(deps)
    final = graph.invoke(
        state, config={"recursion_limit": recursion_limit_for(max_attempts)}
    )
    result = AgentState.model_validate(final)

    log.info(
        "run finished: attempts=%d gave_up=%s reason=%s tokens=%d cost=$%.5f sandbox=%.1fs",
        result.attempt,
        result.gave_up,
        result.give_up_reason,
        result.budget.total_tokens,
        result.budget.cost_usd,
        result.budget.sandbox_seconds,
    )
    return result
