"""Deterministic decision audit and case-history builder for a disruption.

Phase 20 derives a truthful case lifecycle from the stages the API actually
completed, the decisions a human actually recorded, and a case close an
operator actually performed. Every timeline timestamp is a genuine server
time captured at the moment of the corresponding call; nothing is fabricated,
and the system never claims an action was executed.
"""

from datetime import datetime, timezone

from analysis.models import (
    ActionPlanResponse,
    CaseClosure,
    CaseStatusResponse,
    CaseTimelineEntry,
    DecisionAudit,
    DecisionRequirement,
    EvidenceReference,
    HumanDecision,
    ImpactResponse,
    PrioritizationResponse,
    ResponseCoordinationResponse,
    ResponseRole,
)
from gemini.models import DisruptionUnderstanding


DECISION_TYPE_ROLE: dict[str, str] = {
    "approve-recommended-action": "supply_chain_planner",
    "confirm-shipment-review": "logistics_transportation",
    "resolve-inventory-review": "inventory_warehouse",
    "confirm-customer-communication": "customer_service",
}

_STAGE_LABELS: dict[str, str] = {
    "understanding": "Understanding extracted from the disruption notice",
    "matching": "Records matched to the disruption",
    "impact": "Operational impact analyzed",
    "priorities": "Affected orders prioritized",
    "recommendations": "Recommended course and options prepared",
    "analytics": "Operational analytics computed",
    "movement": "Shipment movement evidence prepared",
    "coordination": "Response coordination ready; decisions require review",
    "scenarios": "What-if scenario comparison prepared",
}

_EXECUTION_STATUS_WARNING = (
    "Nothing was executed automatically; every operational action remains "
    "pending explicit operator execution outside this assistant."
)

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _audit_entries(
    requirements: list[DecisionRequirement],
    roles_by_id: dict[str, ResponseRole],
    decided: dict[str, HumanDecision],
) -> list[DecisionAudit]:
    """One audit entry per requirement, reflecting the recorded decision when present."""

    entries: list[DecisionAudit] = []
    for requirement in requirements:
        role = roles_by_id.get(DECISION_TYPE_ROLE.get(requirement.decision_type, ""))
        recorded = decided.get(requirement.decision_id)
        record = recorded if recorded is not None and recorded.status == "recorded" else None
        entries.append(DecisionAudit(
            decision_id=requirement.decision_id,
            requirement_id=requirement.decision_id,
            decision_type=requirement.decision_type,
            assigned_reviewer_role_id=role.role_id if role is not None else None,
            assigned_reviewer_role=role.name if role is not None else None,
            recommended_option=requirement.recommended_option,
            selected_option=record.selected_option if record is not None else None,
            reviewer_role=record.reviewer_role if record is not None else None,
            review_note=record.note if record is not None else None,
            decision_status="recorded" if record is not None else "pending",
            decided_at=record.recorded_at if record is not None else None,
            evidence_references=list(requirement.evidence_references),
            execution_status="not_executed",
        ))
    return entries


def _timeline(
    *,
    reported_at: datetime,
    stages: dict[str, datetime],
    requirements: list[DecisionRequirement],
    decided: dict[str, HumanDecision],
    closure: CaseClosure | None,
) -> list[CaseTimelineEntry]:
    """Chronological history from genuine call stages, decisions, and closure only."""

    requirement_by_id = {requirement.decision_id: requirement for requirement in requirements}
    entries: list[CaseTimelineEntry] = [
        CaseTimelineEntry(
            stage="intake",
            label="Disruption notice received",
            timestamp=reported_at,
            evidence_references=[],
        )
    ]
    for stage in (
        "understanding",
        "matching",
        "impact",
        "priorities",
        "recommendations",
        "analytics",
        "movement",
        "coordination",
        "scenarios",
    ):
        if stage not in stages:
            continue
        entries.append(CaseTimelineEntry(
            stage=stage,
            label=_STAGE_LABELS[stage],
            timestamp=stages[stage],
            evidence_references=[],
        ))
    for decision_id in sorted(decided):
        record = decided[decision_id]
        if record.status != "recorded":
            continue
        requirement = requirement_by_id.get(decision_id)
        entries.append(CaseTimelineEntry(
            stage="decision",
            label=f"Decision recorded: {record.selected_option or decision_id}",
            timestamp=record.recorded_at,
            evidence_references=list(requirement.evidence_references) if requirement else [],
        ))
    if closure is not None:
        label = "Case closed"
        if closure.reviewer_role:
            label += f" by {closure.reviewer_role}"
        entries.append(CaseTimelineEntry(
            stage="close",
            label=label,
            timestamp=closure.closed_at,
            evidence_references=[],
        ))
    entries.sort(key=lambda entry: entry.timestamp or _EPOCH)
    return entries


def build_case_status(
    *,
    disruption_id: str,
    reported_at: datetime,
    understanding: DisruptionUnderstanding | None,
    coordination: ResponseCoordinationResponse | None,
    impact: ImpactResponse | None,
    priorities: PrioritizationResponse | None,
    plan: ActionPlanResponse | None,
    stages: list[tuple[str, datetime]],
    decided: dict[str, HumanDecision],
    closure: CaseClosure | None,
) -> CaseStatusResponse:
    """Return the case lifecycle, decision audit, and truthful timeline for a disruption."""

    stage_times: dict[str, datetime] = {}
    for stage, timestamp in stages:
        stage_times.setdefault(stage, timestamp)

    if understanding is None:
        roles: list[ResponseRole] = []
        roles_by_id: dict[str, ResponseRole] = {}
        requirements: list[DecisionRequirement] = []
    else:
        roles = list(coordination.roles) if coordination is not None else []
        roles_by_id = {role.role_id: role for role in roles}
        requirements = list(coordination.decision_requirements) if coordination is not None else []

    requires_decisions = bool(requirements)
    recorded_count = sum(
        1
        for requirement in requirements
        if (record := decided.get(requirement.decision_id)) is not None and record.status == "recorded"
    )

    if closure is not None:
        lifecycle_state = "closed"
    elif understanding is None:
        lifecycle_state = "new"
    elif not requires_decisions:
        if coordination is not None and coordination.coordination_state == "insufficient_information":
            lifecycle_state = "investigating"
        else:
            lifecycle_state = "no_action_required"
    elif "coordination" not in stage_times:
        lifecycle_state = "investigating"
    elif recorded_count == len(requirements):
        lifecycle_state = "decision_recorded"
    else:
        lifecycle_state = "awaiting_decisions"

    warnings: list[str] = list(coordination.warnings) if coordination is not None else []
    if _EXECUTION_STATUS_WARNING not in warnings:
        warnings.append(_EXECUTION_STATUS_WARNING)

    return CaseStatusResponse(
        disruption_id=disruption_id,
        lifecycle_state=lifecycle_state,
        requires_decisions=requires_decisions,
        decision_progress={
            "required": len(requirements),
            "recorded": recorded_count,
            "pending": len(requirements) - recorded_count,
        },
        roles=roles,
        decision_requirements=requirements,
        decision_audit=_audit_entries(requirements, roles_by_id, decided),
        timeline=_timeline(
            reported_at=reported_at,
            stages=stage_times,
            requirements=requirements,
            decided=decided,
            closure=closure,
        ),
        close=closure,
        execution_status="not_executed",
        warnings=warnings,
    )