"""API models and in-memory endpoints for disruption notice intake."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from analysis.analytics import build_operational_analytics
from analysis.coordination import build_response_coordination
from analysis.impact import analyze_impact
from analysis.models import (
    ActionPlanResponse,
    HumanDecision,
    ImpactResponse,
    OperationalAnalyticsResponse,
    PrioritizationResponse,
    ResponseCoordinationResponse,
    ShipmentMovementResponse,
)
from analysis.movement import build_shipment_movement
from analysis.prioritization import prioritize_orders
from analysis.recommendations import build_action_plan
from gemini.errors import GeminiExtractionError
from gemini.extraction import extract_understanding
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding
from matching.models import MatchingResponse
from models import SupplyChainData
from services.data_loader import load_sample_data


MAX_DESCRIPTION_LENGTH = 5_000


class DisruptionNoticeRequest(BaseModel):
    """Validated input received from a user-provided disruption notice."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(..., max_length=MAX_DESCRIPTION_LENGTH)
    reported_at: datetime | None = None
    source: str | None = None


class DisruptionNoticeResponse(BaseModel):
    """Stored disruption notice returned by the intake API."""

    disruption_id: str
    status: str
    original_description: str
    normalized_description: str
    reported_at: datetime
    source: str | None


class DisruptionUnderstandingResponse(BaseModel):
    disruption_id: str
    original_description: str
    understanding: DisruptionUnderstanding


router = APIRouter(prefix="/api/disruptions", tags=["disruptions"])
_disruptions: dict[str, DisruptionNoticeResponse] = {}
_understandings: dict[str, DisruptionUnderstanding] = {}
_decisions: dict[str, HumanDecision] = {}


def normalize_description(description: str) -> str:
    """Trim the notice and collapse repeated whitespace without changing words."""

    return " ".join(description.split())


def clear_disruptions() -> None:
    """Clear local records for isolated tests and local development."""

    _disruptions.clear()
    _understandings.clear()
    _decisions.clear()


@router.post("", response_model=DisruptionNoticeResponse, status_code=status.HTTP_201_CREATED)
def create_disruption(notice: DisruptionNoticeRequest) -> DisruptionNoticeResponse:
    """Accept and store a disruption notice without interpreting it."""

    normalized_description = normalize_description(notice.description)
    if not normalized_description:
        raise HTTPException(status_code=422, detail="description must not be empty")

    record = DisruptionNoticeResponse(
        disruption_id=f"DIS-{uuid4().hex[:12].upper()}",
        status="accepted",
        original_description=notice.description,
        normalized_description=normalized_description,
        reported_at=notice.reported_at or datetime.now(timezone.utc),
        source=notice.source,
    )
    _disruptions[record.disruption_id] = record
    return record


