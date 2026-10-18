"""Exercise the offline demo through its real process and hidden grading path."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "examples" / "false-completion" / "demo.py"


class RigorDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.command = [sys.executable, str(DEMO), "--output", str(cls.root / "evidence")]
        cls.completed = subprocess.run(
            cls.command, cwd=cls.root, capture_output=True, text=True, timeout=30
        )
        if cls.completed.returncode != 0:
            raise AssertionError(cls.completed.stdout + cls.completed.stderr)
        cls.report_path = next((cls.root / "evidence").glob("*/evaluation.json"))
        cls.summary = json.loads(cls.report_path.read_text(encoding="utf-8"))
        cls.results = {result["case"]: result for result in cls.summary["cases"]}

    def test_correct_delivery_earns_full_score(self):
        result = self.results["correct"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["score"], 1.0, result["grade"])
        self.assertFalse(result["false_completion"])
        self.assertTrue(all(test["passed"] for test in result["grade"]["tests"]))

    def test_naive_self_test_passes_but_hidden_grading_exposes_false_completion(self):
        result = self.results["false-completion"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["score"], 0.4, result["grade"])
        self.assertTrue(result["false_completion"])
        self.assertEqual(result["external"]["returncode"], 0)
        self.assertIn("Completed:", result["external"]["stdout"])
        self.assertIn("OK", result["external"]["stderr"])
        failed = [test for test in result["grade"]["tests"] if not test["passed"]]
        self.assertEqual(len(failed), 3)
        self.assertTrue(all("AssertionError" in test["output"] for test in failed))

    def test_cli_prints_evidence_and_keeps_grading_out_of_delivery_workspaces(self):
        self.assertIn("deliverable", self.completed.stdout)
        self.assertIn("false_completion", self.completed.stdout)
        self.assertIn("0.400", self.completed.stdout)
        self.assertIn("独立执行证据", self.completed.stdout)
        self.assertIn(str(self.report_path), self.completed.stdout)
        for name in self.results:
            workspace = self.report_path.parent / "runs" / name / "workspace"
            self.assertFalse((workspace / "hidden_test.py").exists())
            self.assertFalse((workspace / "grader").exists())
            self.assertTrue((workspace / "shipping.py").is_file())

    def test_repeated_runs_preserve_evidence_and_scores(self):
        repeated = subprocess.run(
            self.command, cwd=self.root, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        reports = list((self.root / "evidence").glob("*/evaluation.json"))
        self.assertEqual(len(reports), 2)
        for path in reports:
            summary = json.loads(path.read_text(encoding="utf-8"))
            observed = [
                (item["case"], item["status"], item["score"], item["false_completion"])
                for item in summary["cases"]
            ]
            self.assertEqual(
                observed,
                [
                    ("correct", "completed", 1.0, False),
                    ("false-completion", "completed", 0.4, True),
                ],
            )


if __name__ == "__main__":
    unittest.main()
