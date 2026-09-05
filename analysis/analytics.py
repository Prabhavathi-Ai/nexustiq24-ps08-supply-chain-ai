"""Deterministic descriptive statistics over committed operational records.

Analytics only summarizes records already present in the committed dataset and
results already computed by the deterministic investigation pipeline. It never
determines which entity is affected, classifies impact, or scores risk; those
decisions remain the responsibility of matching and impact analysis.
"""

from collections import Counter
from collections.abc import Sequence

from analysis.models import (
    DisruptionStatistics,
    ImpactResponse,
    InventoryStatistics,
    InvestigationStatistics,
    OperationalAnalyticsResponse,
    OrderQuantityStatistics,
    ShipmentStatistics,
)
from models import Order, SupplyChainData

INACTIVE_ORDER_STATUSES = {"completed", "cancelled", "canceled"}
INACTIVE_SHIPMENT_STATUSES = {"completed", "cancelled", "canceled"}


def _median(values: Sequence[int]) -> float | None:
    """Return the median of a numeric sequence, or None when it is empty."""

    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _average(values: Sequence[int]) -> float | None:
    """Return the arithmetic mean, or None when it is empty."""

    if not values:
        return None
    return sum(values) / len(values)


def _is_active_order(status: str) -> bool:
    return status.casefold() not in INACTIVE_ORDER_STATUSES


def _is_active_shipment(status: str) -> bool:
    return status.casefold() not in INACTIVE_SHIPMENT_STATUSES


