"""Typed deterministic impact-analysis results."""

from datetime import date
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
Urgency = Literal["monitor", "elevated", "urgent"]
Severity = Literal["low", "medium", "high", "critical"]


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


class InventoryShortage(BaseModel):
    order_quantity: int
    available_quantity: int
    shortage_quantity: int


class PrioritizedOrder(BaseModel):
    order_id: str
    customer_id: str
    customer_name: str
    required_date: date | None
    affected_sku: str
    shipment_ids: list[str] = Field(default_factory=list)
    route_ids: list[str] = Field(default_factory=list)
    container_ids: list[str] = Field(default_factory=list)
    impact_classification: ImpactClassification
    priority_score: int
    urgency: Urgency
    severity: Severity
    reasons: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    inventory_shortage: InventoryShortage | None = None
    insufficient_information: list[str] = Field(default_factory=list)


class PrioritizationResponse(BaseModel):
    overall_state: Literal["prioritized", "no_affected_orders", "review_required", "insufficient_information"]
    overall_urgency: Urgency | None = None
    overall_severity: Severity | None = None
    orders: list[PrioritizedOrder] = Field(default_factory=list)
    insufficient_information: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ActionOption(BaseModel):
    option_id: str
    name: str
    description: str
    suitability: str
    trade_offs: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    affected_order_ids: list[str] = Field(default_factory=list)


class ActionPlanResponse(BaseModel):
    overall_state: Literal["recommendation_available", "no_impact", "review_required", "insufficient_information"]
    recommended_option_id: str | None = None
    recommended_course: str
    why: list[str] = Field(default_factory=list)
    options: list[ActionOption] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    operator_decision_required: str
    warnings: list[str] = Field(default_factory=list)