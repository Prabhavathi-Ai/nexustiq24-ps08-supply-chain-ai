"""API models and in-memory endpoints for disruption notice intake."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from analysis.analytics import build_operational_analytics
from analysis.case import DECISION_TYPE_ROLE, build_case_status
from analysis.coordination import build_response_coordination
from analysis.impact import analyze_impact
from analysis.models import (
    ActionPlanResponse,
    CaseClosure,
    CaseStatusResponse,
    HumanDecision,
    ImpactResponse,
    OperationalAnalyticsResponse,
    PrioritizationResponse,
    ResponseCoordinationResponse,
    ScenarioComparisonResponse,
    ShipmentMovementResponse,
)
from analysis.movement import build_shipment_movement
from analysis.prioritization import prioritize_orders
from analysis.recommendations import build_action_plan
from analysis.scenarios import build_scenario_comparison
from gemini.errors import GeminiExtractionError
from gemini.extraction import extract_understanding
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding
from matching.models import MatchingResponse
from models import SupplyChainData
from services.data_loader import load_sample_data


MAX_DESCRIPTION_LENGTH = 5_000

CLOSE_AUTHORIZED_SESSION_ROLES = {
    "operations_manager",
    "Operations Manager",
}


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
_case_stages: dict[str, list[tuple[str, datetime]]] = {}
_case_closures: dict[str, CaseClosure] = {}


def _record_stage(disruption_id: str, stage: str) -> None:
    """Record one genuine stage completion with the current server time."""

    if disruption_id not in _case_stages:
        _case_stages[disruption_id] = []
    recorded = [entry for entry in _case_stages[disruption_id] if entry[0] == stage]
    if not recorded:
        _case_stages[disruption_id].append((stage, datetime.now(timezone.utc)))


def normalize_description(description: str) -> str:
    """Trim the notice and collapse repeated whitespace without changing words."""

    return " ".join(description.split())


def clear_disruptions() -> None:
    """Clear local records for isolated tests and local development."""

    _disruptions.clear()
    _understandings.clear()
    _decisions.clear()
    _case_stages.clear()
    _case_closures.clear()


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
    _record_stage(disruption_id, "understanding")
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
    _record_stage(disruption_id, "matching")
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
    impact = analyze_impact(disruption_id, matching, data)
    _record_stage(disruption_id, "impact")
    return impact


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
    _record_stage(disruption_id, "priorities")
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
    _record_stage(disruption_id, "recommendations")
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
    _record_stage(disruption_id, "analytics")
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
    _record_stage(disruption_id, "movement")
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
    coordination = build_response_coordination(disruption_id, impact, priorities, plan, data, decided=_decisions)
    _record_stage(disruption_id, "coordination")
    return coordination


@router.post("/{disruption_id}/scenarios", response_model=ScenarioComparisonResponse)
def simulate_disruption_scenarios(disruption_id: str) -> ScenarioComparisonResponse:
    """Return a deterministic comparison of supported what-if scenarios without executing anything."""

    impact, priorities, plan, data = _coordination_context(disruption_id)
    response = build_scenario_comparison(disruption_id, impact, priorities, plan, data)
    if response.scenarios:
        _record_stage(disruption_id, "scenarios")
    return response


@router.post("/{disruption_id}/decision", response_model=HumanDecision)
def record_disruption_decision(disruption_id: str, decision: DecisionRequest) -> HumanDecision:
    """Record a human decision for a valid requirement without executing anything."""

    impact, priorities, plan, data = _coordination_context(disruption_id)
    coordination = build_response_coordination(disruption_id, impact, priorities, plan, data, decided=_decisions)
    requirements = {requirement.decision_id: requirement for requirement in coordination.decision_requirements}
    requirement = requirements.get(decision.decision_id)
    if requirement is None:
        raise HTTPException(status_code=422, detail=f"Unknown decision requirement: {decision.decision_id}")
    if _decisions.get(requirement.decision_id) is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Decision {requirement.decision_id} has already been recorded; "
                "the original decision and audit entry are preserved and cannot be overwritten."
            ),
        )
    allowed_options = [requirement.recommended_option, *requirement.alternative_options]
    if decision.selected_option not in allowed_options:
        raise HTTPException(
            status_code=422,
            detail=f"Selected option must be one of: {', '.join(allowed_options)}",
        )
    if decision.reviewer_role is not None:
        assigned_role_id = DECISION_TYPE_ROLE.get(requirement.decision_type)
        assigned_role = next(
            (role for role in coordination.roles if role.role_id == assigned_role_id),
            None,
        )
        allowed_roles = {
            candidate
            for candidate in (
                assigned_role.role_id if assigned_role is not None else None,
                assigned_role.name if assigned_role is not None else None,
            )
            if candidate is not None
        }
        if decision.reviewer_role not in allowed_roles:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Reviewer role is not the role assigned to this decision requirement. "
                    f"Assigned: {assigned_role.name if assigned_role is not None else 'none'}"
                ),
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
    _record_stage(disruption_id, "coordination")
    return recorded


class CloseCaseRequest(BaseModel):
    """A human operator case-close record that never executes an action."""

    model_config = ConfigDict(extra="forbid")

    reviewer_role: str | None = None
    note: str | None = None


@router.get("/{disruption_id}/case", response_model=CaseStatusResponse)
def get_disruption_case(disruption_id: str) -> CaseStatusResponse:
    """Return the truthful case lifecycle, decision audit, and investigation timeline."""

    record = _disruptions.get(disruption_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    understanding = _understandings.get(disruption_id)
    coordination = None
    impact = None
    priorities = None
    plan = None
    if understanding is not None:
        data = load_sample_data()
        matching = match_understanding(disruption_id, understanding, data)
        impact = analyze_impact(disruption_id, matching, data)
        priorities = prioritize_orders(record.reported_at.date(), impact, data)
        plan = build_action_plan(impact, priorities)
        coordination = build_response_coordination(
            disruption_id, impact, priorities, plan, data, decided=_decisions
        )
    return build_case_status(
        disruption_id=disruption_id,
        reported_at=record.reported_at,
        understanding=understanding,
        coordination=coordination,
        impact=impact,
        priorities=priorities,
        plan=plan,
        stages=list(_case_stages.get(disruption_id, [])),
        decided=_decisions,
        closure=_case_closures.get(disruption_id),
    )


@router.post("/{disruption_id}/close", response_model=CaseStatusResponse)
def close_disruption_case(disruption_id: str, close: CloseCaseRequest) -> CaseStatusResponse:
    """Close a fully reviewed case; the system records the close and executes nothing."""

    record = _disruptions.get(disruption_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Disruption not found: {disruption_id}")
    understanding = _understandings.get(disruption_id)
    if understanding is None:
        raise HTTPException(status_code=409, detail="Disruption understanding is not available")
    if disruption_id in _case_closures:
        raise HTTPException(status_code=422, detail="Case is already closed")
    data = load_sample_data()
    matching = match_understanding(disruption_id, understanding, data)
    impact = analyze_impact(disruption_id, matching, data)
    priorities = prioritize_orders(record.reported_at.date(), impact, data)
    plan = build_action_plan(impact, priorities)
    coordination = build_response_coordination(
        disruption_id, impact, priorities, plan, data, decided=_decisions
    )
    requirements = coordination.decision_requirements
    recorded_ids = {
        requirement.decision_id
        for requirement in requirements
        if (entry := _decisions.get(requirement.decision_id)) is not None and entry.status == "recorded"
    }
    pending_ids = [requirement.decision_id for requirement in requirements if requirement.decision_id not in recorded_ids]
    if pending_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Pending decisions must be recorded first: {', '.join(pending_ids)}",
        )
    if close.reviewer_role is not None:
        known_roles = (
            {role.role_id for role in coordination.roles}
            | {role.name for role in coordination.roles}
            | set(DECISION_TYPE_ROLE.values())
            | CLOSE_AUTHORIZED_SESSION_ROLES
        )
        if close.reviewer_role not in known_roles:
            raise HTTPException(
                status_code=422,
                detail=f"Reviewer role must be one of the assigned coordination roles: {', '.join(sorted(known_roles))}",
            )
    _case_closures[disruption_id] = CaseClosure(
        disruption_id=disruption_id,
        closed_at=datetime.now(timezone.utc),
        reviewer_role=close.reviewer_role,
        note=close.note,
    )
    return build_case_status(
        disruption_id=disruption_id,
        reported_at=record.reported_at,
        understanding=understanding,
        coordination=coordination,
        impact=impact,
        priorities=priorities,
        plan=plan,
        stages=list(_case_stages.get(disruption_id, [])),
        decided=_decisions,
        closure=_case_closures[disruption_id],
    )