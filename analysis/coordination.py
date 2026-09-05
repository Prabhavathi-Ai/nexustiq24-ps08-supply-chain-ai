"""Deterministic response coordination and human decision workflow.

Phase 16 adds a human-in-the-loop coordination layer. Reviewer roles are
assigned only from established impact, priority, and action-plan evidence;
decision requirements mirror the deterministic action options; and the human
decision gate records a reviewer decision without executing anything. This
module never sends messages, places orders, or modifies operational records.
"""

from analysis.models import (
    ActionPlanResponse,
    DecisionRequirement,
    EvidenceReference,
    HumanDecision,
    ImpactResponse,
    PrioritizationResponse,
    ResponseCoordinationResponse,
    ResponseRole,
)
from models import SupplyChainData


NO_COORDINATION_NEXT_STEP = (
    "No operational response coordination is required because no impact was "
    "established from the committed records."
)
COORDINATION_NEXT_STEP = (
    "A human decision is required before any option is executed; "
    "the system only records the decision."
)
RECORD_ONLY_WARNING = (
    "The coordination layer records demo decisions only; it sends no messages "
    "and modifies no operational records."
)


def _affected_shipment_ids(impact: ImpactResponse) -> list[str]:
    return sorted({
        record.entity_id
        for record in impact.direct_impact + impact.downstream_potential_impact
        if record.entity_type == "shipment"
    })


def _coordinated_reference(
    evidence_id: str,
    entity_type: str,
    record_id: str,
    field: str,
    value: str,
) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=evidence_id,
        entity_type=entity_type,
        record_id=record_id,
        field=field,
        value=value,
        relationship="coordination input",
        source_stage="coordination",
    )


def _planner_role(
    action_plan: ActionPlanResponse,
    order_ids: list[str],
) -> ResponseRole:
    related_order_ids = sorted({
        order_id
        for option in action_plan.options
        for order_id in option.affected_order_ids
    })
    if not related_order_ids:
        related_order_ids = order_ids
    return ResponseRole(
        role_id="supply_chain_planner",
        name="Supply Chain Planner",
        priority=1,
        responsibility=(
            "Confirm the recommended course and the priority order allocation "
            "before any operator executes an action."
        ),
        reason=(
            f"The deterministic action plan defines {len(action_plan.options)} "
            "supported options; a human must approve the recommended course first."
        ),
        related_order_ids=related_order_ids,
        related_shipment_ids=[],
        evidence_references=list(action_plan.evidence_references),
    )


def _logistics_role(
    impact: ImpactResponse,
    shipments_by_id: dict,
) -> ResponseRole:
    shipment_ids = _affected_shipment_ids(impact)
    role_references = [
        _coordinated_reference(
            f"coordination:role:logistics_transportation:{shipment_id}",
            "shipment",
            shipment_id,
            "status",
            shipment.status,
        )
        for shipment_id in shipment_ids
        if (shipment := shipments_by_id.get(shipment_id)) is not None
    ]
    return ResponseRole(
        role_id="logistics_transportation",
        name="Logistics & Transportation",
        priority=2,
        responsibility=(
            "Review the affected shipments and their committed route and "
            "movement evidence."
        ),
        reason=(
            "Affected shipment(s) "
            + ", ".join(shipment_ids)
            + " were established from matched operational records; "
            "movement evidence reflects only committed planned routes."
        ),
        related_order_ids=[],
        related_shipment_ids=shipment_ids,
        evidence_references=role_references,
    )


def _inventory_role(
    shortage_orders: list,
    inventory_insufficient_orders: list,
) -> ResponseRole:
    related_order_ids = list(dict.fromkeys(
        [order.order_id for order in shortage_orders]
        + [order.order_id for order in inventory_insufficient_orders]
    ))
    role_references = [
        _coordinated_reference(
            f"coordination:role:inventory_warehouse:{order.order_id}:shortage",
            "order",
            order.order_id,
            "shortage_quantity",
            str(order.inventory_shortage.shortage_quantity),
        )
        for order in shortage_orders
        if order.inventory_shortage is not None
    ]
    reason = (
        "Order quantities exceed linked available inventory or inventory "
        "coverage is not confirmed; warehouse review is warranted."
    )
    if inventory_insufficient_orders:
        reason += " Inventory evidence is incomplete for some affected orders."
    return ResponseRole(
        role_id="inventory_warehouse",
        name="Inventory & Warehouse",
        priority=3,
        responsibility=(
            "Review inventory availability and shortage handling without "
            "reserving stock."
        ),
        reason=reason,
        related_order_ids=related_order_ids,
        related_shipment_ids=[],
        evidence_references=role_references,
    )


