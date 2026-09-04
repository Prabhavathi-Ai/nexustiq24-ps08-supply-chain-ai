"""Typed deterministic impact-analysis results."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from gemini.models import DisruptionUnderstanding
from matching.models import MatchingResponse


ImpactState = Literal[
    "no_impact",
    "impact_identified",
    "review_required",
    "insufficient_information",
]
ImpactClassification = Literal["direct", "downstream", "unknown"]


class ImpactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str
    entity_id: str
    entity_name: str
    relationship: str
    classification: ImpactClassification
    reason: str
    source_record: str
    supporting_fact: str


class ImpactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disruption_id: str
    impact_state: ImpactState
    understanding: DisruptionUnderstanding
    matching: MatchingResponse
    direct_impact: list[ImpactRecord] = Field(default_factory=list)
    downstream_potential_impact: list[ImpactRecord] = Field(default_factory=list)
    insufficient_information: list[ImpactRecord] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)