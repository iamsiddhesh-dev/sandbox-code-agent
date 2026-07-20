# Repair prompt (v1)

The program you wrote failed. Fix ONLY what broke; keep everything that
already worked. Do not redesign the approach, rename things, add
features, or "improve" unrelated code — a repair that changes more than
it must is a failed repair.

Output ONE fenced code block (` ```python `) containing the complete
corrected program, and nothing else — no prose before or after the
block. Every hard rule and output-envelope convention from the original
system prompt still applies.

## Original request

{request}

## Failing code

```{lang}
{code}
```

## Exact error (stderr / traceback)

```
{stderr}
```

{failure_note}

## Checklist before you answer

- Does the traceback point at a specific line? Fix that line.
- Is the last thing printed to stdout still exactly one line of envelope
  JSON, with nothing printed after it?
- Are the imports still limited to the standard library plus `pandas`,
  `numpy`, `matplotlib`?
- Are all writes still confined to `/output`?
