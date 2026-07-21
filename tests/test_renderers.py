"""Renderer + dispatch tests. No sandbox, no LLM — pure data in, RenderOutcome out."""

from __future__ import annotations

from agent.state import RenderedResult
from renderers.dispatch import dispatch
from renderers.envelope import parse_envelope


def test_table_renders_aligned_columns():
    from renderers.table import render_table

    envelope = parse_envelope({"type": "table", "data": [{"a": 1, "bb": "x"}, {"a": 22, "bb": "y"}]})
    outcome = render_table(envelope)

    assert outcome.kind == "table"
    assert outcome.table_rows == envelope.data
    lines = outcome.summary.splitlines()
    assert lines[0].split() == ["a", "bb"]
    assert len(lines[2]) == len(lines[3])  # both data rows padded to equal width


def test_chart_saves_matching_file_by_basename(tmp_path):
    from renderers.chart import render_chart

    envelope = parse_envelope(
        {"type": "chart", "data": None, "artifact_path": "/output/chart.png"}
    )
    outcome = render_chart(envelope, {"chart.png": b"\x89PNG\r\n"}, tmp_path)

    assert outcome.kind == "chart"
    assert outcome.saved_path is not None and outcome.saved_path.exists()
    assert outcome.saved_path.read_bytes() == b"\x89PNG\r\n"


def test_chart_missing_file_reports_instead_of_crashing(tmp_path):
    from renderers.chart import render_chart

    envelope = parse_envelope(
        {"type": "chart", "data": None, "artifact_path": "/output/chart.png"}
    )
    outcome = render_chart(envelope, {}, tmp_path)

    assert outcome.kind == "chart"
    assert outcome.saved_path is None
    assert "no matching file" in outcome.summary


def test_file_saves_under_original_basename(tmp_path):
    from renderers.file import render_file

    envelope = parse_envelope(
        {
            "type": "file",
            "data": None,
            "artifact_path": "/output/script.py",
            "note": "Run with: python script.py",
        }
    )
    outcome = render_file(envelope, {"script.py": b"print(1)"}, tmp_path)

    assert outcome.saved_path == tmp_path / "script.py"
    assert outcome.saved_path.read_text() == "print(1)"
    assert "Run with" in outcome.summary


def test_text_renders_data_verbatim_with_note():
    from renderers.text import render_text

    envelope = parse_envelope({"type": "text", "data": "42", "note": "rounded"})
    outcome = render_text(envelope)

    assert outcome.summary == "42\n\nNote: rounded"


def test_dispatch_routes_success_by_type(tmp_path):
    result = RenderedResult(
        success=True,
        envelope=parse_envelope({"type": "text", "data": "hi"}),
        raw_stdout='{"type": "text", "data": "hi"}',
    )
    outcome = dispatch(result, outputs_dir=tmp_path)
    assert outcome.kind == "text"
    assert outcome.summary == "hi"


def test_dispatch_falls_back_to_raw_stdout_on_malformed_envelope(tmp_path):
    result = RenderedResult(success=True, envelope=None, raw_stdout="not json at all")
    outcome = dispatch(result, outputs_dir=tmp_path)

    assert outcome.kind == "malformed"
    assert outcome.raw_stdout == "not json at all"
    assert "unavailable" in outcome.summary


def test_dispatch_reports_give_up_as_failure(tmp_path):
    result = RenderedResult(success=False, envelope=None, message="gave up", raw_stdout="")
    outcome = dispatch(result, outputs_dir=tmp_path)

    assert outcome.kind == "failure"
    assert outcome.summary == "gave up"
