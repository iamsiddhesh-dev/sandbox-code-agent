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

The Docker backend needs a running Docker daemon; its image is built automatically
on first use from [sandbox/Dockerfile.sandbox](sandbox/Dockerfile.sandbox), or ahead
of time with:

```bash
docker build -f sandbox/Dockerfile.sandbox -t sandbox-code-agent:latest sandbox/
```

### Run Tests

```bash
pytest                # unit tests only
pytest -m slow        # boundary + round-trip tests against real E2B and Docker sandboxes
```

Sandbox-backed tests are marked `slow` and excluded by default because they are
metered (E2B) and take about two minutes.

### Run Demo

```bash
# CLI demo (one-shot)
python -m demo.cli "plot a sine wave"

# Streamlit UI (interactive)
streamlit run demo/app.py
```

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
3. **Phase 2 — The Vault** ✓ (current)
4. Phase 3 — The Loop (generate/execute/retry)
5. Phase 4 — The Render Layer
6. Phase 5 — The Adversary (injection defense)
7. Phase 6 — The Gauntlet (final eval + results)
8. *(Optional)* Phase 7 — The Shipping Lane (hosting)

See [PLAN.md](PLAN.md) and [DETAILED_PLAN.md](DETAILED_PLAN.md) for full architecture and rationale.

## Results

*(To be populated in Phase 6 — [RESULTS.md](RESULTS.md))*
