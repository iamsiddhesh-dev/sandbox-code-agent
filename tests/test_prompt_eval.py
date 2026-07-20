from bench.prompt_eval import extract_single_fenced_block, last_print_envelope_ok

TABLE_CODE = """
import json

row = {"count": 5, "mean": 1.0, "min": 1, "max": 2}
envelope = {"type": "table", "data": [row], "artifact_path": None, "note": None}
print(json.dumps(envelope, default=str))
"""

CHART_CODE = """
import json

envelope = {"type": "chart", "data": None, "artifact_path": "/output/chart.png", "note": None}
print(json.dumps(envelope, default=str))
"""

INLINE_DICT_CODE = """
import json
print(json.dumps({"type": "text", "data": "hi", "artifact_path": None, "note": None}))
"""

BAD_TYPE_CODE = """
import json
envelope = {"type": "video", "data": None, "artifact_path": None}
print(json.dumps(envelope))
"""

NOT_LAST_PRINT_CODE = """
import json
envelope = {"type": "text", "data": "hi", "artifact_path": None}
print(json.dumps(envelope))
print("oops trailing output")
"""

SYNTAX_ERROR_CODE = "def broken(:\n    pass"


def test_extract_single_fenced_block():
    response = "```python\nprint(1)\n```"
    assert extract_single_fenced_block(response) == "print(1)\n"


def test_extract_rejects_zero_or_multiple_blocks():
    assert extract_single_fenced_block("no code here") is None
    assert (
        extract_single_fenced_block("```python\nprint(1)\n```\n```python\nprint(2)\n```")
        is None
    )


def test_table_envelope_detected():
    ok, _ = last_print_envelope_ok(TABLE_CODE)
    assert ok


def test_chart_envelope_detected():
    ok, _ = last_print_envelope_ok(CHART_CODE)
    assert ok


def test_inline_dict_literal_detected():
    ok, _ = last_print_envelope_ok(INLINE_DICT_CODE)
    assert ok


def test_bad_type_rejected():
    ok, reason = last_print_envelope_ok(BAD_TYPE_CODE)
    assert not ok
    assert "type" in reason


def test_trailing_print_after_envelope_rejected():
    ok, reason = last_print_envelope_ok(NOT_LAST_PRINT_CODE)
    assert not ok


def test_syntax_error_rejected():
    ok, reason = last_print_envelope_ok(SYNTAX_ERROR_CODE)
    assert not ok
    assert "parse" in reason
