"""Deterministic what-if scenario comparison for supported response options.

Scenarios wrap the same action options the recommendations stage already built.
No value is invented: every metric is derived from committed records or from
results the prior deterministic investigation stages established. Simulating a
scenario never executes an action and never produces a decision record.
"""

from models import Inventory, Order, SupplyChainData

from .models import (
    ActionOption,
    ActionPlanResponse,
    EvidenceReference,
    ImpactResponse,
    PrioritizationResponse,
    PrioritizedOrder,
    ScenarioComparisonMetrics,
    ScenarioComparisonResponse,
    WhatIfScenario,
)


SIMULATION_NOTICE = "Simulation only \u2014 nothing executed."
ADVISORY_NOTICE = "Recommendation is advisory. Human decision required."
COMPARISON_NOTE = (
    "Scenario comparison is derived deterministically only from the committed dataset and the "
    "results of the prior investigation stages. Quantities, dates, costs, and effects are never "
    "invented, and nothing is executed."
)
NO_IMPACT_NOTE = (
    "No simulated scenario can be derived because no operational impact was established; "
    "simulating one would invent effects without an evidence base."
)
NO_OPTIONS_NOTE = (
    "No simulated scenario can be derived because no supported action options could be built "
    "from the available records."
)
SHIPMENT_SCOPE_OPTION_IDS = {"investigate-affected-shipment"}


def _impacted_shipment_ids(impact: ImpactResponse) -> list[str]:
    return sorted({
        record.entity_id
        for record in impact.direct_impact + impact.downstream_potential_impact
        if record.entity_type == "shipment"
    })


def _shortage_total(covered_orders: list[PrioritizedOrder]) -> tuple[int | None, bool]:
    total = 0
    for order in covered_orders:
        shortage = order.inventory_shortage
        if shortage is None:
            return None, True
        total += shortage.shortage_quantity
    return total, False


def _available_inventory(
    covered_sku_ids: list[str],
    inventory_by_sku: dict[str, Inventory],
) -> tuple[int | None, bool]:
    total = 0
    for sku_id in covered_sku_ids:
        record = inventory_by_sku.get(sku_id)
        if record is None:
            return None, True
        total += record.quantity
    return total, False


def _build_scenario(
    disruption_id: str,
    option: ActionOption,
    plan: ActionPlanResponse,
    priorities: PrioritizationResponse,
    impact: ImpactResponse,
    orders: dict[str, Order],
    inventory_by_sku: dict[str, Inventory],
    shipment_sku: dict[str, str],
    impacted_shipment_ids: list[str],
    total_customer_ids: list[str],
    total_high_priority_ids: list[str],
) -> WhatIfScenario:
    covered_orders = [
        order for order in priorities.orders
        if order.order_id in option.affected_order_ids
    ]
    covered_order_ids = sorted({order.order_id for order in covered_orders})
    covered_customer_ids = sorted({order.customer_id for order in covered_orders})
    covered_sku_ids = sorted({order.affected_sku for order in covered_orders})
    shortage_quantity, shortage_incomplete = _shortage_total(covered_orders)
    available_inventory, inventory_incomplete = _available_inventory(covered_sku_ids, inventory_by_sku)
    if option.option_id in SHIPMENT_SCOPE_OPTION_IDS:
        covered_shipment_ids = list(impacted_shipment_ids)
    else:
        covered_shipment_ids = sorted({
            shipment_id for shipment_id in impacted_shipment_ids
            if shipment_sku.get(shipment_id) in covered_sku_ids
        })
    covered_high_priority = len([
        order for order in covered_orders
        if orders.get(order.order_id) is not None
        and orders[order.order_id].priority.casefold() == "high"
    ])

    metrics = ScenarioComparisonMetrics(
        affected_orders_covered=len(covered_order_ids),
        affected_orders_total=len(priorities.orders),
        affected_customers_covered=len(covered_customer_ids),
        affected_customers_total=len(total_customer_ids),
        affected_shipments_covered=len(covered_shipment_ids),
        affected_shipments_total=len(impacted_shipment_ids),
        priority_orders_covered=covered_high_priority,
        priority_orders_total=len(total_high_priority_ids),
        order_quantity_covered=sum(
            orders[order.order_id].quantity
            for order in covered_orders
            if orders.get(order.order_id) is not None
        ),
        shortage_quantity_covered=shortage_quantity,
        available_inventory_for_covered_skus=available_inventory,
        shortage_incomplete=shortage_incomplete,
        inventory_incomplete=inventory_incomplete,
        covered_order_ids=covered_order_ids,
        covered_customer_ids=covered_customer_ids,
        covered_shipment_ids=covered_shipment_ids,
        covered_sku_ids=covered_sku_ids,
    )

    addresses = [option.suitability]
    if metrics.affected_orders_total > 0:
        rate = round(100 * metrics.affected_orders_covered / metrics.affected_orders_total)
        addresses.append(
            f"Would cover {metrics.affected_orders_covered} of {metrics.affected_orders_total} "
            f"affected order(s) ({rate}%)."
        )
    if metrics.affected_shipments_total > 0:
        addresses.append(
            f"Would bring {metrics.affected_shipments_covered} of "
            f"{metrics.affected_shipments_total} affected shipment(s) into the review context."
        )

    return WhatIfScenario(
        scenario_id=f"scenario:{disruption_id}:{option.option_id}",
        option_id=option.option_id,
        name=option.name,
        description=option.description,
        is_recommended=option.option_id == plan.recommended_option_id,
        addresses=addresses,
        does_not_address=list(option.risks),
        operational_trade_offs=list(option.trade_offs),
        prerequisites=list(option.prerequisites),
        metrics=metrics,
        evidence=list(option.evidence),
        evidence_references=list(option.evidence_references),
        execution_status="simulation_only",
        execution_notice=SIMULATION_NOTICE,
        advisory_notice=ADVISORY_NOTICE,
    )


