"""Focused tests for the PS08 sample data foundation."""

import unittest

from services.data_loader import load_sample_data


class DataFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_sample_data()
        cls.suppliers = {supplier.id: supplier for supplier in cls.data.suppliers}
        cls.shipments = {shipment.id: shipment for shipment in cls.data.shipments}
        cls.containers = {container.id: container for container in cls.data.containers}
        cls.routes = {route.id: route for route in cls.data.routes}
        cls.skus = {sku.id: sku for sku in cls.data.skus}
        cls.inventory = {item.sku_id: item for item in cls.data.inventory}
        cls.customers = {customer.id: customer for customer in cls.data.customers}

    def test_all_sample_data_loads(self) -> None:
        self.assertEqual(len(self.data.suppliers), 5)
        self.assertEqual(len(self.data.shipments), 8)
        self.assertEqual(len(self.data.orders), 16)

    def test_supplier_relationships_are_valid(self) -> None:
        for shipment in self.data.shipments:
            self.assertIn(shipment.supplier_id, self.suppliers)

    def test_shipment_relationships_are_valid(self) -> None:
        for shipment in self.data.shipments:
            self.assertIn(shipment.container_id, self.containers)
            self.assertIn(shipment.route_id, self.routes)

    def test_order_relationships_are_valid(self) -> None:
        for order in self.data.orders:
            self.assertIn(order.sku_id, self.skus)
            self.assertIn(order.customer_id, self.customers)

    def test_limited_components_exists(self) -> None:
        self.assertIn("Limited Components", {supplier.name for supplier in self.data.suppliers})

    def test_chennai_vellore_bengaluru_route_exists(self) -> None:
        self.assertIn(("Chennai", "Vellore", "Bengaluru"), [route.waypoints for route in self.data.routes])

    def test_route_connects_to_shipment_and_container(self) -> None:
        route = next(route for route in self.data.routes if route.waypoints == ("Chennai", "Vellore", "Bengaluru"))
        shipment = next(shipment for shipment in self.data.shipments if shipment.route_id == route.id)
        self.assertIn(shipment.container_id, self.containers)

    def test_shipment_connects_to_sku_and_inventory(self) -> None:
        route = next(route for route in self.data.routes if "Vellore" in route.waypoints)
        shipment = next(shipment for shipment in self.data.shipments if shipment.route_id == route.id)
        self.assertIn(shipment.sku_id, self.inventory)

    def test_sku_has_connected_order(self) -> None:
        route = next(route for route in self.data.routes if "Vellore" in route.waypoints)
        shipment = next(shipment for shipment in self.data.shipments if shipment.route_id == route.id)
        self.assertTrue(any(order.sku_id == shipment.sku_id for order in self.data.orders))


if __name__ == "__main__":
    unittest.main()