"""Phase 14 focused tests for the deterministic operational analytics layer."""

import asyncio
import json
import unittest
from dataclasses import dataclass, replace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException

from analysis.analytics import (
    build_operational_analytics,
    disruption_statistics,
    inventory_statistics,
    investigation_statistics,
    order_quantity_statistics,
    shipment_statistics,
)
from analysis.impact import analyze_impact
from analysis.models import OperationalAnalyticsResponse
from api.disruptions import (
    DisruptionNoticeRequest,
    analyze_disruption_analytics,
    analyze_disruption_impact,
    clear_disruptions,
    create_disruption,
    match_disruption,
    prioritize_disruption_orders,
    recommend_disruption_actions,
    router,
    understand_disruption,
)
from data.sample_data import SAMPLE_DATA
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding


def route_impact(data=SAMPLE_DATA):
    matching = match_understanding("DIS-ANALYTICS", DisruptionUnderstanding(locations=["Vellore"]), data)
    return analyze_impact("DIS-ANALYTICS", matching, data)


def route_analytics(data=SAMPLE_DATA) -> OperationalAnalyticsResponse:
    return build_operational_analytics("DIS-ANALYTICS", route_impact(data), data)


def no_impact_analytics(data=SAMPLE_DATA) -> OperationalAnalyticsResponse:
    matching = match_understanding("DIS-ANALYTICS", DisruptionUnderstanding(locations=["Kandla"]), data)
    impact = analyze_impact("DIS-ANALYTICS", matching, data)
    return build_operational_analytics("DIS-ANALYTICS", impact, data)


def all_active_orders(data=SAMPLE_DATA):
    return [order for order in data.orders]


class OrderAnalyticsTests(unittest.TestCase):
    def test_active_order_count_and_quantity_statistics(self) -> None:
        stats = order_quantity_statistics(SAMPLE_DATA)
        self.assertEqual(stats.total_active_orders, 16)
        self.assertEqual(stats.total_ordered_quantity, 340)
        self.assertEqual(stats.minimum_order_quantity, 8)
        self.assertEqual(stats.maximum_order_quantity, 45)
        self.assertEqual(stats.median_order_quantity, 20.0)
        self.assertAlmostEqual(stats.average_order_quantity, 21.25)
        self.assertEqual(len(stats.active_order_ids), 16)

    def test_priority_and_status_counts(self) -> None:
        stats = order_quantity_statistics(SAMPLE_DATA)
        self.assertEqual(stats.priority_counts, {"high": 5, "standard": 11})
        self.assertEqual(stats.status_counts, {"open": 16})

    def test_completed_orders_are_excluded_from_active(self) -> None:
        orders = tuple(
            replace(order, status="completed") if order.id == "ORD-001" else order
            for order in SAMPLE_DATA.orders
        )
        data = replace(SAMPLE_DATA, orders=orders)
        stats = order_quantity_statistics(data)
        self.assertEqual(stats.total_active_orders, 15)
        self.assertNotIn("ORD-001", stats.active_order_ids)

    def test_empty_order_set_returns_nullable_statistics(self) -> None:
        stats = order_quantity_statistics(replace(SAMPLE_DATA, orders=()))
        self.assertEqual(stats.total_active_orders, 0)
        self.assertEqual(stats.total_ordered_quantity, 0)
        self.assertIsNone(stats.average_order_quantity)
        self.assertIsNone(stats.median_order_quantity)
        self.assertIsNone(stats.minimum_order_quantity)
        self.assertIsNone(stats.maximum_order_quantity)
        self.assertEqual(stats.priority_counts, {})

    def test_zero_quantity_orders_do_not_break_statistics(self) -> None:
        orders = tuple(
            replace(order, quantity=0) if order.id == "ORD-001" else order
            for order in SAMPLE_DATA.orders
        )
        stats = order_quantity_statistics(replace(SAMPLE_DATA, orders=orders))
        self.assertEqual(stats.minimum_order_quantity, 0)
        self.assertEqual(stats.total_ordered_quantity, 295)

    def test_duplicate_order_ids_are_rejected(self) -> None:
        orders = SAMPLE_DATA.orders + (SAMPLE_DATA.orders[0],)
        data = replace(SAMPLE_DATA, orders=orders)
        with self.assertRaises(ValueError):
            order_quantity_statistics(data)


