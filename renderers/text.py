"""Renders a TextEnvelope: the plain-text answer, verbatim."""

from __future__ import annotations

from renderers.envelope import TextEnvelope
from renderers.outcome import RenderOutcome


def render_text(envelope: TextEnvelope) -> RenderOutcome:
    summary = envelope.data
    if envelope.note:
        summary += f"\n\nNote: {envelope.note}"
    return RenderOutcome(kind="text", summary=summary, note=envelope.note)
