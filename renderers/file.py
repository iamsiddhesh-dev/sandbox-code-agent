"""Renders a FileEnvelope: saves the generated script/file to disk and surfaces its usage note.

A script *request* returns runnable code, not just the code's output, so the
saved bytes are the deliverable here.
"""

from __future__ import annotations

from pathlib import Path

from renderers.envelope import FileEnvelope
from renderers.outcome import RenderOutcome


def render_file(
    envelope: FileEnvelope, files: dict[str, bytes], outputs_dir: Path
) -> RenderOutcome:
    basename = Path(envelope.artifact_path).name
    content = files.get(basename)
    if content is None:
        return RenderOutcome(
            kind="file",
            summary=(
                f"File was requested (artifact_path={envelope.artifact_path}) but no "
                "matching file came back from the sandbox."
            ),
            note=envelope.note,
        )

    outputs_dir.mkdir(parents=True, exist_ok=True)
    saved_path = outputs_dir / basename
    saved_path.write_bytes(content)

    summary = f"Saved to {saved_path}"
    if envelope.note:
        summary += f"\nUsage: {envelope.note}"
    return RenderOutcome(
        kind="file", summary=summary, saved_path=saved_path, file_bytes=content, note=envelope.note
    )
