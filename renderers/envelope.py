"""Output envelope contract: the last stdout line of generated code must parse into one of these."""

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, ValidationError


class TableEnvelope(BaseModel):
    type: Literal["table"]
    data: list[dict]
    artifact_path: None = None
    note: str | None = None


class ChartEnvelope(BaseModel):
    type: Literal["chart"]
    data: None = None
    artifact_path: str
    note: str | None = None


class TextEnvelope(BaseModel):
    type: Literal["text"]
    data: str
    artifact_path: None = None
    note: str | None = None


class FileEnvelope(BaseModel):
    type: Literal["file"]
    data: None = None
    artifact_path: str
    note: str | None = None


Envelope = Annotated[
    Union[TableEnvelope, ChartEnvelope, TextEnvelope, FileEnvelope],
    Field(discriminator="type"),
]

_envelope_adapter: TypeAdapter[Envelope] = TypeAdapter(Envelope)


def parse_envelope(raw: dict | str) -> Envelope:
    """Validate a decoded envelope dict (or JSON string) against the contract.

    Raises pydantic.ValidationError on an unknown/malformed type.
    """
    if isinstance(raw, str):
        return _envelope_adapter.validate_json(raw)
    return _envelope_adapter.validate_python(raw)


class MalformedEnvelope(Exception):
    """The last stdout line did not parse into a valid Envelope."""

    def __init__(self, reason: str, raw_stdout: str):
        super().__init__(reason)
        self.reason = reason
        self.raw_stdout = raw_stdout


def parse_envelope_from_stdout(stdout: str) -> Envelope:
    """Take an ExecResult.stdout, validate its last non-empty line as an Envelope.

    Raises MalformedEnvelope (never a bare json/pydantic error) on any failure.
    """
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    if not lines:
        raise MalformedEnvelope("stdout is empty", stdout)
    try:
        decoded = json.loads(lines[-1])
    except (json.JSONDecodeError, ValueError) as exc:
        raise MalformedEnvelope(f"last stdout line is not valid JSON: {exc}", stdout) from exc
    if not isinstance(decoded, dict):
        raise MalformedEnvelope("last stdout line is not a JSON object", stdout)
    try:
        return parse_envelope(decoded)
    except ValidationError as exc:
        raise MalformedEnvelope(f"envelope failed validation: {exc}", stdout) from exc


def envelope_from_stdout(stdout: str) -> Envelope | None:
    """Same as parse_envelope_from_stdout but reports failure as None, for classification."""
    try:
        return parse_envelope_from_stdout(stdout)
    except MalformedEnvelope:
        return None
