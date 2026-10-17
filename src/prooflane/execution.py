"""Deterministic checkout scenarios and correlated HTTP/database/browser evidence."""

import hashlib
import hmac
import json
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from prooflane.artifacts import redact, write_json
from prooflane.demo.service import UNIT_PRICE_CENTS, database


class HttpClient:
    def __init__(self, url: str, directory: Path):
        self.url = url
        self.directory = directory
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.exchanges: list[dict] = []

    def request(self, method: str, path: str, body=None) -> tuple[int, dict]:
        trace_id = str(uuid4())
        headers = {"Content-Type": "application/json", "X-Trace-ID": trace_id}
        request = Request(
            self.url + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers=headers,
        )
        start = time.monotonic()
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            status = response.status
            result = json.loads(response.read())
            response_trace = response.headers.get("X-Trace-ID")
        self.exchanges.append(
            redact(
                {
                    "trace_id": trace_id,
                    "response_trace_id": response_trace,
                    "method": method,
                    "path": path,
                    "request": body,
                    "status": status,
                    "response": result,
                    "elapsed_ms": round((time.monotonic() - start) * 1000, 2),
                }
            )
        )
        write_json(self.directory / "http.json", self.exchanges)
        if response_trace != trace_id:
            raise RuntimeError("Request/response trace correlation failed")
        return status, result

    def login(self):
        status, _ = self.request("POST", "/login", {"username": "demo", "password": "demo"})
        expect(status, 200, "login response")

    def order(self, quantity=2, **extra) -> dict:
        status, result = self.request("POST", "/orders", {"quantity": quantity, **extra})
        expect(status, 201, "create order response")
        return result


def expect(actual, expected, label: str):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, observed {actual!r}")


def snapshot(db_path: Path) -> dict:
    with database(db_path) as connection:
        return {
            "stock": connection.execute("SELECT stock FROM inventory").fetchone()[0],
            "orders": [
                dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY rowid")
            ],
        }


def stored_order(db_path: Path, order_id: str) -> dict:
    with database(db_path) as connection:
        row = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise AssertionError("Created order is missing from the database")
    return dict(row)


def callback(client: HttpClient, order_id: str, secret: str):
    signature = hmac.new(secret.encode(), order_id.encode(), hashlib.sha256).hexdigest()
    status, result = client.request(
        "POST", "/callbacks/payment", {"order_id": order_id, "signature": signature}
    )
    expect(status, 200, "payment callback response")
    expect(result.get("accepted"), True, "payment acceptance")


def run_api_scenario(name: str, client: HttpClient, environment: dict) -> dict:
    db_path = environment["database"]
    before = snapshot(db_path)
    if name == "anonymous_order":
        status, _ = client.request("POST", "/orders", {"quantity": 2})
        expect(status, 401, "anonymous order")
        expect(snapshot(db_path), before, "unauthorized side effects")
        return {"http_status": status, "database_unchanged": True}

    client.login()
    if name == "quantity_validation":
        for quantity in [0, -1, 1.5, "2", True, None]:
            status, _ = client.request("POST", "/orders", {"quantity": quantity})
            expect(status, 400, f"quantity {quantity!r}")
        expect(snapshot(db_path), before, "invalid quantity side effects")
        return {"rejected_values": [0, -1, 1.5, "2", True, None]}

    if name == "stock_reservation":
        order = client.order()
        after = snapshot(db_path)
        expect(after["stock"], before["stock"] - 2, "reserved stock")
        expect(stored_order(db_path, order["id"])["quantity"], 2, "stored quantity")
        status, _ = client.request("POST", "/orders", {"quantity": after["stock"] + 1})
        expect(status, 409, "insufficient inventory")
        expect(snapshot(db_path), after, "insufficient inventory side effects")
        return {
            "order_id": order["id"],
            "stock_before": before["stock"],
            "stock_after": after["stock"],
        }

    order = client.order(total=1) if name == "server_pricing" else client.order()
    order_id = order["id"]
    if name == "authenticated_order":
        expect(stored_order(db_path, order_id)["status"], "pending", "new order state")
    elif name == "server_pricing":
        expect(order["total"], 2 * UNIT_PRICE_CENTS, "server-calculated response amount")
        expect(stored_order(db_path, order_id)["total"], 2 * UNIT_PRICE_CENTS, "stored amount")
    elif name == "payment_transition":
        callback(client, order_id, environment["secret"])
        status, response = client.request("GET", f"/orders/{order_id}")
        expect(status, 200, "order retrieval")
        row = stored_order(db_path, order_id)
        # Capture both layers before asserting so a failure retains the complete observation.
        write_json(
            client.directory / "payment-observation.json", {"api": response, "database": row}
        )
        expect(response["status"], "paid", "order API state after accepted callback")
        expect(row["status"], "paid", "database state after accepted callback")
    elif name == "duplicate_callback":
        reserved_stock = snapshot(db_path)["stock"]
        callback(client, order_id, environment["secret"])
        callback(client, order_id, environment["secret"])
        expect(stored_order(db_path, order_id)["payment_count"], 1, "payment idempotency")
        expect(snapshot(db_path)["stock"], reserved_stock, "duplicate callback stock")
    elif name == "invalid_signature":
        unchanged = snapshot(db_path)
        status, _ = client.request(
            "POST", "/callbacks/payment", {"order_id": order_id, "signature": "invalid"}
        )
        expect(status, 403, "invalid signature")
        expect(snapshot(db_path), unchanged, "invalid signature side effects")
    elif name == "payment_timeout":
        status, _ = client.request("POST", "/simulate-timeout", {"order_id": order_id})
        expect(status, 504, "simulated gateway timeout")
        status, response = client.request("GET", f"/orders/{order_id}")
        expect(status, 200, "order retrieval after timeout")
        row = stored_order(db_path, order_id)
        expect(response["status"], "pending_confirmation", "timeout API state")
        expect(row["status"], "pending_confirmation", "timeout database state")
        expect(row["payment_count"], 0, "unconfirmed payment count")
    else:
        raise ValueError(f"Unknown API scenario: {name}")
    return {"order_id": order_id, "database_order": stored_order(db_path, order_id)}


