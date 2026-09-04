"""Focused tests for deterministic action-plan recommendations."""

import unittest
from dataclasses import replace
from datetime import date

from analysis.impact import analyze_impact
from analysis.prioritization import prioritize_orders
from analysis.recommendations import build_action_plan
from data.sample_data import SAMPLE_DATA
from gemini.models import DisruptionUnderstanding
from matching.engine import match_understanding


class RecommendationTests(unittest.TestCase):
    def route_analysis(self, data=SAMPLE_DATA):
        matching = match_understanding("DIS-ACTION", DisruptionUnderstanding(locations=["Vellore"]), data)
        impact = analyze_impact("DIS-ACTION", matching, data)
        priorities = prioritize_orders(date(2026, 9, 4), impact, data)
        return impact, priorities

    def test_shortage_case_recommends_order_review_and_inventory_review(self) -> None:
        data = replace(SAMPLE_DATA, inventory=(replace(SAMPLE_DATA.inventory[0], quantity=10),) + SAMPLE_DATA.inventory[1:])
        impact, priorities = self.route_analysis(data)
        plan = build_action_plan(impact, priorities)
        self.assertEqual(plan.recommended_option_id, "prioritize-order-review")
        self.assertIn("review-inventory-availability", {option.option_id for option in plan.options})
        self.assertTrue(plan.evidence)

    def test_sufficient_inventory_does_not_offer_shortage_review(self) -> None:
        impact, priorities = self.route_analysis()
        plan = build_action_plan(impact, priorities)
        self.assertNotIn("review-inventory-availability", {option.option_id for option in plan.options})

    def test_no_impact_does_not_invent_action(self) -> None:
        matching = match_understanding("DIS-ACTION", DisruptionUnderstanding(locations=["Kandla"]), SAMPLE_DATA)
        impact = analyze_impact("DIS-ACTION", matching, SAMPLE_DATA)
        priorities = prioritize_orders(date(2026, 9, 4), impact, SAMPLE_DATA)
        plan = build_action_plan(impact, priorities)
        self.assertEqual(plan.overall_state, "no_impact")
        self.assertEqual(plan.options, [])
        self.assertIsNone(plan.recommended_option_id)

    def test_missing_inventory_requires_additional_information(self) -> None:
        data = replace(SAMPLE_DATA, inventory=())
        impact, priorities = self.route_analysis(data)
        plan = build_action_plan(impact, priorities)
        order_review = next(option for option in plan.options if option.option_id == "prioritize-order-review")
        self.assertTrue(any("inventory" in prerequisite.lower() for prerequisite in order_review.prerequisites))

    def test_ambiguous_match_requires_review(self) -> None:
        duplicate = SAMPLE_DATA.suppliers[0].__class__("SUP-006", "Limited Components", "Chennai", "normal")
        data = replace(SAMPLE_DATA, suppliers=SAMPLE_DATA.suppliers + (duplicate,))
        matching = match_understanding("DIS-ACTION", DisruptionUnderstanding(entity_hints=["Limited Components"]), data)
        impact = analyze_impact("DIS-ACTION", matching, data)
        priorities = prioritize_orders(date(2026, 9, 4), impact, data)
        plan = build_action_plan(impact, priorities)
        self.assertEqual(plan.overall_state, "review_required")
        self.assertIn("review", plan.operator_decision_required.lower())

    def test_completed_orders_do_not_create_recommendation(self) -> None:
        data = replace(
            SAMPLE_DATA,
            orders=tuple(replace(order, status="completed") for order in SAMPLE_DATA.orders),
        )
        impact, priorities = self.route_analysis(data)
        plan = build_action_plan(impact, priorities)
        self.assertEqual(plan.overall_state, "no_impact")
        self.assertEqual(plan.options, [])

    def test_multiple_affected_orders_are_included_in_review_option(self) -> None:
        impact, priorities = self.route_analysis()
        plan = build_action_plan(impact, priorities)
        review_option = next(option for option in plan.options if option.option_id == "prioritize-order-review")
        self.assertEqual(review_option.affected_order_ids, [order.order_id for order in priorities.orders])

    def test_options_have_tradeoffs_risks_and_evidence(self) -> None:
        impact, priorities = self.route_analysis()
        plan = build_action_plan(impact, priorities)
        for option in plan.options:
            self.assertTrue(option.trade_offs)
            self.assertTrue(option.risks)
            self.assertTrue(option.prerequisites)
            self.assertTrue(option.evidence)

    def test_recommendation_is_deterministic(self) -> None:
        first = build_action_plan(*self.route_analysis()).model_dump()
        second = build_action_plan(*self.route_analysis()).model_dump()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()