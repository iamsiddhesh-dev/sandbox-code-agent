"""Static eval harness for the codegen.v1 prompt (Phase 1).

Calls the runtime code-gen model on a fixed sample of requests and checks
only the *shape* of what comes back: exactly one fenced Python block, the
block parses as valid Python, and its last `print(...)` argument
statically resolves to a dict literal matching the output-envelope
contract (type/data/artifact_path/note, type in the allowed set).

Deliberately does NOT execute the generated code — the sandbox doesn't
exist until Phase 2, so this can't verify what actually prints at
runtime, only what the source implies.
"""

import ast
import json
import re
import sys
from pathlib import Path

from groq import Groq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import CODEGEN_MODEL, GROQ_API_KEY  # noqa: E402

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "codegen.v1.md"
ENVELOPE_TYPES = {"table", "chart", "text", "file"}
FENCE_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)

REQUESTS = [
    # data analysis
    {
        "category": "data-analysis",
        "request": "Given this list of order totals, compute the count, mean, min, and max.",
        "data": "[12.50, 45.00, 8.75, 102.30, 19.99]",
    },
    {
        "category": "data-analysis",
        "request": "Given these daily temperatures (Celsius), find the 3 hottest days and their values.",
        "data": "[21.0, 24.5, 19.8, 30.1, 28.3, 22.2, 26.7]",
    },
    {
        "category": "data-analysis",
        "request": "Count how many times each word appears in this text and return the top 5.",
        "data": "\"the quick brown fox jumps over the lazy dog the fox runs\"",
    },
    {
        "category": "data-analysis",
        "request": "Given this list of (name, score) pairs, rank students by score descending.",
        "data": '[["Alice", 88], ["Bob", 95], ["Cara", 72], ["Dan", 95]]',
    },
    # chart
    {
        "category": "chart",
        "request": "Plot a histogram of these exam scores.",
        "data": "[55, 62, 70, 71, 72, 80, 81, 85, 90, 92, 95, 98]",
    },
    {
        "category": "chart",
        "request": "Plot a line chart of monthly revenue over the year.",
        "data": "[1200, 1500, 1100, 1800, 2000, 2200, 2100, 2500, 2400, 2600, 2900, 3100]",
    },
    {
        "category": "chart",
        "request": "Plot a bar chart comparing sales across these four regions.",
        "data": '{"North": 4200, "South": 3100, "East": 5300, "West": 2900}',
    },
    {
        "category": "chart",
        "request": "Plot a scatter chart of these (x, y) points.",
        "data": "[[1, 2], [2, 4], [3, 5], [4, 8], [5, 9], [6, 13]]",
    },
    # plain scripting
    {
        "category": "scripting",
        "request": "Write me a script that checks whether a given string is a palindrome.",
        "data": None,
    },
    {
        "category": "scripting",
        "request": "Write a script that converts a temperature from Fahrenheit to Celsius given as a command-line argument.",
        "data": None,
    },
    {
        "category": "scripting",
        "request": "Write a script that flattens a nested list of arbitrary depth.",
        "data": None,
    },
    {
        "category": "scripting",
        "request": "Write a script that generates the first N Fibonacci numbers, where N is a command-line argument.",
        "data": None,
    },
]


def build_user_message(sample: dict) -> str:
    if sample["data"] is None:
        return sample["request"]
    return f"{sample['request']}\n\n<data>\n{sample['data']}\n</data>"


def extract_single_fenced_block(response: str) -> str | None:
    blocks = FENCE_RE.findall(response)
    if len(blocks) != 1:
        return None
    return blocks[0]


def find_dict_assignments(tree: ast.AST) -> dict[str, ast.Dict]:
    assignments: dict[str, ast.Dict] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
    return assignments


def dict_literal_to_keys(node: ast.Dict) -> dict[str, ast.expr]:
    keys = {}
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys[k.value] = v
    return keys


def envelope_type_value(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def last_print_envelope_ok(code: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"code does not parse: {e}"

    print_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]
    if not print_calls:
        return False, "no print() calls found"

    last_print = max(print_calls, key=lambda n: (n.lineno, n.col_offset))
    if not last_print.args:
        return False, "last print() has no arguments"

    arg = last_print.args[0]
    envelope_dict: ast.Dict | None = None

    if (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "dumps"
    ) and arg.args:
        inner = arg.args[0]
        if isinstance(inner, ast.Dict):
            envelope_dict = inner
        elif isinstance(inner, ast.Name):
            envelope_dict = find_dict_assignments(tree).get(inner.id)
    elif isinstance(arg, ast.Dict):
        envelope_dict = arg
    elif isinstance(arg, ast.Name):
        envelope_dict = find_dict_assignments(tree).get(arg.id)

    if envelope_dict is None:
        return False, "last print() argument is not a resolvable envelope dict"

    keys = dict_literal_to_keys(envelope_dict)
    if "type" not in keys:
        return False, "envelope dict missing 'type' key"

    type_value = envelope_type_value(keys["type"])
    if type_value not in ENVELOPE_TYPES:
        return False, f"envelope 'type' is not a static literal in {ENVELOPE_TYPES}"

    if "data" not in keys or "artifact_path" not in keys:
        return False, "envelope dict missing 'data' or 'artifact_path' key"

    return True, "ok"


def run_eval() -> list[dict]:
    if not GROQ_API_KEY:
        raise SystemExit("GROQ_API_KEY not set — cannot call the runtime model.")

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    client = Groq(api_key=GROQ_API_KEY)
    results = []

    for sample in REQUESTS:
        user_message = build_user_message(sample)
        completion = client.chat.completions.create(
            model=CODEGEN_MODEL,
            temperature=0.2,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        response = completion.choices[0].message.content

        code = extract_single_fenced_block(response)
        if code is None:
            results.append(
                {
                    "category": sample["category"],
                    "request": sample["request"],
                    "pass": False,
                    "reason": "not exactly one fenced ```python block",
                }
            )
            continue

        ok, reason = last_print_envelope_ok(code)
        results.append(
            {
                "category": sample["category"],
                "request": sample["request"],
                "pass": ok,
                "reason": reason,
            }
        )

    return results


def main() -> None:
    results = run_eval()
    passed = sum(r["pass"] for r in results)
    total = len(results)

    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        print(f"[{status}] ({r['category']}) {r['request']}")
        if not r["pass"]:
            print(f"         reason: {r['reason']}")

    rate = passed / total if total else 0.0
    print(f"\n{passed}/{total} passed ({rate:.0%})")
    print(json.dumps({"passed": passed, "total": total, "rate": rate}))

    if rate < 0.9:
        sys.exit(1)


if __name__ == "__main__":
    main()
