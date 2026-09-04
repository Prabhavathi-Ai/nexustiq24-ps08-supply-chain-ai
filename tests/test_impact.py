"""Focused tests for deterministic Phase 5 impact traversal."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from analysis.impact import analyze_impact
from data.sample_data import SAMPLE_DATA
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding
from matching.models import MatchCandidate, MatchingResponse


class ImpactAnalysisTests(unittest.TestCase):
    def route_match(self) -> MatchingResponse:
        return match_understanding(
            "DIS-IMPACT",
            DisruptionUnderstanding(locations=["Vellore"]),
            SAMPLE_DATA,
        )

    def test_route_reaches_shipments_containers_skus_inventory_orders_customers(self) -> None:
        result = analyze_impact("DIS-IMPACT", self.route_match(), SAMPLE_DATA)
        downstream = {(item.entity_type, item.entity_id) for item in result.downstream_potential_impact}
        self.assertIn(("shipment", "SHP-001"), downstream)
        self.assertIn(("container", "CNT-1042"), downstream)
        self.assertIn(("sku", "SKU-001"), downstream)
        self.assertIn(("inventory", "SKU-001@Bengaluru DC"), downstream)
        self.assertIn(("order", "ORD-001"), downstream)
        self.assertIn(("customer", "CUS-001"), downstream)
        self.assertEqual(result.direct_impact[0].entity_id, "R-001")

    def test_direct_shipment_match_is_direct(self) -> None:
        understanding = DisruptionUnderstanding(entity_hints=["SHP-001"])
        matching = MatchingResponse(
            disruption_id="DIS-IMPACT",
            match_status="matched",
            impact_status="not_calculated",
            understanding=understanding,
            shipments=[MatchCandidate(
                entity_type="shipment", entity_id="SHP-001", entity_name="SHP-001",
                match_reason="Shipment identifier explicitly matched.", matched_field="id",
                source_fact="SHP-001", category="explicit_identifier",
            )],
        )
        result = analyze_impact("DIS-IMPACT", matching, SAMPLE_DATA)
        self.assertEqual(result.direct_impact[0].entity_id, "SHP-001")

    def test_direct_sku_match_reaches_inventory_and_orders(self) -> None:
        matching = MatchingResponse(
            disruption_id="DIS-IMPACT",
            match_status="matched",
            impact_status="not_calculated",
            understanding=DisruptionUnderstanding(entity_hints=["SKU-001"]),
            skus=[MatchCandidate(
                entity_type="sku", entity_id="SKU-001", entity_name="Control Module A",
                match_reason="SKU identifier explicitly matched.", matched_field="name",
                source_fact="SKU-001", category="explicit_identifier",
            )],
        )
        result = analyze_impact("DIS-IMPACT", matching, SAMPLE_DATA)
        downstream = {(item.entity_type, item.entity_id) for item in result.downstream_potential_impact}
        self.assertIn(("inventory", "SKU-001@Bengaluru DC"), downstream)
        self.assertIn(("order", "ORD-002"), downstream)

    def test_ambiguous_match_requires_review(self) -> None:
        duplicate = SAMPLE_DATA.suppliers[0].__class__("SUP-006", "Limited Components", "Chennai", "normal")
        data = replace(SAMPLE_DATA, suppliers=SAMPLE_DATA.suppliers + (duplicate,))
        matching = match_understanding("DIS-IMPACT", DisruptionUnderstanding(entity_hints=["Limited Components"]), data)
        result = analyze_impact("DIS-IMPACT", matching, data)
        self.assertEqual(result.impact_state, "review_required")
        self.assertIn("Multiple possible matches", " ".join(result.warnings))

    def test_no_match_has_no_fabricated_impact(self) -> None:
        matching = match_understanding("DIS-IMPACT", DisruptionUnderstanding(locations=["Kandla"]), SAMPLE_DATA)
        result = analyze_impact("DIS-IMPACT", matching, SAMPLE_DATA)
        self.assertEqual(result.impact_state, "no_impact")
        self.assertEqual(result.direct_impact, [])
        self.assertEqual(result.downstream_potential_impact, [])

    def test_missing_inventory_relationship_is_insufficient(self) -> None:
        data = replace(SAMPLE_DATA, inventory=())
        result = analyze_impact("DIS-IMPACT", self.route_match(), data)
        self.assertEqual(result.impact_state, "insufficient_information")
        self.assertTrue(any(item.entity_type == "inventory" for item in result.insufficient_information))

    def test_multiple_orders_and_customers_are_traversed(self) -> None:
        result = analyze_impact("DIS-IMPACT", self.route_match(), SAMPLE_DATA)
        orders = {item.entity_id for item in result.downstream_potential_impact if item.entity_type == "order"}
        customers = {item.entity_id for item in result.downstream_potential_impact if item.entity_type == "customer"}
        self.assertEqual(orders, {"ORD-001", "ORD-002", "ORD-003"})
        self.assertEqual(customers, {"CUS-001", "CUS-002", "CUS-003"})

    def test_every_impact_record_has_evidence(self) -> None:
        result = analyze_impact("DIS-IMPACT", self.route_match(), SAMPLE_DATA)
        for item in [*result.direct_impact, *result.downstream_potential_impact, *result.insufficient_information]:
            self.assertTrue(item.reason)
            self.assertTrue(item.source_record)
            self.assertTrue(item.supporting_fact)

    def test_repeated_analysis_is_deterministic(self) -> None:
        first = analyze_impact("DIS-IMPACT", self.route_match(), SAMPLE_DATA).model_dump()
        second = analyze_impact("DIS-IMPACT", self.route_match(), SAMPLE_DATA).model_dump()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()