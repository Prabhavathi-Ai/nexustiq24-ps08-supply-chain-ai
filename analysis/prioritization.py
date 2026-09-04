"""Deterministic urgency, severity, and affected-order prioritization."""

from datetime import date

from models import SupplyChainData

from .models import (
        EvidenceReference,
    ImpactResponse,
    InventoryShortage,
    PrioritizedOrder,
    PrioritizationResponse,
)


def calculate_shortage(order_quantity: int, available_quantity: int) -> int:
    """Return the deterministic quantity shortfall, never below zero."""

    return max(order_quantity - available_quantity, 0)


def _date_points(required_date: date | None, reference_date: date, reasons: list[str]) -> int:
    if required_date is None:
        reasons.append("Required date is unavailable, so date urgency could not be scored.")
        return 0
    days_until_due = (required_date - reference_date).days
    if days_until_due <= 0:
        reasons.append("Required date is today or overdue relative to the disruption report date (+4).")
        return 4
    if days_until_due <= 3:
        reasons.append("Required date is within three days of the disruption report date (+3).")
        return 3
    if days_until_due <= 7:
        reasons.append("Required date is within seven days of the disruption report date (+2).")
        return 2
    reasons.append("Required date is more than seven days after the disruption report date (+1).")
    return 1


def _priority_points(priority: str, reasons: list[str]) -> int:
    if priority.casefold() == "high":
        reasons.append("Order priority is high (+2).")
        return 2
    reasons.append(f"Order priority is {priority} (+0).")
    return 0


def _status_points(status: str, reasons: list[str]) -> int:
    if status.casefold() == "open":
        reasons.append("Order status is open and active (+1).")
        return 1
    reasons.append(f"Order status is {status}; it is not treated as active risk (+0).")
    return 0


def _class_points(classification: str, reasons: list[str]) -> int:
    if classification == "direct":
        reasons.append("Order has a direct impact classification (+2).")
        return 2
    if classification == "downstream":
        reasons.append("Order is downstream of the affected supply-chain path (+1).")
        return 1
    reasons.append("Impact classification is unknown (+0).")
    return 0


def _severity(score: int, shortage: int, required_date: date | None, reference_date: date) -> tuple[str, str]:
    due_soon = required_date is not None and (required_date - reference_date).days <= 3
    if shortage > 0 and due_soon:
        return "critical", "Critical because a shortage is calculated and the required date is within three days."
    if shortage > 0 or score >= 8:
        return "high", "High because the deterministic score is at least 8 or a shortage is calculated."
    if score >= 4:
        return "medium", "Medium because the deterministic score is between 4 and 7."
    return "low", "Low because the deterministic score is below 4."


def _urgency(score: int) -> str:
    if score >= 8:
        return "urgent"
    if score >= 4:
        return "elevated"
    return "monitor"


