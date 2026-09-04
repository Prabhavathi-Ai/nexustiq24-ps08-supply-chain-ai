"""Validated facts extracted from a disruption notice."""

from pydantic import BaseModel, ConfigDict, Field


class DisruptionUnderstanding(BaseModel):
    """Meaning extracted from a notice, without operational impact conclusions."""

    model_config = ConfigDict(extra="forbid")

    event_type: str | None = None
    locations: list[str] = Field(default_factory=list)
    duration_text: str | None = None
    transport_mode: str | None = None
    route_hints: list[str] = Field(default_factory=list)
    entity_hints: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)