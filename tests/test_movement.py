"""Phase 15 focused tests for the deterministic shipment movement/route evidence layer."""

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import patch

from fastapi import FastAPI, HTTPException

from analysis.analytics import build_operational_analytics
from analysis.impact import analyze_impact
from analysis.movement import NO_DATA_NOTE, NO_LIVE_TRACKING_NOTE, build_shipment_movement
from analysis.prioritization import prioritize_orders
from analysis.recommendations import build_action_plan
from api.disruptions import (
    DisruptionNoticeRequest,
    analyze_disruption_analytics,
    analyze_disruption_impact,
    clear_disruptions,
    create_disruption,
    match_disruption,
    prioritize_disruption_orders,
    recommend_disruption_actions,
    shipment_movement_evidence,
    understand_disruption,
    router,
)
from data.sample_data import SAMPLE_DATA
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding


def route_impact(data=SAMPLE_DATA):
    matching = match_understanding("DIS-MOVEMENT", DisruptionUnderstanding(locations=["Vellore"]), data)
    return analyze_impact("DIS-MOVEMENT", matching, data)


def route_movement(data=SAMPLE_DATA):
    return build_shipment_movement("DIS-MOVEMENT", route_impact(data), data)


def no_impact_movement(data=SAMPLE_DATA):
    matching = match_understanding("DIS-MOVEMENT", DisruptionUnderstanding(locations=["Kandla"]), data)
    impact = analyze_impact("DIS-MOVEMENT", matching, data)
    return build_shipment_movement("DIS-MOVEMENT", impact, data)


class MovementEvidenceTests(unittest.TestCase):
    def test_affected_shipment_has_valid_route_evidence(self) -> None:
        movement = route_movement()
        self.assertEqual(movement.affected_shipment_ids, ["SHP-001"])
        self.assertEqual(len(movement.shipments), 1)
        evidence = movement.shipments[0]
        self.assertEqual(evidence.shipment_id, "SHP-001")
        self.assertEqual(evidence.route_id, "R-001")
        self.assertEqual(evidence.sku_id, "SKU-001")
        self.assertEqual(evidence.container_id, "CNT-1042")
        self.assertEqual(evidence.origin, "Chennai")
        self.assertEqual(evidence.destination, "Bengaluru")
        self.assertEqual(evidence.route_path, ["Chennai", "Vellore", "Bengaluru"])
        self.assertEqual(evidence.shipment_status, "in_transit")
        self.assertEqual(evidence.container_status, "in_transit")
        self.assertEqual(evidence.planned_departure, date(2026, 9, 2))
        self.assertEqual(evidence.planned_arrival, date(2026, 9, 6))
        self.assertTrue(evidence.exposure.exposed)
        self.assertEqual(evidence.exposure.on_route_disruption_locations, ["Vellore"])
        self.assertEqual(evidence.source_records, ["SHP-001", "R-001", "CNT-1042"])

    def test_multiple_affected_shipments(self) -> None:
        matching = match_understanding(
            "DIS-MOVEMENT", DisruptionUnderstanding(locations=["Bengaluru"]), SAMPLE_DATA
        )
        impact = analyze_impact("DIS-MOVEMENT", matching, SAMPLE_DATA)
        movement = build_shipment_movement("DIS-MOVEMENT", impact, SAMPLE_DATA)
        self.assertEqual(
            movement.affected_shipment_ids,
            ["SHP-001", "SHP-003", "SHP-006", "SHP-007"],
        )
        self.assertEqual(len(movement.shipments), 4)
        self.assertEqual(len(movement.exposures), 4)
        for evidence in movement.shipments:
            self.assertTrue(evidence.exposure.exposed)
            self.assertEqual(evidence.exposure.on_route_disruption_locations, ["Bengaluru"])
            self.assertTrue(evidence.route_path)
            self.assertTrue(evidence.evidence)

    def test_shipment_with_incomplete_movement_data(self) -> None:
        movement = build_shipment_movement("DIS-MOVEMENT", route_impact(), replace(SAMPLE_DATA, routes=()))
        self.assertEqual(movement.affected_shipment_ids, ["SHP-001"])
        evidence = movement.shipments[0]
        self.assertEqual(evidence.route_path, [])
        self.assertFalse(evidence.exposure.exposed)
        self.assertIn("not present in the committed dataset", evidence.exposure.basis)
        self.assertIn(
            "has no committed route record R-001",
            [warning for warning in movement.warnings if "route record" in warning][0],
        )

    def test_unknown_shipment_id_is_not_fabricated(self) -> None:
        data = replace(
            SAMPLE_DATA,
            shipments=tuple(shipment for shipment in SAMPLE_DATA.shipments if shipment.id != "SHP-001"),
        )
        movement = build_shipment_movement("DIS-MOVEMENT", route_impact(), data)
        self.assertEqual(movement.unknown_shipment_ids, ["SHP-001"])
        self.assertEqual(movement.shipments, [])
        self.assertEqual(movement.availability.status, "unavailable")
        self.assertEqual(movement.availability.note, NO_DATA_NOTE)

    def test_no_impact_disruption_has_no_movement_fabrication(self) -> None:
        movement = no_impact_movement()
        self.assertEqual(movement.affected_shipment_ids, [])
        self.assertEqual(movement.shipments, [])
        self.assertEqual(movement.unknown_shipment_ids, [])
        self.assertEqual(movement.exposures, [])
        self.assertEqual(movement.availability.status, "unavailable")
        self.assertNotEqual(movement.availability.note, NO_LIVE_TRACKING_NOTE)

    def test_no_movement_data_available_returns_explicit_state(self) -> None:
        data = replace(SAMPLE_DATA, shipments=(), routes=(), containers=())
        movement = build_shipment_movement("DIS-MOVEMENT", route_impact(), data)
        self.assertEqual(movement.unknown_shipment_ids, ["SHP-001"])
        self.assertEqual(movement.shipments, [])
        self.assertEqual(movement.availability.status, "unavailable")
        self.assertEqual(movement.availability.note, NO_DATA_NOTE)

    def test_missing_container_status_is_reported_not_fabricated(self) -> None:
        data = replace(
            SAMPLE_DATA,
            containers=tuple(container for container in SAMPLE_DATA.containers if container.id != "CNT-1042"),
        )
        movement = build_shipment_movement("DIS-MOVEMENT", route_impact(), data)
        evidence = movement.shipments[0]
        self.assertIsNone(evidence.container_status)
        self.assertIn("container record CNT-1042", " ".join(movement.warnings))

    def test_no_live_position_or_eta_is_claimed(self) -> None:
        movement = route_movement()
        payload = movement.model_dump()
        self.assertFalse(movement.availability.live_tracking)
        self.assertFalse(movement.availability.current_position_available)
        self.assertNotIn("eta", payload)
        self.assertNotIn("coordinates", payload)
        self.assertNotIn("latitude", payload)
        self.assertNotIn("longitude", payload)
        self.assertTrue(
            any(
                "scheduled dates" in line and "not live telemetry or ETAs" in line
                for line in movement.evidence
            )
        )

    def test_source_and_evidence_traceability(self) -> None:
        movement = route_movement()
        references = movement.evidence_references
        self.assertTrue(references)
        self.assertTrue(all(reference.source_stage == "movement" for reference in references))
        shipment_ref = next(reference for reference in references if reference.entity_type == "shipment")
        self.assertEqual(shipment_ref.record_id, "SHP-001")
        self.assertEqual(shipment_ref.field, "route_id")
        self.assertEqual(shipment_ref.value, "R-001")
        route_refs = [reference for reference in references if reference.entity_type == "route"]
        self.assertTrue(route_refs)
        values = {reference.value for reference in route_refs}
        self.assertIn("Vellore", values)
        evidence_text = "\n".join(movement.evidence)
        self.assertIn("SHP-001", evidence_text)
        self.assertIn("R-001", evidence_text)
        self.assertIn("Vellore", evidence_text)

    def test_movement_is_deterministic(self) -> None:
        first = route_movement().model_dump()
        second = route_movement().model_dump()
        self.assertEqual(first, second)


