"""Typed entities used by the deterministic PS08 sample data foundation."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Supplier:
    id: str
    name: str
    location: str
    status: str


@dataclass(frozen=True)
class Shipment:
    id: str
    supplier_id: str
    container_id: str
    sku_id: str
    origin: str
    destination: str
    route_id: str
    status: str
    planned_departure: date
    planned_arrival: date


@dataclass(frozen=True)
class Container:
    id: str
    shipment_id: str
    status: str


@dataclass(frozen=True)
class Route:
    id: str
    origin: str
    destination: str
    waypoints: tuple[str, ...]


@dataclass(frozen=True)
class SKU:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class Inventory:
    sku_id: str
    warehouse: str
    quantity: int
    daily_demand: int


@dataclass(frozen=True)
class Order:
    id: str
    customer_id: str
    sku_id: str
    quantity: int
    priority: str
    required_date: date
    status: str


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    priority_level: str


@dataclass(frozen=True)
class Disruption:
    id: str
    type: str
    location: str
    description: str
    reported_at: datetime
    expected_duration_days: int


@dataclass(frozen=True)
class SupplyChainData:
    suppliers: tuple[Supplier, ...]
    shipments: tuple[Shipment, ...]
    containers: tuple[Container, ...]
    routes: tuple[Route, ...]
    skus: tuple[SKU, ...]
    inventory: tuple[Inventory, ...]
    orders: tuple[Order, ...]
    customers: tuple[Customer, ...]
    disruptions: tuple[Disruption, ...]