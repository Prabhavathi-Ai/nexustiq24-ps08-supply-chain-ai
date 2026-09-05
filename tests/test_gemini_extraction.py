"""Tests for the isolated, mocked Gemini disruption understanding service."""

import unittest
from unittest.mock import patch

import httpx

from gemini.errors import GeminiConfigurationError, GeminiResponseError
from gemini.extraction import (
    GoogleGeminiTextClient,
    GEMINI_REQUEST_TIMEOUT_MS,
    GEMINI_RETRY_ATTEMPTS,
    GEMINI_RETRY_STATUS_CODES,
    extract_understanding,
)


class FakeGeminiClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response

    def generate(self, prompt: str) -> str:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


EXTRACTION_JSON = (
    '{"event_type":"flood","locations":["Vellore"],"duration_text":"5 days",'
    '"transport_mode":"road","route_hints":["Chennai-Bengaluru"],'
    '"entity_hints":[],"uncertainties":[]}'
)


class ScriptedGeminiClient:
    """Real Gemini SDK client backed by a scripted httpx transport."""

    def __init__(self, statuses: list[int]) -> None:
        self.calls = 0
        self.statuses = statuses
        transport = httpx.MockTransport(self._handler)
        self.client = GoogleGeminiTextClient(
            "test-api-key",
            http_options={
                "httpx_client": httpx.Client(transport=transport),
                "retry_options": {
                    "attempts": GEMINI_RETRY_ATTEMPTS,
                    "initial_delay": 0.01,
                    "max_delay": 0.03,
                    "exp_base": 2,
                    "jitter": 0.01,
                    "http_status_codes": list(GEMINI_RETRY_STATUS_CODES),
                },
            },
        )

    def _handler(self, request: httpx.Request) -> httpx.Response:
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        if status == 200:
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": EXTRACTION_JSON}]}}]},
                request=request,
            )
        return httpx.Response(
            status,
            json={"error": {"code": status, "message": "simulated failure"}},
            request=request,
        )


class StalledGeminiClient:
    """Real Gemini SDK client whose upstream request stalls and never responds."""

    def __init__(self) -> None:
        self.calls = 0
        transport = httpx.MockTransport(self._handler)
        self.client = GoogleGeminiTextClient(
            "test-api-key",
            http_options={
                "httpx_client": httpx.Client(transport=transport),
                "timeout": 100,
                "retry_options": {
                    "attempts": GEMINI_RETRY_ATTEMPTS,
                    "initial_delay": 0.01,
                    "max_delay": 0.03,
                    "exp_base": 2,
                    "jitter": 0.01,
                    "http_status_codes": list(GEMINI_RETRY_STATUS_CODES),
                },
            },
        )

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise httpx.ReadTimeout("upstream stalled", request=request)


class GeminiExtractionTests(unittest.TestCase):
    def test_successful_extraction_is_structured(self) -> None:
        client = FakeGeminiClient(
            '{"event_type":"flood","locations":["Vellore"],"duration_text":"5 days",'
            '"transport_mode":"road","route_hints":["Chennai-Bengaluru"],'
            '"entity_hints":[],"uncertainties":[]}'
        )
        result = extract_understanding("Messy flood notice", client)
        self.assertEqual(result.event_type, "flood")
        self.assertEqual(result.locations, ["Vellore"])
        self.assertEqual(result.duration_text, "5 days")

    def test_missing_information_remains_empty(self) -> None:
        client = FakeGeminiClient('{"event_type":"storm","locations":[],"route_hints":[],"entity_hints":[],"uncertainties":[]}')
        result = extract_understanding("A storm was reported.", client)
        self.assertIsNone(result.duration_text)
        self.assertEqual(result.locations, [])

    def test_ambiguous_notice_preserves_uncertainty(self) -> None:
        client = FakeGeminiClient(
            '{"event_type":"flood","locations":["near Vellore"],"route_hints":[],"entity_hints":[],'
            '"uncertainties":["Exact affected road is unclear"]}'
        )
        result = extract_understanding("Flooding may be near Vellore.", client)
        self.assertEqual(result.uncertainties, ["Exact affected road is unclear"])

    def test_malformed_output_is_rejected(self) -> None:
        with self.assertRaises(GeminiResponseError):
            extract_understanding("Flooding.", FakeGeminiClient("not-json"))

    def test_invalid_structured_output_is_rejected(self) -> None:
        with self.assertRaises(GeminiResponseError):
            extract_understanding("Flooding.", FakeGeminiClient('{"event_type": 12, "unexpected": true}'))

    def test_gemini_failure_is_controlled(self) -> None:
        with self.assertRaises(GeminiResponseError):
            extract_understanding("Flooding.", FakeGeminiClient(RuntimeError("network down")))

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key_is_controlled(self) -> None:
        with self.assertRaises(GeminiConfigurationError):
            extract_understanding("Flooding.")


