"""Output envelope contract: the last stdout line of generated code must parse into one of these."""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


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
