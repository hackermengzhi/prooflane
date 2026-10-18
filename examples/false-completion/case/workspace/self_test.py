"""A happy-path self-check that both deliverables can pass."""

import unittest

from shipping import shipping_fee


class ShippingSelfTest(unittest.TestCase):
    def test_ordinary_orders(self):
        self.assertEqual(shipping_fee(1000), 500)
        self.assertEqual(shipping_fee(10000), 0)
