"""Phase 16 focused tests for the deterministic response coordination & human decision workflow."""

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI, HTTPException

from analysis.coordination import build_response_coordination
from analysis.impact import analyze_impact
from analysis.models import HumanDecision
from analysis.prioritization import prioritize_orders
from analysis.recommendations import build_action_plan
from api.disruptions import (
    DisruptionNoticeRequest,
    analyze_disruption_analytics,
    analyze_disruption_impact,
    clear_disruptions,
    coordinate_disruption_response,
    create_disruption,
    match_disruption,
    prioritize_disruption_orders,
    recommend_disruption_actions,
    record_disruption_decision,
    router,
    shipment_movement_evidence,
    understand_disruption,
)
from data.sample_data import SAMPLE_DATA
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding


def route_impact(data=SAMPLE_DATA):
    matching = match_understanding("DIS-COORD", DisruptionUnderstanding(locations=["Vellore"]), data)
    return analyze_impact("DIS-COORD", matching, data)


def route_coordination(data=SAMPLE_DATA, decided=None):
    impact = route_impact(data)
    priorities = prioritize_orders(date(2026, 9, 4), impact, data)
    plan = build_action_plan(impact, priorities)
    return build_response_coordination("DIS-COORD", impact, priorities, plan, data, decided=decided)


def no_impact_coordination(data=SAMPLE_DATA):
    matching = match_understanding("DIS-COORD", DisruptionUnderstanding(locations=["Kandla"]), data)
    impact = analyze_impact("DIS-COORD", matching, data)
    priorities = prioritize_orders(date(2026, 9, 4), impact, data)
    plan = build_action_plan(impact, priorities)
    return build_response_coordination("DIS-COORD", impact, priorities, plan, data)


