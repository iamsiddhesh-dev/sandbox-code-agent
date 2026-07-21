# Sandbox Code Agent

A natural-language → code → sandboxed execution → rendered result system. Users describe what they need ("plot the distribution of X", "clean this CSV", "write me a script that does Y"), and the agent generates Python or JavaScript, runs it inside a locked-down sandbox, recovers automatically when code fails, and returns a chart, table, or working script.

## Architecture

```
User Request
    ↓
[Code Generation (LLM)]
    ↓
[Sandbox Execution]
    ├─ Success? → [Render Result]
    └─ Failure? → [Repair Loop] → [Re-execute] → ...
```

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

**Not in this phase, by choice:**
- **CSV upload** — the Streamlit layout in the plan calls for a file uploader,
  but `SandboxBackend.run()` only supports code in / files out, no input files.
  Wiring that through both backends (and the boundary tests that would need to
  come with it) is out of scope here, so the uploader is cut; requests that need
  input data pass it inline in the prompt text instead.
- **JavaScript path** — `lang="js"` is plumbed through the sandbox and agent
  state, but [`prompts/codegen.v1.md`](prompts/codegen.v1.md) only instructs the
  model to write Python. The JS path is interface-ready, not built.

## Project Structure

```
sandbox-agent/
├── agent/              # LangGraph orchestration (state, nodes, graph)
├── sandbox/            # Swappable backends (E2B, Docker)
├── prompts/            # System prompts (code-gen, repair)
├── renderers/          # Output formatters (table, chart, text, file)
├── demo/               # CLI and Streamlit UI
├── bench/              # Benchmarks and eval harnesses
├── tests/              # Security and integration tests
├── config.py           # Environment loading
└── hello_world.py      # Phase 0 hello-world stub
```

## Security

- **Sandbox boundaries:** No network, no filesystem escape, resource caps (memory/CPU/time) — each proven by a test on both backends
- **Prompt injection defense:** Defense-in-depth (boundary + sandbox isolation as backstop)
- **No secrets in sandbox:** No API key is ever placed in the sandbox environment
- See [sandbox/SECURITY.md](sandbox/SECURITY.md) for the full threat model, per-flag rationale, and residual risks.

## Phases

1. **Phase 0 — Groundwork** ✓
2. **Phase 1 — The Prompt Contract** ✓
3. **Phase 2 — The Vault** ✓
4. **Phase 3 — The Loop** ✓
5. **Phase 4 — The Render Layer** ✓ (current)
6. Phase 5 — The Adversary (injection defense)
7. Phase 6 — The Gauntlet (final eval + results)
8. *(Optional)* Phase 7 — The Shipping Lane (hosting)

See [PLAN.md](PLAN.md) and [DETAILED_PLAN.md](DETAILED_PLAN.md) for full architecture and rationale.

## Results

*(To be populated in Phase 6 — [RESULTS.md](RESULTS.md))*
