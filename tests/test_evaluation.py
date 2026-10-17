import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.evaluation import mutate, run_evaluation
from prooflane.store import Store

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases"


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(str(self.root / "state.db"))

    def evaluate(self, names, **options):
        return run_evaluation(CASES, self.store, self.root / "eval", names=names, **options)

    def test_reference_solutions_earn_full_scores(self):
        names = ["api-coupon", "script-inventory"]
        if os.environ.get("PROOFLANE_TEST_BROWSER") == "1":
            names.append("web-signup")
        summary = self.evaluate(names, reference=True)
        for case in summary["cases"]:
            self.assertEqual(case["score"], 1.0, json.dumps(case["grade"], ensure_ascii=False))
        # The agent sees the seeded defect but never the reference or hidden tests.
        workspace = Path(summary["directory"]) / "api-coupon" / "workspace"
        self.assertIn("subtotal <= COUPON_THRESHOLD", (workspace / "service.py").read_text())
        self.assertFalse((workspace / "reference_service.py").exists())

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
