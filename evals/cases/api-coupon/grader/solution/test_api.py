"""Reference answer used only to validate the grader; never copied into agent workspaces."""

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from service import create_server


class CartApiTests(unittest.TestCase):
    def setUp(self):
        server = create_server()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}"

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def fill(self, **quantities):
        for sku, qty in quantities.items():
            self.assertEqual(self.call("/cart/items", {"sku": sku, "qty": qty})[0], 200)

    def cart(self):
        return self.call("/cart")[1]

    def apply_coupon(self):
        return self.call("/cart/coupon", {"code": "SAVE10"})[0]

    def test_r1_quantity_must_be_integer_from_1_to_99(self):
        for qty in [0, 100, -1, 1.5, "2"]:
            self.assertEqual(self.call("/cart/items", {"sku": "BOOK", "qty": qty})[0], 400, qty)
        self.fill(BOOK=1, PEN=99)
        self.assertEqual(
            self.cart()["items"], [{"sku": "BOOK", "qty": 1}, {"sku": "PEN", "qty": 99}]
        )

    def test_r1_cumulative_quantity_limit_keeps_cart(self):
        self.fill(PEN=60)
        self.assertEqual(self.call("/cart/items", {"sku": "PEN", "qty": 40})[0], 400)
        self.assertEqual(self.cart()["items"], [{"sku": "PEN", "qty": 60}])

    def test_r2_unknown_sku(self):
        self.assertEqual(self.call("/cart/items", {"sku": "NOPE", "qty": 1})[0], 404)
        self.assertEqual(self.cart()["items"], [])

    def test_r3_coupon_threshold_includes_exact_amount(self):
        self.fill(BOOK=2, PEN=2)
        self.assertEqual(self.cart()["subtotal"], 10000)
        self.assertEqual(self.apply_coupon(), 200)

    def test_r3_coupon_below_threshold_unknown_and_reuse(self):
        self.fill(BOOK=2, PEN=1, CLIP=33)
        self.assertEqual(self.cart()["subtotal"], 9995)
        self.assertEqual(self.apply_coupon(), 422)
        self.assertEqual(self.call("/cart/coupon", {"code": "NOPE"})[0], 404)
        self.fill(CLIP=1)
        self.assertEqual(self.apply_coupon(), 200)
        self.assertEqual(self.apply_coupon(), 409)

    def test_r4_discount_rounds_down(self):
        self.fill(LAMP=1, CLIP=1)
        self.apply_coupon()
        cart = self.cart()
        self.assertEqual((cart["subtotal"], cart["discount"]), (12015, 1201))

    def test_r5_shipping_uses_payable_amount_and_boundary(self):
        self.assertEqual(self.cart()["shipping"], 0)
        self.fill(BOOK=2, MUG=1)
        self.assertEqual((self.cart()["shipping"], self.cart()["total"]), (0, 9900))
        self.fill(PEN=1)
        self.apply_coupon()
        cart = self.cart()
        self.assertEqual((cart["discount"], cart["shipping"], cart["total"]), (1040, 800, 10160))

    def test_r6_checkout_clears_cart_and_rejects_empty(self):
        self.assertEqual(self.call("/checkout", {})[0], 400)
        self.fill(BOOK=2, PEN=2)
        self.apply_coupon()
        status, order = self.call("/checkout", {})
        self.assertEqual((status, order["total"], order["order_id"]), (201, 9000 + 800, 1))
        self.assertEqual((self.cart()["items"], self.cart()["coupon"]), ([], None))
        self.assertEqual(self.call("/checkout", {})[0], 400)


if __name__ == "__main__":
    unittest.main()
