"""Focused tests for deterministic entity matching."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from api.disruptions import (
    DisruptionNoticeRequest,
    clear_disruptions,
    create_disruption,
    match_disruption,
    understand_disruption,
)
from data.sample_data import SAMPLE_DATA
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding, normalize_match_text


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_disruptions()

    def test_exact_supplier_match(self) -> None:
        result = match_understanding("DIS-TEST", DisruptionUnderstanding(entity_hints=["Limited Components"]), SAMPLE_DATA)
        self.assertEqual([candidate.entity_id for candidate in result.suppliers], ["SUP-001"])
        self.assertEqual(result.match_status, "matched")

    def test_case_and_punctuation_normalization(self) -> None:
        self.assertEqual(normalize_match_text(" LIMITED-Components "), "limited components")
        result = match_understanding("DIS-TEST", DisruptionUnderstanding(entity_hints=["limited-components"]), SAMPLE_DATA)
        self.assertEqual([candidate.entity_id for candidate in result.suppliers], ["SUP-001"])

    def test_location_matches_route(self) -> None:
        result = match_understanding("DIS-TEST", DisruptionUnderstanding(locations=["Vellore"]), SAMPLE_DATA)
        self.assertEqual([candidate.entity_id for candidate in result.routes], ["R-001"])
        self.assertTrue(any(candidate.entity_id == "SHP-001" for candidate in result.shipments))

    def test_route_relationship_exposes_container_and_sku(self) -> None:
        result = match_understanding("DIS-TEST", DisruptionUnderstanding(locations=["Vellore"]), SAMPLE_DATA)
        self.assertEqual([candidate.entity_id for candidate in result.containers], ["CNT-1042"])
        self.assertEqual([candidate.entity_id for candidate in result.skus], ["SKU-001"])

    def test_unrelated_location_returns_no_match(self) -> None:
        result = match_understanding("DIS-TEST", DisruptionUnderstanding(locations=["Kandla"]), SAMPLE_DATA)
        self.assertEqual(result.match_status, "no_match")
        self.assertEqual(result.impact_status, "no_matching_records")
        self.assertEqual(result.warnings, ["No matching supply-chain records found."])

    def test_ambiguous_supplier_matches_are_retained(self) -> None:
        data = replace(
            SAMPLE_DATA,
            suppliers=SAMPLE_DATA.suppliers + (SAMPLE_DATA.suppliers[0].__class__("SUP-006", "Limited Components", "Chennai", "normal"),),
        )
        result = match_understanding("DIS-TEST", DisruptionUnderstanding(entity_hints=["Limited Components"]), data)
        self.assertEqual(result.match_status, "ambiguous")
        self.assertEqual({candidate.entity_id for candidate in result.suppliers}, {"SUP-001", "SUP-006"})

    def test_every_match_has_evidence(self) -> None:
        result = match_understanding("DIS-TEST", DisruptionUnderstanding(locations=["Vellore"]), SAMPLE_DATA)
        for candidate in [*result.routes, *result.shipments, *result.containers, *result.skus]:
            self.assertTrue(candidate.match_reason)
            self.assertTrue(candidate.source_fact)

    def test_no_hallucinated_records_are_returned(self) -> None:
        result = match_understanding("DIS-TEST", DisruptionUnderstanding(entity_hints=["SUP-999", "SKU-999"]), SAMPLE_DATA)
        self.assertEqual(result.match_status, "no_match")

    def test_api_flow_stores_understanding_then_returns_matches(self) -> None:
        disruption = create_disruption(DisruptionNoticeRequest(description="Flooding around Vellore."))
        understood = DisruptionUnderstanding(locations=["Vellore"], event_type="flood")
        with patch("api.disruptions.extract_understanding", return_value=understood):
            understanding_response = understand_disruption(disruption.disruption_id)
        self.assertEqual(understanding_response.understanding.event_type, "flood")
        matches = match_disruption(disruption.disruption_id)
        self.assertEqual(matches.match_status, "matched")
        self.assertEqual(matches.routes[0].entity_id, "R-001")


if __name__ == "__main__":
    unittest.main()