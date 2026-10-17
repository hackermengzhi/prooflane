import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from prooflane.agent import run_agent
from prooflane.models import ModelOutputError
from prooflane.store import Store
from prooflane.tools import ToolPolicy, Tools


def process_check(argv):
    return {
        "tool": "process.run",
        "arguments": {"argv": argv},
        "assertions": [{"path": ["returncode"], "equals": 0}],
    }


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.store = Store(str(self.root / "state.db"))

    def run_goal(self, **options):
        return run_agent(
            self.store,
            "Implement a double function and verify behavior",
            ToolPolicy(self.workspace, allow_process=True),
            self.root / "runs",
            **options,
        )

    def test_model_loop_repairs_real_code_after_actual_failed_verification(self):
        checks = [
            process_check(
                [
                    "$PYTHON",
                    "-c",
                    "from program import double; assert double(3) == 6; assert double(-2) == -4",
                ]
            )
        ]
        decisions = [
            {
                "action": "call",
                "tool": "files.write",
                "arguments": {
                    "path": "program.py",
                    "content": "def double(value):\n    return value + 2\n",
                },
                "reason": "first implementation",
            },
            {"action": "finish", "reason": "verify the implementation"},
            {
                "action": "call",
                "tool": "files.write",
                "arguments": {
                    "path": "program.py",
                    "content": "def double(value):\n    return value * 2\n",
                },
                "reason": "repair based on failed assertion",
            },
            {"action": "finish", "reason": "rerun unchanged acceptance checks"},
        ]
        observations = []

        def model_stub(prompt, payload):
            observations.append(payload)
            return decisions[len(observations) - 1], "contract-stub"

        with patch("prooflane.agent.request_json", side_effect=model_stub):
            result = self.run_goal(checks=checks)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(observations[2]["last_verification"][0]["status"], "failed")
        self.assertIn(
            "AssertionError",
            observations[2]["last_verification"][0]["observation"]["data"]["output"],
        )
        self.assertTrue(all(item["status"] == "passed" for item in result["verification"]))
        self.assertEqual(
            json.loads((Path(result["directory"]) / "checks.json").read_text()), checks
        )
        self.assertIn("value * 2", (self.workspace / "program.py").read_text())

    def test_acceptance_checks_cannot_be_replaced(self):
        decisions = [
            (
                {
                    "action": "set_checks",
                    "checks": [process_check(["$PYTHON", "-c", "raise SystemExit(1)"])],
                    "reason": "define acceptance",
                },
                "stub",
            ),
        ] + [
            (
                {
                    "action": "set_checks",
                    "checks": [process_check(["$PYTHON", "-c", "pass"])],
                    "reason": "make it pass",
                },
                "stub",
            )
        ] * 3
        with patch("prooflane.agent.request_json", side_effect=decisions):
            result = self.run_goal()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("immutable", result["error"])
        self.assertEqual(
            [step["action"] for step in result["steps"]], ["set_checks", "rejected", "rejected"]
        )
        checks = json.loads((Path(result["directory"]) / "checks.json").read_text())
        self.assertIn("raise SystemExit(1)", checks[0]["arguments"]["argv"][2])

    def test_model_corrects_malformed_decisions_from_feedback(self):
        checks = [process_check(["$PYTHON", "-c", "import program"])]
        responses = [
            ({"action": "set_checks", "checks": checks, "reason": "acceptance"}, "stub"),
            ModelOutputError("Model returned an invalid JSON response"),
            ({"action": "call", "reason": "forgot the tool name"}, "stub"),
            (
                {
                    "action": "call",
                    "tool": "files.write",
                    "arguments": {"path": "program.py", "content": "VALUE = 1\n"},
                    "reason": "implement",
                },
                "stub",
            ),
            ({"action": "finish", "reason": "verify"}, "stub"),
        ]
        seen = []

        def model_stub(prompt, payload):
            seen.append(payload["recent_steps"])
            response = responses[len(seen) - 1]
            if isinstance(response, Exception):
                raise response
            return response

        with patch("prooflane.agent.request_json", side_effect=model_stub):
            result = self.run_goal()
        self.assertEqual(result["status"], "completed", result["error"])
        rejected = [step for step in result["steps"] if step["action"] == "rejected"]
        self.assertEqual([step["attempted"] for step in rejected], [None, "call"])
        # The model sees why its previous decision was rejected before trying again.
        self.assertEqual(seen[3][-1]["observation"], {"error": "Tool name is required"})

    def test_recipe_failure_and_missing_verification_cannot_report_completion(self):
        result = self.run_goal(
            recipe={
                "steps": [],
                "checks": [process_check(["$PYTHON", "-c", "raise SystemExit(3)"])],
            }
        )
        self.assertEqual(result["status"], "failed")
        with patch(
            "prooflane.agent.request_json",
            return_value=({"action": "finish", "reason": "pretend done"}, "stub"),
        ):
            result = self.run_goal()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("acceptance checks", result["error"])

    def test_step_budget_stops_unfinished_agent(self):
        with patch(
            "prooflane.agent.request_json",
            return_value=(
                {"action": "call", "tool": "files.list", "arguments": {}, "reason": "observe"},
                "stub",
            ),
        ):
            result = self.run_goal(max_steps=1)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("step budget", result["error"])

    def test_model_missing_configuration_does_not_invoke_a_recipe(self):
        with patch.dict(os.environ, {}, clear=True):
            result = self.run_goal()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["engine"], "llm")
        self.assertEqual(result["steps"], [])

    def test_farm_automation_recipe_writes_script_and_passes_independent_acceptance(self):
        example = Path(__file__).resolve().parents[1] / "examples/farm-automation"
        for filename in ["requirements.md", "test_acceptance.py"]:
            (self.workspace / filename).write_bytes((example / filename).read_bytes())
        recipe = json.loads((example / "recipe.json").read_text())
        result = self.run_goal(recipe=recipe)
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue((self.workspace / "automation.py").is_file())
        output = result["verification"][0]["observation"]["data"]["output"]
        self.assertIn("Ran 3 tests", output)
        self.assertIn("test_repeat_run", output)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.tools = Tools(ToolPolicy(self.workspace, allow_process=True), self.root / "tools")

    def test_workspace_escape_and_external_symlink_are_rejected(self):
        for path in ["../outside.txt", str(self.root / "outside.txt")]:
            self.assertEqual(
                self.tools.call("files.write", {"path": path, "content": "bad"})["status"],
                "blocked",
            )
        (self.workspace / "linked").symlink_to(self.root)
        self.assertEqual(
            self.tools.call("files.read", {"path": "linked/outside.txt"})["status"], "blocked"
        )
        self.assertFalse((self.root / "outside.txt").exists())

    def test_edit_replaces_one_exact_occurrence_and_keeps_backup(self):
        (self.workspace / "app.py").write_text("A = 1\nB = 1\n")

        def edit(old, new):
            return self.tools.call("files.edit", {"path": "app.py", "old": old, "new": new})

        self.assertEqual(edit("= 1", "= 2")["status"], "blocked")
        self.assertEqual(edit("C = 1", "C = 2")["status"], "blocked")
        result = edit("B = 1", "B = 2")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual((self.workspace / "app.py").read_text(), "A = 1\nB = 2\n")
        backup = self.root / result["evidence"] / "previous.bin"
        self.assertEqual(backup.read_text(), "A = 1\nB = 1\n")

    def test_process_permission_timeout_nonzero_and_output_limit(self):
        disabled = Tools(ToolPolicy(self.workspace), self.root / "disabled")
        self.assertEqual(
            disabled.call("process.run", {"argv": ["$PYTHON", "-c", "pass"]})["status"], "blocked"
        )
        timed = self.tools.call(
            "process.run", {"argv": ["$PYTHON", "-c", "import time; time.sleep(60)"], "timeout": 1}
        )
        self.assertEqual(timed["data"]["stopped"], "timeout")
        failed = self.tools.call(
            "process.run",
            {"argv": ["$PYTHON", "-c", "print('actual failure'); raise SystemExit(7)"]},
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["data"]["returncode"], 7)
        self.assertIn("actual failure", failed["data"]["output"])
        limited = self.tools.call("process.run", {"argv": ["$PYTHON", "-c", "print('x' * 600000)"]})
        self.assertEqual(limited["data"]["stopped"], "output_limit")

    def test_python_inspection_uses_actual_syntax_and_symbols(self):
        (self.workspace / "module.py").write_text("def example(value):\n    return value\n")
        result = self.tools.call("code.inspect", {"path": "module.py"})
        self.assertEqual(result["data"]["symbols"][0]["name"], "example")
        (self.workspace / "module.py").write_text("def broken(:\n")
        self.assertEqual(self.tools.call("code.inspect", {"path": "module.py"})["status"], "failed")

    def test_http_origin_policy_and_actual_response(self):
        with local_page() as url:
            denied = self.tools.call("http.request", {"url": url})
            self.assertEqual(denied["status"], "blocked")
            enabled = Tools(ToolPolicy(self.workspace, allowed_origins=(url,)), self.root / "http")
            observed = enabled.call("http.request", {"url": url + "/health"})
            self.assertEqual(observed["data"], {"http_status": 200, "body": {"ready": True}})

    @unittest.skipUnless(
        os.environ.get("PROOFLANE_TEST_BROWSER") == "1", "Browser integration opt-in"
    )
    def test_generic_browser_clicks_non_payment_page_and_retains_screenshot(self):
        with local_page() as url:
            tools = Tools(
                ToolPolicy(
                    self.workspace,
                    allowed_origins=(url,),
                    browser_channel=os.environ.get("PROOFLANE_BROWSER_CHANNEL"),
                ),
                self.root / "browser",
            )
            result = tools.call(
                "browser.run",
                {
                    "url": url,
                    "actions": [
                        {"op": "click", "selector": "button"},
                        {"op": "expect_text", "selector": "#state", "text": "ready"},
                    ],
                },
            )
            self.assertEqual(result["status"], "succeeded", result)
            self.assertIn("ready", result["data"]["text"])
            self.assertGreater((self.root / result["evidence"] / "page.png").stat().st_size, 1000)


@contextmanager
def local_page():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            data = (
                b'{"ready": true}'
                if self.path == "/health"
                else b"<title>Automation fixture</title><button onclick=\"document.querySelector('#state').textContent='ready'\">Prepare</button><p id=\"state\">waiting</p>"
            )
            self.send_response(200)
            self.send_header(
                "Content-Type", "application/json" if self.path == "/health" else "text/html"
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=2)
