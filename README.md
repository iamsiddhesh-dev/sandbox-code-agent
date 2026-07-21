# Sandbox Code Agent

A natural-language → code → sandboxed execution → rendered result system. Users describe what they need ("plot the distribution of X", "clean this CSV", "write me a script that does Y"), and the agent generates Python or JavaScript, runs it inside a locked-down sandbox, recovers automatically when code fails, and returns a chart, table, or working script.

## Architecture

```
User Request (untrusted text, <data> delimited)
    ↓
┌─────────────────────────────────────────────┐
│  agent/graph.py — LangGraph state machine     │
│                                               │
│   generate ──▶ execute ──▶ evaluate           │
│                              │                │
│              success ────────┼──── END        │
│                              │                │
│              repairable ─────┤                │
│                 │            │                │
│                 ▼            │                │
│              repair ─────────┘                │
│                 (loops back to execute)        │
│                              │                │
│              terminal/cap ───┴──▶ give_up ──▶ END │
└─────────────────────────────────────────────┘
    ↓                                    ↑
[Sandbox: E2B microVM /               attempt < max_attempts
 Docker --network=none container]    AND budget not exhausted
    ↓
renderers/dispatch.py → table | chart | text | file | malformed-fallback
```

Every `execute` is one real run of generated code inside the sandbox — no
mocking, no static-only checks. `evaluate` classifies the result into
`syntax` / `runtime` / `envelope` (repairable — fed back with the exact
traceback) or `timeout` / `security` (terminal — the boundary held, so
retrying would only spend tokens arguing with the kernel). Termination is
structural: `attempt` is incremented in `execute` and no graph edge leads
back to `repair` once `attempt == max_attempts` or the per-request `Budget`
(tokens, cost, sandbox seconds) is exhausted — a run that hits either ceiling
fails closed through the same graceful `give_up` node, never a silent hang.

## Features

- **Reliable code generation** with format-constrained output (JSON envelope contract)
- **Real sandbox isolation** via E2B microVMs or hardened Docker containers
- **Self-healing generation/execute/retry loop** with hard retry caps
- **Multi-format output rendering**: tables, charts, text, scripts/files
- **Prompt injection defense** with defense-in-depth (prompt boundaries + sandbox isolation)
- **Budget guardrails** to prevent runaway repair loops and metered sandbox credit drain

## Tech Stack

- **Orchestration:** LangGraph for generate→execute→retry state graph
- **Sandbox:** E2B (primary) or Docker (fallback) behind a swappable interface
- **Code generation:** Groq `llama-3.3-70b-versatile` (fast, free-tier inference; repair loop absorbs quality gaps)
- **LLM orchestration:** Fable 5 (Claude Agent SDK)

## The Prompt Contract

The system prompt (`prompts/codegen.v*.md`) is versioned, never edited in place —
each fix gets a new file so results stay diffable against what produced them.
`codegen.v3.md` is the current production default
([`agent/nodes.py`](agent/nodes.py)); `v1`/`v2` remain as the record of what
Phase 6 found and fixed (see [RESULTS.md](RESULTS.md)). Every version enforces
the same hard contract: one fenced Python block, no prose, and a single-line
JSON envelope (`{"type": "table"|"chart"|"text"|"file", "data": ..., "artifact_path": ..., "note": ...}`)
as the last line of stdout — that contract is what makes rendering
deterministic instead of free-form text parsing.

## Quick Start

### Install

```bash
# Clone the repo
git clone https://github.com/iamsiddhesh-dev/sandbox-code-agent
cd sandbox-code-agent

# Create .env from example
cp .env.example .env
# Edit .env with your API keys

# Install dependencies
pip install -e .
```

### Run Hello World

```bash
python hello_world.py
```

This runs a hardcoded snippet through the sandbox stub and prints output.

### Run Code in a Sandbox

```python
from sandbox import get_backend

backend = get_backend()          # honors SANDBOX_BACKEND ("e2b" | "docker" | "fake")
result = backend.run('print("hello")', timeout_s=30)
print(result.stdout, result.exit_code, result.timed_out, list(result.files))
```

