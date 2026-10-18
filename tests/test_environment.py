import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.tools import ToolPolicy, Tools


class EnvironmentSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.calls = 0

    def snapshot(self, allow_process=False, **arguments):
        self.calls += 1
        evidence = self.root / f"evidence-{self.calls}"
        tools = Tools(ToolPolicy(self.workspace, allow_process=allow_process), evidence)
        result = tools.call("env.snapshot", arguments)
        self.assertEqual(result["status"], "succeeded", result)
        return result["data"]

    def test_core_fields_are_present_and_typed(self):
        data = self.snapshot()
        self.assertEqual(
            set(data), {"platform", "python", "workspace", "packages", "env_keys", "time"}
        )
        self.assertIsInstance(data["platform"]["system"], str)
        self.assertIsInstance(data["python"]["version"], str)
        self.assertTrue(data["time"]["utc"].endswith("Z"))
        self.assertIsInstance(data["time"]["monotonic"], float)
        self.assertEqual(data["workspace"]["path"], str(self.workspace.resolve()))

    def test_packages_resolve_known_and_missing_names(self):
        data = self.snapshot(packages=["ruff", "definitely-not-installed-xyz"])
        self.assertIsInstance(data["packages"]["ruff"], str)
        self.assertIsNone(data["packages"]["definitely-not-installed-xyz"])

    def test_env_keys_filter_by_prefix_and_never_expose_values(self):
        # A prefix unlikely to collide with any ambient variable (CI sets PROOFLANE_TEST_*).
        secret = "s3cr3t-value-should-not-leak"
        injected = {"ENVSNAPTEST_ONE": secret, "ENVSNAPTEST_TWO": "x", "OTHER_VAR": "y"}
        with patch.dict("os.environ", injected, clear=False):
            data = self.snapshot(env_prefixes=["ENVSNAPTEST_"])
        self.assertEqual(data["env_keys"], ["ENVSNAPTEST_ONE", "ENVSNAPTEST_TWO"])
        self.assertNotIn(secret, repr(data))

    def test_git_fields_report_commit_and_dirty_in_a_repository(self):
        def git(*arguments):
            subprocess.run(
                ["git", "-C", str(self.workspace), *arguments],
                check=True,
                capture_output=True,
            )

        git("init")
        git("config", "user.email", "t@example.test")
        git("config", "user.name", "Test")
        (self.workspace / "code.py").write_text("x = 1\n")
        git("add", "code.py")
        git("commit", "-m", "init")
        clean = self.snapshot()["workspace"]["git"]
        self.assertIsInstance(clean["commit"], str)
        self.assertFalse(clean["dirty"])
        (self.workspace / "code.py").write_text("x = 2\n")
        self.assertTrue(self.snapshot()["workspace"]["git"]["dirty"])

    def test_non_repository_workspace_has_null_git(self):
        self.assertIsNone(self.snapshot()["workspace"]["git"])

    def test_snapshot_needs_no_process_permission(self):
        self.assertIn("platform", self.snapshot(allow_process=False))

    def test_invalid_arguments_are_rejected(self):
        tools = Tools(ToolPolicy(self.workspace), self.root / "bad")
        for arguments in [{"packages": "ruff"}, {"env_prefixes": [1]}, {"packages": [2]}]:
            with self.subTest(arguments=arguments):
                self.assertEqual(tools.call("env.snapshot", arguments)["status"], "blocked")

    def test_catalog_exposes_arguments(self):
        tools = Tools(ToolPolicy(self.workspace), self.root / "cat")
        self.assertEqual(
            set(tools.catalog()["env.snapshot"]["arguments"]), {"packages", "env_prefixes"}
        )


if __name__ == "__main__":
    unittest.main()
