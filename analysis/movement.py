"""Deterministic shipment movement evidence derived only from committed records.

Phase 15 adds a truthful route/movement-evidence layer for the investigation
experience. It never introduces live GPS, telemetry, coordinates, or ETAs:
everything it reports comes from the committed operational dataset (shipment,
route, and container records) and from the deterministic investigation already
produced by matching and impact analysis. When the committed data is
insufficient, it returns an explicit unavailable state instead of inventing
facts.
"""

from analysis.models import (
    EvidenceReference,
    ImpactResponse,
    MovementDataAvailability,
    MovementExposure,
    ShipmentMovementEvidence,
    ShipmentMovementResponse,
)
from models import Route, SupplyChainData


NO_LIVE_TRACKING_NOTE = (
    "Movement evidence is derived only from committed planned records; no live GPS or telemetry is used."
)
NO_AFFECTED_SHIPMENTS_NOTE = (
    "No affected shipments were established for this investigation; no movement evidence is reported and none was fabricated."
)
NO_DATA_NOTE = "Movement data unavailable from current operational dataset."


def _affected_shipment_ids(impact: ImpactResponse) -> list[str]:
    return sorted({
        item.entity_id
        for item in impact.direct_impact + impact.downstream_potential_impact
        if item.entity_type == "shipment"
    })


def _full_path(route: Route) -> list[str]:
    """Return the committed waypoint path for a route, including origin and destination."""

    path = list(route.waypoints)
    if not path:
        return []
    if path[0] != route.origin:
        path.insert(0, route.origin)
    if path[-1] != route.destination:
        path.append(route.destination)
    return path


def _exposure_locations_by_route(impact: ImpactResponse) -> dict[str, list[str]]:
    """Map each matched route to the disruption locations matched onto its waypoints."""

    by_route: dict[str, list[str]] = {}
    for candidate in impact.matching.routes:
        if candidate.category != "location_match":
            continue
        location = candidate.source_fact.strip()
        if not location:
            continue
        locations = by_route.setdefault(candidate.entity_id, [])
        if location not in locations:
            locations.append(location)
    return by_route


def _shipment_reference(shipment_id: str, route_id: str) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=f"movement:shipment:{shipment_id}:route_id",
        entity_type="shipment",
        record_id=shipment_id,
        field="route_id",
        value=route_id,
        relationship="shipment route",
        source_stage="movement",
    )


def _route_reference(route: Route, relationship: str, field: str, value: str) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=f"movement:route:{route.id}:{relationship.replace(' ', '-')}",
        entity_type="route",
        record_id=route.id,
        field=field,
        value=value,
        relationship=relationship,
        source_stage="movement",
    )


def _container_reference(container_id: str, container_status: str) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=f"movement:container:{container_id}:status",
        entity_type="container",
        record_id=container_id,
        field="status",
        value=container_status,
        relationship="shipment container",
        source_stage="movement",
    )


