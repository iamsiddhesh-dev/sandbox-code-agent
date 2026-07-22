# Phase 6 — The Gauntlet: Results

The first **execution-based** evaluation of the prompt contract (Phase 1's eval was
static-only — parseable code and a well-formed envelope *literal*, never actually run).
This is the "here's the data, not just a happy-path demo" phase.

- **Benchmark set:** [`bench/requests.jsonl`](bench/requests.jsonl) — 30 requests,
  10 each of data-analysis (against a bundled CSV, [`bench/sample_sales.csv`](bench/sample_sales.csv)),
  chart, and scripting. 5 are tagged `hard` (deliberately ambiguous/complex), 3 are
  tagged `fail_first` (chosen to be likely to break on the first attempt and exercise
  repair).
- **Harness:** [`bench/run_gauntlet.py`](bench/run_gauntlet.py) — runs every request
  through the real loop (`agent.graph.build_graph`), Docker backend (deterministic,
  no E2B metering), real Groq codegen model. Logs first-attempt success,
  success-after-repair, hard-fail, attempts, wall time, tokens, and cost per request;
  aggregates per category. Output is a reproducible JSON file per pass
  (`bench/results/<label>.json`).
- **Two full passes**, per the Phase 6 plan: **baseline** against `prompts/codegen.v1.md`,
  **postfix** against `prompts/codegen.v2.md` after fixing the top failure baseline
  surfaced. A third, *targeted* 10-request re-check against `prompts/codegen.v3.md`
  verified a second fix found while analyzing the postfix pass (see below) — deliberately
  scoped to the affected category rather than a full third 30-request pass, given the
  free-tier daily token cap encountered along the way (see **Operational note**).

## Baseline — `codegen.v1.md`

| Category | First-attempt | After repair | Hard fail | Mean attempts | Mean cost/request |
|---|---|---|---|---|---|
| data-analysis | 90% | 0% | 10% | 1.20 | $0.00210 |
| chart | 90% | 10% | 0% | 1.10 | $0.00173 |
| scripting | 90% | 10% | 0% | 1.10 | $0.00117 |
| **overall** | **90%** | **6.7%** | **3.3%** | **1.13** | **$0.00167** |

30 requests, $0.04998 total, mean wall time 13.2s/request.

