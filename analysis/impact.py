"""Deterministic traversal from Phase 4 matches to operational records."""

from collections import defaultdict

from matching.models import MatchCandidate, MatchingResponse
from models import SupplyChainData

from .models import ImpactRecord, ImpactResponse
from .models import EvidenceReference, ImpactRecord, ImpactResponse


def _matched_ids(candidates: list[MatchCandidate]) -> set[str]:
    return {candidate.entity_id for candidate in candidates}


def _record(
    entity_type: str,
    entity_id: str,
    entity_name: str,
    relationship: str,
    classification: str,
    reason: str,
    source_record: str,
    supporting_fact: str,
) -> ImpactRecord:
    evidence = EvidenceReference(
        evidence_id=f"impact:{entity_type}:{entity_id}:{relationship.replace(' ', '-')}",
        entity_type=entity_type,
        record_id=source_record,
        field=relationship,
        value=supporting_fact,
        relationship=relationship,
        source_stage="impact",
    )
    return ImpactRecord(
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=entity_name,
        relationship=relationship,
        classification=classification,
        reason=reason,
        source_record=source_record,
        supporting_fact=supporting_fact,
        evidence_references=[evidence],
    )


def _unknown(
    entity_type: str,
    relationship: str,
    source_record: str,
    supporting_fact: str,
    reason: str,
) -> ImpactRecord:
    return _record(
        entity_type,
        f"unknown-for-{source_record}",
        f"Unknown {entity_type} relationship",
        relationship,
        "unknown",
        reason,
        source_record,
        supporting_fact,
    )


def _candidate_fact(candidates: list[MatchCandidate], entity_id: str, fallback: str) -> str:
    return next((candidate.source_fact for candidate in candidates if candidate.entity_id == entity_id), fallback)


