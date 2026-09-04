"""Phase 8 evidence, uncertainty, and no-impact regression checks."""

import unittest
from datetime import date
from unittest.mock import patch

from analysis.impact import analyze_impact
from analysis.prioritization import prioritize_orders
from analysis.recommendations import build_action_plan
from api.disruptions import clear_disruptions, create_disruption, understand_disruption
from data.sample_data import SAMPLE_DATA
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding


class TraceabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_disruptions()

    def test_direct_and_downstream_impact_have_references(self) -> None:
        matching = match_understanding("DIS-TRACE", DisruptionUnderstanding(locations=["Vellore"]), SAMPLE_DATA)
        impact = analyze_impact("DIS-TRACE", matching, SAMPLE_DATA)
        self.assertTrue(impact.direct_impact[0].evidence_references)
        self.assertTrue(impact.downstream_potential_impact[0].evidence_references)
        self.assertTrue(all(reference.source_stage == "impact" for reference in impact.evidence_references))

    def test_priority_references_order_facts(self) -> None:
        matching = match_understanding("DIS-TRACE", DisruptionUnderstanding(locations=["Vellore"]), SAMPLE_DATA)
        impact = analyze_impact("DIS-TRACE", matching, SAMPLE_DATA)
        priorities = prioritize_orders(date(2026, 9, 4), impact, SAMPLE_DATA)
        order = next(order for order in priorities.orders if order.order_id == "ORD-001")
        fields = {reference.field for reference in order.evidence_references}
        self.assertTrue({"required_date", "priority", "status"}.issubset(fields))

    def test_recommendation_references_priority_evidence(self) -> None:
        matching = match_understanding("DIS-TRACE", DisruptionUnderstanding(locations=["Vellore"]), SAMPLE_DATA)
        impact = analyze_impact("DIS-TRACE", matching, SAMPLE_DATA)
        priorities = prioritize_orders(date(2026, 9, 4), impact, SAMPLE_DATA)
        plan = build_action_plan(impact, priorities)
        self.assertTrue(plan.evidence_references)
        self.assertTrue(all(reference.source_stage in {"impact", "prioritization"} for reference in plan.evidence_references))

    def test_no_impact_has_no_orders_or_recommendation(self) -> None:
        matching = match_understanding("DIS-TRACE", DisruptionUnderstanding(locations=["Kandla"]), SAMPLE_DATA)
        impact = analyze_impact("DIS-TRACE", matching, SAMPLE_DATA)
        priorities = prioritize_orders(date(2026, 9, 4), impact, SAMPLE_DATA)
        plan = build_action_plan(impact, priorities)
        self.assertEqual(impact.impact_state, "no_impact")
        self.assertEqual(priorities.orders, [])
        self.assertEqual(plan.overall_state, "no_impact")
        self.assertEqual(plan.options, [])

    def test_missing_information_is_explicit(self) -> None:
        matching = match_understanding("DIS-TRACE", DisruptionUnderstanding(locations=["Vellore"]), SAMPLE_DATA)
        impact = analyze_impact("DIS-TRACE", matching, SAMPLE_DATA)
        self.assertTrue(all(item.reason for item in impact.insufficient_information)) if impact.insufficient_information else self.assertEqual(impact.insufficient_information, [])

    def test_understanding_preserves_source_stage_boundary(self) -> None:
        record = create_disruption(__import__("api.disruptions", fromlist=["DisruptionNoticeRequest"]).DisruptionNoticeRequest(description="Flooding near Vellore."))
        with patch("api.disruptions.extract_understanding", return_value=DisruptionUnderstanding(locations=["Vellore"])):
            response = understand_disruption(record.disruption_id)
        self.assertEqual(response.understanding.locations, ["Vellore"])


if __name__ == "__main__":
    unittest.main()