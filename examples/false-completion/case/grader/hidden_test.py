"""Independent reference examples, overlaid only after the completion claim."""

import unittest

from shipping import shipping_fee


class ShippingAcceptance(unittest.TestCase):
    def check_fees(self, examples):
        # Expected amounts come from the specification, never from the submitted code.
        for subtotal, discount, expected in examples:
            with self.subTest(subtotal=subtotal, discount=discount):
                self.assertEqual(shipping_fee(subtotal, discount), expected)

    def test_ordinary_orders(self):
        self.check_fees([(100, 0, 500), (2000, 0, 500), (8000, 0, 0)])

    def test_free_shipping_boundary(self):
        self.check_fees([(4999, 0, 500), (5000, 0, 0), (5001, 0, 0)])

    def test_empty_order(self):
        self.check_fees([(0, 0, 0), (1500, 1500, 0), (6000, 6000, 0)])

    def test_discount_affects_shipping(self):
        self.check_fees([(5000, 1, 500), (6000, 1001, 500), (6000, 1000, 0), (9000, 500, 0)])

    def test_invalid_amounts(self):
        for subtotal, discount in [
            (-1, 0),
            (100, -1),
            (100, 101),
            (True, 0),
            (100, False),
            (100.0, 0),
            (100, 1.5),
            ("100", 0),
            (100, "1"),
            (None, 0),
        ]:
            with self.subTest(subtotal=subtotal, discount=discount):
                with self.assertRaises(ValueError):
                    shipping_fee(subtotal, discount)