class InventoryAnalyticsTests(unittest.TestCase):
    def test_inventory_aggregation(self) -> None:
        stats = inventory_statistics(SAMPLE_DATA, active_orders=all_active_orders())
        self.assertEqual(stats.total_available_quantity, 725)
        self.assertEqual(stats.median_inventory_per_sku, 83.0)
        self.assertAlmostEqual(stats.average_inventory_per_sku, 90.625)
        self.assertEqual(len(stats.tracked_sku_ids), 8)

    def test_dataset_shortage_is_zero_when_inventory_covers_orders(self) -> None:
        stats = inventory_statistics(SAMPLE_DATA, active_orders=all_active_orders())
        self.assertEqual(stats.total_shortage_quantity, 0)
        self.assertEqual(stats.shortage_sku_ids, [])
        self.assertFalse(stats.shortage_incomplete)

    def test_missing_inventory_makes_shortage_incomplete(self) -> None:
        data = replace(SAMPLE_DATA, inventory=())
        stats = inventory_statistics(data, active_orders=all_active_orders(data))
        self.assertIsNone(stats.total_shortage_quantity)
        self.assertTrue(stats.shortage_incomplete)

    def test_empty_inventory_is_not_fabricated(self) -> None:
        stats = inventory_statistics(replace(SAMPLE_DATA, inventory=()), active_orders=[])
        self.assertEqual(stats.total_available_quantity, 0)
        self.assertIsNone(stats.average_inventory_per_sku)
        self.assertIsNone(stats.median_inventory_per_sku)
        self.assertEqual(stats.tracked_sku_ids, [])


class ShipmentAndDisruptionAnalyticsTests(unittest.TestCase):
    def test_active_shipment_count_and_statuses(self) -> None:
        stats = shipment_statistics(SAMPLE_DATA)
        self.assertEqual(stats.total_active_shipments, 8)
        self.assertEqual(stats.shipment_status_counts, {"in_transit": 5, "delayed": 1, "at_origin": 2})
        self.assertEqual(len(stats.active_shipment_ids), 8)

    def test_disruption_counts_by_event_type(self) -> None:
        stats = disruption_statistics(SAMPLE_DATA)
        self.assertEqual(stats.total_disruptions, 2)
        self.assertEqual(stats.counts_by_event_type, {"flood": 1, "port_delay": 1})


class InvestigationAnalyticsTests(unittest.TestCase):
    def test_affected_vellore_investigation_is_traceable(self) -> None:
        analytics = route_analytics()
        investigation = analytics.investigation
        self.assertEqual(investigation.impact_state, "impact_identified")
        self.assertEqual(investigation.affected_order_ids, ["ORD-001", "ORD-002", "ORD-003"])
        self.assertEqual(investigation.affected_customer_ids, ["CUS-001", "CUS-002", "CUS-003"])
        self.assertEqual(investigation.affected_shipment_ids, ["SHP-001"])
        self.assertEqual(investigation.affected_sku_ids, ["SKU-001"])
        self.assertEqual(investigation.affected_order_count, 3)
        self.assertEqual(investigation.affected_customer_count, 3)
        self.assertEqual(investigation.affected_shipment_count, 1)
        self.assertEqual(investigation.affected_order_quantity, 95)
        self.assertEqual(investigation.affected_orders_shortage_quantity, 0)
        self.assertEqual(investigation.affected_orders_shortage_rate, 0.0)
        self.assertEqual(investigation.impact_classification_counts, {"downstream": 3})

    def test_no_impact_investigation_does_not_fabricate_statistics(self) -> None:
        analytics = no_impact_analytics()
        investigation = analytics.investigation
        self.assertEqual(investigation.impact_state, "no_impact")
        self.assertEqual(investigation.affected_order_count, 0)
        self.assertEqual(investigation.affected_customer_count, 0)
        self.assertEqual(investigation.affected_shipment_count, 0)
        self.assertEqual(investigation.affected_order_ids, [])
        self.assertEqual(investigation.affected_shipment_ids, [])
        self.assertEqual(investigation.affected_customer_ids, [])
        self.assertEqual(investigation.affected_order_quantity, 0)
        self.assertEqual(investigation.affected_orders_shortage_quantity, 0)
        self.assertIsNone(investigation.affected_orders_shortage_rate)
        self.assertEqual(investigation.impact_classification_counts, {})
        self.assertTrue(any("not fabricated" in warning for warning in analytics.warnings))

    def test_missing_inventory_for_affected_order_is_incomplete(self) -> None:
        data = replace(SAMPLE_DATA, inventory=tuple(
            record for record in SAMPLE_DATA.inventory if record.sku_id != "SKU-001"
        ))
        analytics = route_analytics(data)
        investigation = analytics.investigation
        self.assertEqual(investigation.affected_order_ids, ["ORD-001", "ORD-002", "ORD-003"])
        self.assertIsNone(investigation.affected_orders_shortage_quantity)
        self.assertTrue(investigation.shortage_incomplete)

    def test_review_required_state_is_reported(self) -> None:
        duplicate = replace(SAMPLE_DATA.suppliers[0], id="SUP-006")
        data = replace(SAMPLE_DATA, suppliers=SAMPLE_DATA.suppliers + (duplicate,))
        matching = match_understanding("DIS-ANALYTICS", DisruptionUnderstanding(entity_hints=["Limited Components"]), data)
        impact = analyze_impact("DIS-ANALYTICS", matching, data)
        analytics = build_operational_analytics("DIS-ANALYTICS", impact, data)
        self.assertEqual(analytics.investigation.impact_state, "review_required")

    def test_analytics_are_deterministic(self) -> None:
        first = route_analytics().model_dump()
        second = route_analytics().model_dump()
        self.assertEqual(first, second)


