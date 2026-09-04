"""Tests for the isolated, mocked Gemini disruption understanding service."""

import unittest
from unittest.mock import patch

from gemini.errors import GeminiConfigurationError, GeminiResponseError
from gemini.extraction import extract_understanding


class FakeGeminiClient:
    def __init__(self, response: str | Exception) -> None:
        self.response = response

    def generate(self, prompt: str) -> str:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


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


if __name__ == "__main__":
    unittest.main()