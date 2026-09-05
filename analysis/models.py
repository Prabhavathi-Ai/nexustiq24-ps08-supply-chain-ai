"""Typed deterministic impact-analysis results."""

from datetime import date, datetime
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


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    entity_type: str
    record_id: str
    field: str
    value: str
    relationship: str
    source_stage: Literal["understanding", "matching", "impact", "prioritization", "recommendation", "movement", "coordination"]


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
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


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
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


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
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
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
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


class ActionPlanResponse(BaseModel):
    overall_state: Literal["recommendation_available", "no_impact", "review_required", "insufficient_information"]
    recommended_option_id: str | None = None
    recommended_course: str
    why: list[str] = Field(default_factory=list)
    options: list[ActionOption] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    operator_decision_required: str
    warnings: list[str] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


class ScenarioComparisonMetrics(BaseModel):
    """Operational scope of one what-if scenario, derived only from committed records."""

    model_config = ConfigDict(extra="forbid")

    affected_orders_covered: int
    affected_orders_total: int
    affected_customers_covered: int
    affected_customers_total: int
    affected_shipments_covered: int
    affected_shipments_total: int
    priority_orders_covered: int
    priority_orders_total: int
    order_quantity_covered: int
    shortage_quantity_covered: int | None = None
    available_inventory_for_covered_skus: int | None = None
    shortage_incomplete: bool = False
    inventory_incomplete: bool = False
    covered_order_ids: list[str] = Field(default_factory=list)
    covered_customer_ids: list[str] = Field(default_factory=list)
    covered_shipment_ids: list[str] = Field(default_factory=list)
    covered_sku_ids: list[str] = Field(default_factory=list)


class WhatIfScenario(BaseModel):
    """A deterministic what-if wrapper around one supported response option."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    option_id: str
    name: str
    description: str
    is_recommended: bool = False
    addresses: list[str] = Field(default_factory=list)
    does_not_address: list[str] = Field(default_factory=list)
    operational_trade_offs: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    metrics: ScenarioComparisonMetrics
    evidence: list[str] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    execution_status: Literal["simulation_only"]
    execution_notice: str
    advisory_notice: str


class ScenarioComparisonResponse(BaseModel):
    """Deterministic cross-scenario comparison that simulates nothing externally."""

    model_config = ConfigDict(extra="forbid")

    disruption_id: str
    simulation_state: Literal[
        "scenario_comparison_available",
        "no_scenario_created",
        "insufficient_information",
    ]
    recommended_scenario_id: str | None = None
    scenarios: list[WhatIfScenario] = Field(default_factory=list)
    comparison_note: str
    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class OrderQuantityStatistics(BaseModel):
    """Descriptive statistics over active order records in the committed dataset."""

    model_config = ConfigDict(extra="forbid")

    total_active_orders: int
    total_ordered_quantity: int | None = None
    average_order_quantity: float | None = None
    median_order_quantity: float | None = None
    minimum_order_quantity: int | None = None
    maximum_order_quantity: int | None = None
    active_order_ids: list[str] = Field(default_factory=list)
    priority_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)


class InventoryStatistics(BaseModel):
    """Aggregated inventory position and deterministic shortage totals."""

    model_config = ConfigDict(extra="forbid")

    total_available_quantity: int | None = None
    average_inventory_per_sku: float | None = None
    median_inventory_per_sku: float | None = None
    tracked_sku_ids: list[str] = Field(default_factory=list)
    total_shortage_quantity: int | None = None
    shortage_sku_ids: list[str] = Field(default_factory=list)
    shortage_incomplete: bool = False


class ShipmentStatistics(BaseModel):
    """Descriptive counts over active shipment records."""

    model_config = ConfigDict(extra="forbid")

    total_active_shipments: int
    active_shipment_ids: list[str] = Field(default_factory=list)
    shipment_status_counts: dict[str, int] = Field(default_factory=dict)


class DisruptionStatistics(BaseModel):
    """Counts over committed disruption records grouped by event type."""

    model_config = ConfigDict(extra="forbid")

    total_disruptions: int
    counts_by_event_type: dict[str, int] = Field(default_factory=dict)


class InvestigationStatistics(BaseModel):
    """Investigation-specific metrics derived only from established impact records."""

    model_config = ConfigDict(extra="forbid")

    impact_state: ImpactState
    affected_shipment_count: int
    affected_order_count: int
    affected_customer_count: int
    affected_order_quantity: int | None = None
    affected_orders_shortage_quantity: int | None = None
    affected_orders_shortage_rate: float | None = None
    shortage_incomplete: bool = False
    affected_shipment_ids: list[str] = Field(default_factory=list)
    affected_order_ids: list[str] = Field(default_factory=list)
    affected_customer_ids: list[str] = Field(default_factory=list)
    affected_sku_ids: list[str] = Field(default_factory=list)
    impact_classification_counts: dict[str, int] = Field(default_factory=dict)


class OperationalAnalyticsResponse(BaseModel):
    """Deterministic descriptive statistics for a disruption investigation."""

    model_config = ConfigDict(extra="forbid")

    disruption_id: str
    orders: OrderQuantityStatistics
    inventory: InventoryStatistics
    shipments: ShipmentStatistics
    disruptions: DisruptionStatistics
    investigation: InvestigationStatistics
    warnings: list[str] = Field(default_factory=list)


class MovementExposure(BaseModel):
    """Whether a shipment's committed planned route passes through a matched disruption location."""

    model_config = ConfigDict(extra="forbid")

    exposed: bool
    on_route_disruption_locations: list[str] = Field(default_factory=list)
    basis: str