class AnalyticsApiTests(unittest.TestCase):
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

    def test_analytics_endpoint_returns_typed_schema(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        status_code, payload = self.request("POST", f"/api/disruptions/{disruption_id}/analytics")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["disruption_id"], disruption_id)
        self.assertEqual(payload["orders"]["total_active_orders"], 16)
        self.assertEqual(payload["orders"]["total_ordered_quantity"], 340)
        self.assertEqual(payload["inventory"]["total_available_quantity"], 725)
        self.assertEqual(payload["investigation"]["affected_order_count"], 3)
        self.assertEqual(payload["investigation"]["affected_order_ids"], ["ORD-001", "ORD-002", "ORD-003"])
        self.assertEqual(payload["investigation"]["impact_classification_counts"], {"downstream": 3})
        self.assertIn("orders", payload)
        self.assertIn("inventory", payload)
        self.assertIn("shipments", payload)
        self.assertIn("disruptions", payload)
        self.assertIn("investigation", payload)

    def test_analytics_endpoint_requires_stored_understanding(self) -> None:
        disruption = create_disruption(DisruptionNoticeRequest(description="Flooding near Vellore."))
        status_code, _ = self.request("POST", f"/api/disruptions/{disruption.disruption_id}/analytics")
        self.assertEqual(status_code, 409)

    def test_analytics_endpoint_unknown_id_returns_404(self) -> None:
        status_code, _ = self.request("POST", "/api/disruptions/DIS-UNKNOWN/analytics")
        self.assertEqual(status_code, 404)
        with self.assertRaises(HTTPException) as context:
            analyze_disruption_analytics("DIS-UNKNOWN")
        self.assertEqual(context.exception.status_code, 404)

    def test_existing_investigation_flow_remains_unchanged(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding has affected transport routes near Vellore.",
            DisruptionUnderstanding(event_type="flood", locations=["Vellore"], transport_mode="road"),
        )
        matches = match_disruption(disruption_id)
        toward_impact = analyze_disruption_impact(disruption_id)
        analytics = analyze_disruption_analytics(disruption_id)
        priorities = prioritize_disruption_orders(disruption_id)
        plan = recommend_disruption_actions(disruption_id)
        self.assertEqual(matches.match_status, "matched")
        self.assertEqual(toward_impact.impact_state, "impact_identified")
        self.assertEqual(analytics.investigation.impact_state, "impact_identified")
        self.assertTrue(priorities.orders)
        self.assertEqual(plan.overall_state, "recommendation_available")

    def test_no_impact_api_flow_remains_unchanged(self) -> None:
        disruption_id = self.seed_understanding(
            "Severe flooding near Kandla.",
            DisruptionUnderstanding(event_type="flood", locations=["Kandla"]),
        )
        result = analyze_disruption_analytics(disruption_id)
        self.assertEqual(match_disruption(disruption_id).match_status, "no_match")
        self.assertEqual(result.investigation.impact_state, "no_impact")
        self.assertEqual(result.investigation.affected_order_count, 0)
        self.assertEqual(recommend_disruption_actions(disruption_id).overall_state, "no_impact")


if __name__ == "__main__":
    unittest.main()