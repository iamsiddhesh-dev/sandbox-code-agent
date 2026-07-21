"""Renders a ChartEnvelope: pulls PNG bytes out of ExecResult.files (extracted before
sandbox teardown, keyed by basename) and saves them to disk."""

from __future__ import annotations

import time
from pathlib import Path

from renderers.envelope import ChartEnvelope
from renderers.outcome import RenderOutcome


def render_chart(
    envelope: ChartEnvelope, files: dict[str, bytes], outputs_dir: Path
) -> RenderOutcome:
    basename = Path(envelope.artifact_path).name
    content = files.get(basename)
    if content is None:
        return RenderOutcome(
            kind="chart",
            summary=(
                f"Chart was requested (artifact_path={envelope.artifact_path}) but no "
                "matching file came back from the sandbox."
            ),
            note=envelope.note,
        )

    outputs_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(basename).suffix or ".png"
    saved_path = outputs_dir / f"{int(time.time() * 1000)}{ext}"
    saved_path.write_bytes(content)

    summary = f"Chart saved to {saved_path}"
    if envelope.note:
        summary += f"\nNote: {envelope.note}"
    return RenderOutcome(
        kind="chart", summary=summary, saved_path=saved_path, image_bytes=content, note=envelope.note
    )
