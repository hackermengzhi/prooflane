import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.tools import ToolPolicy, Tools
from prooflane.trials import OUTPUT_TAIL, run_trial


class FlakyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "program.py").write_text("VALUE = 6\n", encoding="utf-8")
        (self.workspace / "test_program.py").write_text(
            "import unittest\n"
            "from program import VALUE\n\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(VALUE, 6)\n",
            encoding="utf-8",
        )
        self.suite = ["$PYTHON", "-m", "unittest", "discover", "-v"]
        self.tools = Tools(ToolPolicy(self.workspace, allow_process=True), self.root / "evidence")

    def call(self, **arguments):
        return self.tools.call("quality.flaky", {"suite": self.suite, **arguments})

    def check(self, **arguments):
        result = self.call(**arguments)
        self.assertEqual(result["status"], "succeeded", result)
        return result["data"]

    def snapshot(self):
        return {
            str(path.relative_to(self.workspace)): path.read_bytes() if path.is_file() else None
            for path in self.workspace.rglob("*")
        }

    def test_real_passing_suite_is_stable_with_default_runs(self):
        before = self.snapshot()
        result = self.check()
        self.assertEqual(result["verdict"], "stable_pass")
        self.assertEqual(result["runs"], 5)
        self.assertEqual(result["pass_count"], 5)
        self.assertEqual(result["fail_count"], 0)
        self.assertEqual(
            result["results"],
            [{"index": index, "passed": True, "returncode": 0} for index in range(1, 6)],
        )
        self.assertNotIn("first_failure_output", result)
        self.assertEqual(self.snapshot(), before)

    def test_real_failing_suite_is_stable_and_returns_bounded_output_tail(self):
        before = self.snapshot()
        result = self.check(
            runs=2,
            suite=[
                "$PYTHON",
                "-c",
                "print('discard-prefix' + 'x' * 5000 + 'failure-tail'); raise SystemExit(3)",
            ],
        )
        self.assertEqual(result["verdict"], "stable_fail")
        self.assertEqual(result["runs"], 2)
        self.assertEqual(result["pass_count"], 0)
        self.assertEqual(result["fail_count"], 2)
        self.assertEqual(
            result["results"],
            [{"index": index, "passed": False, "returncode": 3} for index in range(1, 3)],
        )
        self.assertLessEqual(len(result["first_failure_output"]), OUTPUT_TAIL)
        self.assertIn("failure-tail", result["first_failure_output"])
        self.assertNotIn("discard-prefix", result["first_failure_output"])
        self.assertEqual(self.snapshot(), before)

    def test_mixed_outcomes_report_flaky_and_only_first_failure_output(self):
        outcomes = [
            {"passed": True, "returncode": 0, "output": "success-output"},
            {"passed": False, "returncode": 1, "output": "x" * 5000 + "first-failure"},
            {"passed": True, "returncode": 0, "output": "other-success"},
            {"passed": False, "returncode": 2, "output": "later-failure"},
            {"passed": True, "returncode": 0, "output": "final-success"},
        ]
        with patch("prooflane.flaky.run_trial", side_effect=outcomes) as trial:
            result = self.check()
        self.assertEqual(result["verdict"], "flaky")
        self.assertEqual(result["runs"], 5)
        self.assertEqual(result["pass_count"], 3)
        self.assertEqual(result["fail_count"], 2)
        self.assertEqual(
            result["results"],
            [
                {"index": index, "passed": item["passed"], "returncode": item["returncode"]}
                for index, item in enumerate(outcomes, 1)
            ],
        )
        self.assertEqual(result["first_failure_output"], outcomes[1]["output"][-OUTPUT_TAIL:])
        for output in ["success-output", "other-success", "later-failure", "final-success"]:
            self.assertNotIn(output, str(result))
        self.assertEqual(trial.call_count, 5)
        for invocation in trial.call_args_list:
            self.assertEqual(invocation.args, (self.workspace.resolve(), {}, self.suite, 15))

    def test_real_timeouts_count_as_failures_with_null_returncodes(self):
        result = self.check(
            runs=2, timeout=1, suite=["$PYTHON", "-c", "import time; time.sleep(30)"]
        )
        self.assertEqual(result["verdict"], "stable_fail")
        self.assertEqual(result["pass_count"], 0)
        self.assertEqual(result["fail_count"], 2)
        self.assertEqual(result["first_failure_output"], "trial timeout")
        self.assertEqual(
            result["results"],
            [{"index": index, "passed": False, "returncode": None} for index in range(1, 3)],
        )

    def test_suite_writes_only_to_fresh_copies_and_preserves_workspace(self):
        (self.workspace / "empty").mkdir()
        before = self.snapshot()
        suite = [
            "$PYTHON",
            "-c",
            "from pathlib import Path; "
            "assert Path('program.py').read_text() == 'VALUE = 6\\n'; "
            "assert Path('test_program.py').exists(); "
            "assert Path('empty').is_dir(); "
            "assert not Path('created').exists(); "
            "Path('program.py').write_text('changed'); "
            "Path('test_program.py').unlink(); "
            "Path('empty').rmdir(); "
            "Path('created').mkdir(); "
            "Path('created/output.txt').write_text('trial only')",
        ]
        result = self.check(suite=suite, runs=3)
        self.assertEqual(result["verdict"], "stable_pass")
        self.assertEqual(result["pass_count"], 3)
        self.assertEqual(self.snapshot(), before)

    def test_disabled_process_permission_blocks_before_execution(self):
        self.tools = Tools(ToolPolicy(self.workspace), self.root / "disabled")
        with patch("prooflane.flaky.run_trial") as trial:
            result = self.call()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Process capability is disabled", result["data"]["error"])
        trial.assert_not_called()
        self.assertFalse(self.tools.catalog()["quality.flaky"]["enabled"])

    def test_malformed_arguments_are_rejected_without_execution(self):
        arguments = [
            {"suite": []},
            {"suite": "python -m unittest"},
            {"suite": ["$PYTHON", 1]},
            {"suite": None},
            *({"runs": runs} for runs in [1, 21, 2.5, "5", True, None]),
            *({"timeout": timeout} for timeout in [0, 61, 1.5, "15", True, None]),
        ]
        before = self.snapshot()
        with patch("prooflane.flaky.run_trial") as trial:
            for options in arguments:
                with self.subTest(options=options):
                    self.assertEqual(self.call(**options)["status"], "blocked")
        trial.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_run_and_timeout_boundaries_are_accepted_and_forwarded(self):
        for runs, timeout in [(2, 1), (20, 60)]:
            with self.subTest(runs=runs, timeout=timeout):
                with patch(
                    "prooflane.flaky.run_trial",
                    return_value={"passed": True, "returncode": 0, "output": ""},
                ) as trial:
                    result = self.check(runs=runs, timeout=timeout)
                self.assertEqual(result["runs"], runs)
                self.assertEqual(result["pass_count"], runs)
                self.assertEqual(trial.call_count, runs)
                for invocation in trial.call_args_list:
                    self.assertEqual(invocation.args[-1], timeout)

    def test_workspace_symlinks_are_rejected_before_copying(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("external secret", encoding="utf-8")
        for destination in [outside, outside / "secret.txt", self.workspace / "program.py"]:
            with self.subTest(destination=destination):
                link = self.workspace / "linked"
                link.symlink_to(destination)
                try:
                    with patch("prooflane.flaky.run_trial", wraps=run_trial) as trial:
                        result = self.call()
                    self.assertEqual(result["status"], "blocked")
                    self.assertNotIn("external secret", str(result))
                    trial.assert_not_called()
                finally:
                    link.unlink()

    def test_catalog_exposes_permission_and_arguments(self):
        catalog = self.tools.catalog()["quality.flaky"]
        self.assertTrue(catalog["enabled"])
        self.assertEqual(set(catalog["arguments"]), {"suite", "runs", "timeout"})
        self.assertIn("default 5", catalog["arguments"]["runs"])
        self.assertIn("default 15", catalog["arguments"]["timeout"])


if __name__ == "__main__":
    unittest.main()
