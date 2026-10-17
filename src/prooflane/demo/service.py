"""Loopback-only checkout fixture with real HTTP, SQLite and correlated request logs."""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

from prooflane.artifacts import write_json
from prooflane.demo import payment_broken, payment_fixed

INITIAL_STOCK = 100
UNIT_PRICE_CENTS = 1250


@contextmanager
def database(path: Path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class CheckoutServer(HTTPServer):
    def __init__(self, directory: Path, revision: str):
        super().__init__(("127.0.0.1", 0), CheckoutHandler)
        self.directory = directory
        self.revision = revision
        self.payment = payment_broken if revision == "broken" else payment_fixed
        self.secret = os.environ["PROOFLANE_DEMO_SECRET"]
        self.sessions: set[str] = set()
        self.db_path = directory / "checkout.db"
        with database(self.db_path) as connection:
            connection.executescript("""
                CREATE TABLE orders (
                    id TEXT PRIMARY KEY, quantity INTEGER NOT NULL,
                    total INTEGER NOT NULL, status TEXT NOT NULL,
                    payment_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE inventory (stock INTEGER NOT NULL);
            """)
            connection.execute("INSERT INTO inventory VALUES (?)", (INITIAL_STOCK,))


class CheckoutHandler(BaseHTTPRequestHandler):
    server: CheckoutServer

    def log_message(self, format, *args):
        # Request logs below intentionally exclude cookies, signatures and passwords.
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def authenticated(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        session = cookie.get("session")
        return session is not None and session.value in self.server.sessions

    def handle_request(self):
        self.trace_id = self.headers.get("X-Trace-ID") or str(uuid4())
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 65536:
                self.respond(413, {"error": "request too large"})
                return
            body = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            self.dispatch(body)
        except (ValueError, json.JSONDecodeError):
            self.respond(400, {"error": "invalid request"})

    def dispatch(self, body: dict):
        path = self.path
        if self.command == "GET" and path == "/health":
            self.respond(200, {"ready": True, "revision": self.server.revision})
        elif self.command == "GET" and path == "/":
            html = Path(__file__).with_name("checkout.html").read_text(encoding="utf-8")
            self.respond(200, html, content_type="text/html; charset=utf-8")
        elif self.command == "POST" and path == "/login":
            if body.get("username") != "demo" or body.get("password") != "demo":
                self.respond(401, {"error": "invalid credentials"})
                return
            session = secrets.token_urlsafe(24)
            self.server.sessions.add(session)
            self.respond(
                200,
                {"user": "demo"},
                cookie=f"session={session}; HttpOnly; SameSite=Strict; Path=/",
            )
        elif path == "/callbacks/payment" and self.command == "POST":
            self.payment(body)
        elif not self.authenticated():
            self.respond(401, {"error": "login required"})
        elif self.command == "POST" and path == "/orders":
            self.create_order(body)
        elif self.command == "GET" and path.startswith("/orders/"):
            self.order(path.removeprefix("/orders/"))
        elif self.command == "POST" and path == "/simulate-payment":
            # This endpoint belongs only to the local demo, never to a real payment gateway.
            order_id = body.get("order_id", "")
            if not isinstance(order_id, str):
                raise ValueError("Invalid order identifier")
            signature = hmac.new(
                self.server.secret.encode(), order_id.encode(), hashlib.sha256
            ).hexdigest()
            self.payment({"order_id": order_id, "signature": signature})
        elif self.command == "POST" and path == "/simulate-timeout":
            with database(self.server.db_path) as connection:
                changed = connection.execute(
                    "UPDATE orders SET status = 'pending_confirmation' WHERE id = ? AND payment_count = 0",
                    (body.get("order_id"),),
                ).rowcount
            self.respond(504 if changed else 404, {"error": "simulated gateway timeout"})
        else:
            self.respond(404, {"error": "not found"})

    def create_order(self, body: dict):
        quantity = body.get("quantity")
        if type(quantity) is not int or quantity <= 0:
            self.respond(400, {"error": "quantity must be a positive integer"})
            return
        with database(self.server.db_path) as connection:
            if connection.execute("SELECT stock FROM inventory").fetchone()[0] < quantity:
                self.respond(409, {"error": "insufficient stock"})
                return
            order_id = str(uuid4())
            connection.execute("UPDATE inventory SET stock = stock - ?", (quantity,))
            connection.execute(
                "INSERT INTO orders(id, quantity, total, status) VALUES (?, ?, ?, 'pending')",
                (order_id, quantity, quantity * UNIT_PRICE_CENTS),
            )
        self.respond(201, {"id": order_id, "total": quantity * UNIT_PRICE_CENTS})

    def order(self, order_id: str):
        with database(self.server.db_path) as connection:
            row = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        self.respond(200, dict(row)) if row else self.respond(404, {"error": "unknown order"})

    def payment(self, body: dict):
        order_id = body.get("order_id", "")
        signature = body.get("signature", "")
        if not isinstance(order_id, str) or not isinstance(signature, str):
            raise ValueError("Invalid callback")
        expected = hmac.new(
            self.server.secret.encode(), order_id.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            self.respond(403, {"error": "invalid signature"})
            return
        with database(self.server.db_path) as connection:
            if (
                connection.execute("SELECT id FROM orders WHERE id = ?", (order_id,)).fetchone()
                is None
            ):
                self.respond(404, {"error": "unknown order"})
                return
            self.server.payment.apply_payment(connection, order_id)
        self.respond(200, {"accepted": True, "order_id": order_id})

    def respond(self, status: int, body, *, content_type="application/json", cookie=None):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Trace-ID", self.trace_id)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": self.trace_id,
            "method": self.command,
            "path": self.path,
            "status": status,
        }
        with (self.server.directory / "service.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--revision", choices=["broken", "fixed"], required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    with CheckoutServer(args.directory, args.revision) as server:
        ready = args.directory / "ready.tmp"
        write_json(ready, {"url": f"http://127.0.0.1:{server.server_port}", "pid": os.getpid()})
        ready.replace(args.directory / "ready.json")
        server.serve_forever(poll_interval=0.1)


if __name__ == "__main__":
    main()