def _assert_unique_ids(records: Sequence[object], entity_name: str) -> None:
    record_ids = [getattr(record, "id", None) for record in records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError(f"Duplicate {entity_name} id in analytics input")


def _active_orders(data: SupplyChainData) -> list[Order]:
    _assert_unique_ids(data.orders, "order")
    return [order for order in data.orders if _is_active_order(order.status)]


def _available_quantity_by_sku(data: SupplyChainData) -> dict[str, int]:
    totals: dict[str, int] = {}
    for inventory in data.inventory:
        totals[inventory.sku_id] = totals.get(inventory.sku_id, 0) + inventory.quantity
    return totals


def _aggregated_shortage(
    orders: Sequence[Order],
    available: dict[str, int],
) -> tuple[int | None, list[str], bool]:
    """Sum deterministic order shortfalls, claiming incompleteness when a SKU is missing."""

    total = 0
    shortage_skus: list[str] = []
    for order in orders:
        available_quantity = available.get(order.sku_id)
        if available_quantity is None:
            return None, [], True
        shortage = max(order.quantity - available_quantity, 0)
        if shortage > 0:
            total += shortage
            shortage_skus.append(order.sku_id)
    return total, sorted(set(shortage_skus)), False


def order_quantity_statistics(data: SupplyChainData) -> OrderQuantityStatistics:
    """Describe quantities, priorities, and statuses of active order records."""

    active = _active_orders(data)
    quantities = [order.quantity for order in active]
    return OrderQuantityStatistics(
        total_active_orders=len(active),
        total_ordered_quantity=sum(quantities),
        average_order_quantity=_average(quantities),
        median_order_quantity=_median(quantities),
        minimum_order_quantity=min(quantities) if quantities else None,
        maximum_order_quantity=max(quantities) if quantities else None,
        active_order_ids=[order.id for order in active],
        priority_counts=dict(Counter(order.priority for order in active)),
        status_counts=dict(Counter(order.status for order in active)),
    )


def inventory_statistics(
    data: SupplyChainData,
    *,
    active_orders: Sequence[Order],
) -> InventoryStatistics:
    """Aggregate available inventory and deterministic order shortage totals."""

    available = _available_quantity_by_sku(data)
    quantities = sorted(available.values())
    shortage, shortage_skus, incomplete = _aggregated_shortage(active_orders, available)
    return InventoryStatistics(
        total_available_quantity=sum(quantities),
        average_inventory_per_sku=_average(quantities),
        median_inventory_per_sku=_median(quantities),
        tracked_sku_ids=sorted(available),
        total_shortage_quantity=shortage,
        shortage_sku_ids=shortage_skus,
        shortage_incomplete=incomplete,
    )


def shipment_statistics(data: SupplyChainData) -> ShipmentStatistics:
    """Describe active shipment records and their status distribution."""

    _assert_unique_ids(data.shipments, "shipment")
    active = [shipment for shipment in data.shipments if _is_active_shipment(shipment.status)]
    return ShipmentStatistics(
        total_active_shipments=len(active),
        active_shipment_ids=[shipment.id for shipment in active],
        shipment_status_counts=dict(Counter(shipment.status for shipment in active)),
    )


def disruption_statistics(data: SupplyChainData) -> DisruptionStatistics:
    """Count committed disruption records grouped by event type."""

    return DisruptionStatistics(
        total_disruptions=len(data.disruptions),
        counts_by_event_type=dict(Counter(disruption.type for disruption in data.disruptions)),
    )


def investigation_statistics(impact: ImpactResponse, data: SupplyChainData) -> InvestigationStatistics:
    """Summarize only the records the deterministic impact stage actually established."""

    orders = {order.id: order for order in data.orders}
    available = _available_quantity_by_sku(data)
    order_records = [
        (orders[item.entity_id], item)
        for item in impact.direct_impact + impact.downstream_potential_impact
        if item.entity_type == "order"
        and item.entity_id in orders
        and _is_active_order(orders[item.entity_id].status)
    ]
    affected_order_ids = [order.id for order, _ in order_records]
    affected_shipment_ids = sorted({
        item.entity_id
        for item in impact.direct_impact + impact.downstream_potential_impact
        if item.entity_type == "shipment"
    })
    affected_customer_ids = sorted({order.customer_id for order, _ in order_records})
    affected_sku_ids = sorted({order.sku_id for order, _ in order_records})
    affected_order_quantity = sum(order.quantity for order, _ in order_records)
    shortage, _, incomplete = _aggregated_shortage([order for order, _ in order_records], available)
    shortage_rate = (
        shortage / affected_order_quantity
        if shortage is not None and affected_order_quantity > 0
        else None
    )
    return InvestigationStatistics(
        impact_state=impact.impact_state,
        affected_shipment_count=len(affected_shipment_ids),
        affected_order_count=len(affected_order_ids),
        affected_customer_count=len(affected_customer_ids),
        affected_order_quantity=affected_order_quantity,
        affected_orders_shortage_quantity=shortage,
        affected_orders_shortage_rate=shortage_rate,
        shortage_incomplete=incomplete,
        affected_shipment_ids=affected_shipment_ids,
        affected_order_ids=affected_order_ids,
        affected_customer_ids=affected_customer_ids,
        affected_sku_ids=affected_sku_ids,
        impact_classification_counts=dict(Counter(item.classification for _, item in order_records)),
    )


def build_operational_analytics(
    disruption_id: str,
    impact: ImpactResponse,
    data: SupplyChainData,
) -> OperationalAnalyticsResponse:
    """Build the deterministic analytics response for one investigation."""

    active_orders = _active_orders(data)
    order_stats = order_quantity_statistics(data)
    inventory_stats = inventory_statistics(data, active_orders=active_orders)
    shipment_stats = shipment_statistics(data)
    disruption_stats = disruption_statistics(data)
    investigation_stats = investigation_statistics(impact, data)

    warnings: list[str] = list(impact.warnings)
    if inventory_stats.shortage_incomplete:
        warnings.append("Dataset shortage totals are incomplete because some active orders have no linked inventory record; the total was not guessed.")
    if investigation_stats.shortage_incomplete:
        warnings.append("Affected-order shortage could not be fully calculated because some affected orders have no linked inventory record.")
    if impact.impact_state == "no_impact":
        warnings.append("No operational impact was established; affected-record statistics are reported as zero and are not fabricated.")
    if impact.impact_state == "insufficient_information":
        warnings.append("Affected-record statistics are based only on records the deterministic pipeline could establish.")

    return OperationalAnalyticsResponse(
        disruption_id=disruption_id,
        orders=order_stats,
        inventory=inventory_stats,
        shipments=shipment_stats,
        disruptions=disruption_stats,
        investigation=investigation_stats,
        warnings=warnings,
    )