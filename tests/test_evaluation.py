import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.evaluation import compare_runs, grade_reproduce, prepare_workspace, run_evaluation
from prooflane.store import Store
from prooflane.trials import OUTPUT_TAIL, mutate

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases"


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.runs = [
            {
                "mode": "external",
                "agent_command": "example-agent",
                "mean_score": 0.5,
                "false_completions": 2,
                "cases": [
                    {"case": "zeta", "score": 0.0, "false_completion": True},
                    {"case": "gamma", "score": 1.0, "false_completion": False},
                    {"case": "beta", "score": 0.5, "false_completion": True},
                ],
            },
            {
                "mode": "external",
                "agent_command": "example-agent",
                "mean_score": 0.25,
                "false_completions": 1,
                "cases": [
                    {"case": "gamma", "score": 0.25, "false_completion": True},
                    {"case": "beta", "score": 0.5, "false_completion": False},
                    {"case": "alpha", "score": 0.0, "false_completion": False},
                ],
            },
        ]

    def test_compare_runs_preserves_missing_cases_ties_and_summary(self):
        original = json.dumps(self.runs)
        result = compare_runs(self.runs)
        first, second = "example-agent", "example-agent (2)"
        self.assertEqual(result["labels"], [first, second])
        self.assertEqual(
            [case["case"] for case in result["cases"]], ["alpha", "beta", "gamma", "zeta"]
        )
        self.assertEqual(
            result["cases"][0]["runs"],
            {
                first: {"score": None, "false_completion": None},
                second: {"score": 0.0, "false_completion": False},
            },
        )
        self.assertEqual(
            result["cases"][-1]["runs"],
            {
                first: {"score": 0.0, "false_completion": True},
                second: {"score": None, "false_completion": None},
            },
        )
        self.assertEqual(
            result["cases"][1]["runs"],
            {
                first: {"score": 0.5, "false_completion": True},
                second: {"score": 0.5, "false_completion": False},
            },
        )
        self.assertEqual(
            result["per_case_best"],
            {"alpha": [second], "beta": [first, second], "gamma": [first], "zeta": [first]},
        )
        self.assertEqual(
            result["summary"],
            {
                first: {"mean_score": 0.5, "false_completions": 2, "case_count": 3},
                second: {"mean_score": 0.25, "false_completions": 1, "case_count": 3},
            },
        )
        self.assertEqual(json.dumps(self.runs), original)

    def test_compare_runs_uses_mode_and_handles_suffix_collisions(self):
        runs = [
            {"mode": mode, "mean_score": 0.0, "false_completions": 0, "cases": []}
            for mode in ["harness", "harness", "harness (2)"]
        ]
        result = compare_runs(runs)
        self.assertEqual(result["labels"], ["harness", "harness (2)", "harness (2) (2)"])
        self.assertEqual(result["cases"], [])
        self.assertEqual(result["per_case_best"], {})
        self.assertTrue(all(summary["case_count"] == 0 for summary in result["summary"].values()))

    def test_compare_cli_prints_table_and_raw_json_and_requires_two_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / f"run {index}.json" for index in range(2)]
            for path, run in zip(paths, self.runs):
                path.write_text(json.dumps(run), encoding="utf-8")
            database = root / "unused.db"
            base = [sys.executable, "-m", "prooflane", "--db", str(database), "eval-compare"]
            table = subprocess.run(
                [*base, *map(str, paths)], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(table.returncode, 0, table.stderr)
            for value in ["alpha", "beta", "gamma", "zeta", "example-agent", "example-agent (2)"]:
                self.assertIn(value, table.stdout)
            self.assertEqual(
                table.stdout.splitlines()[-1].split(), ["mean_score", "0.500", "0.250"]
            )
            self.assertIn("-", table.stdout.splitlines()[1])
            structured = subprocess.run(
                [*base, *map(str, paths), "--json"], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(structured.returncode, 0, structured.stderr)
            self.assertEqual(json.loads(structured.stdout), compare_runs(self.runs))
            for arguments in [[], [str(paths[0])]]:
                with self.subTest(arguments=arguments):
                    missing = subprocess.run(
                        [*base, *arguments], capture_output=True, text=True, timeout=10
                    )
                    self.assertEqual(missing.returncode, 2)
                    self.assertIn("error:", missing.stderr)
            self.assertFalse(database.exists())


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(str(self.root / "state.db"))

    def evaluate(self, names, **options):
        return run_evaluation(CASES, self.store, self.root / "eval", names=names, **options)

    def external_command(self, source, *arguments):
        script = self.root / "external agent.py"
        script.write_text(source, encoding="utf-8")
        return shlex.join([sys.executable, str(script), *map(str, arguments)])

    def test_external_solution_is_scored_and_saved(self):
        command = self.external_command(
            "import shutil, sys\nshutil.copytree(sys.argv[1], '.', dirs_exist_ok=True)\n",
            CASES / "script-logparser" / "grader" / "solution",
        )
        summary = self.evaluate(["script-logparser"], mode="external", agent_command=command)
        self.assertEqual(summary["mode"], "external")
        self.assertEqual(summary["agent_command"], command)
        self.assertIsNone(summary["usage"])
        case = summary["cases"][0]
        self.assertEqual(case["score"], 1.0, case["grade"])
        self.assertEqual(case["status"], "completed")
        self.assertFalse(case["false_completion"])
        self.assertEqual(case["steps"], 0)
        self.assertIsNone(case["usage"])
        self.assertEqual(case["external"]["returncode"], 0)
        self.assertFalse(case["external"]["timed_out"])
        self.assertGreater(case["external"]["seconds"], 0)
        directory = Path(summary["directory"])
        self.assertEqual(json.loads((directory / "evaluation.json").read_text()), summary)
        workspace = directory / "script-logparser" / "workspace"
        self.assertFalse((workspace / "hidden_test.py").exists())
        self.assertFalse((workspace / "grader").exists())

    def test_external_noop_is_false_completion(self):
        command = self.external_command("pass\n")
        summary = self.evaluate(["script-logparser"], mode="external", agent_command=command)
        case = summary["cases"][0]
        self.assertEqual(case["status"], "completed")
        self.assertLess(case["score"], 1.0)
        self.assertTrue(case["false_completion"])
        self.assertEqual(summary["false_completions"], 1)

    def test_external_nonzero_exit_is_failed_but_still_graded(self):
        command = self.external_command(
            "import sys\nprint('actual failure', file=sys.stderr)\nraise SystemExit(7)\n"
        )
        case = self.evaluate(["script-logparser"], mode="external", agent_command=command)["cases"][
            0
        ]
        self.assertEqual(case["status"], "failed")
        self.assertEqual(case["external"]["returncode"], 7)
        self.assertFalse(case["external"]["timed_out"])
        self.assertIn("actual failure", case["external"]["stderr"])
        self.assertFalse(case["false_completion"])
        self.assertEqual(len(case["grade"]["tests"]), 9)

    def test_external_reads_goal_and_workspace_without_shell_expansion(self):
        literal = "$(touch shell-expanded); * | >"
        command = self.external_command(
            """import json, os, sys
from pathlib import Path
print(json.dumps({
    'stdin': sys.stdin.read(),
    'goal': os.environ['PROOFLANE_GOAL'],
    'workspace': os.environ['PROOFLANE_WORKSPACE'],
    'cwd': str(Path.cwd()),
    'argument': sys.argv[1],
}, ensure_ascii=False))
""",
            literal,
        )
        summary = self.evaluate(["script-logparser"], mode="external", agent_command=command)
        observed = json.loads(summary["cases"][0]["external"]["stdout"])
        goal = json.loads((CASES / "script-logparser" / "case.json").read_text())["goal"]
        workspace = Path(summary["directory"]) / "script-logparser" / "workspace"
        self.assertEqual(observed["stdin"], goal)
        self.assertEqual(observed["goal"], goal)
        self.assertEqual(observed["workspace"], str(workspace))
        self.assertEqual(observed["cwd"], str(workspace))
        self.assertEqual(observed["argument"], literal)
        self.assertFalse((workspace / "shell-expanded").exists())

    def test_external_keeps_full_logs_and_bounded_tails(self):
        command = self.external_command(
            "import os\nos.write(1, b'x' * 20000 + b'OUT\\xff')\n"
            "os.write(2, b'y' * 20000 + b'ERR\\xff')\n"
        )
        case = self.evaluate(["script-logparser"], mode="external", agent_command=command)["cases"][
            0
        ]
        for name, prefix, suffix in [("stdout", b"x", b"OUT\xff"), ("stderr", b"y", b"ERR\xff")]:
            with self.subTest(stream=name):
                external = case["external"]
                path = Path(external[f"{name}_file"])
                self.assertEqual(path.name, f"{name}.txt")
                self.assertEqual(path.parent.name, "script-logparser")
                self.assertEqual(path.read_bytes(), prefix * 20000 + suffix)
                self.assertLessEqual(len(external[name]), OUTPUT_TAIL)
                self.assertTrue(external[name].endswith(suffix.decode("utf-8", errors="replace")))

    def external_with_child(self, *, exit_immediately):
        child = self.root / "child.py"
        child.write_text(
            """import time
from pathlib import Path
while True:
    with Path('heartbeat').open('ab') as stream:
        stream.write(b'.')
    time.sleep(0.01)
"""
        )
        command = self.external_command(
            """import os, subprocess, sys, time
from pathlib import Path
subprocess.Popen([sys.executable, sys.argv[1]])
while not Path('heartbeat').exists():
    time.sleep(0.01)
print(os.getpid(), flush=True)
if sys.argv[2] == 'wait':
    time.sleep(60)
""",
            child,
            "exit" if exit_immediately else "wait",
        )
        summary = self.evaluate(
            ["script-logparser"], mode="external", agent_command=command, agent_timeout=1
        )
        case = summary["cases"][0]
        pid = int(case["external"]["stdout"])

        def cleanup():
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        self.addCleanup(cleanup)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        heartbeat = Path(summary["directory"]) / "script-logparser" / "workspace" / "heartbeat"
        before = heartbeat.read_bytes()
        self.assertTrue(before)
        time.sleep(0.1)
        self.assertEqual(
            heartbeat.read_bytes(), before, "Descendant survived process-group cleanup"
        )
        return case

    def test_external_timeout_terminates_agent_and_descendants(self):
        case = self.external_with_child(exit_immediately=False)
        self.assertEqual(case["status"], "timeout")
        self.assertTrue(case["external"]["timed_out"])
        self.assertEqual(case["external"]["returncode"], -signal.SIGKILL)
        self.assertGreaterEqual(case["external"]["seconds"], 1)
        self.assertLess(case["external"]["seconds"], 5)
        self.assertFalse(case["false_completion"])
        self.assertEqual(len(case["grade"]["tests"]), 9)

    def test_external_completion_cleans_up_descendants(self):
        case = self.external_with_child(exit_immediately=True)
        self.assertEqual(case["status"], "completed")
        self.assertEqual(case["external"]["returncode"], 0)
        self.assertFalse(case["external"]["timed_out"])

    def test_external_missing_executable_is_failed_and_graded(self):
        command = shlex.quote(str(self.root / "missing agent"))
        case = self.evaluate(["script-logparser"], mode="external", agent_command=command)["cases"][
            0
        ]
        self.assertEqual(case["status"], "failed")
        self.assertIsNone(case["external"]["returncode"])
        self.assertIn("missing agent", case["error"])
        self.assertEqual(len(case["grade"]["tests"]), 9)

    def test_external_invalid_options_are_rejected_before_execution(self):
        options = [
            {},
            {"agent_command": ""},
            {"agent_command": "   "},
            {"agent_command": "'unclosed"},
            *[
                {"agent_command": "unused", "agent_timeout": value}
                for value in [0, -1, float("nan"), float("inf")]
            ],
        ]
        for option in options:
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.evaluate(["script-logparser"], mode="external", **option)
        self.assertFalse((self.root / "eval").exists())

    def test_external_cli_runs_agent_and_requires_command(self):
        base = [sys.executable, "-m", "prooflane", "--db", str(self.root / "cli.db"), "eval"]
        missing = subprocess.run([*base, "--mode", "external"], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("requires --agent-command", missing.stderr)
        command = self.external_command("print('cli agent')\n")
        completed = subprocess.run(
            [
                *base,
                "--mode",
                "external",
                "--agent-command",
                command,
                "--agent-timeout",
                "2",
                "--case",
                "script-logparser",
                "--cases",
                str(CASES),
                "--output",
                str(self.root / "cli eval"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        summary = json.loads(Path(result["details"]).read_text())
        self.assertEqual(summary["agent_command"], command)
        self.assertEqual(summary["cases"][0]["status"], "completed")
        self.assertEqual(summary["cases"][0]["external"]["stdout"], "cli agent\n")

    def grade_reproduction(self, suite=None, *, without_decoys=False):
        case_dir = CASES / "reproduce-pagination"
        grading = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))["grading"]
        if suite is None:
            suite = (case_dir / "grader" / "solution" / "test_repro.py").read_text(encoding="utf-8")
        if without_decoys:
            grading["decoys"] = []
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            workspace = prepare_workspace(case_dir, grading, Path(temporary) / "workspace")
            (workspace / "test_repro.py").write_text(suite, encoding="utf-8")
            # Grading must overlay both versions even if an agent edited the target.
            (workspace / "pager.py").write_text("raise RuntimeError('agent edit')\n")
            return grade_reproduce(case_dir, grading, workspace)

    def test_reference_solutions_earn_full_scores(self):
        names = ["api-coupon", "script-inventory", "reproduce-pagination"]
        if os.environ.get("PROOFLANE_TEST_BROWSER") == "1":
            names.append("web-signup")
        summary = self.evaluate(names, reference=True)
        for case in summary["cases"]:
            self.assertEqual(case["score"], 1.0, json.dumps(case["grade"], ensure_ascii=False))
        # The agent sees the seeded defect but never the reference or hidden tests.
        workspace = Path(summary["directory"]) / "api-coupon" / "workspace"
        self.assertIn("subtotal <= COUPON_THRESHOLD", (workspace / "service.py").read_text())
        self.assertFalse((workspace / "reference_service.py").exists())
        workspace = Path(summary["directory"]) / "reproduce-pagination" / "workspace"
        case_dir = CASES / "reproduce-pagination"
        grading = json.loads((case_dir / "case.json").read_text())["grading"]
        reference = (case_dir / "grader" / grading["reference"]).read_text()
        self.assertEqual(
            (workspace / "pager.py").read_text(), mutate(reference, [grading["defect"]])
        )
        self.assertFalse((workspace / grading["reference"]).exists())
        self.assertFalse((case_dir / "workspace" / "pager.py").exists())

    def test_focused_reproduction_earns_full_score(self):
        grade = self.grade_reproduction()
        self.assertTrue(grade["fails_before"])
        self.assertTrue(grade["passes_after"])
        self.assertIn("AssertionError", grade["defect_output"])
        self.assertIsNone(grade["reference_output"])
        self.assertEqual(len(grade["decoys"]), 3)
        self.assertTrue(all(item["passed"] for item in grade["decoys"]))
        self.assertTrue(all(item["output"] is None for item in grade["decoys"]))
        self.assertEqual(grade["decoy_failure_rate"], 0.0)
        self.assertEqual(grade["score"], 1.0)

    def test_suite_passing_on_defect_earns_no_reproduction_credit(self):
        suite = """import unittest
from pager import paginate

class PaginationTests(unittest.TestCase):
    def test_items(self):
        self.assertEqual(paginate([1, 2, 3], page_size=3).items, [1, 2, 3])
"""
        grade = self.grade_reproduction(suite)
        self.assertFalse(grade["fails_before"])
        self.assertTrue(grade["passes_after"])
        self.assertIsNone(grade["defect_output"])
        self.assertEqual(grade["score"], 0.0)

    def test_suite_rejecting_fix_earns_no_reproduction_credit(self):
        suites = [
            """import unittest
from pager import paginate

class PaginationTests(unittest.TestCase):
    def test_extra_cursor(self):
        self.assertIsNotNone(paginate([1, 2, 3], page_size=3).next_cursor)
""",
            """import unittest

class PaginationTests(unittest.TestCase):
    def test_always_fails(self):
        self.fail("unrelated failure")
""",
        ]
        for suite in suites:
            with self.subTest(suite=suite):
                grade = self.grade_reproduction(suite)
                self.assertFalse(grade["passes_after"])
                self.assertIn("AssertionError", grade["reference_output"])
                self.assertEqual(grade["score"], 0.0)

    def test_reproduction_loses_credit_for_unrelated_decoy_failures(self):
        case_dir = CASES / "reproduce-pagination"
        focused = (case_dir / "grader" / "solution" / "test_repro.py").read_text()
        # Keep the reproduction but also constrain an unrelated page-size boundary.
        broad = """
class PageSizeTests(unittest.TestCase):
    def test_page_size_limit(self):
        with self.assertRaises(ValueError):
            paginate([1], page_size=101)
"""
        grade = self.grade_reproduction(focused + broad)
        self.assertTrue(grade["fails_before"])
        self.assertTrue(grade["passes_after"])
        failed = [item for item in grade["decoys"] if not item["passed"]]
        self.assertEqual([item["id"] for item in failed], ["page-size-limit-raised"])
        self.assertIn("AssertionError", failed[0]["output"])
        self.assertAlmostEqual(grade["decoy_failure_rate"], 1 / 3)
        self.assertAlmostEqual(grade["score"], 1 - 0.5 / 3)

    def test_reproduction_without_decoys_earns_full_score(self):
        grade = self.grade_reproduction(without_decoys=True)
        self.assertEqual(grade["decoys"], [])
        self.assertEqual(grade["decoy_failure_rate"], 0.0)
        self.assertEqual(grade["score"], 1.0)

    def test_every_mutant_differs_from_its_reference(self):
        for case_file in CASES.glob("*/case.json"):
            grading = json.loads(case_file.read_text(encoding="utf-8"))["grading"]
            if grading["kind"] != "mutation":
                continue
            reference = (case_file.parent / "grader" / grading["reference"]).read_text()
            ids = {item["id"] for item in grading["mutants"]}
            self.assertLessEqual(set(grading["seeded"]), ids, case_file)
            for mutant in grading["mutants"]:
                self.assertNotEqual(mutate(reference, [mutant["replace"]]), reference, mutant)

    def test_missing_deliverables_score_zero(self):
        with patch(
            "prooflane.agent.request_json",
            return_value=({"action": "blocked", "reason": "no attempt"}, "stub"),
        ):
            summary = self.evaluate(["api-coupon", "script-inventory"], mode="agent")
        self.assertEqual([case["score"] for case in summary["cases"]], [0.0, 0.0])
        self.assertEqual(summary["false_completions"], 0)

    def test_suite_rejecting_correct_behaviour_earns_no_mutation_credit(self):
        always_failing = "import unittest\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail()\n"
        decisions = [
            {
                "action": "set_checks",
                "checks": [
                    {
                        "tool": "files.read",
                        "arguments": {"path": "findings.json"},
                        "assertions": [{"path": ["path"], "equals": "findings.json"}],
                    }
                ],
            },
            {
                "action": "call",
                "tool": "files.write",
                "arguments": {"path": "test_api.py", "content": always_failing},
            },
            {
                "action": "call",
                "tool": "files.write",
                "arguments": {"path": "findings.json", "content": '[{"requirement": "R1"}]'},
            },
            {"action": "finish"},
        ]
        with patch(
            "prooflane.agent.request_json",
            side_effect=[({**item, "reason": "stub"}, "stub") for item in decisions],
        ):
            case = self.evaluate(["api-coupon"], mode="agent")["cases"][0]
        grade = case["grade"]
        self.assertFalse(grade["reference_passed"])
        self.assertEqual(grade["kill_rate"], 1.0)
        self.assertEqual(grade["findings"]["recall"], 1 / 3)
        self.assertAlmostEqual(case["score"], grade["findings"]["f1"] / 2)
        # The agent's own checks passed, so this completion is contradicted by hidden grading.
        self.assertEqual(case["status"], "completed")
        self.assertTrue(case["false_completion"])

    def test_unknown_case_name_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(["missing-case"], reference=True)