def prioritize_orders(
    disruption_report_date: date,
    impact: ImpactResponse,
    data: SupplyChainData,
) -> PrioritizationResponse:
    """Rank only affected, active orders using bounded deterministic rules.

    Maximum score is 12: date 4, order priority 2, active status 1,
    shortage 3, and impact classification 2.
    """

    orders = {order.id: order for order in data.orders}
    customers = {customer.id: customer for customer in data.customers}
    inventory_by_sku = {inventory.sku_id: inventory for inventory in data.inventory}
    shipments_by_sku: dict[str, list] = {}
    for shipment in data.shipments:
        shipments_by_sku.setdefault(shipment.sku_id, []).append(shipment)
    impact_orders = {
        record.entity_id: record
        for record in impact.downstream_potential_impact + impact.direct_impact
        if record.entity_type == "order"
    }
    prioritized: list[PrioritizedOrder] = []
    missing: list[str] = []

    for order_id, impact_record in impact_orders.items():
        order = orders.get(order_id)
        if order is None:
            missing.append(f"Order record {order_id} is unavailable.")
            continue
        if order.status.casefold() in {"completed", "cancelled", "canceled"}:
            continue
        customer = customers.get(order.customer_id)
        reasons: list[str] = ["Impact: the order is connected to the matched supply-chain path."]
        evidence = [f"Order {order.id} came from source record {impact_record.source_record} with fact {impact_record.supporting_fact}."]
        evidence_references = list(impact_record.evidence_references)
        evidence_references.extend([
            EvidenceReference(
                evidence_id=f"prioritization:order:{order.id}:required-date",
                entity_type="order", record_id=order.id, field="required_date",
                value=str(order.required_date), relationship="priority input",
                source_stage="prioritization",
            ),
            EvidenceReference(
                evidence_id=f"prioritization:order:{order.id}:priority",
                entity_type="order", record_id=order.id, field="priority",
                value=order.priority, relationship="priority input",
                source_stage="prioritization",
            ),
            EvidenceReference(
                evidence_id=f"prioritization:order:{order.id}:status",
                entity_type="order", record_id=order.id, field="status",
                value=order.status, relationship="priority input",
                source_stage="prioritization",
            ),
        ])
        insufficient: list[str] = []
        score = _date_points(order.required_date, disruption_report_date, reasons)
        score += _priority_points(order.priority, reasons)
        score += _status_points(order.status, reasons)
        score += _class_points(impact_record.classification, reasons)

        inventory = inventory_by_sku.get(order.sku_id)
        shortage: InventoryShortage | None = None
        shortage_quantity = 0
        if inventory is None:
            insufficient.append(f"No inventory record is linked to SKU {order.sku_id}; shortage was not guessed.")
        else:
            shortage_quantity = calculate_shortage(order.quantity, inventory.quantity)
            shortage = InventoryShortage(
                order_quantity=order.quantity,
                available_quantity=inventory.quantity,
                shortage_quantity=shortage_quantity,
            )
            if shortage_quantity > 0:
                score += 3
                reasons.append(f"Available inventory ({inventory.quantity}) is below order quantity ({order.quantity}); shortage is {shortage_quantity} (+3).")
            else:
                reasons.append(f"Available inventory ({inventory.quantity}) covers order quantity ({order.quantity}) (+0).")
        shipment_records = shipments_by_sku.get(order.sku_id, [])
        shipment_ids = [shipment.id for shipment in shipment_records]
        route_ids = [shipment.route_id for shipment in shipment_records]
        container_ids = [shipment.container_id for shipment in shipment_records]
        if not customer:
            insufficient.append(f"Customer record {order.customer_id} is unavailable.")
        severity, severity_reason = _severity(score, shortage_quantity, order.required_date, disruption_report_date)
        reasons.append(severity_reason)
        prioritized.append(PrioritizedOrder(
            order_id=order.id,
            customer_id=order.customer_id,
            customer_name=customer.name if customer else "Unknown customer",
            required_date=order.required_date,
            affected_sku=order.sku_id,
            shipment_ids=shipment_ids,
            route_ids=route_ids,
            container_ids=container_ids,
            impact_classification=impact_record.classification,
            priority_score=score,
            urgency=_urgency(score),
            severity=severity,
            reasons=reasons,
            evidence=evidence,
            inventory_shortage=shortage,
            insufficient_information=insufficient,
                    evidence_references=evidence_references,
        ))

    prioritized.sort(key=lambda order: (-order.priority_score, order.order_id))
    if not prioritized:
        state = "no_affected_orders" if not missing else "insufficient_information"
        return PrioritizationResponse(overall_state=state, insufficient_information=missing)
    if impact.impact_state == "review_required":
        state = "review_required"
    elif any(order.insufficient_information for order in prioritized):
        state = "insufficient_information"
    else:
        state = "prioritized"
    highest_score = prioritized[0].priority_score
    highest_severity = prioritized[0].severity
    highest_urgency = prioritized[0].urgency
    return PrioritizationResponse(
        overall_state=state,
        overall_urgency=highest_urgency,
        overall_severity=highest_severity,
        orders=prioritized,
        insufficient_information=missing,
        warnings=list(impact.warnings),
    )