class CoordinationPipelineTests(unittest.TestCase):
    def test_affected_case_assigns_deterministic_roles(self) -> None:
        coordination = route_coordination()
        roles = {role.role_id: role for role in coordination.roles}
        self.assertEqual(
            sorted(roles),
            ["customer_service", "logistics_transportation", "supply_chain_planner"],
        )
        self.assertEqual(coordination.coordination_state, "response_coordination_required")
        self.assertEqual(roles["logistics_transportation"].related_shipment_ids, ["SHP-001"])
        self.assertEqual(
            roles["supply_chain_planner"].related_order_ids,
            ["ORD-001", "ORD-002", "ORD-003"],
        )
        self.assertEqual(
            roles["customer_service"].related_order_ids,
            ["ORD-001", "ORD-002", "ORD-003"],
        )
        self.assertNotIn("inventory_warehouse", roles)

    def test_role_priorities_are_ordered_and_deterministic(self) -> None:
        coordination = route_coordination()
        ordered = [role.role_id for role in sorted(coordination.roles, key=lambda role: role.priority)]
        self.assertEqual(
            ordered,
            ["supply_chain_planner", "logistics_transportation", "customer_service"],
        )
        priorities = [role.priority for role in coordination.roles]
        self.assertEqual(sorted(priorities), priorities)

    def test_decision_requirements_cover_supported_decisions(self) -> None:
        coordination = route_coordination()
        requirements = coordination.decision_requirements
        self.assertEqual(len(requirements), 3)
        approve = next(
            requirement for requirement in requirements
            if requirement.decision_type == "approve-recommended-action"
        )
        self.assertEqual(
            approve.decision_id,
            "decision:DIS-COORD:approve-recommended-action",
        )
        self.assertEqual(approve.recommended_option, "Prioritize affected orders for operator review")
        self.assertIn("Investigate affected shipment path", approve.alternative_options)
        decision_ids = [requirement.decision_id for requirement in requirements]
        self.assertIn("decision:DIS-COORD:confirm-shipment-review", decision_ids)
        self.assertIn("decision:DIS-COORD:confirm-customer-communication", decision_ids)

    def test_pending_human_decision_gate(self) -> None:
        coordination = route_coordination()
        human = coordination.human_decision
        self.assertIsNotNone(human)
        self.assertEqual(human.status, "pending")
        self.assertEqual(human.recorded_state, "pending_human_decision")
        self.assertIsNone(human.selected_option)
        self.assertIsNone(human.reviewer_role)
        self.assertIsNone(human.recorded_at)

    def test_recorded_human_decision_overlay(self) -> None:
        recorded = HumanDecision(
            decision_id="decision:DIS-COORD:approve-recommended-action",
            status="recorded",
            recommended_option="Prioritize affected orders for operator review",
            selected_option="Prioritize affected orders for operator review",
            reviewer_role="Supply Chain Planner",
            note="Approved in demo",
            recorded_state="decision_recorded",
            recorded_at=datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
        )
        coordination = route_coordination(decided={recorded.decision_id: recorded})
        human = coordination.human_decision
        self.assertEqual(human.status, "recorded")
        self.assertEqual(human.selected_option, "Prioritize affected orders for operator review")
        self.assertEqual(human.reviewer_role, "Supply Chain Planner")
        self.assertEqual(human.recorded_state, "decision_recorded")

    def test_no_impact_kandla_requires_no_coordination(self) -> None:
        coordination = no_impact_coordination()
        self.assertEqual(coordination.coordination_state, "no_response_coordination_required")
        self.assertEqual(coordination.roles, [])
        self.assertEqual(coordination.decision_requirements, [])
        self.assertIsNone(coordination.human_decision)
        self.assertIn("no impact was established", coordination.recommended_next_step)

    def test_no_false_execution_is_claimed(self) -> None:
        coordination = route_coordination()
        self.assertIn(
            "modifies no operational records",
            " ".join(coordination.warnings),
        )
        self.assertIn("only records the decision", coordination.recommended_next_step)
        self.assertEqual(coordination.human_decision.recorded_state, "pending_human_decision")

    def test_evidence_references_traceability(self) -> None:
        coordination = route_coordination()
        role_refs = [role.evidence_references for role in coordination.roles]
        self.assertTrue(any(role_refs))
        coordination_refs = [
            reference
            for role in coordination.roles
            for reference in role.evidence_references
            if reference.source_stage == "coordination"
        ]
        self.assertTrue(coordination_refs)
        ids = {reference.record_id for reference in coordination_refs}
        self.assertIn("SHP-001", ids)
        self.assertIn("ORD-001", ids)

    def test_coordination_is_deterministic(self) -> None:
        first = route_coordination().model_dump()
        second = route_coordination().model_dump()
        self.assertEqual(first, second)

    def test_insufficient_inventory_evidence_adds_warehouse_review(self) -> None:
        data = replace(SAMPLE_DATA, inventory=())
        impact = route_impact(data)
        priorities = prioritize_orders(date(2026, 9, 4), impact, data)
        plan = build_action_plan(impact, priorities)
        coordination = build_response_coordination("DIS-COORD", impact, priorities, plan, data)
        self.assertEqual(coordination.coordination_state, "insufficient_information")
        roles = {role.role_id: role for role in coordination.roles}
        self.assertIn("inventory_warehouse", roles)
        self.assertEqual(
            roles["inventory_warehouse"].related_order_ids,
            ["ORD-001", "ORD-002", "ORD-003"],
        )
        decision_ids = [requirement.decision_id for requirement in coordination.decision_requirements]
        self.assertIn("decision:DIS-COORD:resolve-inventory-review", decision_ids)


