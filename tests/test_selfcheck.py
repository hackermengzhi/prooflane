import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.tools import MAX_FILE_BYTES, ToolPolicy, Tools
from prooflane.trials import OUTPUT_TAIL, run_trial


class SelfcheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "program.py").write_text(
            "# untested metadata 1\ndef double(value):\n    return value * 2\n",
            encoding="utf-8",
        )
        (self.workspace / "test_program.py").write_text(
            "import unittest\n"
            "from program import double\n\n"
            "class DoubleTests(unittest.TestCase):\n"
            "    def test_double(self):\n"
            "        self.assertEqual(double(3), 6)\n",
            encoding="utf-8",
        )
        self.suite = ["$PYTHON", "-m", "unittest", "discover", "-v"]
        self.mutants = [
            {"id": "wrong_result", "replace": ["value * 2", "value + 2"]},
            {"id": "untested", "replace": ["metadata 1", "metadata 2"]},
        ]
        self.tools = Tools(ToolPolicy(self.workspace, allow_process=True), self.root / "evidence")

    def call(self, **arguments):
        return self.tools.call(
            "quality.mutate",
            {"target": "program.py", "suite": self.suite, "mutants": self.mutants, **arguments},
        )

    def check(self, **arguments):
        result = self.call(**arguments)
        self.assertEqual(result["status"], "succeeded", result)
        return result["data"]

    def snapshot(self):
        return {
            str(path.relative_to(self.workspace)): path.read_bytes()
            for path in self.workspace.rglob("*")
            if path.is_file()
        }

    def test_real_suite_kills_mutation_and_reports_survivor(self):
        result = self.check()
        self.assertTrue(result["baseline_passed"])
        self.assertIsNone(result["baseline_output"])
        self.assertEqual(
            [(item["id"], item["status"]) for item in result["mutants"]],
            [("wrong_result", "killed"), ("untested", "survived")],
        )
        self.assertIn("AssertionError", result["mutants"][0]["output"])
        self.assertIsNone(result["mutants"][1]["output"])
        self.assertEqual(result["survivors"], ["untested"])
        self.assertEqual(result["kill_rate"], 0.5)

    def test_missing_and_repeated_anchors_do_not_stop_other_mutants(self):
        result = self.check(
            mutants=[
                {"id": "missing", "replace": ["absent", "replacement"]},
                {"id": "repeated", "replace": ["value", "number"]},
                *self.mutants,
            ]
        )
        self.assertEqual(
            [item["status"] for item in result["mutants"]],
            ["invalid", "invalid", "killed", "survived"],
        )
        for item in result["mutants"][:2]:
            self.assertIn("exactly once", item["reason"])
        self.assertEqual(result["kill_rate"], 0.5)

    def test_failed_baseline_does_not_run_mutants_and_bounds_output(self):
        suite = ["$PYTHON", "-c", "print('x' * 5000 + 'baseline-tail'); raise SystemExit(1)"]
        with patch("prooflane.selfcheck.run_trial", wraps=run_trial) as trial:
            result = self.check(suite=suite)
        self.assertEqual(trial.call_count, 1)
        self.assertEqual(trial.call_args.args[1], {})
        self.assertFalse(result["baseline_passed"])
        self.assertLessEqual(len(result["baseline_output"]), OUTPUT_TAIL)
        self.assertIn("baseline-tail", result["baseline_output"])
        self.assertEqual(result["mutants"], [])
        self.assertEqual(result["survivors"], [])
        self.assertEqual(result["kill_rate"], 0.0)

    def test_mutant_failure_output_is_a_bounded_tail(self):
        result = self.check(
            mutants=[
                {
                    "id": "noisy",
                    "replace": [
                        "return value * 2",
                        "raise AssertionError('x' * 5000 + 'tail-marker')",
                    ],
                }
            ]
        )
        mutant = result["mutants"][0]
        self.assertEqual(mutant["status"], "killed")
        self.assertLessEqual(len(mutant["output"]), OUTPUT_TAIL)
        self.assertIn("tail-marker", mutant["output"])

    def test_real_timeout_is_separate_from_killed_and_counts_in_denominator(self):
        result = self.check(
            timeout=1,
            mutants=[
                self.mutants[0],
                {
                    "id": "slow",
                    "replace": [
                        "return value * 2",
                        "__import__('time').sleep(5); return value * 2",
                    ],
                },
            ],
        )
        self.assertEqual([item["status"] for item in result["mutants"]], ["killed", "timeout"])
        self.assertEqual(result["kill_rate"], 0.5)
        self.assertEqual(result["survivors"], [])

    def test_baseline_timeout_does_not_run_mutants(self):
        with patch("prooflane.selfcheck.run_trial", wraps=run_trial) as trial:
            result = self.check(timeout=1, suite=["$PYTHON", "-c", "import time; time.sleep(5)"])
        self.assertFalse(result["baseline_passed"])
        self.assertEqual(trial.call_count, 1)
        self.assertEqual(result["mutants"], [])

    def test_workspace_files_remain_identical_even_when_suite_writes_files(self):
        with (self.workspace / "test_program.py").open("a", encoding="utf-8") as stream:
            stream.write(
                "\nfrom pathlib import Path\n"
                "Path('program.py').write_text('changed by suite')\n"
                "Path('created.txt').write_text('trial only')\n"
            )
        before = self.snapshot()
        result = self.check()
        self.assertTrue(result["baseline_passed"])
        self.assertEqual([item["status"] for item in result["mutants"]], ["killed", "survived"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.workspace / "__pycache__").exists())

    def test_disabled_process_permission_blocks_before_running(self):
        self.tools = Tools(ToolPolicy(self.workspace), self.root / "disabled")
        with patch("prooflane.selfcheck.run_trial", wraps=run_trial) as trial:
            result = self.call()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Process capability is disabled", result["data"]["error"])
        trial.assert_not_called()
        self.assertFalse(self.tools.catalog()["quality.mutate"]["enabled"])

    def test_workspace_escape_absolute_paths_and_external_symlinks_are_blocked(self):
        outside = self.root / "outside.py"
        outside.write_text("external secret", encoding="utf-8")
        (self.workspace / "linked.py").symlink_to(outside)
        with patch("prooflane.selfcheck.run_trial", wraps=run_trial) as trial:
            for target in ["../outside.py", str(outside), "linked.py"]:
                with self.subTest(target=target):
                    result = self.call(target=target)
                    self.assertEqual(result["status"], "blocked")
                    self.assertNotIn("external secret", str(result))
        trial.assert_not_called()

    def test_copy_cannot_follow_other_workspace_symlinks(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret").write_text("external secret", encoding="utf-8")
        for destination in [outside, self.workspace / "program.py"]:
            with self.subTest(destination=destination):
                link = self.workspace / "linked"
                link.symlink_to(destination)
                with patch("prooflane.selfcheck.run_trial", wraps=run_trial) as trial:
                    result = self.call()
                self.assertEqual(result["status"], "blocked")
                trial.assert_not_called()
                link.unlink()

    def test_target_uses_existing_text_size_limit(self):
        (self.workspace / "program.py").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        with patch("prooflane.selfcheck.run_trial", wraps=run_trial) as trial:
            result = self.call()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("text context limit", result["data"]["error"])
        trial.assert_not_called()

    def test_more_than_thirty_mutants_are_rejected_before_execution(self):
        with patch("prooflane.selfcheck.run_trial", wraps=run_trial) as trial:
            result = self.call(
                mutants=[{"id": str(index), "replace": ["value * 2", "0"]} for index in range(31)]
            )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("at most 30", result["data"]["error"])
        trial.assert_not_called()

    def test_thirty_invalid_mutants_and_empty_list_have_zero_rate(self):
        for mutants in [
            [{"id": str(index), "replace": ["absent", "0"]} for index in range(30)],
            [],
        ]:
            with self.subTest(count=len(mutants)):
                result = self.check(mutants=mutants)
                self.assertTrue(result["baseline_passed"])
                self.assertEqual(len(result["mutants"]), len(mutants))
                self.assertEqual(result["kill_rate"], 0.0)
                self.assertEqual(result["survivors"], [])

    def test_malformed_arguments_are_rejected_without_execution(self):
        arguments = [
            {"suite": []},
            {"suite": "python -m unittest"},
            {"suite": ["$PYTHON", 1]},
            {"mutants": {}},
            {"mutants": [None]},
            {"mutants": [{"id": "", "replace": ["x", "y"]}]},
            {"mutants": [self.mutants[0], self.mutants[0]]},
            {"mutants": [{"id": "bad", "replace": ["x"]}]},
            {"mutants": [{"id": "bad", "replace": ["", "y"]}]},
            {"mutants": [{"id": "bad", "replace": ["x", 1]}]},
            {"timeout": 0},
            {"timeout": 61},
            {"timeout": "5"},
        ]
        with patch("prooflane.selfcheck.run_trial", wraps=run_trial) as trial:
            for options in arguments:
                with self.subTest(options=options):
                    self.assertEqual(self.call(**options)["status"], "blocked")
        trial.assert_not_called()

    def test_catalog_exposes_permission_and_arguments(self):
        catalog = self.tools.catalog()["quality.mutate"]
        self.assertTrue(catalog["enabled"])
        self.assertEqual(set(catalog["arguments"]), {"target", "suite", "mutants", "timeout"})


if __name__ == "__main__":
    unittest.main()
