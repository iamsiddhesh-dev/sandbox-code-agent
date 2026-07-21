"""The five graph nodes: generate, execute, evaluate, repair, give_up."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from agent.llm import CodegenLLM, LLMResponse
from agent.state import (
    TERMINAL,
    Attempt,
    AgentState,
    FailureClass,
    GiveUpReason,
    RenderedResult,
)
from renderers.envelope import envelope_from_stdout
from sandbox.base import ExecResult, SandboxBackend

log = logging.getLogger("agent.loop")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
CODEGEN_PROMPT_PATH = PROMPTS_DIR / "codegen.v1.md"
REPAIR_PROMPT_PATH = PROMPTS_DIR / "repair.v1.md"

FENCE_RE = re.compile(r"```(?:python|javascript|js)?\n(.*?)```", re.DOTALL)

TIMEOUT_EXIT_CODE = 124
SYNTAX_MARKERS = ("SyntaxError", "IndentationError", "TabError")

# Signals that the sandbox refused the program, not that the program was buggy.
# Repairing these is the wrong move: the boundary held, and asking the model to
# try again just spends tokens arguing with the kernel.
SECURITY_MARKERS = (
    "Network is unreachable",
    "Temporary failure in name resolution",
    "Name or service not known",
    "socket.gaierror",
    "nodename nor servname",
    "Operation not permitted",
    "Read-only file system",
    "urlopen error",
    "Errno 101",
    "Errno -2",
    "Errno -3",
)

NO_BLOCK_STDERR = "the model did not emit exactly one fenced code block"

FAILURE_NOTES: dict[str, str] = {
    "envelope": (
        "## What specifically went wrong\n\n"
        "The program ran to completion, but its final stdout line was not a "
        "valid output envelope. Reprint the envelope as the last line of "
        "stdout, as a single line of JSON built with "
        "`json.dumps(..., default=str)`, and print nothing after it."
    ),
    "syntax": (
        "## What specifically went wrong\n\n"
        "The program never ran — it failed to parse. Fix the syntax at the "
        "reported line and leave the rest of the program alone."
    ),
    "runtime": "",
    "none": "",
}

NO_BLOCK_NOTE = (
    "## What specifically went wrong\n\n"
    "Your previous answer did not contain exactly one fenced ```python "
    "block. Answer with exactly one fenced block containing the complete "
    "program, and no prose outside it."
)


def sha(code: str | None) -> str:
    return hashlib.sha256((code or "").encode()).hexdigest()[:12]


def extract_single_block(text: str) -> str | None:
    """Return the sole fenced block, or None if there are zero or several."""
    blocks = FENCE_RE.findall(text)
    if len(blocks) != 1:
        return None
    return blocks[0].strip("\n")


def classify(result: ExecResult) -> FailureClass:
    """Map an execution result onto the failure taxonomy."""
    if result.stderr == NO_BLOCK_STDERR:
        return "envelope"
    if result.timed_out or result.exit_code == TIMEOUT_EXIT_CODE:
        return "timeout"

    stderr = result.stderr
    if any(marker in stderr for marker in SECURITY_MARKERS):
        return "security"

    if result.exit_code != 0:
        if any(marker in stderr for marker in SYNTAX_MARKERS):
            return "syntax"
        return "runtime"

    if envelope_from_stdout(result.stdout) is None:
        return "envelope"
    return "none"


def build_user_message(state: AgentState) -> str:
    return state.request


def build_repair_message(state: AgentState, template: str) -> str:
    result = state.exec_result
    stderr = (result.stderr if result else "") or "(no stderr captured)"
    envelope_failure_with_code = (
        state.failure_class == "envelope" and result is not None and state.code is not None
    )
    if envelope_failure_with_code:
        tail = result.stdout.strip().splitlines()[-1:] or ["(nothing was printed)"]
        stderr = (
            "The program exited 0, so there is no traceback. Its last stdout "
            f"line was: {tail[0]!r}"
        )

    if state.code is None:
        note = NO_BLOCK_NOTE
    else:
        note = FAILURE_NOTES.get(state.failure_class or "runtime", "")

    return (
        template.replace("{request}", state.request)
        .replace("{lang}", state.lang)
        .replace("{code}", state.code or "(no code was produced)")
        .replace("{stderr}", stderr[-4000:])
        .replace("{failure_note}", note)
    )


@dataclass
class LoopDeps:
    """Everything the nodes talk to, injected so tests can swap it all out."""

    llm: CodegenLLM
    backend: SandboxBackend
    codegen_prompt: str = ""
    repair_prompt: str = ""

    def __post_init__(self) -> None:
        if not self.codegen_prompt:
            self.codegen_prompt = CODEGEN_PROMPT_PATH.read_text(encoding="utf-8")
        if not self.repair_prompt:
            self.repair_prompt = REPAIR_PROMPT_PATH.read_text(encoding="utf-8")


def _charge(state: AgentState, response: LLMResponse) -> dict:
    cost = state.budget.charge_tokens(response.input_tokens, response.output_tokens)
    return {
        "budget": state.budget,
        "pending_input_tokens": response.input_tokens,
        "pending_output_tokens": response.output_tokens,
        "pending_cost_usd": cost,
    }


def make_generate(deps: LoopDeps):
    def generate(state: AgentState) -> dict:
        response = deps.llm.complete(deps.codegen_prompt, build_user_message(state))
        code = extract_single_block(response.text)
        log.info(
            "generate model=%s tokens=%d/%d block=%s",
            getattr(deps.llm, "model", "?"),
            response.input_tokens,
            response.output_tokens,
            "yes" if code else "none-or-multiple",
        )
        return {"code": code, **_charge(state, response)}

    return generate


def make_execute(deps: LoopDeps):
    def execute(state: AgentState) -> dict:
        attempt_no = state.attempt + 1

        if state.code is None:
            # No runnable code, but this still burns an attempt — otherwise a
            # model that never emits a block would loop forever.
            result = ExecResult(
                stdout="", stderr=NO_BLOCK_STDERR, exit_code=1, timed_out=False
            )
            elapsed_ms = 0
        else:
            started = time.perf_counter()
            result = deps.backend.run(
                state.code, state.lang, timeout_s=state.budget.per_run_timeout_s
            )
            elapsed = time.perf_counter() - started
            elapsed_ms = int(elapsed * 1000)
            state.budget.charge_sandbox(elapsed)

        attempt = Attempt(
            attempt=attempt_no,
            code_sha256=sha(state.code),
            failure_class="none",
            duration_ms=elapsed_ms,
            input_tokens=state.pending_input_tokens,
            output_tokens=state.pending_output_tokens,
            cost_usd=state.pending_cost_usd,
        )

        return {
            "exec_result": result,
            "attempt": attempt_no,
            "history": [*state.history, attempt],
            "budget": state.budget,
            "pending_input_tokens": 0,
            "pending_output_tokens": 0,
            "pending_cost_usd": 0.0,
        }

    return execute


def make_evaluate(deps: LoopDeps | None = None):
    def evaluate(state: AgentState) -> dict:
        result = state.exec_result
        if result is None:
            raise RuntimeError("evaluate ran before execute")

        failure_class = classify(result)

        history = list(state.history)
        if history:
            last = history[-1].model_copy(
                update={
                    "failure_class": failure_class,
                    "stderr_excerpt": result.stderr[-500:],
                }
            )
            history[-1] = last

        log.info(
            "attempt=%d sha=%s class=%s exit=%d ms=%d cost=$%.5f",
            state.attempt,
            sha(state.code),
            failure_class,
            result.exit_code,
            history[-1].duration_ms if history else 0,
            state.budget.cost_usd,
        )

        update: dict = {"failure_class": failure_class, "history": history}
        if failure_class == "none":
            envelope = envelope_from_stdout(result.stdout)
            update["final_output"] = RenderedResult(
                success=True,
                envelope=envelope,
                files=result.files,
                message="ok",
                raw_stdout=result.stdout,
            )
        return update

    return evaluate


def make_repair(deps: LoopDeps):
    def repair(state: AgentState) -> dict:
        message = build_repair_message(state, deps.repair_prompt)
        response = deps.llm.complete(deps.codegen_prompt, message)
        code = extract_single_block(response.text)
        log.info(
            "repair attempt=%d prior_class=%s tokens=%d/%d",
            state.attempt,
            state.failure_class,
            response.input_tokens,
            response.output_tokens,
        )
        return {"code": code, **_charge(state, response)}

    return repair


def make_give_up(deps: LoopDeps | None = None):
    def give_up(state: AgentState) -> dict:
        reason = state.give_up_reason or _infer_reason(state)
        message = compose_give_up_message(state, reason)
        log.warning(
            "gave up after %d attempt(s): %s | tokens=%d cost=$%.5f sandbox=%.1fs",
            state.attempt,
            reason,
            state.budget.total_tokens,
            state.budget.cost_usd,
            state.budget.sandbox_seconds,
        )
        return {
            "gave_up": True,
            "give_up_reason": reason,
            "final_output": RenderedResult(
                success=False,
                envelope=None,
                files=state.exec_result.files if state.exec_result else {},
                message=message,
                raw_stdout=state.exec_result.stdout if state.exec_result else "",
            ),
        }

    return give_up


def _infer_reason(state: AgentState) -> GiveUpReason:
    if state.failure_class in TERMINAL:
        return "terminal_failure"
    if state.budget.exhausted():
        return "budget"
    return "max_attempts"


def compose_give_up_message(state: AgentState, reason: str) -> str:
    """An honest failure report: what was tried, what broke, and why we stopped."""
    headline = {
        "max_attempts": (
            f"Gave up after {state.attempt} of {state.max_attempts} attempts — "
            "the generated code kept failing."
        ),
        "budget": (
            f"Stopped after {state.attempt} attempt(s): "
            f"{state.budget.exhausted() or 'budget ceiling reached'}."
        ),
        "terminal_failure": (
            f"Stopped after {state.attempt} attempt(s): the sandbox "
            f"{'timed out running the code' if state.failure_class == 'timeout' else 'blocked the code'}, "
            "which retrying would not fix."
        ),
    }[reason]

    lines = [headline, "", "Attempts:"]
    for record in state.history:
        excerpt = record.stderr_excerpt.strip().splitlines()
        detail = excerpt[-1] if excerpt else "(no stderr)"
        lines.append(
            f"  {record.attempt}. {record.failure_class} "
            f"({record.duration_ms} ms) — {detail[:160]}"
        )
    lines += [
        "",
        f"Spent {state.budget.total_tokens} tokens "
        f"(${state.budget.cost_usd:.5f}) and "
        f"{state.budget.sandbox_seconds:.1f}s of sandbox time.",
    ]
    return "\n".join(lines)
