"""Output-side redaction (Phase 5, defense 5.2d). Pure data-in / RenderOutcome-out."""

from __future__ import annotations

from agent.state import RenderedResult
from renderers.dispatch import dispatch
from renderers.envelope import parse_envelope
from renderers.outcome import RenderOutcome
from renderers.redact import redact_outcome, redact_text

GROQ_SHAPED = "gsk_" + "A1b2C3d4E5f6G7h8I9j0" + "KLMNOPQRST"
E2B_SHAPED = "e2b_" + "0123456789abcdef0123456789abcdef"
OPENAI_SHAPED = "sk-" + "abcdefghijklmnopqrstuvwxyz012345"


def test_redacts_known_key_prefixes():
    for shaped in (GROQ_SHAPED, E2B_SHAPED, OPENAI_SHAPED):
        redacted, hits = redact_text(f"the key is {shaped} keep it safe")
        assert hits == 1
        assert shaped not in redacted
        assert "[REDACTED]" in redacted


def test_leaves_ordinary_prose_untouched():
    text = "The sk- prefix and gsk_ scheme are described in the docs (see e2b_ notes)."
    redacted, hits = redact_text(text)
    assert hits == 0
    assert redacted == text


def test_redacts_aws_github_slack_shapes():
    text = (
        "AKIAIOSFODNN7EXAMPLE and "
        "ghp_" + "a" * 36 + " and "
        "xoxb-123456789012-abcdefghijkl"
    )
    _, hits = redact_text(text)
    assert hits == 3


def test_redact_outcome_scrubs_text_fields_and_counts():
    outcome = RenderOutcome(
        kind="text",
        summary=f"Answer: {GROQ_SHAPED}",
        note=f"aside {E2B_SHAPED}",
        raw_stdout=f"raw {OPENAI_SHAPED}",
    )
    scrubbed = redact_outcome(outcome)

    assert scrubbed.redactions == 3
    assert GROQ_SHAPED not in scrubbed.summary
    assert E2B_SHAPED not in (scrubbed.note or "")
    assert OPENAI_SHAPED not in scrubbed.raw_stdout


def test_redact_outcome_scrubs_table_cell_values():
    outcome = RenderOutcome(
        kind="table",
        summary="table",
        table_rows=[{"name": "prod", "token": GROQ_SHAPED}, {"name": "dev", "token": "none"}],
    )
    scrubbed = redact_outcome(outcome)

    assert scrubbed.redactions == 1
    assert scrubbed.table_rows[0]["token"] == "[REDACTED]"
    assert scrubbed.table_rows[1]["token"] == "none"
    assert scrubbed.table_rows[0]["name"] == "prod"


def test_clean_outcome_is_returned_unchanged():
    outcome = RenderOutcome(kind="text", summary="42 is the answer")
    scrubbed = redact_outcome(outcome)
    assert scrubbed is outcome
    assert scrubbed.redactions == 0


def test_dispatch_applies_redaction_end_to_end():
    envelope = parse_envelope(
        {"type": "text", "data": f"your leaked key is {GROQ_SHAPED}", "artifact_path": None}
    )
    result = RenderedResult(success=True, envelope=envelope, message="ok")
    outcome = dispatch(result)

    assert outcome.redactions == 1
    assert GROQ_SHAPED not in outcome.summary
