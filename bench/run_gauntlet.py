"""Phase 6 measurement harness: run bench/requests.jsonl through the real loop.

Uses the Docker backend deliberately (deterministic, no E2B metering) and the
real Groq codegen model — this is the first *execution-based* eval of the
prompt (Phase 1 was static-only, shape-only checks). A script, not a pytest
test: it spends real tokens and sandbox time, so it runs on demand.

Usage:
    python -m bench.run_gauntlet --label baseline
    python -m bench.run_gauntlet --label postfix --codegen-prompt prompts/codegen.v2.md
    python -m bench.run_gauntlet --label smoke --ids da-1,ch-1,sc-1
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from agent.graph import build_graph, recursion_limit_for
from agent.llm import GroqCodegen
from agent.nodes import LoopDeps
from agent.state import AgentState, Budget
from sandbox.docker_backend import DockerBackend, docker_available

BENCH_DIR = Path(__file__).resolve().parent
ROOT_DIR = BENCH_DIR.parent
REQUESTS_PATH = BENCH_DIR / "requests.jsonl"
RESULTS_DIR = BENCH_DIR / "results"
DEFAULT_CODEGEN_PROMPT = ROOT_DIR / "prompts" / "codegen.v3.md"
DEFAULT_REPAIR_PROMPT = ROOT_DIR / "prompts" / "repair.v1.md"


def load_requests(ids: list[str] | None = None) -> list[dict]:
    lines = [ln for ln in REQUESTS_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    requests = [json.loads(ln) for ln in lines]
    if ids:
        wanted = set(ids)
        requests = [r for r in requests if r["id"] in wanted]
    return requests


def build_message(req: dict) -> str:
    if req.get("data"):
        return f"{req['request']}\n\n<data>\n{req['data']}\n</data>"
    return req["request"]


def run_one(message: str, deps: LoopDeps, max_attempts: int, budget_model: str) -> tuple[AgentState, float]:
    state = AgentState(
        request=message,
        max_attempts=max_attempts,
        budget=Budget(model=budget_model),
    )
    graph = build_graph(deps)
    started = time.perf_counter()
    final = graph.invoke(state, config={"recursion_limit": recursion_limit_for(max_attempts)})
    wall_s = time.perf_counter() - started
    return AgentState.model_validate(final), wall_s


def classify_outcome(state: AgentState) -> str:
    if state.final_output is not None and state.final_output.success:
        return "first_attempt_success" if state.attempt <= 1 else "success_after_repair"
    return "hard_fail"


def summarize(records: list[dict]) -> dict:
    if not records:
        return {}
    n = len(records)
    first = sum(r["outcome"] == "first_attempt_success" for r in records)
    repaired = sum(r["outcome"] == "success_after_repair" for r in records)
    failed = sum(r["outcome"] == "hard_fail" for r in records)
    return {
        "count": n,
        "first_attempt_success_rate": round(first / n, 3),
        "success_after_repair_rate": round(repaired / n, 3),
        "hard_fail_rate": round(failed / n, 3),
        "mean_attempts": round(statistics.mean(r["attempts"] for r in records), 2),
        "mean_wall_s": round(statistics.mean(r["wall_s"] for r in records), 2),
        "mean_cost_usd": round(statistics.mean(r["cost_usd"] for r in records), 5),
        "total_cost_usd": round(sum(r["cost_usd"] for r in records), 5),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.run_gauntlet")
    parser.add_argument("--label", required=True, help="name for this pass, e.g. baseline / postfix")
    parser.add_argument("--codegen-prompt", default=str(DEFAULT_CODEGEN_PROMPT))
    parser.add_argument("--repair-prompt", default=str(DEFAULT_REPAIR_PROMPT))
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--ids", default=None, help="comma-separated request ids to run (default: all)")
    parser.add_argument("--out", default=None, help="output JSON path (default: bench/results/<label>.json)")
    parser.add_argument(
        "--resume", action="store_true",
        help="skip ids already recorded in <label>.partial.jsonl and append to it",
    )
    args = parser.parse_args()

    if not config.GROQ_API_KEY:
        raise SystemExit("GROQ_API_KEY not set — cannot run the gauntlet")
    if not docker_available():
        raise SystemExit("Docker daemon not reachable — start Docker Desktop first")

    ids = args.ids.split(",") if args.ids else None
    requests = load_requests(ids)
    if not requests:
        raise SystemExit("no matching requests found")

    backend = DockerBackend()
    backend.ensure_image()
    llm = GroqCodegen()
    codegen_prompt_path = Path(args.codegen_prompt)
    repair_prompt_path = Path(args.repair_prompt)
    deps = LoopDeps(
        llm=llm,
        backend=backend,
        codegen_prompt=codegen_prompt_path.read_text(encoding="utf-8"),
        repair_prompt=repair_prompt_path.read_text(encoding="utf-8"),
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else RESULTS_DIR / f"{args.label}.json"
    partial_path = RESULTS_DIR / f"{args.label}.partial.jsonl"

    records: list[dict] = []
    if args.resume and partial_path.exists():
        records = [json.loads(ln) for ln in partial_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        done_ids = {r["id"] for r in records}
        requests = [r for r in requests if r["id"] not in done_ids]
        print(f"resuming: {len(records)} already done, {len(requests)} remaining", flush=True)
    elif not args.resume:
        partial_path.write_text("", encoding="utf-8")

    partial_f = partial_path.open("a", encoding="utf-8")
    for i, req in enumerate(requests, 1):
        message = build_message(req)
        print(f"[{i}/{len(requests)}] {req['id']} ({req['category']}) ...", flush=True)
        state, wall_s = run_one(message, deps, args.max_attempts, llm.model)
        outcome = classify_outcome(state)
        attempts_detail = [
            {"attempt": a.attempt, "failure_class": a.failure_class, "stderr_excerpt": a.stderr_excerpt[-500:]}
            for a in state.history
        ]
        last_stderr = state.history[-1].stderr_excerpt if state.history else ""
        envelope_type = (
            state.final_output.envelope.type
            if state.final_output and state.final_output.envelope
            else None
        )
        record = {
            "id": req["id"],
            "category": req["category"],
            "hard": req.get("hard", False),
            "fail_first": req.get("fail_first", False),
            "outcome": outcome,
            "attempts": state.attempt,
            "gave_up": state.gave_up,
            "give_up_reason": state.give_up_reason,
            "failure_classes": [a.failure_class for a in state.history],
            "attempts_detail": attempts_detail,
            "envelope_type": envelope_type,
            "last_stderr_excerpt": last_stderr[-500:],
            "give_up_message": state.final_output.message if (state.gave_up and state.final_output) else "",
            "tokens": state.budget.total_tokens,
            "cost_usd": round(state.budget.cost_usd, 6),
            "wall_s": round(wall_s, 2),
            "sandbox_seconds": round(state.budget.sandbox_seconds, 2),
        }
        records.append(record)
        partial_f.write(json.dumps(record) + "\n")
        partial_f.flush()
        print(
            f"    -> {outcome} (attempts={record['attempts']}, "
            f"${record['cost_usd']:.5f}, {record['wall_s']:.1f}s)",
            flush=True,
        )
    partial_f.close()

    by_category: dict[str, dict] = {}
    for cat in sorted({r["category"] for r in records}):
        by_category[cat] = summarize([r for r in records if r["category"] == cat])

    output = {
        "label": args.label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "codegen_prompt": str(codegen_prompt_path),
        "repair_prompt": str(repair_prompt_path),
        "codegen_model": llm.model,
        "max_attempts": args.max_attempts,
        "overall": summarize(records),
        "by_category": by_category,
        "requests": records,
    }

    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"\n=== {args.label} ===")
    print(json.dumps(output["overall"], indent=2))
    print("\nby category:")
    print(json.dumps(by_category, indent=2))
    print(f"\nwrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