Anything the code writes to `/output` comes back as bytes in `result.files`.

### Run the Agent Loop

```python
from agent.graph import run_request

state = run_request("Given [12.5, 45.0, 8.75], compute the count, mean, min and max.")
print(state.final_output.envelope)
print(state.attempt, state.gave_up, state.budget.cost_usd)
```

The loop is `generate → execute → evaluate → (repair → execute)* → END`. Every
failure is classified as `syntax`, `runtime`, `envelope`, `timeout`, or
`security`; the first three are repaired from the exact traceback, while a
sandbox timeout or a blocked syscall skips repair entirely — the boundary held,
and retrying only spends tokens. Termination is structural: `attempt` is
incremented in `execute` and no edge leads back to `repair` once it reaches
`max_attempts`.

Each run carries a `Budget` that meters cumulative tokens, estimated cost, and
sandbox seconds against per-request ceilings. Hitting any ceiling aborts through
the same graceful give-up path as an exhausted retry cap — the run fails closed
with an honest report of every attempt and what it cost, never a silent hang.

The Docker backend needs a running Docker daemon; its image is built automatically
on first use from [sandbox/Dockerfile.sandbox](sandbox/Dockerfile.sandbox), or ahead
of time with:

```bash
docker build -f sandbox/Dockerfile.sandbox -t sandbox-code-agent:latest sandbox/
```

### Run Tests

```bash
pytest                # unit tests only
pytest -m slow        # sandbox- and LLM-backed tests against real E2B, Docker, and Groq
```

Tests that touch a real sandbox or the code-gen model are marked `slow` and
excluded by default because they are metered (E2B credits, Groq tokens) and take
a couple of minutes.

### Reproduce the Gauntlet metrics

```bash
python -m bench.run_gauntlet --label mypass                 # all 30 requests, current prompt
python -m bench.run_gauntlet --label mypass --resume        # continue after a rate-limit interruption
python -m bench.run_gauntlet --label mypass --ids da-1,ch-1 # a subset, for a quick check
```

Requires a running Docker daemon and `GROQ_API_KEY`. Writes a reproducible
`bench/results/<label>.json` (per-request and per-category metrics) — see
[RESULTS.md](RESULTS.md) for the numbers this produced. Note: Groq's free tier
caps `llama-3.3-70b-versatile` at 100,000 tokens/day, and one full 30-request
pass can use most of that; `--resume` picks up where a rate-limited run left off.

### Run Demo

```bash
# CLI demo (one-shot)
python -m demo.cli "plot a sine wave"

# Streamlit UI (interactive)
streamlit run demo/app.py
```

The CLI prints the rendered result plus the attempt log (failure class, duration,
tokens, cost per attempt). The Streamlit UI streams one status line per node
transition (`Attempt 1 — generating…` / `executing…` / `succeeded.`), then
renders the result by type — `st.dataframe` for tables, `st.image` for charts,
`st.download_button` for files/scripts, markdown for text — with a "Show
generated code" expander and a footer of attempts/wall time/cost.

Both entry points route through [`renderers/dispatch.py`](renderers/dispatch.py):
envelope type → renderer, and a malformed/missing envelope degrades to raw
stdout with a banner instead of crashing.

### Scope

- **Python is the primary and only working target.** Every prompt, few-shot,
  and benchmark request in `bench/requests.jsonl` is Python. **JavaScript is
  interface-ready, not built** — `lang="js"` is plumbed through the sandbox
  and agent state end to end, but no codegen prompt instructs the model to
  write JS and no JS eval exists. Wiring it up is a matter of adding a prompt
  variant and few-shots, not changing the loop or sandbox.
- **CSV upload was cut.** The Streamlit layout in the plan calls for a file
  uploader, but `SandboxBackend.run()` only supports code in / files out —
  no input-file path into either backend. Wiring that through both backends
  (and the boundary tests that would need to come with it) was out of scope;
  requests that need input data pass it inline in the request text instead
  (see `bench/requests.jsonl` for the pattern — CSV content embedded between
  `<data>` tags).
