"""One-shot CLI runner: python -m demo.cli "plot a sine wave" """

from __future__ import annotations

import argparse
import sys

import config
from agent.graph import run_request
from renderers.dispatch import dispatch
from renderers.outcome import RenderOutcome


def render_outcome(outcome: RenderOutcome) -> None:
    if outcome.kind == "malformed":
        print("--- structured output unavailable — raw stdout ---")
        print(outcome.raw_stdout)
        return
    print(outcome.summary)
    if outcome.kind == "table" and outcome.table_rows is not None:
        print(f"\n({len(outcome.table_rows)} row(s))")
    if outcome.redactions:
        print(f"\n[redacted {outcome.redactions} secret-shaped string(s) from output]")


def print_attempt_log(state) -> None:
    print("\n--- attempts ---")
    for record in state.history:
        print(
            f"  {record.attempt}. {record.failure_class} "
            f"sha={record.code_sha256} {record.duration_ms}ms "
            f"tokens={record.input_tokens}/{record.output_tokens} "
            f"cost=${record.cost_usd:.5f}"
        )
    print(
        f"\ntotal: {state.attempt} attempt(s), "
        f"{state.budget.total_tokens} tokens, "
        f"${state.budget.cost_usd:.5f}, "
        f"{state.budget.sandbox_seconds:.1f}s sandbox time"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo.cli")
    parser.add_argument("request", help="natural language request")
    parser.add_argument("--lang", default="python", choices=["python", "js"])
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args(argv)

    print(f"backend={config.SANDBOX_BACKEND} model={config.CODEGEN_MODEL}")
    print(f"request: {args.request}\n")

    state = run_request(args.request, lang=args.lang, max_attempts=args.max_attempts)

    if state.final_output is None:
        print("no output was produced")
        print_attempt_log(state)
        return 1

    outcome = dispatch(state.final_output)
    render_outcome(outcome)
    print_attempt_log(state)

    return 0 if state.final_output.success else 1


if __name__ == "__main__":
    sys.exit(main())
