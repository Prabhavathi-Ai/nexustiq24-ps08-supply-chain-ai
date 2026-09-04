"""Deterministic loading and relationship validation for sample data."""

from collections.abc import Iterable

from data.sample_data import SAMPLE_DATA
from models import SupplyChainData


def _index(records: Iterable[object], entity_name: str) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for record in records:
        record_id = getattr(record, "id", None)
        if record_id is None:
            raise ValueError(f"{entity_name} record has no id")
        if record_id in indexed:
            raise ValueError(f"Duplicate {entity_name} id: {record_id}")
        indexed[record_id] = record
    return indexed


def _require(reference_id: str, records: dict[str, object], relationship: str) -> None:
    if reference_id not in records:
        raise ValueError(f"Unknown {relationship}: {reference_id}")


def validate_data(data: SupplyChainData) -> None:
    """Raise ValueError when a cross-entity reference is invalid."""

    suppliers = _index(data.suppliers, "supplier")
    shipments = _index(data.shipments, "shipment")
    containers = _index(data.containers, "container")
    routes = _index(data.routes, "route")
    skus = _index(data.skus, "SKU")
    customers = _index(data.customers, "customer")

    for shipment in data.shipments:
        _require(shipment.supplier_id, suppliers, "shipment supplier")
        _require(shipment.container_id, containers, "shipment container")
        _require(shipment.route_id, routes, "shipment route")
        _require(shipment.sku_id, skus, "shipment SKU")

    for container in data.containers:
        _require(container.shipment_id, shipments, "container shipment")
        shipment = shipments[container.shipment_id]
        if shipment.container_id != container.id:
            raise ValueError(f"Container {container.id} does not match shipment {container.shipment_id}")

    for inventory in data.inventory:
        _require(inventory.sku_id, skus, "inventory SKU")

    for order in data.orders:
        _require(order.sku_id, skus, "order SKU")
        _require(order.customer_id, customers, "order customer")


def load_sample_data() -> SupplyChainData:
    """Load and validate the committed deterministic sample records."""

    validate_data(SAMPLE_DATA)
    return SAMPLE_DATA