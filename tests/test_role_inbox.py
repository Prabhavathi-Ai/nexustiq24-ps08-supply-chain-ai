"""Phase 21 focused tests for the role-based decision inbox & stakeholder handoff backend.

These tests pin the role-per-requirement enforcement added to the decision endpoint
and the control-tower decision progress surfaced by the case status endpoint, without
inventing authentication or changing any existing Phase 16-20 behavior.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI

from analysis.case import DECISION_TYPE_ROLE
from api.disruptions import (
    DisruptionNoticeRequest,
    clear_disruptions,
    create_disruption,
    router,
    understand_disruption,
)
from gemini.models import DisruptionUnderstanding

ROLE_ID_BY_TYPE = lambda decision_type: DECISION_TYPE_ROLE.get(decision_type)
VELLORE_REQUIREMENTS = [
    ("approve-recommended-action", "supply_chain_planner", "Prioritize affected orders for operator review"),
    ("confirm-shipment-review", "logistics_transportation", "Record shipment review outcome"),
    ("confirm-customer-communication", "customer_service", "Record communication decision; no message will be sent"),
]


class RoleInboxApiTests(unittest.TestCase):
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

    def seed_vellore(self) -> str:
        return self.seed_understanding(
            "Heavy flooding near Vellore.", DisruptionUnderstanding(locations=["Vellore"])
        )

    def test_vellore_requirements_map_to_assigned_roles(self) -> None:
        disruption_id = self.seed_vellore()
        status_code, _ = self.request("POST", f"/api/disruptions/{disruption_id}/coordination")
        self.assertEqual(status_code, 200)
        status_code, case = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(case["lifecycle_state"], "awaiting_decisions")
        self.assertEqual(case["decision_progress"], {"required": 3, "recorded": 0, "pending": 3})
        by_type = {requirement["decision_type"]: requirement for requirement in case["decision_requirements"]}
        self.assertEqual(set(by_type), {"approve-recommended-action", "confirm-shipment-review", "confirm-customer-communication"})
        audit_by_type = {entry["decision_type"]: entry for entry in case["decision_audit"]}
        for decision_type, expected_role, recommended in VELLORE_REQUIREMENTS:
            requirement = by_type[decision_type]
            self.assertEqual(requirement["recommended_option"], recommended)
            self.assertEqual(audit_by_type[decision_type]["assigned_reviewer_role_id"], expected_role)
            self.assertEqual(ROLE_ID_BY_TYPE(decision_type), expected_role)
        self.assertNotIn("resolve-inventory-review", by_type)

    def test_unassigned_role_cannot_record_another_roles_decision(self) -> None:
        disruption_id = self.seed_vellore()
        decision_id = f"decision:{disruption_id}:approve-recommended-action"
        status_code, payload = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {
                "decision_id": decision_id,
                "selected_option": VELLORE_REQUIREMENTS[0][2],
                "reviewer_role": "logistics_transportation",
                "note": "Wrong stakeholder attempts to record.",
            },
        )
        self.assertEqual(status_code, 422)
        self.assertIn("role assigned to this decision requirement", payload["detail"])
        _, case = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(case["decision_progress"]["recorded"], 0)

    def test_assigned_role_by_role_id_records_its_decision(self) -> None:
        disruption_id = self.seed_vellore()
        decision_id = f"decision:{disruption_id}:confirm-shipment-review"
        status_code, decision = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {
                "decision_id": decision_id,
                "selected_option": "No corrective movement required",
                "reviewer_role": "logistics_transportation",
            },
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(decision["status"], "recorded")
        self.assertEqual(decision["reviewer_role"], "logistics_transportation")
        self.assertEqual(decision["recorded_state"], "decision_recorded")
        _, case = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(case["decision_progress"], {"required": 3, "recorded": 1, "pending": 2})
        self.assertEqual(case["lifecycle_state"], "awaiting_decisions")
        audit_entry = next(
            entry for entry in case["decision_audit"] if entry["decision_type"] == "confirm-shipment-review"
        )
        self.assertEqual(audit_entry["decision_status"], "recorded")
        self.assertEqual(audit_entry["assigned_reviewer_role_id"], "logistics_transportation")
        self.assertEqual(audit_entry["reviewer_role"], "logistics_transportation")
        self.assertEqual(audit_entry["execution_status"], "not_executed")

    def test_assigned_role_by_role_name_records_its_decision(self) -> None:
        disruption_id = self.seed_vellore()
        decision_id = f"decision:{disruption_id}:confirm-customer-communication"
        _, case_before = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(case_before["decision_progress"]["pending"], 3)
        status_code, decision = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {
                "decision_id": decision_id,
                "selected_option": "Hold customer communication",
                "reviewer_role": "Customer Service",
                "note": "Customer communication kept on hold.",
            },
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(decision["reviewer_role"], "Customer Service")
        self.assertEqual(decision["note"], "Customer communication kept on hold.")
        _, case_after = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(case_after["decision_progress"], {"required": 3, "recorded": 1, "pending": 2})

    def test_all_recorded_completes_without_auto_close(self) -> None:
        disruption_id = self.seed_vellore()
        remaining = len(VELLORE_REQUIREMENTS)
        for decision_type, role_id, selected in VELLORE_REQUIREMENTS:
            status_code, _ = self.request(
                "POST",
                f"/api/disruptions/{disruption_id}/decision",
                {
                    "decision_id": f"decision:{disruption_id}:{decision_type}",
                    "selected_option": selected,
                    "reviewer_role": role_id,
                },
            )
            self.assertEqual(status_code, 200)
            remaining -= 1
            _, case = self.request("GET", f"/api/disruptions/{disruption_id}/case")
            self.assertEqual(case["decision_progress"]["pending"], remaining)
        status_code, case = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(case["decision_progress"], {"required": 3, "recorded": 3, "pending": 0})
        self.assertEqual(case["lifecycle_state"], "decision_recorded")
        self.assertIsNone(case["close"])
        self.assertEqual(case["execution_status"], "not_executed")
        self.assertEqual({entry["decision_status"] for entry in case["decision_audit"]}, {"recorded"})
        decision_entries = [entry for entry in case["timeline"] if entry["stage"] == "decision"]
        self.assertEqual(len(decision_entries), 3)
        self.assertEqual([entry for entry in case["timeline"] if entry["stage"] == "close"], [])

    def test_kandla_requires_no_decisions_and_stays_no_action(self) -> None:
        disruption_id = self.seed_understanding(
            "Severe flooding near Kandla.",
            DisruptionUnderstanding(event_type="flood", locations=["Kandla"]),
        )
        status_code, case = self.request("GET", f"/api/disruptions/{disruption_id}/case")
        self.assertEqual(status_code, 200)
        self.assertEqual(case["lifecycle_state"], "no_action_required")
        self.assertEqual(case["decision_requirements"], [])
        self.assertEqual(case["decision_progress"], {"required": 0, "recorded": 0, "pending": 0})
        decision_status, payload = self.request(
            "POST",
            f"/api/disruptions/{disruption_id}/decision",
            {"decision_id": "decision:anything", "selected_option": "any", "reviewer_role": "planner"},
        )
        self.assertEqual(decision_status, 422)
        self.assertIn("Unknown decision requirement", payload["detail"])

    def test_unknown_disruption_stays_untouched(self) -> None:
        status_code, payload = self.request("GET", "/api/disruptions/DIS-UNKNOWN/case")
        self.assertEqual(status_code, 404)
        self.assertIn("Disruption not found", payload["detail"])
        decision_status, _ = self.request(
            "POST",
            "/api/disruptions/DIS-UNKNOWN/decision",
            {"decision_id": "decision:x", "selected_option": "any", "reviewer_role": "planner"},
        )
        self.assertEqual(decision_status, 404)


if __name__ == "__main__":
    unittest.main()