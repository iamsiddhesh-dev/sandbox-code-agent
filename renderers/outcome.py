"""Shared result type every renderer returns, so CLI and Streamlit can consume it identically."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

RenderKind = Literal["table", "chart", "text", "file", "malformed", "failure"]


class RenderOutcome(BaseModel):
    kind: RenderKind
    summary: str
    table_rows: list[dict] | None = None
    saved_path: Path | None = None
    image_bytes: bytes | None = None
    file_bytes: bytes | None = None
    note: str | None = None
    raw_stdout: str = ""

    model_config = {"arbitrary_types_allowed": True}
