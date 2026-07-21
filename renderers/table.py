"""Renders a TableEnvelope: aligned text table for the CLI, raw rows for st.dataframe."""

from __future__ import annotations

from renderers.envelope import TableEnvelope
from renderers.outcome import RenderOutcome


def render_table(envelope: TableEnvelope) -> RenderOutcome:
    rows = envelope.data
    summary = _format_table(rows) if rows else "(empty table)"
    if envelope.note:
        summary += f"\n\nNote: {envelope.note}"
    return RenderOutcome(kind="table", summary=summary, table_rows=rows, note=envelope.note)


def _format_table(rows: list[dict]) -> str:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    widths = {
        col: max(len(col), *(len(str(row.get(col, ""))) for row in rows)) for col in columns
    }

    def fmt_row(values: dict) -> str:
        return "  ".join(str(values.get(col, "")).ljust(widths[col]) for col in columns)

    header = "  ".join(col.ljust(widths[col]) for col in columns)
    separator = "  ".join("-" * widths[col] for col in columns)
    lines = [header, separator, *(fmt_row(row) for row in rows)]
    return "\n".join(lines)
