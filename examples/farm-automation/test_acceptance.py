"""Independent acceptance tests using an actual loopback HTTP fixture."""

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class FarmHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.respond(200, {"plots": self.server.plots})

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.server.fail_harvest:
            self.respond(503, {"error": "temporary failure"})
            return
        for plot in self.server.plots:
            if plot["id"] == request.get("plot_id") and plot["state"] == "ready":
                if request.get("idempotency_key") != "harvest:" + plot["id"]:
                    self.respond(400, {"error": "missing stable operation identifier"})
                    return
                plot["state"] = "empty"
                self.server.harvest_count += 1
                self.respond(200, {"harvested": True})
                return
        self.respond(409, {"error": "plot not ready"})


class AutomationAcceptance(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), FarmHandler)
        self.server.plots = [
            {"id": "p1", "state": "ready"},
            {"id": "p2", "state": "growing"},
            {"id": "p3", "state": "empty"},
        ]
        self.server.harvest_count = 0
        self.server.fail_harvest = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def run_automation(self):
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("automation.py")),
                "--base-url",
                f"http://127.0.0.1:{self.server.server_port}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_harvests_only_ready_plots_and_preserves_other_states(self):
        result = self.run_automation()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"harvested": ["p1"]})
        self.assertEqual(self.server.harvest_count, 1)
        self.assertEqual(
            [plot["state"] for plot in self.server.plots], ["empty", "growing", "empty"]
        )

    def test_repeat_run_does_not_harvest_again(self):
        first = self.run_automation()
        self.assertEqual(first.returncode, 0, first.stderr)
        repeated = self.run_automation()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout), {"harvested": []})
        self.assertEqual(self.server.harvest_count, 1)

    def test_http_failure_cannot_be_reported_as_success(self):
        self.server.fail_harvest = True
        result = self.run_automation()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(self.server.harvest_count, 0)


if __name__ == "__main__":
    unittest.main()
