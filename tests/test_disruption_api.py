"""Focused tests for disruption notice intake and retrieval."""

import unittest
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

from fastapi import FastAPI

from api.disruptions import (
    MAX_DESCRIPTION_LENGTH,
    clear_disruptions,
    router,
)


class DisruptionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = FastAPI()
        cls.app.include_router(router)

    def request(self, method: str, path: str, body: object = None, raw_body: bytes | None = None) -> "ApiTestResponse":
        encoded_body = raw_body if raw_body is not None else json.dumps(body).encode()
        headers = [(b"content-type", b"application/json")]

        async def send_request() -> ApiTestResponse:
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
            return ApiTestResponse(response_status, json.loads(response_body))

        return asyncio.run(send_request())

    def setUp(self) -> None:
        clear_disruptions()

    def test_valid_disruption_is_accepted(self) -> None:
        response = self.request("POST", "/api/disruptions", {"description": "Flooding near Vellore."})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "accepted")

    def test_missing_description_is_rejected(self) -> None:
        self.assertEqual(self.request("POST", "/api/disruptions", {}).status_code, 422)

    def test_empty_description_is_rejected(self) -> None:
        self.assertEqual(self.request("POST", "/api/disruptions", {"description": ""}).status_code, 422)

    def test_whitespace_only_description_is_rejected(self) -> None:
        self.assertEqual(self.request("POST", "/api/disruptions", {"description": " \t\n "}).status_code, 422)

    def test_repeated_whitespace_is_normalized(self) -> None:
        response = self.request("POST", "/api/disruptions", {"description": " Heavy   flooding\nnear Vellore. "})
        self.assertEqual(response.json()["normalized_description"], "Heavy flooding near Vellore.")

    def test_original_description_is_preserved(self) -> None:
        description = " Heavy   flooding near Vellore. "
        response = self.request("POST", "/api/disruptions", {"description": description})
        self.assertEqual(response.json()["original_description"], description)

    def test_valid_reported_at_is_accepted(self) -> None:
        reported_at = "2026-09-04T08:30:00+00:00"
        response = self.request("POST", "/api/disruptions", {"description": "Flooding.", "reported_at": reported_at})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(datetime.fromisoformat(response.json()["reported_at"].replace("Z", "+00:00")), datetime.fromisoformat(reported_at))

    def test_invalid_reported_at_is_rejected(self) -> None:
        response = self.request("POST", "/api/disruptions", {"description": "Flooding.", "reported_at": "not-a-date"})
        self.assertEqual(response.status_code, 422)

    def test_post_creates_retrievable_disruption(self) -> None:
        created = self.request("POST", "/api/disruptions", {"description": "Flooding."}).json()
        retrieved = self.request("GET", f"/api/disruptions/{created['disruption_id']}")
        self.assertEqual(retrieved.status_code, 200)
        self.assertEqual(retrieved.json(), created)

    def test_get_returns_the_disruption(self) -> None:
        created = self.request("POST", "/api/disruptions", {"description": "Flooding.", "source": "operator"}).json()
        retrieved = self.request("GET", f"/api/disruptions/{created['disruption_id']}")
        self.assertEqual(retrieved.json()["source"], "operator")

    def test_unknown_id_returns_404(self) -> None:
        response = self.request("GET", "/api/disruptions/DIS-UNKNOWN")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Disruption not found", response.json()["detail"])

    def test_oversized_description_is_rejected(self) -> None:
        description = "x" * (MAX_DESCRIPTION_LENGTH + 1)
        self.assertEqual(self.request("POST", "/api/disruptions", {"description": description}).status_code, 422)

    def test_invalid_request_body_is_handled(self) -> None:
        response = self.request("POST", "/api/disruptions", raw_body=b"not-json")
        self.assertEqual(response.status_code, 422)


@dataclass
class ApiTestResponse:
    status_code: int
    payload: object

    def json(self) -> object:
        return self.payload


if __name__ == "__main__":
    unittest.main()