class CoordinationApiTests(unittest.TestCase):
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

    def test_coordination_endpoint_returns_typed_schema(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        status_code, payload = self.request("POST", f"/api/disruptions/{disruption_id}/coordination")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["disruption_id"], disruption_id)
        self.assertEqual(payload["coordination_state"], "response_coordination_required")
        self.assertEqual(
            [role["role_id"] for role in payload["roles"]],
            ["supply_chain_planner", "logistics_transportation", "customer_service"],
        )
        self.assertEqual(len(payload["decision_requirements"]), 3)
        self.assertEqual(payload["human_decision"]["status"], "pending")
        self.assertEqual(payload["human_decision"]["recorded_state"], "pending_human_decision")
        self.assertIn("evidence_references", payload)
        self.assertIn("recommended_next_step", payload)

    def test_coordination_endpoint_pending_then_recorded_gate(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        _, first = self.request("POST", f"/api/disruptions/{disruption_id}/coordination")
        self.assertEqual(first["human_decision"]["status"], "pending")

        decision_id = f"decision:{disruption_id}:approve-recommended-action"
        status_code, decision = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {"decision_id": decision_id, "selected_option": "Prioritize affected orders for operator review"},
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(decision["status"], "recorded")
        self.assertEqual(decision["recorded_state"], "decision_recorded")
        self.assertEqual(decision["selected_option"], "Prioritize affected orders for operator review")
        self.assertIsNotNone(decision["recorded_at"])

        _, second = self.request("POST", f"/api/disruptions/{disruption_id}/coordination")
        self.assertEqual(second["human_decision"]["status"], "recorded")
        self.assertEqual(second["human_decision"]["selected_option"], "Prioritize affected orders for operator review")
        self.assertEqual(second["human_decision"]["recorded_state"], "decision_recorded")

    def test_decision_endpoint_requires_stored_understanding(self) -> None:
        disruption = create_disruption(DisruptionNoticeRequest(description="Flooding near Vellore."))
        status_code, _ = self.request(
            "POST",
            f"/api/disruptions/{disruption.disruption_id}/decision",
            {"decision_id": "decision:x", "selected_option": "any"},
        )
        self.assertEqual(status_code, 409)

    def test_decision_endpoint_unknown_id_returns_404(self) -> None:
        status_code, _ = self.request(
            "POST",
            "/api/disruptions/DIS-UNKNOWN/decision",
            {"decision_id": "decision:x", "selected_option": "any"},
        )
        self.assertEqual(status_code, 404)
        with self.assertRaises(HTTPException) as context:
            record_disruption_decision("DIS-UNKNOWN", object())
        self.assertEqual(context.exception.status_code, 404)

    def test_decision_endpoint_rejects_unknown_requirement(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        status_code, payload = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {"decision_id": "decision:DIS-INVALID:nonexistent", "selected_option": "any"},
        )
        self.assertEqual(status_code, 422)
        self.assertIn("Unknown decision requirement", payload["detail"])

    def test_decision_endpoint_rejects_unlisted_option(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        decision_id = f"decision:{disruption_id}:approve-recommended-action"
        status_code, payload = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {"decision_id": decision_id, "selected_option": "Execute the plan immediately"},
        )
        self.assertEqual(status_code, 422)
        self.assertIn("Selected option must be one of", payload["detail"])

    def test_decision_endpoint_rejects_unassigned_reviewer_role(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        decision_id = f"decision:{disruption_id}:approve-recommended-action"
        status_code, _ = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {
                "decision_id": decision_id,
                "selected_option": "Prioritize affected orders for operator review",
                "reviewer_role": "Unassigned Role",
            },
        )
        self.assertEqual(status_code, 422)

    def test_decision_endpoint_accepts_role_name_reviewer(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        decision_id = f"decision:{disruption_id}:approve-recommended-action"
        status_code, decision = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {
                "decision_id": decision_id,
                "selected_option": "Prioritize affected orders for operator review",
                "reviewer_role": "Supply Chain Planner",
                "note": "Approved by the assigned planner.",
            },
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(decision["reviewer_role"], "Supply Chain Planner")
        self.assertEqual(decision["note"], "Approved by the assigned planner.")

    def test_no_impact_kandla_coordination_flow(self) -> None:
        disruption_id = self.seed_understanding(
            "Severe flooding near Kandla.",
            DisruptionUnderstanding(event_type="flood", locations=["Kandla"]),
        )
        status_code, payload = self.request("POST", f"/api/disruptions/{disruption_id}/coordination")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["coordination_state"], "no_response_coordination_required")
        self.assertEqual(payload["roles"], [])
        self.assertEqual(payload["decision_requirements"], [])
        self.assertIsNone(payload["human_decision"])
        decision_status, _ = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {"decision_id": "decision:anything", "selected_option": "any"},
        )
        self.assertEqual(decision_status, 422)

    def test_full_investigation_flow_with_coordination_stays_consistent(self) -> None:
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
        coordination = coordinate_disruption_response(disruption_id)
        self.assertEqual(matches.match_status, "matched")
        self.assertEqual(impact.impact_state, "impact_identified")
        self.assertEqual(analytics.investigation.affected_order_ids, ["ORD-001", "ORD-002", "ORD-003"])
        self.assertEqual(movement.affected_shipment_ids, ["SHP-001"])
        self.assertEqual(priorities.overall_state, "prioritized")
        self.assertEqual(plan.overall_state, "recommendation_available")
        self.assertEqual(coordination.coordination_state, "response_coordination_required")
        self.assertEqual({role.role_id for role in coordination.roles}, {
            "supply_chain_planner",
            "logistics_transportation",
            "customer_service",
        })
        self.assertEqual(coordination.human_decision.status, "pending")


if __name__ == "__main__":
    unittest.main()