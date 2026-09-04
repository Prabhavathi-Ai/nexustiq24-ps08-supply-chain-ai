"""Phase 10 end-to-end evaluator scenarios using mocked Gemini understanding."""

import unittest
from unittest.mock import patch

from analysis.impact import analyze_impact
from analysis.prioritization import prioritize_orders
from analysis.recommendations import build_action_plan
from api.disruptions import (
    DisruptionNoticeRequest,
    clear_disruptions,
    create_disruption,
    match_disruption,
    prioritize_disruption_orders,
    recommend_disruption_actions,
    analyze_disruption_impact,
    understand_disruption,
)
from gemini.models import DisruptionUnderstanding


class DemoFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_disruptions()

    def test_affected_demo_completes_full_flow_with_evidence(self) -> None:
        notice = create_disruption(DisruptionNoticeRequest(
            description="Heavy flooding has affected transport routes near Vellore.",
        ))
        understanding = DisruptionUnderstanding(
            event_type="flood",
            locations=["Vellore"],
            duration_text="five days",
            transport_mode="road",
        )
        with patch("api.disruptions.extract_understanding", return_value=understanding):
            understood = understand_disruption(notice.disruption_id)
        matches = match_disruption(notice.disruption_id)
        impact = analyze_disruption_impact(notice.disruption_id)
        priorities = prioritize_disruption_orders(notice.disruption_id)
        plan = recommend_disruption_actions(notice.disruption_id)

        self.assertEqual(understood.understanding.locations, ["Vellore"])
        self.assertEqual(matches.match_status, "matched")
        self.assertEqual(impact.impact_state, "impact_identified")
        self.assertTrue(priorities.orders)
        self.assertEqual(plan.overall_state, "recommendation_available")
        self.assertTrue(impact.evidence_references)
        self.assertTrue(priorities.orders[0].evidence_references)
        self.assertTrue(plan.evidence_references)

    def test_no_impact_demo_has_no_fabricated_downstream_effects(self) -> None:
        notice = create_disruption(DisruptionNoticeRequest(
            description="Severe flooding has been reported near Kandla.",
        ))
        understanding = DisruptionUnderstanding(event_type="flood", locations=["Kandla"])
        with patch("api.disruptions.extract_understanding", return_value=understanding):
            understand_disruption(notice.disruption_id)
        matches = match_disruption(notice.disruption_id)
        impact = analyze_disruption_impact(notice.disruption_id)
        priorities = prioritize_disruption_orders(notice.disruption_id)
        plan = recommend_disruption_actions(notice.disruption_id)

        self.assertEqual(matches.match_status, "no_match")
        self.assertEqual(impact.impact_state, "no_impact")
        self.assertEqual(impact.direct_impact, [])
        self.assertEqual(impact.downstream_potential_impact, [])
        self.assertEqual(priorities.overall_state, "no_affected_orders")
        self.assertEqual(priorities.orders, [])
        self.assertEqual(plan.overall_state, "no_impact")
        self.assertEqual(plan.options, [])
        self.assertIsNone(plan.recommended_option_id)


if __name__ == "__main__":
    unittest.main()