def build_scenario_comparison(
    disruption_id: str,
    impact: ImpactResponse,
    priorities: PrioritizationResponse,
    plan: ActionPlanResponse,
    data: SupplyChainData,
) -> ScenarioComparisonResponse:
    """Build the deterministic scenario comparison for one investigation."""

    if impact.impact_state == "no_impact" or priorities.overall_state == "no_affected_orders":
        return ScenarioComparisonResponse(
            disruption_id=disruption_id,
            simulation_state="no_scenario_created",
            scenarios=[],
            comparison_note=NO_IMPACT_NOTE,
            warnings=[
                "No operational impact was established; simulating a what-if scenario would "
                "invent effects without an evidence base."
            ],
        )

    if not plan.options:
        return ScenarioComparisonResponse(
            disruption_id=disruption_id,
            simulation_state="insufficient_information",
            scenarios=[],
            comparison_note=NO_OPTIONS_NOTE,
            warnings=[
                "Additional operational information is required before a scenario comparison is meaningful."
            ],
        )

    orders = {order.id: order for order in data.orders}
    inventory_by_sku = {record.sku_id: record for record in data.inventory}
    shipment_sku = {shipment.id: shipment.sku_id for shipment in data.shipments}
    impacted_shipment_ids = _impacted_shipment_ids(impact)
    total_customer_ids = sorted({order.customer_id for order in priorities.orders})
    total_high_priority_ids = sorted({
        order.order_id
        for order in priorities.orders
        if orders.get(order.order_id) is not None
        and orders[order.order_id].priority.casefold() == "high"
    })

    scenarios = [
        _build_scenario(
            disruption_id,
            option,
            plan,
            priorities,
            impact,
            orders,
            inventory_by_sku,
            shipment_sku,
            impacted_shipment_ids,
            total_customer_ids,
            total_high_priority_ids,
        )
        for option in plan.options
    ]

    warnings = list(plan.warnings)
    if any(scenario.metrics.shortage_incomplete for scenario in scenarios):
        warnings.append(
            "Scenario shortage totals were left unspecified because some covered orders have "
            "no linked inventory record; values were not guessed."
        )
    if any(scenario.metrics.inventory_incomplete for scenario in scenarios):
        warnings.append(
            "Scenario inventory availability could not be fully calculated because some covered "
            "SKUs have no linked inventory record; totals were not guessed."
        )
    if len(scenarios) > 1:
        metric_dumps = [scenario.metrics.model_dump() for scenario in scenarios]
        if all(metric == metric_dumps[0] for metric in metric_dumps[1:]):
            warnings.append(
                "Scenario comparison currently produces no measurable difference between the "
                "supported options."
            )

    return ScenarioComparisonResponse(
        disruption_id=disruption_id,
        simulation_state="scenario_comparison_available",
        recommended_scenario_id=(
            f"scenario:{disruption_id}:{plan.recommended_option_id}"
            if plan.recommended_option_id
            else None
        ),
        scenarios=scenarios,
        comparison_note=COMPARISON_NOTE,
        evidence=list(plan.evidence),
        warnings=warnings,
    )
