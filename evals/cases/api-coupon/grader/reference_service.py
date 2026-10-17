"""Shopping cart HTTP API under test. Run with: python service.py --port 8000"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CATALOG = {"BOOK": 4500, "PEN": 500, "MUG": 900, "CLIP": 15, "LAMP": 12000}
MAX_QUANTITY = 99
COUPON_CODE = "SAVE10"
COUPON_THRESHOLD = 10000
FREE_SHIPPING_FROM = 9900
SHIPPING_FEE = 800


class CartHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None

    def totals(self):
        cart = self.server.cart
        subtotal = sum(CATALOG[sku] * quantity for sku, quantity in cart["items"].items())
        discount = subtotal // 10 if cart["coupon"] else 0
        payable = subtotal - discount
        shipping = SHIPPING_FEE if 0 < payable < FREE_SHIPPING_FROM else 0
        return {
            "items": [{"sku": sku, "qty": qty} for sku, qty in sorted(cart["items"].items())],
            "subtotal": subtotal,
            "discount": discount,
            "shipping": shipping,
            "total": payable + shipping,
            "coupon": cart["coupon"],
        }

    def do_GET(self):
        if self.path == "/cart":
            self.respond(200, self.totals())
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self):
        body = self.read_body()
        if not isinstance(body, dict):
            self.respond(400, {"error": "invalid json"})
            return
        cart = self.server.cart
        if self.path == "/cart/items":
            sku, quantity = body.get("sku"), body.get("qty")
            if type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY:
                self.respond(400, {"error": "qty must be an integer from 1 to 99"})
            elif sku not in CATALOG:
                self.respond(404, {"error": "unknown sku"})
            elif cart["items"].get(sku, 0) + quantity > MAX_QUANTITY:
                self.respond(400, {"error": "qty must be an integer from 1 to 99"})
            else:
                cart["items"][sku] = cart["items"].get(sku, 0) + quantity
                self.respond(200, self.totals())
        elif self.path == "/cart/coupon":
            subtotal = self.totals()["subtotal"]
            if body.get("code") != COUPON_CODE:
                self.respond(404, {"error": "unknown coupon"})
            elif cart["coupon"]:
                self.respond(409, {"error": "coupon already applied"})
            elif subtotal < COUPON_THRESHOLD:
                self.respond(422, {"error": "subtotal below coupon threshold"})
            else:
                cart["coupon"] = COUPON_CODE
                self.respond(200, self.totals())
        elif self.path == "/checkout":
            if not cart["items"]:
                self.respond(400, {"error": "cart is empty"})
                return
            self.server.orders.append(self.totals())
            order = {"order_id": len(self.server.orders), **self.server.orders[-1]}
            cart["items"], cart["coupon"] = {}, None
            self.respond(201, order)
        else:
            self.respond(404, {"error": "not found"})


def create_server(host="127.0.0.1", port=0):
    server = ThreadingHTTPServer((host, port), CartHandler)
    server.cart = {"items": {}, "coupon": None}
    server.orders = []
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    create_server(port=parser.parse_args().port).serve_forever()
