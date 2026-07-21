"""Output-side secret scrubbing: the last layer before anything reaches the user.

This is defense-in-depth, not the guarantee. The sandbox ships an empty
environment (`envs={}` / no `-e`), so there is normally no secret material to
leak in the first place. This scan is the backstop for the cases the empty env
does not cover — most realistically, a user who pastes their *own* key into the
request text and a model that echoes it back in a text or table result.

It is pattern-based and knows no real key values, so it stays a pure function
of its input and never couples the render layer to `config`. It scans only
human-readable channels; binary artifacts (chart PNGs, downloadable files) are
left untouched — redacting bytes would corrupt a legitimate deliverable, and
those channels are covered by the empty-env guarantee, not by this scan.
"""

from __future__ import annotations

import re

from renderers.outcome import RenderOutcome

PLACEHOLDER = "[REDACTED]"

# Prefix-anchored, high-min-length patterns. Anchoring on a known key prefix and
# requiring a long tail keeps this from mangling ordinary prose (a sentence that
# happens to contain "sk-" is not 20 base62 characters long).
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),        # OpenAI / Anthropic style
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),         # Groq
    re.compile(r"e2b_[A-Za-z0-9]{16,}"),         # E2B
    re.compile(r"AKIA[0-9A-Z]{16}"),             # AWS access key id
    re.compile(r"ghp_[A-Za-z0-9]{36}"),          # GitHub personal access token
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack token
)


def redact_text(text: str) -> tuple[str, int]:
    """Return `text` with key-shaped substrings replaced, and how many were hit."""
    hits = 0
    for pattern in SECRET_PATTERNS:
        text, n = pattern.subn(PLACEHOLDER, text)
        hits += n
    return text, hits


def _redact_rows(rows: list[dict]) -> tuple[list[dict], int]:
    hits = 0
    scrubbed: list[dict] = []
    for row in rows:
        new_row: dict = {}
        for key, value in row.items():
            if isinstance(value, str):
                value, n = redact_text(value)
                hits += n
            new_row[key] = value
        scrubbed.append(new_row)
    return scrubbed, hits


def redact_outcome(outcome: RenderOutcome) -> RenderOutcome:
    """Scrub every text-bearing field of a RenderOutcome, recording the hit count.

    Binary fields (`image_bytes`, `file_bytes`) are deliberately not scanned.
    """
    summary, s_hits = redact_text(outcome.summary)
    raw_stdout, r_hits = redact_text(outcome.raw_stdout)
    note, n_hits = (redact_text(outcome.note) if outcome.note else (outcome.note, 0))
    table_rows, t_hits = (
        _redact_rows(outcome.table_rows) if outcome.table_rows is not None else (outcome.table_rows, 0)
    )

    total = s_hits + r_hits + n_hits + t_hits
    if total == 0:
        return outcome

    return outcome.model_copy(
        update={
            "summary": summary,
            "raw_stdout": raw_stdout,
            "note": note,
            "table_rows": table_rows,
            "redactions": outcome.redactions + total,
        }
    )