def _customer_role(
    order_ids: list[str],
    customer_ids: list[str],
    orders_by_id: dict,
) -> ResponseRole:
    role_references = [
        _coordinated_reference(
            f"coordination:role:customer_service:{order_id}",
            "order",
            order_id,
            "customer_id",
            order.customer_id,
        )
        for order_id in order_ids
        if (order := orders_by_id.get(order_id)) is not None
    ]
    return ResponseRole(
        role_id="customer_service",
        name="Customer Service",
        priority=4,
        responsibility=(
            "Confirm the communication update for affected accounts; "
            "no message is sent by the system."
        ),
        reason=(
            "Affected orders reference customer(s) "
            + ", ".join(customer_ids)
            + "; account updates need a recorded decision."
        ),
        related_order_ids=list(order_ids),
        related_shipment_ids=[],
        evidence_references=role_references,
    )


def build_response_coordination(
    disruption_id: str,
    impact: ImpactResponse,
    priorities: PrioritizationResponse,
    action_plan: ActionPlanResponse,
    data: SupplyChainData,
    decided: dict[str, HumanDecision] | None = None,
) -> ResponseCoordinationResponse:
    """Assign reviewers and decision requirements deterministically from evidence."""

    decided = decided or {}
    orders_by_id = {order.id: order for order in data.orders}
    shipments_by_id = {shipment.id: shipment for shipment in data.shipments}

    order_ids = [order.order_id for order in priorities.orders]
    customer_ids = sorted({order.customer_id for order in priorities.orders})
    affected_shipment_ids = _affected_shipment_ids(impact)
    shortage_orders = [
        order
        for order in priorities.orders
        if order.inventory_shortage is not None and order.inventory_shortage.shortage_quantity > 0
    ]
    inventory_insufficient_orders = [
        order
        for order in priorities.orders
        if any("inventory" in message.lower() for message in order.insufficient_information)
    ]

    established = bool(order_ids or affected_shipment_ids or action_plan.options)
    if impact.impact_state == "no_impact" or not established:
        return ResponseCoordinationResponse(
            disruption_id=disruption_id,
            coordination_state="no_response_coordination_required",
            roles=[],
            decision_requirements=[],
            human_decision=None,
            recommended_next_step=NO_COORDINATION_NEXT_STEP,
            evidence=[
                "No affected orders, shipments, or supported options were "
                "established from the committed records."
            ],
            evidence_references=[],
            warnings=[
                "No affected records or orders were established from the available data."
            ],
        )

    roles: list[ResponseRole] = []
    if action_plan.options:
        roles.append(_planner_role(action_plan, order_ids))
    if affected_shipment_ids:
        roles.append(_logistics_role(impact, shipments_by_id))
    if shortage_orders or inventory_insufficient_orders:
        roles.append(_inventory_role(shortage_orders, inventory_insufficient_orders))
    if order_ids:
        roles.append(_customer_role(order_ids, customer_ids, orders_by_id))

    decision_requirements: list[DecisionRequirement] = []
    if action_plan.recommended_option_id:
        decision_requirements.append(DecisionRequirement(
            decision_id=f"decision:{disruption_id}:approve-recommended-action",
            decision_type="approve-recommended-action",
            question="Approve the recommended course of action before it may be executed?",
            recommended_option=action_plan.recommended_course,
            alternative_options=[
                option.name
                for option in action_plan.options
                if option.option_id != action_plan.recommended_option_id
            ],
            rationale=list(action_plan.why),
            evidence_references=list(action_plan.evidence_references),
        ))
    if affected_shipment_ids:
        decision_requirements.append(DecisionRequirement(
            decision_id=f"decision:{disruption_id}:confirm-shipment-review",
            decision_type="confirm-shipment-review",
            question=(
                "Confirm the logistics review outcome for "
                + ", ".join(affected_shipment_ids)
                + "?"
            ),
            recommended_option="Record shipment review outcome",
            alternative_options=["Escalate logistics review", "No corrective movement required"],
            rationale=[
                "Affected shipments were established from matched records.",
                "Movement evidence reflects only committed planned routes.",
            ],
            evidence_references=[
                reference
                for role in roles
                if role.role_id == "logistics_transportation"
                for reference in role.evidence_references
            ],
        ))
    if shortage_orders or inventory_insufficient_orders:
        decision_requirements.append(DecisionRequirement(
            decision_id=f"decision:{disruption_id}:resolve-inventory-review",
            decision_type="resolve-inventory-review",
            question="Confirm the inventory review outcome for the shortage orders?",
            recommended_option="Record inventory review outcome",
            alternative_options=["Escalate inventory review", "No inventory action"],
            rationale=[
                "Linked available inventory does not cover all affected order quantities.",
            ],
            evidence_references=[
                reference
                for role in roles
                if role.role_id == "inventory_warehouse"
                for reference in role.evidence_references
            ],
        ))
    if order_ids:
        decision_requirements.append(DecisionRequirement(
            decision_id=f"decision:{disruption_id}:confirm-customer-communication",
            decision_type="confirm-customer-communication",
            question="Confirm the customer communication update for affected accounts?",
            recommended_option="Record communication decision; no message will be sent",
            alternative_options=["Hold customer communication"],
            rationale=[
                "Affected orders reference customers that may need an update.",
            ],
            evidence_references=[
                reference
                for role in roles
                if role.role_id == "customer_service"
                for reference in role.evidence_references
            ],
        ))

    primary_requirement = decision_requirements[0] if decision_requirements else None
    if primary_requirement is None:
        human_decision = None
    else:
        recorded = decided.get(primary_requirement.decision_id)
        if recorded is not None and recorded.status == "recorded":
            human_decision = recorded
        else:
            human_decision = HumanDecision(
                decision_id=primary_requirement.decision_id,
                status="pending",
                recommended_option=primary_requirement.recommended_option,
                selected_option=None,
                reviewer_role=None,
                note=None,
                recorded_state="pending_human_decision",
                recorded_at=None,
            )

    evidence: list[str] = []
    if action_plan.recommended_course:
        evidence.append(f"Recommended course: {action_plan.recommended_course}.")
    if affected_shipment_ids:
        evidence.append(f"Affected shipments for coordination: {', '.join(affected_shipment_ids)}.")
    if order_ids:
        evidence.append(f"Affected orders for coordination: {', '.join(order_ids)}.")
        evidence.append(f"Affected customers for coordination: {', '.join(customer_ids)}.")

    warnings: list[str] = list(dict.fromkeys(list(impact.warnings) + list(priorities.warnings)))
    if roles:
        warnings.append(RECORD_ONLY_WARNING)
    if impact.impact_state == "review_required" or priorities.overall_state == "review_required":
        warnings.append("Review is required before any decision can be recorded.")

    if impact.impact_state == "insufficient_information" or priorities.overall_state == "insufficient_information":
        coordination_state = "insufficient_information"
    else:
        coordination_state = "response_coordination_required"

    all_references: list[EvidenceReference] = []
    seen_ids: set[str] = set()
    for role in roles:
        for reference in role.evidence_references:
            if reference.evidence_id not in seen_ids:
                seen_ids.add(reference.evidence_id)
                all_references.append(reference)

    return ResponseCoordinationResponse(
        disruption_id=disruption_id,
        coordination_state=coordination_state,
        roles=roles,
        decision_requirements=decision_requirements,
        human_decision=human_decision,
        recommended_next_step=COORDINATION_NEXT_STEP,
        evidence=evidence,
        evidence_references=all_references,
        warnings=warnings,
    )