class ShipmentMovementEvidence(BaseModel):
    """Deterministic movement evidence for one affected shipment, from committed records only."""

    model_config = ConfigDict(extra="forbid")

    shipment_id: str
    route_id: str
    sku_id: str
    container_id: str
    origin: str
    destination: str
    route_path: list[str] = Field(default_factory=list)
    shipment_status: str
    container_status: str | None = None
    planned_departure: date | None = None
    planned_arrival: date | None = None
    exposure: MovementExposure
    source_records: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


class MovementDataAvailability(BaseModel):
    """Honesty state describing how much movement information the committed dataset provides."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unavailable"]
    live_tracking: bool = False
    current_position_available: bool = False
    note: str


class ShipmentMovementResponse(BaseModel):
    """Movement evidence for the affected shipments of one disruption investigation."""

    model_config = ConfigDict(extra="forbid")

    disruption_id: str
    availability: MovementDataAvailability
    shipments: list[ShipmentMovementEvidence] = Field(default_factory=list)
    affected_shipment_ids: list[str] = Field(default_factory=list)
    unknown_shipment_ids: list[str] = Field(default_factory=list)
    exposures: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ResponseRole(BaseModel):
    """A deterministic reviewer assignment grounded in established impact and priority evidence."""

    model_config = ConfigDict(extra="forbid")

    role_id: str
    name: str
    responsibility: str
    reason: str
    priority: int
    related_order_ids: list[str] = Field(default_factory=list)
    related_shipment_ids: list[str] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


class DecisionRequirement(BaseModel):
    """A decision the human reviewer must record before any option is executed."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    decision_type: str
    question: str
    recommended_option: str
    alternative_options: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


class HumanDecision(BaseModel):
    """Recorded gate state: the system recommends, the human decides, the system never executes."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    status: Literal["pending", "recorded"]
    recommended_option: str | None = None
    selected_option: str | None = None
    reviewer_role: str | None = None
    note: str | None = None
    recorded_state: Literal["pending_human_decision", "decision_recorded"]
    recorded_at: datetime | None = None


class ResponseCoordinationResponse(BaseModel):
    """Deterministic coordination plan: reviewers, required decisions, and the human decision gate."""

    model_config = ConfigDict(extra="forbid")

    disruption_id: str
    coordination_state: Literal[
        "response_coordination_required",
        "no_response_coordination_required",
        "insufficient_information",
    ]
    roles: list[ResponseRole] = Field(default_factory=list)
    decision_requirements: list[DecisionRequirement] = Field(default_factory=list)
    human_decision: HumanDecision | None = None
    recommended_next_step: str
    evidence: list[str] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


CaseLifecycleState = Literal[
    "new",
    "investigating",
    "awaiting_decisions",
    "decision_recorded",
    "closed",
    "no_action_required",
]


class CaseTimelineEntry(BaseModel):
    """One genuine stage marker in a case history; timestamps are never fabricated."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    label: str
    timestamp: datetime | None = None
    evidence_references: list[EvidenceReference] = Field(default_factory=list)


class DecisionAudit(BaseModel):
    """Audit view of one decision requirement referencing the existing decision state."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    requirement_id: str
    decision_type: str
    assigned_reviewer_role_id: str | None = None
    assigned_reviewer_role: str | None = None
    recommended_option: str | None = None
    selected_option: str | None = None
    reviewer_role: str | None = None
    review_note: str | None = None
    decision_status: Literal["pending", "recorded"]
    decided_at: datetime | None = None
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    execution_status: Literal["not_executed"]


class CaseClosure(BaseModel):
    """A genuine operator case-close record; closing never executes an action."""

    model_config = ConfigDict(extra="forbid")

    disruption_id: str
    closed_at: datetime
    reviewer_role: str | None = None
    note: str | None = None


class CaseStatusResponse(BaseModel):
    """Deterministic case lifecycle, decision audit, and investigation timeline."""

    model_config = ConfigDict(extra="forbid")

    disruption_id: str
    lifecycle_state: CaseLifecycleState
    requires_decisions: bool
    decision_progress: dict[str, int] = Field(default_factory=dict)
    roles: list[ResponseRole] = Field(default_factory=list)
    decision_requirements: list[DecisionRequirement] = Field(default_factory=list)
    decision_audit: list[DecisionAudit] = Field(default_factory=list)
    timeline: list[CaseTimelineEntry] = Field(default_factory=list)
    close: CaseClosure | None = None
    execution_status: Literal["not_executed"]
    warnings: list[str] = Field(default_factory=list)