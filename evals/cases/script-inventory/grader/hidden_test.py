"""Hidden acceptance tests: copied next to the agent's sync.py only after the agent finishes."""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT = Path(__file__).with_name("sync.py")
REMOTE_PAGES = {
    None: {"items": [{"sku": "A1", "qty": 3}, {"sku": "B2", "qty": 1}], "next": "p2"},
    "p2": {"items": [{"sku": "D4", "qty": 4}, {"sku": "E5", "qty": 2}], "next": None},
}
LOCAL_CSV = "sku,qty\nA1,5\nB2,0\nC3,7\nA1,2\nE5,2\n"
EXPECTED = [
    {"sku": "A1", "delta": 4},
    {"sku": "B2", "delta": -1},
    {"sku": "C3", "delta": 7},
    {"sku": "D4", "delta": -4},
]


class WarehouseHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def planned_failure(self, key):
        planned = self.server.failures.get(key)
        return planned.pop(0) if planned else None

    def do_GET(self):
        url = urlparse(self.path)
        cursor = parse_qs(url.query).get("cursor", [None])[0]
        self.server.cursors.append(cursor)
        failure = self.planned_failure("GET")
        if failure:
            self.respond(failure, {"error": "planned"})
        elif url.path != "/stock" or cursor not in REMOTE_PAGES:
            self.respond(404, {"error": "unknown page"})
        else:
            self.respond(200, REMOTE_PAGES[cursor])

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.attempts.append(body)
        failure = self.planned_failure(body.get("sku"))
        if failure:
            self.respond(failure, {"error": "planned"})
        elif self.path != "/adjust":
            self.respond(404, {"error": "unknown path"})
        else:
            self.server.adjustments.append(body)
            self.respond(200, {"ok": True})


class InventorySyncAcceptance(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), WarehouseHandler)
        self.server.failures, self.server.cursors = {}, []
        self.server.attempts, self.server.adjustments = [], []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.csv = Path(directory.name) / "inventory.csv"
        self.csv.write_text(LOCAL_CSV, encoding="utf-8")

    def run_sync(self, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--base-url",
                f"http://127.0.0.1:{self.server.server_port}",
                "--csv",
                str(self.csv),
                *extra,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_sync_sends_sorted_adjustments_and_reports_them(self):
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"adjustments": EXPECTED, "dry_run": False})
        self.assertEqual(
            self.server.adjustments,
            [{**item, "idempotency_key": f"{item['sku']}:{item['delta']}"} for item in EXPECTED],
        )

    def test_reads_every_remote_page_with_cursor(self):
        self.assertEqual(self.run_sync("--dry-run").returncode, 0)
        self.assertEqual(self.server.cursors, [None, "p2"])

    def test_dry_run_sends_no_adjustment(self):
        result = self.run_sync("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"adjustments": EXPECTED, "dry_run": True})
        self.assertEqual(self.server.attempts, [])

    def test_invalid_csv_exits_2_without_writes(self):
        # A missing script or unknown option also exits 2, so first prove valid input works.
        self.assertEqual(self.run_sync("--dry-run").returncode, 0)
        for content in ["sku,qty\nA1,-1\n", "sku,qty\nA1,x\n", "item,count\nA1,1\n"]:
            self.csv.write_text(content, encoding="utf-8")
            result = self.run_sync()
            self.assertEqual(result.returncode, 2, content)
            self.assertTrue(result.stderr.strip(), content)
        self.assertEqual(self.server.attempts, [])

    def test_retries_503_up_to_three_attempts(self):
        self.server.failures = {"A1": [503, 503], "GET": [503]}
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([item["sku"] for item in self.server.attempts].count("A1"), 3)
        self.assertEqual(len(self.server.adjustments), len(EXPECTED))

    def test_persistent_503_exits_1(self):
        self.server.failures = {"GET": [503, 503, 503, 503]}
        result = self.run_sync()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.server.cursors), 3)
        self.assertEqual(self.server.attempts, [])

    def test_other_errors_are_not_retried(self):
        self.server.failures = {"A1": [500, 500]}
        result = self.run_sync()
        self.assertEqual(result.returncode, 1)
        self.assertEqual([item["sku"] for item in self.server.attempts], ["A1"])


if __name__ == "__main__":
    unittest.main()
