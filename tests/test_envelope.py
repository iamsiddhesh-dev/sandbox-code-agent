import pytest
from pydantic import ValidationError

from renderers.envelope import parse_envelope


def test_table_envelope():
    env = parse_envelope({"type": "table", "data": [{"a": 1}], "note": None})
    assert env.type == "table"
    assert env.data == [{"a": 1}]


def test_chart_envelope():
    env = parse_envelope(
        {"type": "chart", "data": None, "artifact_path": "/output/chart.png"}
    )
    assert env.type == "chart"
    assert env.artifact_path == "/output/chart.png"


def test_text_envelope():
    env = parse_envelope({"type": "text", "data": "hello"})
    assert env.type == "text"
    assert env.data == "hello"


def test_file_envelope():
    env = parse_envelope(
        {"type": "file", "data": None, "artifact_path": "/output/script.py"}
    )
    assert env.type == "file"
    assert env.artifact_path == "/output/script.py"


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        parse_envelope({"type": "video", "data": None})


def test_json_string_input():
    env = parse_envelope('{"type": "text", "data": "hi"}')
    assert env.type == "text"


def test_chart_missing_artifact_path_rejected():
    with pytest.raises(ValidationError):
        parse_envelope({"type": "chart", "data": None})
