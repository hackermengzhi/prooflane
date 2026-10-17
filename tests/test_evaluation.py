import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.evaluation import grade_reproduce, prepare_workspace, run_evaluation
from prooflane.store import Store
from prooflane.trials import mutate

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases"


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(str(self.root / "state.db"))

    def evaluate(self, names, **options):
        return run_evaluation(CASES, self.store, self.root / "eval", names=names, **options)

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
