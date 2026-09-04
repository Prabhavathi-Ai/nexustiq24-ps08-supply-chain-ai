"""Deterministic, human-in-the-loop action options for disruption response."""

from .models import ActionOption, ActionPlanResponse, ImpactResponse, PrioritizationResponse


def build_action_plan(
    impact: ImpactResponse,
    priorities: PrioritizationResponse,
) -> ActionPlanResponse:
    """Build supported options without executing or inventing operational actions."""

    decision_notice = "The system only recommends options; an operator must decide and execute any action."
    if impact.impact_state == "review_required" or priorities.overall_state == "review_required":
        decision_notice = "Review is required before selecting an option; the system does not choose between ambiguous records or execute actions."
    if impact.impact_state == "no_impact" or priorities.overall_state == "no_affected_orders":
        return ActionPlanResponse(
            overall_state="no_impact",
            recommended_course="No action recommendation is made because no operational impact was established.",
            operator_decision_required=decision_notice,
            warnings=["No affected records or orders were established from the available data."],
        )

    order_ids = [order.order_id for order in priorities.orders]
    top_order = priorities.orders[0] if priorities.orders else None
    evidence = list(top_order.evidence) if top_order else []
    why = list(top_order.reasons) if top_order else []
    options: list[ActionOption] = []

    if top_order:
        options.append(ActionOption(
            option_id="prioritize-order-review",
            name="Prioritize affected orders for operator review",
            description=f"Review {top_order.order_id} first using its deterministic priority result.",
            suitability="Supported because the order is affected and ranked highest by the existing priority rules.",
            trade_offs=["Lower-ranked affected orders may wait for review."],
            risks=["This does not reserve inventory or change the order."],
            prerequisites=["Operator confirmation of the business priority is required."],
            evidence=evidence,
            affected_order_ids=order_ids,
        ))

    missing_inventory = [
        message
        for order in priorities.orders
        for message in order.insufficient_information
        if "inventory" in message.lower()
    ]
    if missing_inventory and options:
        options[0].prerequisites.extend(missing_inventory)

    matched_shipments = [item for item in impact.downstream_potential_impact if item.entity_type == "shipment"]
    if matched_shipments:
        shipment_ids = [item.entity_id for item in matched_shipments]
        shipment_evidence = [item.reason for item in matched_shipments]
        options.append(ActionOption(
            option_id="investigate-affected-shipment",
            name="Investigate affected shipment path",
            description=f"Review shipment status and route records for {', '.join(shipment_ids)}.",
            suitability="Supported because the shipment is linked to the matched disruption path.",
            trade_offs=["Investigation may not change delivery timing."],
            risks=["Current data does not confirm live location or alternate logistics capacity."],
            prerequisites=["Verify current shipment and route status with the responsible operator."],
            evidence=shipment_evidence,
            affected_order_ids=order_ids,
        ))

    shortage_orders = [order for order in priorities.orders if order.inventory_shortage and order.inventory_shortage.shortage_quantity > 0]
    if shortage_orders:
        shortage_ids = [order.order_id for order in shortage_orders]
        shortage_evidence = [evidence for order in shortage_orders for evidence in order.evidence]
        options.append(ActionOption(
            option_id="review-inventory-availability",
            name="Review inventory availability for shortage orders",
            description=f"Review inventory records connected to {', '.join(shortage_ids)}.",
            suitability="Supported because the dataset shows order quantities above linked available inventory.",
            trade_offs=["Reviewing one order's stock position does not allocate shared inventory."],
            risks=["The dataset does not establish replenishment or reallocation capability."],
            prerequisites=["Confirm current stock and allocation policy with the operator."],
            evidence=shortage_evidence,
            affected_order_ids=shortage_ids,
        ))

    if not options:
        return ActionPlanResponse(
            overall_state="insufficient_information",
            recommended_course="No supported action option can be recommended from the available records.",
            operator_decision_required=decision_notice,
            warnings=["Additional operational information is required before considering an option."],
        )

    state = "review_required" if impact.impact_state == "review_required" or priorities.overall_state == "review_required" else "recommendation_available"
    return ActionPlanResponse(
        overall_state=state,
        recommended_option_id=options[0].option_id,
        recommended_course=options[0].name,
        why=why,
        options=options,
        evidence=evidence,
        operator_decision_required=decision_notice,
        warnings=list(priorities.warnings),
    )