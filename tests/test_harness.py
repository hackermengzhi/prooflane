import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.harness import ROLE_GUIDES, RoleTools, run_harness
from prooflane.store import Store
from prooflane.tools import ToolPolicy


class HarnessTests(unittest.TestCase):
    def test_role_guides_explicitly_limit_allowed_actions(self):
        allowed_actions = {
            "leader": ("delegate", "finish", "blocked"),
            "product": ("call", "set_checks", "report", "blocked"),
            "developer": ("call", "report", "blocked"),
            "tester": ("call", "verify", "report", "blocked"),
        }
        all_actions = {action for actions in allowed_actions.values() for action in actions}
        for role, actions in allowed_actions.items():
            with self.subTest(role=role):
                guide = ROLE_GUIDES[role]
                action_list = guide.split(";", 1)[0]
                self.assertIn("You may only use these actions:", action_list)
                self.assertIn("do not use any other actions", guide)
                for action in actions:
                    self.assertIn(action, action_list)
                for action in all_actions.difference(actions):
                    self.assertNotIn(action, action_list)

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
            self.assertEqual([step["attempted"] for step in result["steps"]], ["finish", "finish"])

    def test_stuck_specialist_returns_to_leader_instead_of_aborting(self):
        # Leader delegates to the tester, who keeps choosing an action it is not allowed;
        # the run must hand control back to the leader rather than dying outright.
        decisions = [
            {"action": "delegate", "role": "tester", "task": "verify"},
            {"action": "finish"},
            {"action": "finish"},
            {"action": "finish"},
            {"action": "blocked"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch(
                "prooflane.harness.request_json",
                side_effect=[({**d, "reason": "r"}, "stub") for d in decisions],
            ):
                result = run_harness(
                    Store(str(root / "state.db")),
                    "Do work",
                    ToolPolicy(root),
                    root / "runs",
                    max_steps=20,
                )
            roles = [step["role"] for step in result["steps"]]
            # The tester got stuck but the run was not killed by the contract-abort;
            # control returned to the leader, whose own "blocked" ended the run.
            self.assertIn("tester", roles)
            self.assertNotIn("3 times in a row", str(result["error"]))
            self.assertEqual(result["error"], "RuntimeError: r")

    def test_role_catalogs_enforce_tool_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = ToolPolicy(root)
            product = RoleTools(policy, root / "evidence", "product")
            tester = RoleTools(policy, root / "test-evidence", "tester")
            developer = RoleTools(policy, root / "dev-evidence", "developer")
            self.assertNotIn("process.run", product.catalog())
            self.assertNotIn("files.write", product.catalog())
            self.assertIn("files.write", developer.catalog())
            self.assertNotIn("browser.run", developer.catalog())
            self.assertIn("browser.run", tester.catalog())
            self.assertEqual(
                product.call("files.write", {"path": "bad", "content": "bad"})["status"], "blocked"
            )

    def test_tester_writes_own_test_assets_but_not_code_under_test(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "service.py").write_text("VALUE = 1\n")
            tester = RoleTools(ToolPolicy(root), root / "evidence", "tester")

            def write(path, content):
                return tester.call("files.write", {"path": path, "content": content})["status"]

            self.assertEqual(write("test_service.py", "draft"), "succeeded")
            self.assertEqual(write("test_service.py", "revised"), "succeeded")
            self.assertEqual(write("service.py", "VALUE = 2\n"), "blocked")
            edit = {"path": "service.py", "old": "1", "new": "2"}
            self.assertEqual(tester.call("files.edit", edit)["status"], "blocked")
            own_edit = {"path": "test_service.py", "old": "revised", "new": "final"}
            self.assertEqual(tester.call("files.edit", own_edit)["status"], "succeeded")
            write("test_service.py", "revised")
            self.assertEqual((root / "service.py").read_text(), "VALUE = 1\n")
            self.assertEqual((root / "test_service.py").read_text(), "revised")