def analyze_impact(disruption_id: str, matching: MatchingResponse, data: SupplyChainData) -> ImpactResponse:
    """Traverse only relationships represented by the committed data records."""

    direct: list[ImpactRecord] = []
    downstream: list[ImpactRecord] = []
    insufficient: list[ImpactRecord] = []
    seen: set[tuple[str, str]] = set()

    def add(target: list[ImpactRecord], item: ImpactRecord) -> None:
        key = (item.entity_type, item.entity_id)
        if key not in seen:
            seen.add(key)
            target.append(item)

    if matching.match_status == "no_match":
        return ImpactResponse(
            disruption_id=disruption_id,
            impact_state="no_impact",
            understanding=matching.understanding,
            matching=matching,
            warnings=["No supply-chain impact could be established from the available records."],
        )

    supplier_ids = _matched_ids(matching.suppliers)
    route_ids = _matched_ids(matching.routes)
    shipment_ids = _matched_ids(matching.shipments)
    sku_ids = _matched_ids(matching.skus)
    container_ids = _matched_ids(matching.containers)

    suppliers = {supplier.id: supplier for supplier in data.suppliers}
    routes = {route.id: route for route in data.routes}
    shipments = {shipment.id: shipment for shipment in data.shipments}
    containers = {container.id: container for container in data.containers}
    skus = {sku.id: sku for sku in data.skus}
    inventory_by_sku: dict[str, list] = defaultdict(list)
    orders_by_sku: dict[str, list] = defaultdict(list)
    customers = {customer.id: customer for customer in data.customers}
    for inventory in data.inventory:
        inventory_by_sku[inventory.sku_id].append(inventory)
    for order in data.orders:
        orders_by_sku[order.sku_id].append(order)

    for candidate in matching.suppliers:
        supplier = suppliers.get(candidate.entity_id)
        if supplier:
            add(direct, _record("supplier", supplier.id, supplier.name, "matched supplier", "direct", candidate.match_reason, supplier.id, candidate.source_fact))
    for candidate in matching.routes:
        route = routes.get(candidate.entity_id)
        if route:
            add(direct, _record("route", route.id, " -> ".join(route.waypoints), "matched route", "direct", candidate.match_reason, route.id, candidate.source_fact))

    for candidate in matching.shipments:
        shipment = shipments.get(candidate.entity_id)
        if shipment and candidate.matched_field != "relationship":
            add(direct, _record("shipment", shipment.id, shipment.id, "matched shipment", "direct", candidate.match_reason, shipment.id, candidate.source_fact))
    for candidate in matching.containers:
        container = containers.get(candidate.entity_id)
        if container and candidate.matched_field != "shipment_id":
            add(direct, _record("container", container.id, container.id, "matched container", "direct", candidate.match_reason, container.id, candidate.source_fact))
    for candidate in matching.skus:
        sku = skus.get(candidate.entity_id)
        if sku and candidate.matched_field == "name":
            add(direct, _record("sku", sku.id, sku.name, "matched SKU", "direct", candidate.match_reason, sku.id, candidate.source_fact))

    impacted_shipments = {
        shipment.id: shipment
        for shipment in data.shipments
        if shipment.id in shipment_ids
        or shipment.route_id in route_ids
        or shipment.supplier_id in supplier_ids
        or shipment.sku_id in sku_ids
    }
    for route_id in route_ids:
        route_shipments = [shipment for shipment in data.shipments if shipment.route_id == route_id]
        if not route_shipments:
            insufficient.append(_unknown("shipment", "route to shipment", route_id, _candidate_fact(matching.routes, route_id, route_id), f"No shipment record is assigned to route {route_id}."))
    for shipment in impacted_shipments.values():
        source = shipment.route_id if shipment.route_id in route_ids else shipment.id
        fact = _candidate_fact(matching.routes, shipment.route_id, source)
        add(downstream, _record("shipment", shipment.id, shipment.id, "shipment assigned to matched route/supplier/SKU", "downstream", f"Shipment {shipment.id} is potentially affected because it is linked to {source}.", source, fact))
        container = containers.get(shipment.container_id)
        if container:
            add(downstream, _record("container", container.id, container.id, "container belongs to shipment", "downstream", f"Container {container.id} belongs to shipment {shipment.id}.", shipment.id, shipment.id))
        else:
            insufficient.append(_unknown("container", "shipment to container", shipment.id, shipment.id, f"Shipment {shipment.id} has no matching container record."))
        sku = skus.get(shipment.sku_id)
        if sku:
            sku_ids.add(sku.id)
            add(downstream, _record("sku", sku.id, sku.name, "SKU carried by shipment", "downstream", f"SKU {sku.id} is carried by shipment {shipment.id}.", shipment.id, shipment.id))
        else:
            insufficient.append(_unknown("SKU", "shipment to SKU", shipment.id, shipment.id, f"Shipment {shipment.id} has no matching SKU record."))

    for sku_id in sku_ids:
        sku = skus.get(sku_id)
        if sku is None:
            insufficient.append(_unknown("SKU", "matched SKU record", sku_id, sku_id, f"No SKU record exists for {sku_id}."))
            continue
        inventory_records = inventory_by_sku.get(sku_id, [])
        if not inventory_records:
            insufficient.append(_unknown("inventory", "SKU to inventory", sku_id, sku_id, f"No inventory record is connected to SKU {sku_id}."))
        for inventory in inventory_records:
            add(downstream, _record("inventory", f"{inventory.sku_id}@{inventory.warehouse}", inventory.warehouse, "inventory connected to SKU", "downstream", f"Inventory at {inventory.warehouse} is connected to SKU {sku_id}.", sku_id, sku_id))
        orders = orders_by_sku.get(sku_id, [])
        if not orders:
            insufficient.append(_unknown("order", "SKU to order", sku_id, sku_id, f"No order record is connected to SKU {sku_id}."))
        for order in orders:
            add(downstream, _record("order", order.id, order.id, "order contains SKU", "downstream", f"Order {order.id} contains SKU {sku_id}.", sku_id, sku_id))
            customer = customers.get(order.customer_id)
            if customer:
                add(downstream, _record("customer", customer.id, customer.name, "customer owns order", "downstream", f"Customer {customer.id} is linked to order {order.id}.", order.id, order.id))
            else:
                insufficient.append(_unknown("customer", "order to customer", order.id, order.id, f"Order {order.id} has no matching customer record."))

    if matching.match_status == "ambiguous":
        state = "review_required"
    elif insufficient and not (direct or downstream):
        state = "insufficient_information"
    elif insufficient:
        state = "insufficient_information"
    else:
        state = "impact_identified"

    evidence = [item.reason for item in [*direct, *downstream, *insufficient]]
    evidence_references = [
        reference
        for item in [*direct, *downstream, *insufficient]
        for reference in item.evidence_references
    ]
    warnings = list(matching.warnings)
    if insufficient:
        warnings.append("Some relationship paths could not be established from the available records.")
    return ImpactResponse(
        disruption_id=disruption_id,
        impact_state=state,
        understanding=matching.understanding,
        matching=matching,
        direct_impact=direct,
        downstream_potential_impact=downstream,
        insufficient_information=insufficient,
        evidence=evidence,
        evidence_references=evidence_references,
        warnings=warnings,
    )