class GeminiRetryTests(unittest.TestCase):
    def test_successful_request_still_succeeds(self) -> None:
        harness = ScriptedGeminiClient([200])
        result = extract_understanding("Heavy flooding near Vellore.", harness.client)
        self.assertEqual(result.event_type, "flood")
        self.assertEqual(result.locations, ["Vellore"])
        self.assertEqual(harness.calls, 1)

    def test_transient_503_is_retried_and_succeeds(self) -> None:
        harness = ScriptedGeminiClient([503, 200])
        result = extract_understanding("Heavy flooding near Vellore.", harness.client)
        self.assertEqual(result.event_type, "flood")
        self.assertEqual(result.locations, ["Vellore"])
        self.assertEqual(harness.calls, 2)

    def test_persistent_transient_failures_raise_after_bounded_retries(self) -> None:
        harness = ScriptedGeminiClient([503, 503, 503, 503])
        with self.assertRaises(GeminiResponseError):
            extract_understanding("Heavy flooding near Vellore.", harness.client)
        self.assertEqual(harness.calls, GEMINI_RETRY_ATTEMPTS)

    def test_client_error_is_not_retried(self) -> None:
        harness = ScriptedGeminiClient([401, 200])
        with self.assertRaises(GeminiResponseError):
            extract_understanding("Heavy flooding near Vellore.", harness.client)
        self.assertEqual(harness.calls, 1)

    def test_retry_policy_is_small_bounded_and_transient_only(self) -> None:
        self.assertLessEqual(GEMINI_RETRY_ATTEMPTS, 4)
        self.assertGreaterEqual(GEMINI_RETRY_ATTEMPTS, 1)
        self.assertTrue({500, 502, 503, 504}.issubset(set(GEMINI_RETRY_STATUS_CODES)))
        self.assertNotIn(400, GEMINI_RETRY_STATUS_CODES)
        self.assertNotIn(401, GEMINI_RETRY_STATUS_CODES)
        self.assertNotIn(403, GEMINI_RETRY_STATUS_CODES)


class GeminiTimeoutTests(unittest.TestCase):
    def test_timeout_constant_is_finite_and_demo_safe(self) -> None:
        self.assertIsInstance(GEMINI_REQUEST_TIMEOUT_MS, int)
        self.assertGreater(GEMINI_REQUEST_TIMEOUT_MS, 0)
        self.assertLessEqual(GEMINI_REQUEST_TIMEOUT_MS, 60_000)

    def test_default_request_timeout_is_wired_into_the_gemini_client(self) -> None:
        harness = ScriptedGeminiClient([200])
        resolved = harness.client._client._api_client._http_options
        self.assertEqual(resolved.timeout, GEMINI_REQUEST_TIMEOUT_MS)

    def test_stalled_request_becomes_controlled_timeout_error(self) -> None:
        harness = StalledGeminiClient()
        with self.assertRaises(GeminiResponseError) as raised:
            extract_understanding("Heavy flooding near Vellore.", harness.client)
        self.assertIn("timed out", str(raised.exception))

    def test_timeout_is_not_treated_as_operational_no_impact(self) -> None:
        harness = StalledGeminiClient()
        try:
            extract_understanding("Heavy flooding near Vellore.", harness.client)
        except GeminiResponseError:
            pass
        else:
            self.fail("stalled upstream must be a failure, not a no-impact result")
        self.assertEqual(harness.calls, GEMINI_RETRY_ATTEMPTS)

    def test_timeout_still_uses_bounded_retries(self) -> None:
        harness = StalledGeminiClient()
        with self.assertRaises(GeminiResponseError):
            extract_understanding("Heavy flooding near Vellore.", harness.client)
        self.assertEqual(harness.calls, GEMINI_RETRY_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()