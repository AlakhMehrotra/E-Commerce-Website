import unittest

import app


class ShippingPricingTests(unittest.TestCase):
    def test_shipping_is_free_for_prepaid_orders(self):
        self.assertEqual(app.compute_shipping_cost(50000, "upi"), 0)
        self.assertEqual(app.compute_shipping_cost(49999, "upi"), 0)

    def test_cod_has_only_handling_fee(self):
        self.assertEqual(app.compute_shipping_cost(50000, "cod"), 50)
        self.assertEqual(app.compute_shipping_cost(49999, "cod"), 50)


if __name__ == "__main__":
    unittest.main()