def run_browser(environment: dict, directory: Path, channel: str | None) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Browser dependency missing; install prooflane[browser] and Chromium"
        ) from None
    exchanges = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel)
        try:
            context = browser.new_context(viewport={"width": 1100, "height": 800})
            page = context.new_page()
            page.set_default_timeout(10000)

            def record_response(response):
                if response.request.resource_type not in {"fetch", "xhr"}:
                    return
                exchanges.append(
                    redact(
                        {
                            "path": response.url.removeprefix(environment["url"]),
                            "method": response.request.method,
                            "status": response.status,
                            "request": response.request.post_data_json,
                            "response": response.json(),
                            "trace_id": response.headers.get("x-trace-id"),
                        }
                    )
                )

            page.on("response", record_response)
            try:
                page.goto(environment["url"], wait_until="load")
                page.get_by_role("button", name="登录演示账户").click()
                page.get_by_role("button", name="创建订单").click()
                page.wait_for_function(
                    "document.querySelector('#status').textContent === 'pending'"
                )
                page.get_by_role("button", name="模拟支付成功").click()
                # The GET triggered by payment must complete before observing the visible state.
                page.wait_for_function("window.paymentFinished === true")
                order_id = page.locator("#order").inner_text()
                state = page.locator("#status").inner_text()
                row = stored_order(environment["database"], order_id)
                observation = {"order_id": order_id, "page_state": state, "database_order": row}
                write_json(directory / "browser-observation.json", observation)
                expect(state, "paid", "visible order state after payment")
                expect(row["status"], "paid", "browser flow database state")
                return observation
            finally:
                write_json(directory / "browser-network.json", exchanges)
                (directory / "page.html").write_text(page.content(), encoding="utf-8")
                page.screenshot(path=str(directory / "page.png"), full_page=True)
        finally:
            browser.close()


def execute_case(
    case: dict, environment: dict, directory: Path, *, browser_enabled: bool, channel: str | None
) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "database-before.json", snapshot(environment["database"]))
    client = HttpClient(environment["url"], directory)
    result = {**case, "status": "blocked", "observation": None, "error": None}
    start = time.monotonic()
    try:
        if case["scenario"] == "browser_checkout":
            if not browser_enabled:
                raise RuntimeError(
                    "Browser execution explicitly disabled; end-to-end coverage is blocked"
                )
            result["observation"] = run_browser(environment, directory, channel)
        else:
            result["observation"] = run_api_scenario(case["scenario"], client, environment)
        result["status"] = "passed"
    except AssertionError as error:
        result.update(status="failed", error=str(error))
    except Exception as error:
        # Tool failures are inconclusive, never product failures or successful tests.
        result.update(status="blocked", error=f"{type(error).__name__}: {error}")
    finally:
        write_json(directory / "database-after.json", snapshot(environment["database"]))
    result["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
    result["evidence"] = [str(path.name) for path in sorted(directory.iterdir()) if path.is_file()]
    write_json(directory / "result.json", result)
    return result
