"""envelope.type -> renderer, with a raw-stdout fallback that never crashes."""

from __future__ import annotations

from pathlib import Path

from agent.state import RenderedResult
from renderers.chart import render_chart
from renderers.envelope import MalformedEnvelope, parse_envelope_from_stdout
from renderers.file import render_file
from renderers.outcome import RenderOutcome
from renderers.redact import redact_outcome
from renderers.table import render_table
from renderers.text import render_text

DEFAULT_OUTPUTS_DIR = Path("outputs")


def dispatch(
    result: RenderedResult, outputs_dir: Path = DEFAULT_OUTPUTS_DIR
) -> RenderOutcome:
    return redact_outcome(_dispatch(result, outputs_dir))


def _dispatch(result: RenderedResult, outputs_dir: Path) -> RenderOutcome:
    if not result.success:
        return RenderOutcome(kind="failure", summary=result.message, raw_stdout=result.raw_stdout)

    envelope = result.envelope
    if envelope is None:
        try:
            envelope = parse_envelope_from_stdout(result.raw_stdout)
        except MalformedEnvelope:
            return RenderOutcome(
                kind="malformed",
                summary="Structured output unavailable — showing raw stdout instead.",
                raw_stdout=result.raw_stdout,
            )

    if envelope.type == "table":
        return render_table(envelope)
    if envelope.type == "chart":
        return render_chart(envelope, result.files, outputs_dir)
    if envelope.type == "text":
        return render_text(envelope)
    return render_file(envelope, result.files, outputs_dir)
