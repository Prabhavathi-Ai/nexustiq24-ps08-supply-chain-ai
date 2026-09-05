"""Focused tests for the deterministic what-if scenario comparison."""

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import patch

from fastapi import FastAPI

from analysis.impact import analyze_impact
from analysis.prioritization import prioritize_orders
from analysis.models import ActionPlanResponse
from analysis.recommendations import build_action_plan
from analysis.scenarios import build_scenario_comparison
from api.disruptions import (
    DisruptionNoticeRequest,
    clear_disruptions,
    create_disruption,
    router,
    understand_disruption,
)
from data.sample_data import SAMPLE_DATA
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding


def _vellore_context(data=SAMPLE_DATA):
    matching = match_understanding("DIS-SCEN", DisruptionUnderstanding(locations=["Vellore"]), data)
    impact = analyze_impact("DIS-SCEN", matching, data)
    priorities = prioritize_orders(date(2026, 9, 4), impact, data)
    plan = build_action_plan(impact, priorities)
    return impact, priorities, plan


class ScenarioBuilderTests(unittest.TestCase):
    def comparison(self, data=SAMPLE_DATA):
        impact, priorities, plan = _vellore_context(data)
        return build_scenario_comparison("DIS-SCEN", impact, priorities, plan, data)

    def test_vellore_returns_recommended_and_alternative_scenarios(self) -> None:
        result = self.comparison()
        self.assertEqual(result.simulation_state, "scenario_comparison_available")
        self.assertGreaterEqual(len(result.scenarios), 2)
        self.assertEqual(sum(1 for scenario in result.scenarios if scenario.is_recommended), 1)
        recommended = next(scenario for scenario in result.scenarios if scenario.is_recommended)
        self.assertEqual(result.recommended_scenario_id, recommended.scenario_id)
        for scenario in result.scenarios:
            self.assertEqual(scenario.execution_status, "simulation_only")
            self.assertIn("nothing executed", scenario.execution_notice)
            self.assertIn("advisory", scenario.advisory_notice)
            self.assertIn(f"scenario:DIS-SCEN:{scenario.option_id}", scenario.scenario_id)

    def test_kandla_no_impact_creates_no_scenarios(self) -> None:
        matching = match_understanding("DIS-SCEN", DisruptionUnderstanding(locations=["Kandla"]), SAMPLE_DATA)
        impact = analyze_impact("DIS-SCEN", matching, SAMPLE_DATA)
        priorities = prioritize_orders(date(2026, 9, 4), impact, SAMPLE_DATA)
        plan = build_action_plan(impact, priorities)
        result = build_scenario_comparison("DIS-SCEN", impact, priorities, plan, SAMPLE_DATA)
        self.assertEqual(result.simulation_state, "no_scenario_created")
        self.assertEqual(result.scenarios, [])

    def test_recommended_scenario_metrics_match_committed_records(self) -> None:
        result = self.comparison()
        recommended = next(scenario for scenario in result.scenarios if scenario.is_recommended)
        metrics = recommended.metrics
        self.assertEqual(metrics.affected_orders_covered, metrics.affected_orders_total)
        self.assertEqual(metrics.affected_customers_covered, metrics.affected_customers_total)
        self.assertEqual(metrics.affected_shipments_covered, 1)
        self.assertEqual(metrics.affected_shipments_total, 1)
        self.assertEqual(metrics.priority_orders_covered, metrics.priority_orders_total)
        self.assertEqual(metrics.order_quantity_covered, 45 + 30 + 20)
        self.assertEqual(metrics.available_inventory_for_covered_skus, 180)
        self.assertEqual(metrics.shortage_quantity_covered, 0)
        self.assertEqual(metrics.covered_order_ids, ["ORD-001", "ORD-002", "ORD-003"])
        self.assertEqual(metrics.covered_customer_ids, ["CUS-001", "CUS-002", "CUS-003"])
        self.assertEqual(metrics.covered_shipment_ids, ["SHP-001"])

    def test_shortage_scenario_reports_committed_shortage(self) -> None:
        data = replace(SAMPLE_DATA, inventory=(replace(SAMPLE_DATA.inventory[0], quantity=10),) + SAMPLE_DATA.inventory[1:])
        result = self.comparison(data)
        shortage_scenario = next(s for s in result.scenarios if s.option_id == "review-inventory-availability")
        self.assertEqual(shortage_scenario.metrics.shortage_quantity_covered, (45 - 10) + (30 - 10) + (20 - 10))
        self.assertEqual(shortage_scenario.metrics.available_inventory_for_covered_skus, 10)
        self.assertEqual(shortage_scenario.metrics.affected_orders_covered, 3)

    def test_missing_inventory_marks_metrics_incomplete(self) -> None:
        result = self.comparison(replace(SAMPLE_DATA, inventory=()))
        recommended = next(scenario for scenario in result.scenarios if scenario.is_recommended)
        self.assertIsNone(recommended.metrics.shortage_quantity_covered)
        self.assertTrue(recommended.metrics.shortage_incomplete)
        self.assertIsNone(recommended.metrics.available_inventory_for_covered_skus)
        self.assertTrue(recommended.metrics.inventory_incomplete)
        self.assertTrue(any("not guessed" in warning for warning in result.warnings))

    def test_no_action_options_is_insufficient_information(self) -> None:
        impact, priorities, _ = _vellore_context()
        plan = ActionPlanResponse(
            overall_state="insufficient_information",
            recommended_course="No supported action option can be recommended.",
            operator_decision_required="Review first.",
            options=[],
        )
        result = build_scenario_comparison("DIS-SCEN", impact, priorities, plan, SAMPLE_DATA)
        self.assertEqual(result.simulation_state, "insufficient_information")
        self.assertEqual(result.scenarios, [])

    def test_scenario_recommendation_always_matches_the_plan(self) -> None:
        result = self.comparison()
        for scenario in result.scenarios:
            if scenario.is_recommended:
                self.assertEqual(scenario.scenario_id, result.recommended_scenario_id)

    def test_scenario_comparison_is_deterministic(self) -> None:
        first = self.comparison().model_dump()
        second = self.comparison().model_dump()
        self.assertEqual(first, second)

    def test_scenario_options_carry_tradeoffs_and_evidence(self) -> None:
        result = self.comparison()
        for scenario in result.scenarios:
            self.assertTrue(scenario.addresses)
            self.assertTrue(scenario.does_not_address)
            self.assertTrue(scenario.operational_trade_offs)
            self.assertTrue(scenario.prerequisites)
            self.assertTrue(scenario.evidence)


class ScenarioApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = FastAPI()
        cls.app.include_router(router)

    def setUp(self) -> None:
        clear_disruptions()

    def request(self, method: str, path: str) -> tuple[int, object]:
        headers = [(b"content-type", b"application/json")]

        async def send_request() -> tuple[int, object]:
            messages = [{"type": "http.request", "body": b"", "more_body": False}]
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

    def test_scenarios_endpoint_returns_typed_schema(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        status_code, payload = self.request("POST", f"/api/disruptions/{disruption_id}/scenarios")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["disruption_id"], disruption_id)
        self.assertEqual(payload["simulation_state"], "scenario_comparison_available")
        self.assertTrue(payload["scenarios"])
        self.assertEqual(payload["scenarios"][0]["execution_status"], "simulation_only")
        self.assertIn("nothing executed", payload["scenarios"][0]["execution_notice"])
        self.assertIn("recommended_scenario_id", payload)
        self.assertIn("comparison_note", payload)
        self.assertIn("warnings", payload)

    def test_scenarios_endpoint_kandla_creates_no_scenarios(self) -> None:
        disruption_id = self.seed_understanding(
            "Port delays near Kandla.", DisruptionUnderstanding(locations=["Kandla"])
        )
        status_code, payload = self.request("POST", f"/api/disruptions/{disruption_id}/scenarios")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["simulation_state"], "no_scenario_created")
        self.assertEqual(payload["scenarios"], [])

    def test_scenarios_endpoint_unknown_id_returns_404(self) -> None:
        status_code, payload = self.request("POST", "/api/disruptions/DIS-UNKNOWN/scenarios")
        self.assertEqual(status_code, 404)
        self.assertIn("Disruption not found", payload["detail"])

    def test_scenarios_endpoint_requires_stored_understanding(self) -> None:
        disruption = create_disruption(DisruptionNoticeRequest(description="Flooding near Vellore."))
        status_code, payload = self.request("POST", f"/api/disruptions/{disruption.disruption_id}/scenarios")
        self.assertEqual(status_code, 409)
        self.assertIn("understanding", payload["detail"])


if __name__ == "__main__":
    unittest.main()
