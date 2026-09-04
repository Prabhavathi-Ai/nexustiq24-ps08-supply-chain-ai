"""Typed deterministic match results and API response models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from gemini.models import DisruptionUnderstanding


MatchCategory = Literal[
    "exact",
    "normalized_exact",
    "location_match",
    "route_match",
    "explicit_identifier",
]


class MatchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str
    entity_id: str
    entity_name: str
    match_reason: str
    matched_field: str
    source_fact: str
    category: MatchCategory


class MatchingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disruption_id: str
    match_status: Literal["matched", "ambiguous", "no_match"]
    impact_status: Literal["not_calculated", "no_matching_records"]
    understanding: DisruptionUnderstanding
    suppliers: list[MatchCandidate] = Field(default_factory=list)
    shipments: list[MatchCandidate] = Field(default_factory=list)
    containers: list[MatchCandidate] = Field(default_factory=list)
    routes: list[MatchCandidate] = Field(default_factory=list)
    skus: list[MatchCandidate] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)