"""Focused tests for the deterministic decision audit and case history."""

import asyncio
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import FastAPI

from analysis.case import build_case_status
from analysis.coordination import build_response_coordination
from analysis.impact import analyze_impact
from analysis.models import CaseClosure, HumanDecision
from analysis.prioritization import prioritize_orders
from analysis.recommendations import build_action_plan
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


REPORTED_AT = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)


def _vellore_context():
    matching = match_understanding("DIS-CASE", DisruptionUnderstanding(locations=["Vellore"]), SAMPLE_DATA)
    impact = analyze_impact("DIS-CASE", matching, SAMPLE_DATA)
    priorities = prioritize_orders(REPORTED_AT.date(), impact, SAMPLE_DATA)
    plan = build_action_plan(impact, priorities)
    return impact, priorities, plan


class CaseBuilderTests(unittest.TestCase):
    def build(self, *, stages=(), decided=None, closure=None, understanding=True, reported_at=REPORTED_AT):
        impact, priorities, plan = _vellore_context()
        decided = decided or {}
        coordination = build_response_coordination(
            "DIS-CASE", impact, priorities, plan, SAMPLE_DATA, decided=decided
        )
        return build_case_status(
            disruption_id="DIS-CASE",
            reported_at=reported_at,
            understanding=DisruptionUnderstanding(locations=["Vellore"]) if understanding else None,
            coordination=coordination,
            impact=impact,
            priorities=priorities,
            plan=plan,
            stages=list(stages),
            decided=decided,
            closure=closure,
        )

    def test_new_when_no_understanding_was_captured(self) -> None:
        result = self.build(understanding=False)
        self.assertEqual(result.lifecycle_state, "new")
        self.assertFalse(result.requires_decisions)
        self.assertEqual(result.decision_progress, {"required": 0, "recorded": 0, "pending": 0})
        self.assertEqual(result.timeline[0].stage, "intake")
        self.assertEqual(result.timeline[0].label, "Disruption notice received")

    def test_investigating_until_coordination_stage_completes(self) -> None:
        stages = [
            ("understanding", datetime(2026, 9, 4, 6, 1, tzinfo=timezone.utc)),
            ("impact", datetime(2026, 9, 4, 6, 2, tzinfo=timezone.utc)),
        ]
        result = self.build(stages=stages)
        self.assertEqual(result.lifecycle_state, "investigating")
        self.assertTrue(result.requires_decisions)
        self.assertEqual(result.decision_progress["required"], 3)

    def test_awaiting_decisions_when_coordination_ready_without_recordings(self) -> None:
        stages = [
            ("understanding", datetime(2026, 9, 4, 6, 1, tzinfo=timezone.utc)),
            ("coordination", datetime(2026, 9, 4, 6, 2, tzinfo=timezone.utc)),
        ]
        result = self.build(stages=stages)
        self.assertEqual(result.lifecycle_state, "awaiting_decisions")
        self.assertEqual(result.decision_progress["recorded"], 0)
        self.assertEqual(result.decision_progress["pending"], 3)

    def test_decision_recorded_when_all_requirements_are_recorded(self) -> None:
        decided = {}
        for requirement in self.build().decision_requirements:
            decided[requirement.decision_id] = HumanDecision(
                decision_id=requirement.decision_id,
                status="recorded",
                recommended_option=requirement.recommended_option,
                selected_option=requirement.recommended_option,
                reviewer_role="supply_chain_planner",
                note="Approved",
                recorded_state="decision_recorded",
                recorded_at=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc),
            )
        stages = [
            ("understanding", datetime(2026, 9, 4, 6, 1, tzinfo=timezone.utc)),
            ("coordination", datetime(2026, 9, 4, 6, 2, tzinfo=timezone.utc)),
        ]
        result = self.build(stages=stages, decided=decided)
        self.assertEqual(result.lifecycle_state, "decision_recorded")
        self.assertEqual(result.decision_progress["recorded"], 3)

    def test_partial_recordings_remain_awaiting_decisions(self) -> None:
        decided = {}
        requirements = self.build().decision_requirements
        first = requirements[0]
        decided[first.decision_id] = HumanDecision(
            decision_id=first.decision_id,
            status="recorded",
            recommended_option=first.recommended_option,
            selected_option=first.recommended_option,
            reviewer_role=None,
            note=None,
            recorded_state="decision_recorded",
            recorded_at=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc),
        )
        stages = [
            ("understanding", datetime(2026, 9, 4, 6, 1, tzinfo=timezone.utc)),
            ("coordination", datetime(2026, 9, 4, 6, 2, tzinfo=timezone.utc)),
        ]
        result = self.build(stages=stages, decided=decided)
        self.assertEqual(result.lifecycle_state, "awaiting_decisions")
        self.assertEqual(result.decision_progress["recorded"], 1)

    def test_closed_reflects_a_recorded_case_closure(self) -> None:
        result = self.build(
            stages=[("coordination", datetime(2026, 9, 4, 6, 2, tzinfo=timezone.utc))],
            closure=CaseClosure(
                disruption_id="DIS-CASE",
                closed_at=datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc),
                reviewer_role="supply_chain_planner",
                note="Reviewed and completed.",
            ),
        )
        self.assertEqual(result.lifecycle_state, "closed")
        self.assertEqual(result.close.reviewer_role, "supply_chain_planner")
        self.assertEqual(result.close.note, "Reviewed and completed.")

    def test_no_action_required_for_no_impact_case(self) -> None:
        matching = match_understanding("DIS-CASE", DisruptionUnderstanding(locations=["Kandla"]), SAMPLE_DATA)
        impact = analyze_impact("DIS-CASE", matching, SAMPLE_DATA)
        priorities = prioritize_orders(REPORTED_AT.date(), impact, SAMPLE_DATA)
        plan = build_action_plan(impact, priorities)
        coordination = build_response_coordination("DIS-CASE", impact, priorities, plan, SAMPLE_DATA)
        result = build_case_status(
            disruption_id="DIS-CASE",
            reported_at=REPORTED_AT,
            understanding=DisruptionUnderstanding(locations=["Kandla"]),
            coordination=coordination,
            impact=impact,
            priorities=priorities,
            plan=plan,
            stages=[],
            decided={},
            closure=None,
        )
        self.assertEqual(result.lifecycle_state, "no_action_required")
        self.assertFalse(result.requires_decisions)
        self.assertEqual(result.roles, [])
        self.assertEqual(result.close, None)

    def test_execution_status_is_always_not_executed(self) -> None:
        for result in (self.build(understanding=False), self.build()):
            self.assertEqual(result.execution_status, "not_executed")
        self.assertEqual(self.build().decision_audit[0].execution_status, "not_executed")

    def test_audit_reflects_selected_option_and_reviewer_role(self) -> None:
        requirement = self.build().decision_requirements[0]
        decided = {
            requirement.decision_id: HumanDecision(
                decision_id=requirement.decision_id,
                status="recorded",
                recommended_option=requirement.recommended_option,
                selected_option="Confirm the recommended course",
                reviewer_role="supply_chain_planner",
                note="Ready to proceed",
                recorded_state="decision_recorded",
                recorded_at=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc),
            )
        }
        result = self.build(decided=decided)
        audit = next(entry for entry in result.decision_audit if entry.decision_id == requirement.decision_id)
        self.assertEqual(audit.decision_status, "recorded")
        self.assertEqual(audit.selected_option, "Confirm the recommended course")
        self.assertEqual(audit.reviewer_role, "supply_chain_planner")
        self.assertEqual(audit.review_note, "Ready to proceed")
        self.assertEqual(audit.decided_at, datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc))

    def test_audit_assigns_reviewer_roles_and_documents_role_ids(self) -> None:
        result = self.build()
        mapped = {entry.decision_type: entry for entry in result.decision_audit}
        self.assertEqual(mapped["approve-recommended-action"].assigned_reviewer_role_id, "supply_chain_planner")
        self.assertEqual(mapped["approve-recommended-action"].assigned_reviewer_role, "Supply Chain Planner")
        self.assertEqual(mapped["confirm-shipment-review"].assigned_reviewer_role_id, "logistics_transportation")
        self.assertEqual(mapped["confirm-customer-communication"].assigned_reviewer_role_id, "customer_service")
        self.assertEqual(mapped["confirm-customer-communication"].assigned_reviewer_role, "Customer Service")

    def test_audit_carries_evidence_references(self) -> None:
        result = self.build()
        for entry in result.decision_audit:
            for reference in entry.evidence_references:
                self.assertTrue(reference.evidence_id)
                self.assertTrue(reference.record_id)
                self.assertIn(reference.source_stage, {"understanding", "matching", "impact", "prioritization", "recommendation", "movement", "coordination"})

    def test_timeline_contains_no_fabricated_events(self) -> None:
        stages = [
            ("understanding", datetime(2026, 9, 4, 6, 1, tzinfo=timezone.utc)),
            ("impact", datetime(2026, 9, 4, 6, 2, tzinfo=timezone.utc)),
        ]
        result = self.build(stages=stages)
        expected_stages = ["intake", "understanding", "impact"]
        self.assertEqual([entry.stage for entry in result.timeline], expected_stages)
        for entry in result.timeline:
            self.assertIsNotNone(entry.timestamp)

    def test_timeline_includes_genuine_decision_and_close_entries(self) -> None:
        requirement = self.build().decision_requirements[0]
        stages = [
            ("understanding", datetime(2026, 9, 4, 6, 1, tzinfo=timezone.utc)),
            ("coordination", datetime(2026, 9, 4, 6, 2, tzinfo=timezone.utc)),
        ]
        decided = {
            requirement.decision_id: HumanDecision(
                decision_id=requirement.decision_id,
                status="recorded",
                recommended_option=requirement.recommended_option,
                selected_option="Confirm the recommended course",
                reviewer_role="supply_chain_planner",
                note=None,
                recorded_state="decision_recorded",
                recorded_at=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc),
            )
        }
        closure = CaseClosure(
            disruption_id="DIS-CASE",
            closed_at=datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc),
            reviewer_role="supply_chain_planner",
            note=None,
        )
        result = self.build(stages=stages, decided=decided, closure=closure)
        stages_in_order = [entry.stage for entry in result.timeline]
        self.assertIn("decision", stages_in_order)
        self.assertEqual(stages_in_order[-1], "close")
        decision_entry = next(entry for entry in result.timeline if entry.stage == "decision")
        self.assertIn("Confirm the recommended course", decision_entry.label)
        self.assertIsNotNone(decision_entry.timestamp)

    def test_builder_is_deterministic(self) -> None:
        args = dict(
            stages=[
                ("understanding", datetime(2026, 9, 4, 6, 1, tzinfo=timezone.utc)),
                ("coordination", datetime(2026, 9, 4, 6, 2, tzinfo=timezone.utc)),
            ],
        )
        first = self.build(**args).model_dump()
        second = self.build(**args).model_dump()
        self.assertEqual(first, second)


class CaseApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = FastAPI()
        cls.app.include_router(router)

    def setUp(self) -> None:
        clear_disruptions()

    def request(self, method: str, path: str, body: object | None = None) -> tuple[int, object]:
        if body is not None:
            encoded_body = json.dumps(body).encode()
        else:
            encoded_body = b""
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

    def advance_to_coordination(self, disruption_id: str) -> object:
        status_code, coordination = self.request("POST", f"/api/disruptions/{disruption_id}/coordination")
        self.assertEqual(status_code, 200)
        return coordination

    def record_all_decisions(self, disruption_id: str, coordination: object) -> list[str]:
        recorded = []
        for requirement in coordination["decision_requirements"]:
            status_code, decision = self.request(
                "POST",
                f"/api/disruptions/{disruption_id}/decision",
                {
                    "decision_id": requirement["decision_id"],
                    "selected_option": requirement["recommended_option"],
                    "reviewer_role": None,
                    "note": None,
                },
            )
            self.assertEqual(status_code, 200)
            recorded.append(decision["decision_id"])
        return recorded

    def test_case_endpoint_returns_typed_schema(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["disruption_id"], disruption_id)
        self.assertIn("lifecycle_state", payload)
        self.assertIn("decision_progress", payload)
        self.assertIn("decision_audit", payload)
        self.assertIn("timeline", payload)
        self.assertIn("warnings", payload)
        self.assertEqual(payload["execution_status"], "not_executed")

    def test_case_endpoint_unknown_id_returns_404(self) -> None:
        status_code, payload = self.request("GET", "/api/disruptions/DIS-UNKNOWN/case")
        self.assertEqual(status_code, 404)
        self.assertIn("Disruption not found", payload["detail"])

    def test_notice_only_case_is_new(self) -> None:
        disruption = create_disruption(DisruptionNoticeRequest(description="Flooding near Vellore."))
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption.disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["lifecycle_state"], "new")
        self.assertFalse(payload["requires_decisions"])
        self.assertEqual(payload["decision_audit"], [])
        self.assertEqual([entry["stage"] for entry in payload["timeline"]], ["intake"])

    def test_lifecycle_advances_from_investigating_to_awaiting_decisions(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(payload["lifecycle_state"], "investigating")
        self.assertEqual(payload["decision_progress"]["required"], 3)
        coordination = self.advance_to_coordination(disruption_id)
        self.assertEqual(len(coordination["decision_requirements"]), 3)
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["lifecycle_state"], "awaiting_decisions")

    def test_recorded_decisions_move_case_to_decision_recorded(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        coordination = self.advance_to_coordination(disruption_id)
        self.record_all_decisions(disruption_id, coordination)
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["lifecycle_state"], "decision_recorded")
        self.assertEqual(payload["decision_progress"]["pending"], 0)
        self.assertEqual(len(payload["decision_audit"]), 3)

    def test_cannot_close_with_pending_decisions(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        self.advance_to_coordination(disruption_id)
        status_code, payload = self.request(
            "POST", f"/api/disruptions/{disruption_id}/close", {"reviewer_role": "supply_chain_planner"}
        )
        self.assertEqual(status_code, 422)
        self.assertIn("Pending decisions", payload["detail"])

    def test_close_requires_stored_understanding(self) -> None:
        disruption = create_disruption(DisruptionNoticeRequest(description="Flooding near Vellore."))
        status_code, payload = self.request("POST", f"/api/disruptions/{disruption.disruption_id}/close", {"note": "n/a"})
        self.assertEqual(status_code, 409)
        self.assertIn("understanding", payload["detail"])

    def test_close_unknown_id_returns_404(self) -> None:
        status_code, payload = self.request("POST", "/api/disruptions/DIS-UNKNOWN/close", {"note": "n/a"})
        self.assertEqual(status_code, 404)
        self.assertIn("Disruption not found", payload["detail"])

    def test_close_rejects_unassigned_reviewer_role(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        coordination = self.advance_to_coordination(disruption_id)
        self.record_all_decisions(disruption_id, coordination)
        status_code, payload = self.request(
            "POST", f"/api/disruptions/{disruption_id}/close", {"reviewer_role": "unassigned_role"}
        )
        self.assertEqual(status_code, 422)
        self.assertIn("Reviewer role", payload["detail"])

    def test_close_records_closure_and_lifecycle_becomes_closed(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        coordination = self.advance_to_coordination(disruption_id)
        self.record_all_decisions(disruption_id, coordination)
        status_code, payload = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/close",
            {"reviewer_role": "supply_chain_planner", "note": "All reviews recorded."},
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["lifecycle_state"], "closed")
        self.assertIsNotNone(payload["close"]["closed_at"])
        self.assertEqual(payload["close"]["reviewer_role"], "supply_chain_planner")
        self.assertEqual(payload["timeline"][-1]["stage"], "close")
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["lifecycle_state"], "closed")

    def test_close_records_operations_manager_role(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        coordination = self.advance_to_coordination(disruption_id)
        self.record_all_decisions(disruption_id, coordination)
        status_code, payload = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/close",
            {"reviewer_role": "Operations Manager", "note": "Closed by the operations manager."},
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["lifecycle_state"], "closed")
        self.assertEqual(payload["close"]["reviewer_role"], "Operations Manager")
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["close"]["reviewer_role"], "Operations Manager")
        self.assertEqual(payload["timeline"][-1]["stage"], "close")
        self.assertIn("by Operations Manager", payload["timeline"][-1]["label"])

    def test_close_accepts_session_role_ids(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        coordination = self.advance_to_coordination(disruption_id)
        self.record_all_decisions(disruption_id, coordination)
        status_code, payload = self.request(
            "POST", f"/api/disruptions/{disruption_id}/close", {"reviewer_role": "operations_manager"}
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["close"]["reviewer_role"], "operations_manager")

    def test_close_accepts_session_role_for_no_action_case(self) -> None:
        disruption_id = self.seed_understanding(
            "Port delays near Kandla.", DisruptionUnderstanding(locations=["Kandla"])
        )
        status_code, payload = self.request(
            "POST", f"/api/disruptions/{disruption_id}/close", {"reviewer_role": "Operations Manager"}
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["lifecycle_state"], "closed")
        self.assertEqual(payload["close"]["reviewer_role"], "Operations Manager")

    def test_second_close_is_rejected(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        coordination = self.advance_to_coordination(disruption_id)
        self.record_all_decisions(disruption_id, coordination)
        status_code, payload = self.request(
            "POST", f"/api/disruptions/{disruption_id}/close", {"note": "first"}
        )
        self.assertEqual(status_code, 200)
        status_code, payload = self.request(
            "POST", f"/api/disruptions/{disruption_id}/close", {"note": "second"}
        )
        self.assertEqual(status_code, 422)
        self.assertIn("already closed", payload["detail"])

    def test_no_impact_kandla_case_is_no_action_required(self) -> None:
        disruption_id = self.seed_understanding(
            "Port delays near Kandla.", DisruptionUnderstanding(locations=["Kandla"])
        )
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["lifecycle_state"], "no_action_required")
        self.assertFalse(payload["requires_decisions"])
        self.assertEqual(payload["roles"], [])
        self.assertIsNone(payload["close"])
        self.assertEqual(payload["execution_status"], "not_executed")

    def test_close_works_for_no_action_case(self) -> None:
        disruption_id = self.seed_understanding(
            "Port delays near Kandla.", DisruptionUnderstanding(locations=["Kandla"])
        )
        status_code, payload = self.request("POST", f"/api/disruptions/{disruption_id}/close", {"note": "No action."})
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["lifecycle_state"], "closed")

    def test_timeline_reflects_only_genuine_calls(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        status_code, _ = self.request("POST", f"/api/disruptions/{disruption_id}/impact")
        self.assertEqual(status_code, 200)
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        stages = [entry["stage"] for entry in payload["timeline"]]
        self.assertEqual(stages, ["intake", "understanding", "impact"])
        for entry in payload["timeline"]:
            self.assertIsNotNone(entry["timestamp"])

    def test_audit_reflects_recorded_option_role_and_role_name(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        coordination = self.advance_to_coordination(disruption_id)
        requirement = coordination["decision_requirements"][0]
        status_code, decision = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {
                "decision_id": requirement["decision_id"],
                "selected_option": requirement["recommended_option"],
                "reviewer_role": "supply_chain_planner",
                "note": "Confirmed by planner",
            },
        )
        self.assertEqual(status_code, 200)
        status_code, payload = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        audit = next(entry for entry in payload["decision_audit"] if entry["decision_id"] == requirement["decision_id"])
        self.assertEqual(audit["decision_status"], "recorded")
        self.assertEqual(audit["selected_option"], requirement["recommended_option"])
        self.assertEqual(audit["reviewer_role"], "supply_chain_planner")
        self.assertEqual(audit["review_note"], "Confirmed by planner")
        self.assertEqual(audit["assigned_reviewer_role_id"], "supply_chain_planner")
        self.assertIsNotNone(audit["decided_at"])

    def test_case_endpoint_is_read_only(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        first = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        second = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(first[1], second[1])

    def test_existing_endpoints_remain_consistent(self) -> None:
        disruption_id = self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )
        status_code, coordination = self.request("POST", f"/api/disruptions/{disruption_id}/coordination")
        self.assertEqual(status_code, 200)
        self.assertEqual(
            [role["role_id"] for role in coordination["roles"]],
            ["supply_chain_planner", "logistics_transportation", "customer_service"],
        )
        record = self.record_all_decisions(disruption_id, coordination)
        self.assertEqual(len(record), 3)
        status_code, scenarios = self.request("POST", f"/api/disruptions/{disruption_id}/scenarios")
        self.assertEqual(status_code, 200)
        self.assertEqual(scenarios["simulation_state"], "scenario_comparison_available")


if __name__ == "__main__":
    unittest.main()