def build_shipment_movement(
    disruption_id: str,
    impact: ImpactResponse,
    data: SupplyChainData,
) -> ShipmentMovementResponse:
    """Derive route/movement evidence for affected shipments without inventing locations or times."""

    shipments_by_id = {shipment.id: shipment for shipment in data.shipments}
    routes_by_id = {route.id: route for route in data.routes}
    containers_by_id = {container.id: container for container in data.containers}
    exposure_locations = _exposure_locations_by_route(impact)

    unknown_shipment_ids: list[str] = []
    warnings: list[str] = []
    movement_shipments: list[ShipmentMovementEvidence] = []
    exposures: list[str] = []
    all_evidence: list[str] = []
    all_references: list[EvidenceReference] = []

    for shipment_id in _affected_shipment_ids(impact):
        shipment = shipments_by_id.get(shipment_id)
        if shipment is None:
            unknown_shipment_ids.append(shipment_id)
            continue
        route = routes_by_id.get(shipment.route_id)
        container = containers_by_id.get(shipment.container_id)
        route_path = _full_path(route) if route else []
        matched_locations = exposure_locations.get(shipment.route_id, [])
        ship_evidence: list[str] = []
        ship_references: list[EvidenceReference] = []

        ship_evidence.append(
            f"Shipment {shipment.id} is assigned committed route {shipment.route_id} "
            f"(origin {shipment.origin} to destination {shipment.destination})."
        )
        ship_references.append(_shipment_reference(shipment.id, shipment.route_id))

        if route is None:
            matched_locations = []
            exposed = False
            basis = (
                f"Route record {shipment.route_id} is not present in the committed dataset; "
                "exposure cannot be established and nothing was fabricated."
            )
            ship_evidence.append(basis)
            warnings.append(
                f"Shipment {shipment.id} has no committed route record {shipment.route_id}; "
                "route path and exposure are unavailable."
            )
        else:
            path_text = " -> ".join(route_path)
            ship_evidence.append(f"Route {route.id} is planned as {path_text}.")
            ship_references.append(
                _route_reference(route, "planned route path", "waypoints", path_text)
            )
            exposed = bool(matched_locations)
            if exposed:
                basis = f"Matched disruption location {matched_locations[0]} is a planned waypoint on the committed route {route.id}."
                ship_evidence.append(
                    f"Disruption location {matched_locations[0]} is a planned waypoint on route {route.id}, "
                    "which is assigned to this shipment; the planned path is exposed to the disruption."
                )
            else:
                basis = "No matched disruption location is a planned waypoint of this shipment's committed route."
                ship_evidence.append(basis)
            for location in matched_locations:
                ship_references.append(
                    _route_reference(route, "disruption waypoint exposure", "waypoints", location)
                )

        planned_schedule = (
            f"Planned departure {shipment.planned_departure} to planned arrival {shipment.planned_arrival} "
            "are scheduled dates from the committed dataset; they are not live telemetry or ETAs."
        )
        ship_evidence.append(planned_schedule)

        container_status = None
        if container is None:
            warnings.append(
                f"Shipment {shipment.id} has no committed container record {shipment.container_id}; "
                "container status is unavailable."
            )
        else:
            container_status = container.status
            ship_references.append(_container_reference(container.id, container.status))

        ship_references.append(
            EvidenceReference(
                evidence_id=f"movement:shipment:{shipment.id}:schedule",
                entity_type="shipment",
                record_id=shipment.id,
                field="planned_departure/planned_arrival",
                value=f"{shipment.planned_departure} / {shipment.planned_arrival}",
                relationship="planned schedule",
                source_stage="movement",
            )
        )

        source_records = [shipment.id, shipment.route_id]
        if container is not None:
            source_records.append(container.id)

        if exposed:
            exposures.append(
                f"{shipment.id}: committed route {shipment.route_id} planned path passes through disruption location {matched_locations[0]}."
            )
        else:
            exposures.append(f"{shipment.id}: no confirmed disruption-location overlap on committed route {shipment.route_id}.")

        evidence_item = ShipmentMovementEvidence(
            shipment_id=shipment.id,
            route_id=shipment.route_id,
            sku_id=shipment.sku_id,
            container_id=shipment.container_id,
            origin=shipment.origin,
            destination=shipment.destination,
            route_path=route_path,
            shipment_status=shipment.status,
            container_status=container_status,
            planned_departure=shipment.planned_departure,
            planned_arrival=shipment.planned_arrival,
            exposure=MovementExposure(
                exposed=exposed,
                on_route_disruption_locations=matched_locations,
                basis=basis,
            ),
            source_records=source_records,
            evidence=ship_evidence,
            evidence_references=ship_references,
        )
        movement_shipments.append(evidence_item)
        all_evidence.extend(ship_evidence)
        all_references.extend(ship_references)

    if unknown_shipment_ids:
        warnings.append(
            "Affected shipment IDs have no committed shipment record; movement evidence was not fabricated."
        )

    if not _affected_shipment_ids(impact):
        availability = MovementDataAvailability(
            status="unavailable",
            note=NO_AFFECTED_SHIPMENTS_NOTE,
        )
    elif movement_shipments:
        availability = MovementDataAvailability(status="available", note=NO_LIVE_TRACKING_NOTE)
    else:
        availability = MovementDataAvailability(status="unavailable", note=NO_DATA_NOTE)

    return ShipmentMovementResponse(
        disruption_id=disruption_id,
        availability=availability,
        shipments=movement_shipments,
        affected_shipment_ids=_affected_shipment_ids(impact),
        unknown_shipment_ids=sorted(unknown_shipment_ids),
        exposures=exposures,
        evidence=all_evidence,
        evidence_references=all_references,
        warnings=warnings,
    )