**The one hard fail — `da-5`** ("percentage change in revenue from Q1 to Q2, per
region"): all 3 attempts failed with the *same* error class across the whole repair
loop —

```
1. runtime — KeyError: "['2026Q1', '2026Q2'] not in index"
2. runtime — KeyError: "['2026Q1', '2026Q2'] not in index"
3. runtime — TypeError: keys must be str, int, float, bool or None, not Period
```

The model built a pandas quarter bucket (`Period`-typed), then tried to index by a
plain string that never matched the label it actually created, and — even after
"fixing" the KeyError — ended up trying to `json.dumps` a dict with a `Period` object
as a *key*, which `default=str` cannot rescue because that hook only ever touches
values, never keys. All three repair attempts converged on variations of the same
root confusion rather than escaping it, which is itself worth noting: a repair loop
given only a traceback can fixate on the wrong mental model of the data just as
easily as fix it.

## Fix — `codegen.v2.md`

Added an explicit rule: build time-bucket labels as plain strings *once*, at the
point of creation, and reuse that exact string for every later lookup — never let
pandas assign `Period`/`Timestamp` labels and convert them "later" (by then the
column and the lookup key have already diverged). Added a worked few-shot
(quarter-bucketed revenue table) demonstrating the pattern. `prompts/codegen.v1.md`
was left untouched; `codegen.v2.md` is a separate file.

## Postfix — `codegen.v2.md`

| Category | First-attempt | After repair | Hard fail | Mean attempts | Mean cost/request |
|---|---|---|---|---|---|
| data-analysis | 100% | 0% | 0% | 1.00 | $0.00193 |
| chart | 100% | 0% | 0% | 1.00 | $0.00190 |
| scripting | 70% | 20% | 10% | 1.40 | $0.00214 |
| **overall** | **90%** | **6.7%** | **3.3%** | **1.13** | **$0.00199** |

30 requests, $0.05971 total. `da-5` now succeeds first-attempt — confirmed by an
isolated re-run against `codegen.v2.md`, not just the one sample in this pass.
data-analysis and chart both reached a clean 100%/0%/0%, with zero regressions
from baseline.

**But scripting got worse** — `sc-5` ("compute GCD and LCM... given as
command-line arguments") hard-failed, which baseline hadn't. Re-running `sc-5`
alone against `codegen.v2.md` twice more reproduced the same hard fail both times —
this was not sampling noise, it was a second, independent systemic bug that the
larger sample size of a full pass happened to surface.

**Root cause:** the model generated code that read `sys.argv` in the *executed*
code (not just the delivered `/output/script.py`), gated on `len(sys.argv) != 3`,
and — finding no arguments, because the sandbox never passes any to the executed
code — printed a valid `type="text"` envelope explaining correct usage and then
called `sys.exit(1)`. The exit code is non-zero, so `classify()` in
[`agent/nodes.py`](agent/nodes.py) marks it `runtime` regardless of the envelope
already printed, and it gets "repaired" three times into the ground rather than
recognized as a contract violation in the *prompt*, not the loop.

## Fix — `codegen.v3.md`

Added a hard rule: the code you write is the only thing that ever executes —
unattended, with no CLI arguments and nothing on stdin. If the user's script needs
inputs, the executed code must pick concrete example values itself and call the
logic directly; the interactive/CLI version is only ever the *text* written to
`/output/script.py` for the user to run later, never something the executed code
invokes with `sys.argv`. Reworked the script few-shot to show this split explicitly
(delivered script uses `sys.argv`; the code around it that writes the file and
prints the envelope does not).

## Verification — `codegen.v3.md` (scripting category, 10 requests)

| Category | First-attempt | After repair | Hard fail | Mean attempts |
|---|---|---|---|---|
| scripting | 90% | 10% | 0% | 1.10 |

`sc-5` now succeeds first-attempt; no regression on the other 9 scripting requests
(`sc-6` still needs one repair — at the time this was recorded as an unrelated
curiosity, which **later measurement proved wrong**: it is the same bug family as
`sc-5` and v3 does not fix it. See
[the sc-6 investigation](#the-sc-6-investigation--a-fix-that-didnt-work) below).
data-analysis and chart were not re-run in full a third
time: the v2→v3 diff only touches the Script output-type convention and one new
hard rule, neither of which those categories' generated code exercises, so a
third full 30-request pass would have spent real quota re-confirming code paths
the diff never touched.

`codegen.v3.md` is now the production default (`agent/nodes.py:CODEGEN_PROMPT_PATH`
and `bench/run_gauntlet.py`'s default both point at it) — the fixes ship, they
don't just live in a benchmark result.

## Regression check — injection suite

All 16 adversarial cases in [`bench/injections.jsonl`](bench/injections.jsonl)
(Phase 5) were re-run live against `codegen.v3.md`: **16/16 pass**, no regression.
No real API-key material, no host file content, and nothing key-shaped survived
redaction in any case — the sandbox boundary and output-side redaction are
independent of the codegen prompt version, exactly as defense-in-depth predicts.

## The sc-6 investigation — a fix that didn't work

This section documents a **negative result**. It is here because "we tried the
obvious fix and measured that it didn't work" is a finding, and burying it would
make the rest of this file less trustworthy.

### What was believed

Both full passes recorded `sc-6` ("parse a comma-separated line of `key=value`
pairs into a dictionary and print it as JSON") as `success_after_repair` — one
repair, then fine. It was written off above as an unrelated curiosity that the
loop handled as designed, and left alone.

### What is actually true

Re-running `sc-6`'s **first attempt** 8 times against `codegen.v3.md` gives a
first-attempt pass rate of **1/8**, not the ~90% that one-sample-per-pass
implied. Two failure modes, one root cause:

| Mode | Count | Mechanism |
|---|---|---|
| `EOFError` | 5/8 | Code calls `input()` as a fallback; stdin is closed in the sandbox, so it raises immediately |
| No envelope | 2/8 | Code prints the bare parsed dict (or an `{"error": ...}` object) instead of the envelope |

The root cause is that a request phrased *"write a script that…"* leads the model
to author a standalone CLI program **and execute it**, rather than following the
Script convention (write the CLI version to `/output/script.py` as text, then
demonstrate the logic on hardcoded values). This is the **same family as the
`sc-5` bug** that produced `codegen.v3.md` — v3 fixed the `sys.exit(1)`-on-argv-guard
variant and left the `input()` variant standing.

Worth stating explicitly, because it was the initial hypothesis: this is **not**
caused by `classify()` treating any non-zero exit as failure. In every observed
failure the program prints a usage string or a bare dict and never a valid
envelope, so a more forgiving `classify()` would still — correctly — call it a
failure. The generated code is genuinely wrong; the loop is judging it correctly.

### Two fixes attempted, both measured

Both were tested as `codegen.v4` candidates, 8 first-attempt samples each, Docker
backend, same request:

| Prompt | First-attempt pass | Dominant remaining failure |
|---|---|---|
| `codegen.v3.md` (baseline) | **1/8** | `EOFError` from `input()` |
| Attempt A | **4/8** | `sys.argv` guard → `print("Usage: …")` + `sys.exit(1)` |
| Attempt B | **0/8** | same guard, hoisted into `def main()` |

**Attempt A** explained *why* `input()` fails (`EOFError`, not just "don't") and
removed the `sys.argv[1] if len(sys.argv) > 1 else input(...)` idiom from
few-shot Example 4 — which had been actively teaching the model the exact
construct the rules forbade. It eliminated the `EOFError` mode entirely, but the
model moved to the argv-guard mode instead.

**Attempt B** added an explicit banned-constructs table naming
`if __name__ == "__main__":`. This made things **worse than baseline**: the model
routed around the literal prohibition by hoisting the identical CLI logic into a
`def main()` and calling it from the guard. Banning a syntactic form does not
remove the model's intent to write a command-line program.

### Why it was not shipped

Attempt A is the best measured candidate, and it was still rejected:

- **4/8 is not a fix.** A 50% first-attempt failure rate on a simple request is
  not shippable regardless of the delta.
- **The delta is not significant.** 4/8 vs 1/8 is Fisher's exact two-tailed
  **p = 0.28**. With n=8 that is suggestive at best, and shipping on it would be
  exactly the "one sample looked better" reasoning this file exists to avoid.
- **Two of three attempts made it worse or no better**, which is itself evidence
  that the prompt is the wrong layer. The failure is one of *intent* — the model
  wants to write a CLI program when asked for "a script" — and prose
  prohibitions have now demonstrably failed to redirect that intent three ways.

`codegen.v3.md` therefore remains the production default. The rejected candidate
is kept verbatim as [`prompts/codegen.v4-rejected.md`](prompts/codegen.v4-rejected.md)
— named so it cannot be mistaken for shipped history — so the attempt is
reproducible rather than merely described.

### What would likely work

Not more prose. Two plausible next levers, neither attempted:

1. **Make the Script convention structural rather than advisory** — e.g. classify
   script-shaped requests before codegen and select a dedicated Script-only
   prompt, so the model never chooses between conventions in the first place.
2. **Treat it as routing, not generation** — the ambiguity is real (`"prints it
   as JSON"` genuinely reads as both a `text` and a `file` request), so forcing
   that decision earlier removes the failure rather than asking the model to
   resist it.

Neither is blocking: `sc-6` has never hard-failed. The repair loop catches it on
attempt 2 every time, which is exactly what the repair loop is for — this is a
first-attempt quality gap, not a correctness or termination problem.

## Operational note: Groq free-tier daily token cap

Groq's free/on-demand tier enforces two separate limits on
`llama-3.3-70b-versatile`: 12,000 tokens/minute (handled transparently — see the
retry/backoff added to [`agent/llm.py`](agent/llm.py)) and **100,000 tokens/day**,
which is not. A single 30-request gauntlet pass costs roughly 80,000–95,000
tokens depending on how much repair it triggers, meaning one full pass can consume
most or all of a day's budget on its own. This is a genuine, measured constraint
on iterating quickly with the free tier — comparable in kind to the E2B
session-credit metering already documented in `sandbox/SECURITY.md`, just on the
LLM side instead of the sandbox side.

## Budget-ceiling reconciliation

Every dollar figure in this file is **notional list-price cost**, not money billed.
The runs were made on Groq's free tier, which bills $0.00; the numbers are what the
same token counts would cost at list price, and they exist so the guardrails have a
unit to reason in. The per-million-token rates in
[`agent/state.py`](agent/state.py) (`$0.59` in / `$0.79` out for
`llama-3.3-70b-versatile`, `$0.05` / `$0.08` for `llama-3.1-8b-instant`) were
re-checked against Groq's published pricing and are correct as of 2026-07-22.

Checking the *ceilings* against those rates surfaced a real defect: two of the three
were unreachable, and so were never guardrails at all.

| Ceiling | Was | Reachable? | Now |
|---|---|---|---|
| `max_total_tokens` | 24,000 | yes (barely) | 16,000 |
| `max_cost_usd` | $0.05 | **no** — 24,000 tokens can cost at most $0.019 | $0.010 |
| `max_sandbox_seconds` | 180s | **no** — `max_attempts` (3) x `per_run_timeout_s` (30) caps it at 90s | 75s |

The cost ceiling was shadowed by the token ceiling and the sandbox-time ceiling by
the attempt cap, so in every run ever measured the only thing that could actually
stop a loop was `max_attempts`. The belt-and-braces budget abort described in
Phase 3 was, in practice, one belt.

The new values are calibrated against the worst case across all 60 measured
gauntlet runs (11,329 tokens, $0.00729, 34.3s sandbox): each ceiling sits roughly
1.4-2x above worst-observed, so it backstops a runaway without aborting known-good
work, and each is now reachable — token-heavy runs trip tokens first, output-heavy
runs trip cost first. Three tests in
[`tests/test_agent_state.py`](tests/test_agent_state.py) assert the reachability
relationships directly, so a future retune cannot silently re-shadow a ceiling.

## Demo verification

One clean end-to-end request per category, run live against the production
default (`codegen.v3.md`). The same four output types are captured as UI
screenshots in [the README](README.md#what-it-looks-like)
(`docs/img/demo-*.png`), each a real first-attempt run.

**Data-analysis** (`python -m demo.cli`, E2B backend):
```
count  mean   min   max
-----  -----  ----  -----
5      37.71  8.75  102.3

1 attempt(s), 2750 tokens, $0.00165, 2.7s sandbox time
```

**Chart** (`python -m demo.cli`, Docker backend):
```
Chart saved to outputs\1784621566391.png
1 attempt(s), 2767 tokens, $0.00166, 15.7s sandbox time
```
Also verified visually end-to-end through the Streamlit UI (histogram request):
generate → execute → render pipeline, code expander, and footer
(`1 attempt(s) · 8.7s wall time · $0.00167 · sandbox torn down`) all rendered
correctly.

**Scripting** (`python -m demo.cli`, E2B backend):
```
Saved to outputs\script.py
Usage: Run with: python script.py
1 attempt(s), 2748 tokens, $0.00165, 3.1s sandbox time
```

## Summary

| Pass | First-attempt | After repair | Hard fail |
|---|---|---|---|
| Baseline (v1) | 90% | 6.7% | 3.3% |
| Postfix (v2) | 90% | 6.7% | 3.3% |
| v3 (scripting re-check) | 90% | 10% | 0% |

The aggregate rate looks unchanged between baseline and postfix only because one
fixed failure (`da-5`) and one newly-surfaced failure (`sc-5`) canceled out in the
same 30-request sample — the per-category breakdown and the isolated re-runs above
are what actually show the fixes working. Two independent, reproducible failure
modes were found and fixed in this phase; both survive their own targeted
re-verification, and neither regressed the adversarial suite.
