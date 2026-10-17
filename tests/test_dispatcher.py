import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.artifacts import digest
from prooflane.demo import service
from prooflane.dispatcher import choose_action, run_task
from prooflane.store import Store

REQUIREMENTS = Path(service.__file__).with_name("requirements.md")


def performed(result):
    return [decision["stage"] for decision in result["decisions"] if decision["action"] == "run"]


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.store = Store(str(self.directory / "state.db"))

    def run_goal(self, goal, **options):
        return run_task(self.store, goal, self.directory / "runs", **options)

    def test_review_only_does_not_start_service_or_claim_full_loop(self):
        result = self.run_goal("只做需求审查", requirements=REQUIREMENTS)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["closed_loop"])
        self.assertEqual(performed(result), ["review"])
        self.assertTrue(result["review"])
        self.assertFalse((Path(result["directory"]) / "baseline").exists())

    def test_missing_inputs_and_conflicting_stage_bounds_block(self):
        result = self.run_goal("调查失败原因", entry="investigate")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Requirements missing", result["error"])
        result = self.run_goal(
            "需求审查", requirements=REQUIREMENTS, entry="regress", target="review"
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(performed(result), [])

    def test_late_entry_backfills_prerequisites_and_stops_at_requested_target(self):
        result = self.run_goal(
            "执行测试",
            requirements=REQUIREMENTS,
            entry="execute",
            target="execute",
            browser_enabled=False,
        )
        self.assertEqual(performed(result), ["review", "design", "execute"])
        self.assertEqual(result["regression"], [])
        self.assertEqual(result["status"], "blocked")

    def test_reuse_plan_skips_review_and_design_without_changing_source(self):
        prior = self.run_goal("生成测试计划", requirements=REQUIREMENTS)
        source = Path(prior["directory"])
        original_manifest = digest(source / "manifest.json")
        result = self.run_goal("执行测试", prior_run=source, entry="execute", browser_enabled=False)
        self.assertEqual(performed(result), ["execute"])
        self.assertEqual(set(result["reused_stages"]), {"review", "design"})
        self.assertEqual(digest(source / "manifest.json"), original_manifest)

    def test_investigation_reuses_failures_without_rerunning_tools(self):
        prior = self.run_goal("执行测试", requirements=REQUIREMENTS, browser_enabled=False)
        result = self.run_goal(
            "调查失败原因", prior_run=Path(prior["directory"]), entry="investigate"
        )
        self.assertEqual(performed(result), ["investigate"])
        self.assertTrue(result["defects"])
        self.assertEqual(result["regression"], [])

    def test_changed_evidence_is_rejected_before_execution(self):
        prior = self.run_goal("生成测试计划", requirements=REQUIREMENTS)
        source = Path(prior["directory"])
        (source / "plan.json").write_text("{}")
        result = self.run_goal("执行测试", prior_run=source)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("integrity check failed", result["error"])
        self.assertEqual(performed(result), [])

    def test_changed_source_invalidates_execution_but_keeps_plan(self):
        prior = self.run_goal("执行测试", requirements=REQUIREMENTS, browser_enabled=False)
        from prooflane.reuse import import_prior, verify_run

        source = Path(prior["directory"])
        verified = verify_run(source)
        summary = {"reuse_notes": []}
        with (
            patch("prooflane.reuse.EXECUTION_SOURCES", {"dispatcher.py"}),
            patch(
                "prooflane.reuse.digest",
                side_effect=lambda path: (
                    "changed-code" if path.name == "dispatcher.py" else digest(path)
                ),
            ),
        ):
            completed = import_prior(
                source, self.directory / "imported", summary, source / "requirements.md", verified
            )
        self.assertEqual(completed, {"review", "design"})

    def test_model_cannot_skip_prerequisites_or_complete_early(self):
        with patch(
            "prooflane.dispatcher.request_json",
            return_value=({"action": "complete", "stage": None, "reason": "pretend done"}, "stub"),
        ):
            with self.assertRaisesRegex(ValueError, "premature completion"):
                choose_action(
                    "完整闭环", "regress", set(), None, "llm", {"decisions": [], "baseline": []}
                )

    def test_model_can_request_later_stage_and_runtime_resolves_prerequisite(self):
        with patch(
            "prooflane.dispatcher.request_json",
            return_value=(
                {
                    "action": "run",
                    "stage": "investigate",
                    "reason": "investigate the reported payment symptom",
                },
                "stub",
            ),
        ):
            decision = choose_action(
                "调查失败原因", "investigate", set(), None, "llm", {"decisions": [], "baseline": []}
            )
        self.assertEqual(decision["requested_stage"], "investigate")
        self.assertEqual(decision["stage"], "review")

    def test_model_cannot_shrink_explicit_full_loop_goal(self):
        with patch(
            "prooflane.dispatcher.request_json",
            return_value=({"target": "review", "reason": "do less work"}, "stub"),
        ):
            result = self.run_goal("完成完整闭环", requirements=REQUIREMENTS, scheduler="llm")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("cannot narrow", result["error"])

    def test_model_scheduler_missing_configuration_does_not_fall_back_to_rules(self):
        with patch.dict(os.environ, {}, clear=True):
            result = self.run_goal("需求审查", requirements=REQUIREMENTS, scheduler="llm")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(performed(result), [])
        self.assertIn("PROOFLANE_LLM_URL", result["error"])

    def test_model_routes_goal_and_records_validated_decisions_using_stub(self):
        responses = [
            ({"target": "review", "reason": "review only"}, "stub"),
            ({"action": "run", "stage": "review", "reason": "needs requirements review"}, "stub"),
            ({"action": "complete", "stage": None, "reason": "review exists"}, "stub"),
        ]
        with patch("prooflane.dispatcher.request_json", side_effect=responses):
            result = self.run_goal(
                "Evaluate whether these acceptance criteria are clear",
                requirements=REQUIREMENTS,
                scheduler="llm",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(performed(result), ["review"])
        self.assertEqual(result["intent"]["model"], "stub")
        self.assertTrue(all(item["model"] == "stub" for item in result["decisions"]))

    @unittest.skipUnless(
        os.environ.get("PROOFLANE_TEST_BROWSER") == "1", "Browser integration opt-in"
    )
    def test_regression_entry_reuses_baseline_but_reexecutes_fixed_revision(self):
        channel = os.environ.get("PROOFLANE_BROWSER_CHANNEL")
        prior = self.run_goal("执行测试", requirements=REQUIREMENTS, channel=channel)
        self.assertEqual(prior["status"], "failed")
        result = self.run_goal(
            "修复后回归", prior_run=Path(prior["directory"]), entry="regress", channel=channel
        )
        self.assertEqual(performed(result), ["regress"])
        self.assertTrue(result["closed_loop"], result)
        self.assertEqual(len(result["regression"]), 10)
