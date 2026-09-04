"""Focused tests for deterministic affected-order prioritization."""

import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import patch

from analysis.impact import analyze_impact
from analysis.models import ImpactResponse, ImpactRecord
from analysis.prioritization import calculate_shortage, prioritize_orders
from api.disruptions import (
    DisruptionNoticeRequest,
    clear_disruptions,
    create_disruption,
    understand_disruption,
    prioritize_disruption_orders,
)
from data.sample_data import SAMPLE_DATA
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding


REFERENCE_DATE = date(2026, 9, 4)


class PrioritizationTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_disruptions()

    def route_impact(self, data=SAMPLE_DATA) -> ImpactResponse:
        matching = match_understanding("DIS-PRIORITY", DisruptionUnderstanding(locations=["Vellore"]), data)
        return analyze_impact("DIS-PRIORITY", matching, data)

    def test_affected_order_receives_deterministic_score(self) -> None:
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(), SAMPLE_DATA)
        order = next(order for order in result.orders if order.order_id == "ORD-001")
        self.assertEqual(order.priority_score, 6)
        self.assertEqual(order.urgency, "elevated")

    def test_earlier_required_date_increases_urgency(self) -> None:
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(), SAMPLE_DATA)
        earlier = next(order for order in result.orders if order.order_id == "ORD-001")
        later = next(order for order in result.orders if order.order_id == "ORD-003")
        self.assertGreater(earlier.priority_score, later.priority_score)

    def test_order_priority_affects_ranking(self) -> None:
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(), SAMPLE_DATA)
        self.assertLess(result.orders.index(next(order for order in result.orders if order.order_id == "ORD-001")), result.orders.index(next(order for order in result.orders if order.order_id == "ORD-002")))

    def test_open_status_is_active_and_closed_status_is_excluded(self) -> None:
        closed_orders = tuple(
            replace(order, status="completed") if order.id == "ORD-001" else order
            for order in SAMPLE_DATA.orders
        )
        data = replace(SAMPLE_DATA, orders=closed_orders)
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(data), data)
        self.assertNotIn("ORD-001", {order.order_id for order in result.orders})

    def test_inventory_shortage_is_calculated(self) -> None:
        self.assertEqual(calculate_shortage(45, 10), 35)
        data = replace(SAMPLE_DATA, inventory=(replace(SAMPLE_DATA.inventory[0], quantity=10),) + SAMPLE_DATA.inventory[1:])
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(data), data)
        order = next(order for order in result.orders if order.order_id == "ORD-001")
        self.assertEqual(order.inventory_shortage.shortage_quantity, 35)
        self.assertEqual(order.severity, "high")

    def test_missing_inventory_is_insufficient_information(self) -> None:
        data = replace(SAMPLE_DATA, inventory=())
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(data), data)
        order = next(order for order in result.orders if order.order_id == "ORD-001")
        self.assertIsNone(order.inventory_shortage)
        self.assertTrue(order.insufficient_information)
        self.assertEqual(result.overall_state, "insufficient_information")

    def test_missing_required_date_is_insufficient_for_date_scoring(self) -> None:
        orders = tuple(
            replace(order, required_date=None) if order.id == "ORD-001" else order
            for order in SAMPLE_DATA.orders
        )
        data = replace(SAMPLE_DATA, orders=orders)
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(data), data)
        order = next(order for order in result.orders if order.order_id == "ORD-001")
        self.assertIn("Required date is unavailable", " ".join(order.reasons))
        self.assertIsNone(order.required_date)

    def test_downstream_impact_remains_distinguishable(self) -> None:
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(), SAMPLE_DATA)
        order = next(order for order in result.orders if order.order_id == "ORD-001")
        self.assertEqual(order.impact_classification, "downstream")
        self.assertTrue(any("downstream" in reason for reason in order.reasons))

    def test_unrelated_orders_are_excluded(self) -> None:
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(), SAMPLE_DATA)
        self.assertNotIn("ORD-004", {order.order_id for order in result.orders})

    def test_equal_scores_use_order_id_tie_breaker(self) -> None:
        orders = tuple(
            replace(order, priority="standard", required_date=date(2026, 9, 10))
            if order.id in {"ORD-001", "ORD-002"} else order
            for order in SAMPLE_DATA.orders
        )
        data = replace(SAMPLE_DATA, orders=orders)
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(data), data)
        relevant = [order.order_id for order in result.orders if order.order_id in {"ORD-001", "ORD-002"}]
        self.assertEqual(relevant, ["ORD-001", "ORD-002"])

    def test_severity_classification_works(self) -> None:
        result = prioritize_orders(REFERENCE_DATE, self.route_impact(), SAMPLE_DATA)
        self.assertEqual(next(order for order in result.orders if order.order_id == "ORD-001").severity, "medium")

    def test_no_affected_orders_are_handled(self) -> None:
        matching = match_understanding("DIS-PRIORITY", DisruptionUnderstanding(locations=["Kandla"]), SAMPLE_DATA)
        result = prioritize_orders(REFERENCE_DATE, analyze_impact("DIS-PRIORITY", matching, SAMPLE_DATA), SAMPLE_DATA)
        self.assertEqual(result.overall_state, "no_affected_orders")
        self.assertEqual(result.orders, [])

    def test_repeated_calculation_is_identical(self) -> None:
        first = prioritize_orders(REFERENCE_DATE, self.route_impact(), SAMPLE_DATA).model_dump()
        second = prioritize_orders(REFERENCE_DATE, self.route_impact(), SAMPLE_DATA).model_dump()
        self.assertEqual(first, second)

    def test_priority_endpoint_uses_stored_understanding(self) -> None:
        disruption = create_disruption(DisruptionNoticeRequest(description="Flooding near Vellore."))
        with patch("api.disruptions.extract_understanding", return_value=DisruptionUnderstanding(locations=["Vellore"])):
            understand_disruption(disruption.disruption_id)
        result = prioritize_disruption_orders(disruption.disruption_id)
        self.assertTrue(result.orders)
        self.assertEqual(result.orders[0].order_id, "ORD-001")


if __name__ == "__main__":
    unittest.main()