import pytest
from pydantic import ValidationError

from renderers.envelope import (
    MalformedEnvelope,
    envelope_from_stdout,
    parse_envelope,
    parse_envelope_from_stdout,
)


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


def test_parse_from_stdout_uses_last_non_empty_line():
    stdout = 'debug chatter\n\n{"type": "text", "data": "hi"}\n'
    env = parse_envelope_from_stdout(stdout)
    assert env.type == "text" and env.data == "hi"


@pytest.mark.parametrize(
    "stdout",
    ["", "   \n  \n", "not json at all", '{"type": "video", "data": 1}', "{}"],
)
def test_parse_from_stdout_raises_malformed_envelope(stdout):
    with pytest.raises(MalformedEnvelope):
        parse_envelope_from_stdout(stdout)


def test_envelope_from_stdout_returns_none_on_malformed_instead_of_raising():
    assert envelope_from_stdout("garbage") is None
    assert envelope_from_stdout('{"type": "text", "data": "hi"}').type == "text"
