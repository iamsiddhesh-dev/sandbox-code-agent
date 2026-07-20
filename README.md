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
- **Code generation:** Haiku 4.5 (cost-optimized) or Sonnet 5 (quality if needed)
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

- **Sandbox boundaries:** No network, no filesystem escape, resource caps (memory/CPU/time)
- **Prompt injection defense:** Defense-in-depth (boundary + sandbox isolation as backstop)
- **No secrets in sandbox:** Environment is sanitized; no API keys mounted
- See [sandbox/SECURITY.md](sandbox/SECURITY.md) (Phase 2) for threat model details.

## Phases

1. **Phase 0 — Groundwork** ✓ (current)
2. Phase 1 — The Prompt Contract
3. Phase 2 — The Vault (security boundaries)
4. Phase 3 — The Loop (generate/execute/retry)
5. Phase 4 — The Render Layer
6. Phase 5 — The Adversary (injection defense)
7. Phase 6 — The Gauntlet (final eval + results)
8. *(Optional)* Phase 7 — The Shipping Lane (hosting)

See [PLAN.md](PLAN.md) and [DETAILED_PLAN.md](DETAILED_PLAN.md) for full architecture and rationale.

## Results

*(To be populated in Phase 6 — [RESULTS.md](RESULTS.md))*

## License

MIT
