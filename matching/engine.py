"""Conservative, deterministic mapping from extracted facts to sample records."""

import re

from models import SupplyChainData
from gemini.models import DisruptionUnderstanding
from matching.models import MatchCandidate, MatchingResponse


def normalize_match_text(value: str) -> str:
    """Normalize case, punctuation, and whitespace for exact comparison."""

    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _candidate(
    entity_type: str,
    entity_id: str,
    entity_name: str,
    reason: str,
    matched_field: str,
    source_fact: str,
    category: str,
) -> MatchCandidate:
    return MatchCandidate(
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=entity_name,
        match_reason=reason,
        matched_field=matched_field,
        source_fact=source_fact,
        category=category,
    )


def match_understanding(
    disruption_id: str,
    understanding: DisruptionUnderstanding,
    data: SupplyChainData,
) -> MatchingResponse:
    """Map extracted hints to real records without inferring business impact."""

    suppliers: list[MatchCandidate] = []
    routes: list[MatchCandidate] = []
    shipments: list[MatchCandidate] = []
    containers: list[MatchCandidate] = []
    skus: list[MatchCandidate] = []
    evidence: list[str] = []
    facts = [*understanding.entity_hints, *understanding.locations, *understanding.route_hints]

    for hint in understanding.entity_hints:
        normalized_hint = normalize_match_text(hint)
        for supplier in data.suppliers:
            if normalized_hint in {normalize_match_text(supplier.id), normalize_match_text(supplier.name)}:
                category = "exact" if hint.casefold() == supplier.name.casefold() else "normalized_exact"
                suppliers.append(_candidate(
                    "supplier", supplier.id, supplier.name,
                    "Supplier name explicitly matches the extracted supplier hint.",
                    "name", hint, category,
                ))
                evidence.append(f"Supplier {supplier.name} matched explicit entity hint '{hint}'.")
        for sku in data.skus:
            if normalized_hint in {normalize_match_text(sku.id), normalize_match_text(sku.name)}:
                skus.append(_candidate(
                    "sku", sku.id, sku.name,
                    "SKU name or identifier explicitly matches the extracted entity hint.",
                    "name", hint, "exact" if normalized_hint == normalize_match_text(sku.name) else "explicit_identifier",
                ))
                evidence.append(f"SKU {sku.name} matched explicit entity hint '{hint}'.")

    for location in understanding.locations:
        normalized_location = normalize_match_text(location)
        for route in data.routes:
            waypoint_match = next(
                (waypoint for waypoint in route.waypoints if normalize_match_text(waypoint) == normalized_location),
                None,
            )
            if waypoint_match:
                route_name = " -> ".join(route.waypoints)
                routes.append(_candidate(
                    "route", route.id, route_name,
                    f"Disruption location {waypoint_match} matches a route waypoint.",
                    "waypoints", location, "location_match",
                ))
                evidence.append(f"Route {route.id} contains disruption location {waypoint_match} as a planned waypoint.")

    for route_hint in understanding.route_hints:
        normalized_hint = normalize_match_text(route_hint)
        for route in data.routes:
            route_text = normalize_match_text(" ".join(route.waypoints))
            if normalized_hint and (normalized_hint == route_text or all(part in route_text for part in normalized_hint.split())):
                if not any(candidate.entity_id == route.id for candidate in routes):
                    routes.append(_candidate(
                        "route", route.id, " -> ".join(route.waypoints),
                        "Route hint matches the planned route waypoints.",
                        "waypoints", route_hint, "route_match",
                    ))
                    evidence.append(f"Route {route.id} matches route hint '{route_hint}'.")

    for shipment in data.shipments:
        if any(candidate.entity_id == shipment.route_id for candidate in routes) or any(
            candidate.entity_id == shipment.supplier_id for candidate in suppliers
        ) or any(candidate.entity_id == shipment.sku_id for candidate in skus):
            shipments.append(_candidate(
                "shipment", shipment.id, shipment.id,
                "Shipment is explicitly linked to a matched supplier, route, or SKU in the supply-chain data.",
                "relationship", next((fact for fact in facts if normalize_match_text(fact) in normalize_match_text(shipment.route_id)), shipment.route_id),
                "route_match" if any(candidate.entity_id == shipment.route_id for candidate in routes) else "exact",
            ))
            containers.append(_candidate(
                "container", shipment.container_id, shipment.container_id,
                f"Container is linked to matched shipment {shipment.id} in the supply-chain data.",
                "shipment_id", shipment.id, "exact",
            ))
            if not any(candidate.entity_id == shipment.sku_id for candidate in skus):
                sku = next(sku for sku in data.skus if sku.id == shipment.sku_id)
                skus.append(_candidate(
                    "sku", sku.id, sku.name,
                    f"SKU is linked to matched shipment {shipment.id} in the supply-chain data.",
                    "shipment.sku_id", shipment.id, "exact",
                ))
                evidence.append(f"SKU {sku.id} is linked to shipment {shipment.id}.")

    all_candidates = suppliers + routes + shipments + containers + skus
    if not all_candidates:
        return MatchingResponse(
            disruption_id=disruption_id,
            match_status="no_match",
            impact_status="no_matching_records",
            understanding=understanding,
            evidence=[],
            warnings=["No matching supply-chain records found."],
        )

    ambiguous = len(suppliers) > 1 or len(routes) > 1
    warnings = ["Multiple possible matches found - review required."] if ambiguous else []
    return MatchingResponse(
        disruption_id=disruption_id,
        match_status="ambiguous" if ambiguous else "matched",
        impact_status="not_calculated",
        understanding=understanding,
        suppliers=suppliers,
        shipments=shipments,
        containers=containers,
        routes=routes,
        skus=skus,
        evidence=evidence,
        warnings=warnings,
    )