class MovementRegressionsTests(unittest.TestCase):
    def test_phase14_analytics_remains_unchanged(self) -> None:
        analytics = build_operational_analytics("DIS-MOVEMENT", route_impact(), SAMPLE_DATA)
        self.assertEqual(analytics.orders.total_active_orders, 16)
        self.assertEqual(analytics.orders.total_ordered_quantity, 340)
        self.assertEqual(analytics.inventory.total_available_quantity, 725)
        self.assertEqual(analytics.investigation.affected_order_ids, ["ORD-001", "ORD-002", "ORD-003"])
        self.assertEqual(analytics.investigation.affected_shipment_ids, ["SHP-001"])

    def test_impact_prioritization_recommendation_behavior_unchanged(self) -> None:
        matching = match_understanding(
            "DIS-MOVEMENT", DisruptionUnderstanding(locations=["Vellore"]), SAMPLE_DATA
        )
        impact = analyze_impact("DIS-MOVEMENT", matching, SAMPLE_DATA)
        priorities = prioritize_orders(date(2026, 9, 4), impact, SAMPLE_DATA)
        plan = build_action_plan(impact, priorities)
        self.assertEqual(impact.impact_state, "impact_identified")
        self.assertEqual(priorities.overall_state, "prioritized")
        self.assertEqual({order.order_id for order in priorities.orders}, {"ORD-001", "ORD-002", "ORD-003"})
        self.assertEqual(plan.overall_state, "recommendation_available")
        self.assertTrue(plan.options)

    def test_no_impact_flow_unchanged(self) -> None:
        matching = match_understanding(
            "DIS-MOVEMENT", DisruptionUnderstanding(locations=["Kandla"]), SAMPLE_DATA
        )
        impact = analyze_impact("DIS-MOVEMENT", matching, SAMPLE_DATA)
        priorities = prioritize_orders(date(2026, 9, 4), impact, SAMPLE_DATA)
        plan = build_action_plan(impact, priorities)
        self.assertEqual(impact.impact_state, "no_impact")
        self.assertEqual(priorities.overall_state, "no_affected_orders")
        self.assertEqual(plan.overall_state, "no_impact")
        self.assertEqual(plan.options, [])


class MovementApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = FastAPI()
        cls.app.include_router(router)

    def setUp(self) -> None:
        clear_disruptions()

    def request(self, method: str, path: str, body: object = None) -> tuple[int, object]:
        encoded_body = json.dumps(body).encode()
        headers = [(b"content-type", b"application/json")]

        async def send_request() -> tuple[int, object]:
            messages = [{"type": "http.request", "body": encoded_body, "more_body": False}]
            response_status = 500
            response_body = bytearray()

            async def receive() -> dict[str, object]:
                return messages.pop(0) if messages else {"type": "http.disconnect"}

            async def send(message: dict[str, object]) -> None:
                nonlocal response_status
                if message["type"] == "http.response.start":
                    response_status = int(message["status"])
                elif message["type"] == "http.response.body":
                    response_body.extend(message.get("body", b""))

            await self.app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"",
                    "headers": headers,
                    "client": ("testclient", 50000),
                    "server": ("testserver", 80),
                },
                receive,
                send,
            )
            return response_status, json.loads(response_body)

        return asyncio.run(send_request())

    def seed_understanding(self, description: str, understanding: DisruptionUnderstanding) -> str:
        disruption = create_disruption(DisruptionNoticeRequest(description=description))
        with patch("api.disruptions.extract_understanding", return_value=understanding):
            understand_disruption(disruption.disruption_id)
        return disruption.disruption_id

    def test_movement_endpoint_returns_typed_schema(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        status_code, payload = self.request("POST", f"/api/disruptions/{disruption_id}/movement")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["disruption_id"], disruption_id)
        self.assertEqual(payload["availability"]["status"], "available")
        self.assertFalse(payload["availability"]["live_tracking"])
        self.assertFalse(payload["availability"]["current_position_available"])
        self.assertNotIn("eta", payload)
        self.assertEqual(len(payload["shipments"]), 1)
        self.assertEqual(payload["shipments"][0]["shipment_id"], "SHP-001")
        self.assertEqual(payload["shipments"][0]["route_path"], ["Chennai", "Vellore", "Bengaluru"])
        self.assertTrue(payload["shipments"][0]["exposure"]["exposed"])
        self.assertEqual(payload["shipments"][0]["exposure"]["on_route_disruption_locations"], ["Vellore"])
        self.assertEqual(payload["shipments"][0]["source_records"], ["SHP-001", "R-001", "CNT-1042"])
        self.assertIn("availability", payload)
        self.assertIn("shipments", payload)
        self.assertIn("exposures", payload)
        self.assertIn("evidence_references", payload)

    def test_movement_endpoint_requires_stored_understanding(self) -> None:
        disruption = create_disruption(DisruptionNoticeRequest(description="Flooding near Vellore."))
        status_code, _ = self.request("POST", f"/api/disruptions/{disruption.disruption_id}/movement")
        self.assertEqual(status_code, 409)

    def test_movement_endpoint_unknown_id_returns_404(self) -> None:
        status_code, _ = self.request("POST", "/api/disruptions/DIS-UNKNOWN/movement")
        self.assertEqual(status_code, 404)
        with self.assertRaises(HTTPException) as context:
            shipment_movement_evidence("DIS-UNKNOWN")
        self.assertEqual(context.exception.status_code, 404)

    def test_full_investigation_flow_with_movement_stays_consistent(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding has affected transport routes near Vellore.",
            DisruptionUnderstanding(event_type="flood", locations=["Vellore"], transport_mode="road"),
        )
        matches = match_disruption(disruption_id)
        impact = analyze_disruption_impact(disruption_id)
        analytics = analyze_disruption_analytics(disruption_id)
        movement = shipment_movement_evidence(disruption_id)
        priorities = prioritize_disruption_orders(disruption_id)
        plan = recommend_disruption_actions(disruption_id)
        self.assertEqual(matches.match_status, "matched")
        self.assertEqual(impact.impact_state, "impact_identified")
        self.assertEqual(analytics.investigation.impact_state, "impact_identified")
        self.assertEqual(movement.affected_shipment_ids, ["SHP-001"])
        self.assertEqual(movement.availability.status, "available")
        self.assertEqual(priorities.overall_state, "prioritized")
        self.assertEqual(plan.overall_state, "recommendation_available")

    def test_no_impact_api_flow_with_movement(self) -> None:
        disruption_id = self.seed_understanding(
            "Severe flooding near Kandla.",
            DisruptionUnderstanding(event_type="flood", locations=["Kandla"]),
        )
        movement = shipment_movement_evidence(disruption_id)
        self.assertEqual(movement.affected_shipment_ids, [])
        self.assertEqual(movement.shipments, [])
        self.assertEqual(movement.availability.status, "unavailable")
        self.assertNotIn("live telemetry", movement.availability.note)


if __name__ == "__main__":
    unittest.main()