- **Local demo, not hosted.** The deliverable is a local CLI/Streamlit demo
  plus recorded results, per the project's cost posture — a public link would
  add hosting cost and a public code-execution attack surface for marginal
  portfolio benefit (see PLAN.md's Phase 7, intentionally left optional and
  unbuilt).

## Project Structure

```
sandbox-agent/
├── agent/              # LangGraph orchestration (state, nodes, graph)
├── sandbox/            # Swappable backends (E2B, Docker)
├── prompts/            # System prompts (code-gen v1/v2/v3, repair)
├── renderers/          # Output formatters (table, chart, text, file, redaction)
├── demo/               # CLI and Streamlit UI
├── bench/              # Benchmarks: injections.jsonl, requests.jsonl, harnesses, results/
├── tests/              # Security, loop, renderer, and injection tests
├── config.py           # Environment loading
├── RESULTS.md          # Phase 6 measured metrics
└── hello_world.py      # Phase 0 hello-world stub
```

## Security

Defense-in-depth, in order of how much they're actually trusted:

1. **The prompt** (first layer, not the guarantee): user input is delimited in
   `<data>` tags and never treated as instructions; the model is told to
   refuse or explain rather than comply with unsafe requests.
2. **The sandbox** (the real boundary): no network, no filesystem escape,
   resource caps (memory/CPU/time) — each proven by a dedicated test on
   *both* backends, and `--network=none` on Docker makes egress provably
   impossible rather than merely discouraged.
3. **Empty sandbox environment:** no API key is ever placed in the sandbox's
   env — even a fully injected prompt has nothing to steal.
4. **Output-side redaction** (last line of defense, best-effort): stdout,
   summaries, and table cells are scanned for key-shaped strings (`sk-`,
   `gsk_`, `ghp_`, AWS/Slack key prefixes, …) before rendering.

All 16 cases in `bench/injections.jsonl` (rule-override, exfil-code,
sandbox-escape, secret-disclosure, plus indirect variants hidden in the
`<data>` payload) pass live through the full loop — no case reached network
egress, host file content, or real secret material in any output surface.
See [sandbox/SECURITY.md](sandbox/SECURITY.md) for the full threat model,
per-flag rationale, and residual risks (notably: E2B's `allow_internet_access=False`
blocks DNS but a literal-IP `connect()` can still return success with zero
bytes back — Docker's `--network=none` is the provable boundary).

## Phases

1. **Phase 0 — Groundwork** ✓
2. **Phase 1 — The Prompt Contract** ✓
3. **Phase 2 — The Vault** ✓
4. **Phase 3 — The Loop** ✓
5. **Phase 4 — The Render Layer** ✓
6. **Phase 5 — The Adversary** ✓ (prompt injection defense, 16-case suite)
7. **Phase 6 — The Gauntlet** ✓ (execution-based eval, two fixed failure modes — see below)
8. *(Optional, unbuilt)* Phase 7 — The Shipping Lane (hosting)

See [PLAN.md](PLAN.md) and [DETAILED_PLAN.md](DETAILED_PLAN.md) for full architecture and rationale.

## Results

Phase 6 ran the full benchmark (30 requests: 10 data-analysis, 10 chart, 10
scripting) through the real loop twice — once against `codegen.v1.md`, once
after fixing the top failure it surfaced — plus a targeted re-check after a
second fix:

| Pass | First-attempt | After repair | Hard fail |
|---|---|---|---|
| Baseline (v1) | 90% | 6.7% | 3.3% |
| Postfix (v2) | 90% | 6.7% | 3.3% |
| v3 (scripting re-check) | 90% | 10% | 0% |

Two independent, reproducible failure modes were found and fixed: a pandas
time-bucket key mismatch (`Period` objects used as both dict keys and lookup
keys inconsistently) and a `sys.argv`-dependent script that hard-failed
because the sandbox never passes command-line arguments to the executed
code. Both fixes were verified with isolated re-runs, and the full 16-case
injection suite was re-run against the final prompt with zero regressions.
Full methodology, per-category cost, and root-cause analysis in
[RESULTS.md](RESULTS.md).