@router.get("/{disruption_id}", response_model=DisruptionNoticeResponse)
def get_disruption(disruption_id: str) -> DisruptionNoticeResponse:
    """Return a stored disruption notice by identifier."""

    record = _disruptions.get(disruption_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    return record


@router.post("/{disruption_id}/understanding", response_model=DisruptionUnderstandingResponse)
def understand_disruption(disruption_id: str) -> DisruptionUnderstandingResponse:
    """Extract notice facts without calculating operational impact."""

    record = _disruptions.get(disruption_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    try:
        understanding = extract_understanding(record.original_description)
    except GeminiExtractionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    _understandings[disruption_id] = understanding
    return DisruptionUnderstandingResponse(
        disruption_id=record.disruption_id,
        original_description=record.original_description,
        understanding=understanding,
    )


@router.post("/{disruption_id}/matches", response_model=MatchingResponse)
def match_disruption(disruption_id: str) -> MatchingResponse:
    """Map a previously understood notice to deterministic supply-chain records."""

    if disruption_id not in _disruptions:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    understanding = _understandings.get(disruption_id)
    if understanding is None:
        raise HTTPException(status_code=409, detail="Disruption understanding is not available")
    return match_understanding(disruption_id, understanding, load_sample_data())


@router.post("/{disruption_id}/impact", response_model=ImpactResponse)
def analyze_disruption_impact(disruption_id: str) -> ImpactResponse:
    """Calculate deterministic operational impact from stored understanding and matches."""

    if disruption_id not in _disruptions:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    understanding = _understandings.get(disruption_id)
    if understanding is None:
        raise HTTPException(status_code=409, detail="Disruption understanding is not available")
    data = load_sample_data()
    matching = match_understanding(disruption_id, understanding, data)
    return analyze_impact(disruption_id, matching, data)


@router.post("/{disruption_id}/priorities", response_model=PrioritizationResponse)
def prioritize_disruption_orders(disruption_id: str) -> PrioritizationResponse:
    """Prioritize affected orders using deterministic dataset fields only."""

    record = _disruptions.get(disruption_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    understanding = _understandings.get(disruption_id)
    if understanding is None:
        raise HTTPException(status_code=409, detail="Disruption understanding is not available")
    data = load_sample_data()
    matching = match_understanding(disruption_id, understanding, data)
    impact = analyze_impact(disruption_id, matching, data)
    return prioritize_orders(record.reported_at.date(), impact, data)


@router.post("/{disruption_id}/recommendations", response_model=ActionPlanResponse)
def recommend_disruption_actions(disruption_id: str) -> ActionPlanResponse:
    """Return evidence-grounded options without executing any action."""

    record = _disruptions.get(disruption_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    understanding = _understandings.get(disruption_id)
    if understanding is None:
        raise HTTPException(status_code=409, detail="Disruption understanding is not available")
    data = load_sample_data()
    matching = match_understanding(disruption_id, understanding, data)
    impact = analyze_impact(disruption_id, matching, data)
    priorities = prioritize_orders(record.reported_at.date(), impact, data)
    return build_action_plan(impact, priorities)


@router.post("/{disruption_id}/analytics", response_model=OperationalAnalyticsResponse)
def analyze_disruption_analytics(disruption_id: str) -> OperationalAnalyticsResponse:
    """Return deterministic descriptive statistics for the committed data and investigation."""

    if disruption_id not in _disruptions:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    understanding = _understandings.get(disruption_id)
    if understanding is None:
        raise HTTPException(status_code=409, detail="Disruption understanding is not available")
    data = load_sample_data()
    matching = match_understanding(disruption_id, understanding, data)
    impact = analyze_impact(disruption_id, matching, data)
    return build_operational_analytics(disruption_id, impact, data)


@router.post("/{disruption_id}/movement", response_model=ShipmentMovementResponse)
def shipment_movement_evidence(disruption_id: str) -> ShipmentMovementResponse:
    """Return deterministic route/movement evidence for affected shipments from committed records only."""

    if disruption_id not in _disruptions:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    understanding = _understandings.get(disruption_id)
    if understanding is None:
        raise HTTPException(status_code=409, detail="Disruption understanding is not available")
    data = load_sample_data()
    matching = match_understanding(disruption_id, understanding, data)
    impact = analyze_impact(disruption_id, matching, data)
    return build_shipment_movement(disruption_id, impact, data)


class DecisionRequest(BaseModel):
    """A recorded human decision for one coordination decision requirement."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    selected_option: str
    reviewer_role: str | None = None
    note: str | None = None


def _coordination_context(disruption_id: str) -> tuple[ImpactResponse, PrioritizationResponse, ActionPlanResponse, SupplyChainData]:
    """Load the deterministic coordination inputs for a stored disruption."""

    record = _disruptions.get(disruption_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    understanding = _understandings.get(disruption_id)
    if understanding is None:
        raise HTTPException(status_code=409, detail="Disruption understanding is not available")
    data = load_sample_data()
    matching = match_understanding(disruption_id, understanding, data)
    impact = analyze_impact(disruption_id, matching, data)
    priorities = prioritize_orders(record.reported_at.date(), impact, data)
    plan = build_action_plan(impact, priorities)
    return impact, priorities, plan, data


@router.post("/{disruption_id}/coordination", response_model=ResponseCoordinationResponse)
def coordinate_disruption_response(disruption_id: str) -> ResponseCoordinationResponse:
    """Return deterministic reviewer roles, decision requirements, and the human decision gate."""

    impact, priorities, plan, data = _coordination_context(disruption_id)
    return build_response_coordination(disruption_id, impact, priorities, plan, data, decided=_decisions)


@router.post("/{disruption_id}/decision", response_model=HumanDecision)
def record_disruption_decision(disruption_id: str, decision: DecisionRequest) -> HumanDecision:
    """Record a human decision for a valid requirement without executing anything."""

    impact, priorities, plan, data = _coordination_context(disruption_id)
    coordination = build_response_coordination(disruption_id, impact, priorities, plan, data, decided=_decisions)
    requirements = {requirement.decision_id: requirement for requirement in coordination.decision_requirements}
    requirement = requirements.get(decision.decision_id)
    if requirement is None:
        raise HTTPException(status_code=422, detail=f"Unknown decision requirement: {decision.decision_id}")
    allowed_options = [requirement.recommended_option, *requirement.alternative_options]
    if decision.selected_option not in allowed_options:
        raise HTTPException(
            status_code=422,
            detail=f"Selected option must be one of: {', '.join(allowed_options)}",
        )
    known_roles = {role.role_id for role in coordination.roles} | {role.name for role in coordination.roles}
    if decision.reviewer_role is not None and decision.reviewer_role not in known_roles:
        raise HTTPException(
            status_code=422,
            detail=f"Reviewer role must be one of the assigned coordination roles: {', '.join(sorted(known_roles))}",
        )
    recorded = HumanDecision(
        decision_id=requirement.decision_id,
        status="recorded",
        recommended_option=requirement.recommended_option,
        selected_option=decision.selected_option,
        reviewer_role=decision.reviewer_role,
        note=decision.note,
        recorded_state="decision_recorded",
        recorded_at=datetime.now(timezone.utc),
    )
    _decisions[requirement.decision_id] = recorded
    return recorded