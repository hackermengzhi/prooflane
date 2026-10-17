import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.harness import RoleTools, run_harness
from prooflane.store import Store
from prooflane.tools import ToolPolicy


class HarnessTests(unittest.TestCase):
    def test_leader_delegates_implementation_and_independent_tester_verifies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "project"
            workspace.mkdir()
            checks = [
                {
                    "tool": "process.run",
                    "arguments": {
                        "argv": [
                            "$PYTHON",
                            "-c",
                            "from solution import double; assert double(4)==8",
                        ]
                    },
                    "assertions": [{"path": ["returncode"], "equals": 0}],
                }
            ]
            actions = [
                {"action": "delegate", "role": "product", "task": "Define acceptance"},
                {"action": "set_checks", "checks": checks},
                {"action": "report", "message": "Acceptance defined"},
                {"action": "delegate", "role": "developer", "task": "Implement double"},
                {
                    "action": "call",
                    "tool": "files.write",
                    "arguments": {
                        "path": "solution.py",
                        "content": "def double(value):\n    return value * 2\n",
                    },
                },
                {"action": "report", "message": "Implementation ready"},
                {"action": "delegate", "role": "tester", "task": "Verify independently"},
                {"action": "verify"},
                {"action": "report", "message": "Actual process assertion passed"},
                {"action": "finish"},
            ]
            with patch(
                "prooflane.harness.request_json",
                side_effect=[
                    ({**action, "reason": "test delegation"}, "contract-stub") for action in actions
                ],
            ):
                result = run_harness(
                    Store(str(root / "state.db")),
                    "Build double",
                    ToolPolicy(workspace, allow_process=True),
                    root / "runs",
                )
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(
                [item["assigned_to"] for item in result["handoffs"]],
                ["product", "developer", "tester"],
            )
            self.assertEqual(result["verification"][0]["status"], "passed")
            self.assertEqual(result["checks_origin"], "product-proposed")

    def test_leader_cannot_self_certify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch(
                "prooflane.harness.request_json",
                return_value=({"action": "finish", "reason": "pretend done"}, "stub"),
            ):
                result = run_harness(
                    Store(str(root / "state.db")), "Do work", ToolPolicy(root), root / "runs"
                )
            self.assertEqual(result["status"], "blocked")
            self.assertIn("independent tester", result["error"])

    def test_role_catalogs_enforce_tool_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = ToolPolicy(root)
            product = RoleTools(policy, root / "evidence", "product")
            tester = RoleTools(policy, root / "test-evidence", "tester")
            developer = RoleTools(policy, root / "dev-evidence", "developer")
            self.assertNotIn("process.run", product.catalog())
            self.assertNotIn("files.write", tester.catalog())
            self.assertIn("files.write", developer.catalog())
            self.assertEqual(
                tester.call("files.write", {"path": "bad", "content": "bad"})["status"], "blocked"
            )
