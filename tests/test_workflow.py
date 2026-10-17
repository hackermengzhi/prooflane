import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from prooflane.artifacts import digest
from prooflane.demo import service
from prooflane.execution import HttpClient, snapshot
from prooflane.process import checkout_service
from prooflane.store import Store
from prooflane.workflow import run_closed_loop

REQUIREMENTS = Path(service.__file__).with_name("requirements.md")


class CheckoutTests(unittest.TestCase):
    def test_service_cleanup_even_when_caller_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "caller failed"):
                with checkout_service(Path(temporary), "fixed") as environment:
                    url = environment["url"]
                    with urlopen(url + "/health", timeout=2) as response:
                        self.assertEqual(response.status, 200)
                    raise RuntimeError("caller failed")
            with self.assertRaises(URLError):
                urlopen(url + "/health", timeout=1)

    def test_invalid_credentials_and_unknown_callback_do_not_change_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with checkout_service(directory, "fixed") as environment:
                client = HttpClient(environment["url"], directory)
                before = snapshot(environment["database"])
                status, _ = client.request(
                    "POST", "/login", {"username": "demo", "password": "wrong"}
                )
                self.assertEqual(status, 401)
                status, _ = client.request(
                    "POST", "/callbacks/payment", {"order_id": "unknown", "signature": "wrong"}
                )
                self.assertEqual(status, 403)
                self.assertEqual(snapshot(environment["database"]), before)
                evidence = (directory / "http.json").read_text()
                self.assertNotIn('"wrong"', evidence)


class WorkflowTests(unittest.TestCase):
    def test_core_red_green_evidence_and_honest_browser_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = Store(str(directory / "state.db"))
            result = run_closed_loop(store, REQUIREMENTS, directory / "runs", browser_enabled=False)
            self.assertFalse(result["closed_loop"])
            self.assertEqual(result["status"], "blocked")
            baseline = {case["scenario"]: case for case in result["baseline"]}
            regression = {case["scenario"]: case for case in result["regression"]}
            self.assertEqual(baseline["payment_transition"]["status"], "failed")
            self.assertIn("observed 'pending'", baseline["payment_transition"]["error"])
            self.assertEqual(regression["payment_transition"]["status"], "passed")
            self.assertEqual(regression["browser_checkout"]["status"], "blocked")
            self.assertEqual(sum(case["status"] == "passed" for case in result["regression"]), 9)
            self.assertEqual(result["defects"][0]["regression_status"], "passed")
            self.assertEqual(store.tasks()[0]["status"], "blocked")
            self.assertEqual(
                [event["stage"] for event in store.events(result["task_id"])],
                [
                    "planning",
                    "running",
                    "investigating",
                    "regressing",
                    "blocked",
                ],
            )
            run = Path(result["directory"])
            traces = json.loads(
                (run / "baseline/payment_transition/service-trace.json").read_text()
            )
            self.assertIn("/callbacks/payment", [trace["path"] for trace in traces])
            observation = json.loads(
                (run / "baseline/payment_transition/payment-observation.json").read_text()
            )
            self.assertEqual(observation["database"]["status"], "pending")
            for item in json.loads((run / "manifest.json").read_text()):
                self.assertEqual(digest(run / item["path"]), item["sha256"])
            self.assertEqual(digest(run / "plan.json"), result["plan_sha256"])
            self.assertTrue(Path(result["report"]).is_file())

    def test_changed_requirements_block_before_starting_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            requirements = directory / "changed.md"
            requirements.write_text(REQUIREMENTS.read_text().replace("正整数", "非负整数"))
            store = Store(str(directory / "state.db"))
            result = run_closed_loop(store, requirements, directory / "runs", browser_enabled=False)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["baseline"], [])
            self.assertIn("requirement contract", result["error"])
            self.assertFalse((Path(result["directory"]) / "baseline").exists())

    @unittest.skipUnless(
        os.environ.get("PROOFLANE_TEST_BROWSER") == "1", "Browser integration opt-in"
    )
    def test_full_browser_loop(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = run_closed_loop(
                Store(str(directory / "state.db")),
                REQUIREMENTS,
                directory / "runs",
                channel=os.environ.get("PROOFLANE_BROWSER_CHANNEL"),
            )
            self.assertTrue(result["closed_loop"], result)
            self.assertEqual(sum(case["status"] == "failed" for case in result["baseline"]), 2)
            self.assertTrue(all(case["status"] == "passed" for case in result["regression"]))
            run = Path(result["directory"])
            for phase, state in [("baseline", "pending"), ("regression", "paid")]:
                evidence = run / phase / "browser_checkout"
                self.assertGreater((evidence / "page.png").stat().st_size, 1000)
                observation = json.loads((evidence / "browser-observation.json").read_text())
                self.assertEqual(observation["page_state"], state)
                self.assertTrue(json.loads((evidence / "service-trace.json").read_text()))
