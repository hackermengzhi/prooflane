import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.__main__ import main
from prooflane.agent import run_agent
from prooflane.harness import run_harness
from prooflane.store import Store
from prooflane.tools import ToolPolicy

UNITTEST_CHECK = {
    "tool": "process.run",
    "arguments": {"argv": ["$PYTHON", "-m", "unittest", "-q", "test_solution"]},
    "assertions": [{"path": ["returncode"], "equals": 0}],
}
DOUBLE_CHECK = {
    "tool": "process.run",
    "arguments": {"argv": ["$PYTHON", "-c", "from program import double; assert double(3) == 6"]},
    "assertions": [{"path": ["returncode"], "equals": 0}],
}
TEST_FILE = (
    "import unittest\nfrom solution import double\n\n\n"
    "class DoubleTest(unittest.TestCase):\n"
    "    def test_double(self):\n"
    "        self.assertEqual(double(4), 8)\n"
)


def decisions(*actions):
    return [({**action, "reason": "contract stub"}, "contract-stub") for action in actions]


def write(path, content):
    return {
        "action": "call",
        "tool": "files.write",
        "arguments": {"path": path, "content": content},
    }


def edit(path, old, new):
    return {
        "action": "call",
        "tool": "files.edit",
        "arguments": {"path": path, "old": old, "new": new},
    }


def delegate(role):
    return {"action": "delegate", "role": role, "task": f"{role} work"}


REPORT = {"action": "report", "message": "work reported"}


class ResumeTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.store = Store(str(self.root / "state.db"))
        self.policy = ToolPolicy(self.workspace, allow_process=True)


class HarnessResumeTests(ResumeTestCase):
    def harness(self, actions, max_steps, goal=None, **options):
        with patch("prooflane.harness.request_json", side_effect=decisions(*actions)) as model:
            result = run_harness(
                self.store, goal, self.policy, self.root / "runs", max_steps=max_steps, **options
            )
        return result, [call.args[1] for call in model.call_args_list]

    def test_resumed_team_keeps_checks_and_test_files_but_must_verify_again(self):
        interrupted, _ = self.harness(
            [
                delegate("product"),
                {"action": "set_checks", "checks": [UNITTEST_CHECK]},
                REPORT,
                delegate("developer"),
                write("solution.py", "def double(value):\n    return value * 2\n"),
                REPORT,
                delegate("tester"),
                write("test_solution.py", TEST_FILE),
                {"action": "verify"},
            ],
            max_steps=9,
            goal="Build and test double",
        )
        self.assertEqual(interrupted["status"], "blocked")
        self.assertEqual(interrupted["verification"][0]["status"], "passed")

        resumed, payloads = self.harness(
            [
                {"action": "finish"},
                delegate("tester"),
                edit("test_solution.py", "double(4), 8", "double(-3), -6"),
                {"action": "verify"},
                REPORT,
                {"action": "finish"},
            ],
            max_steps=6,
            resume=interrupted["task_id"],
        )
        self.assertEqual(resumed["status"], "completed", resumed["error"])
        self.assertEqual(resumed["goal"], "Build and test double")
        self.assertEqual(resumed["resumed_from"], interrupted["task_id"])
        self.assertEqual(resumed["checks_origin"], "product-proposed")
        # The earlier passing verification never counts toward the resumed delivery.
        self.assertEqual(resumed["steps"][0]["attempted"], "finish")
        self.assertEqual(resumed["steps"][2]["observation"]["status"], "succeeded")
        previous = payloads[0]["previous_session"]
        self.assertEqual(previous["task_id"], interrupted["task_id"])
        self.assertEqual(previous["error"], "Harness reached its step budget")
        self.assertEqual(previous["recent_steps"][-1]["action"], "verify")
        self.assertIn("test_solution.py", previous["tester_files"])
        self.assertEqual(payloads[0]["checks"], [UNITTEST_CHECK])
        self.assertEqual(self.store.task(interrupted["task_id"])["status"], "blocked")

    def test_tester_loses_ownership_of_a_file_changed_between_sessions(self):
        interrupted, _ = self.harness(
            [delegate("tester"), write("test_solution.py", TEST_FILE)],
            max_steps=2,
            goal="Write tests",
            checks=[UNITTEST_CHECK],
        )
        (self.workspace / "test_solution.py").write_text(TEST_FILE + "# edited by a person\n")
        resumed, payloads = self.harness(
            [delegate("tester"), edit("test_solution.py", "double(4), 8", "double(1), 2")],
            max_steps=2,
            resume=interrupted["task_id"],
        )
        observation = resumed["steps"][1]["observation"]
        self.assertEqual(observation["status"], "blocked")
        self.assertIn("Tester may only", observation["data"]["error"])
        self.assertEqual(payloads[0]["previous_session"]["tester_files"], {})

    def test_ownership_and_checks_carry_through_a_chain_of_resumptions(self):
        padding = [step for _ in range(4) for step in (delegate("product"), REPORT)]
        first, _ = self.harness(
            [delegate("tester"), write("test_solution.py", TEST_FILE), REPORT, *padding],
            max_steps=11,
            goal="Write tests",
            checks=[UNITTEST_CHECK],
        )
        second, _ = self.harness([delegate("product")], max_steps=1, resume=first["task_id"])
        third, payloads = self.harness(
            [delegate("tester"), edit("test_solution.py", "double(4), 8", "double(1), 2")],
            max_steps=2,
            resume=second["task_id"],
        )
        self.assertEqual(third["steps"][1]["observation"]["status"], "succeeded")
        self.assertEqual(third["checks_origin"], "user-provided")
        self.assertEqual(payloads[0]["checks"], [UNITTEST_CHECK])
        # The tester's write is older than the inherited window but its ownership survives.
        inherited = [step.get("tool") for step in payloads[0]["previous_session"]["recent_steps"]]
        self.assertNotIn("files.write", inherited)


class AgentResumeTests(ResumeTestCase):
    def agent(self, actions, goal=None, **options):
        with patch("prooflane.agent.request_json", side_effect=decisions(*actions)) as model:
            result = run_agent(self.store, goal, self.policy, self.root / "runs", **options)
        return result, [call.args[1] for call in model.call_args_list]

    def test_resumed_agent_continues_repair_under_its_original_checks(self):
        interrupted, _ = self.agent(
            [
                {"action": "set_checks", "checks": [DOUBLE_CHECK]},
                write("program.py", "def double(value):\n    return value + 2\n"),
            ],
            goal="Implement double",
            max_steps=2,
        )
        self.assertEqual(interrupted["status"], "blocked")
        resumed, payloads = self.agent(
            [
                {"action": "set_checks", "checks": [DOUBLE_CHECK]},
                edit("program.py", "value + 2", "value * 2"),
                {"action": "finish"},
            ],
            resume=interrupted["task_id"],
            max_steps=3,
        )
        self.assertEqual(resumed["status"], "completed", resumed["error"])
        self.assertEqual(resumed["checks_origin"], "model-proposed")
        self.assertEqual(resumed["steps"][0]["action"], "rejected")
        self.assertIn("immutable", resumed["steps"][0]["observation"]["error"])
        self.assertEqual(payloads[0]["acceptance_checks"], [DOUBLE_CHECK])
        self.assertEqual(
            [step["action"] for step in payloads[0]["previous_session"]["recent_steps"]],
            ["set_checks", "call"],
        )
        self.assertIn("续跑自任务", Path(resumed["report"]).read_text(encoding="utf-8"))

    def test_cancelled_task_resumes_and_fresh_tasks_get_no_previous_session(self):
        with patch("prooflane.agent.request_json", side_effect=KeyboardInterrupt):
            cancelled = run_agent(self.store, "Inspect", self.policy, self.root / "runs")
        self.assertEqual(cancelled["status"], "cancelled")
        resumed, payloads = self.agent(
            [{"action": "blocked", "reason": "nothing to do"}], resume=cancelled["task_id"]
        )
        self.assertEqual(resumed["resumed_from"], cancelled["task_id"])
        self.assertEqual(payloads[0]["previous_session"]["error"], "Execution cancelled by user")
        fresh, payloads = self.agent([{"action": "blocked"}], goal="Inspect")
        self.assertIsNone(fresh["resumed_from"])
        self.assertNotIn("previous_session", payloads[0])

    def test_unresumable_requests_are_refused_before_creating_a_task(self):
        blocked, _ = self.agent([{"action": "blocked"}], goal="Inspect")
        step = {**write("program.py", "def double(v):\n    return v * 2\n"), "reason": "recipe"}
        recipe = {"checks": [DOUBLE_CHECK], "steps": [step]}
        completed = run_agent(self.store, "Recipe", self.policy, self.root / "runs", recipe=recipe)
        self.assertEqual(completed["status"], "completed")
        other = self.root / "other"
        other.mkdir()
        refusals = [
            ("Unknown task", {"resume": "missing"}),
            ("not completed", {"resume": completed["task_id"]}),
            ("original goal", {"resume": blocked["task_id"], "goal": "Another goal"}),
            ("Recipes", {"resume": blocked["task_id"], "recipe": recipe}),
            ("this engine and workspace", {"resume": blocked["task_id"], "workspace": other}),
        ]
        before = len(self.store.tasks())
        for message, options in refusals:
            with self.subTest(message):
                policy = ToolPolicy(options.pop("workspace", self.workspace))
                with self.assertRaisesRegex(ValueError, message):
                    run_agent(
                        self.store, options.pop("goal", None), policy, self.root / "runs", **options
                    )
        self.assertEqual(len(self.store.tasks()), before)

    def test_supplied_checks_cannot_replace_inherited_ones(self):
        interrupted, _ = self.agent([{"action": "blocked"}], goal="Inspect", checks=[DOUBLE_CHECK])
        with self.assertRaisesRegex(ValueError, "immutable"):
            run_agent(
                self.store,
                None,
                self.policy,
                self.root / "runs",
                checks=[UNITTEST_CHECK],
                resume=interrupted["task_id"],
            )

    def test_command_line_reports_unresumable_task_as_usage_error(self):
        argv = ["prooflane", "--db", self.store.path, "agent", "--workspace", str(self.workspace)]
        stderr = io.StringIO()
        for extra, message in [([], "requires a goal"), (["--resume", "missing"], "Unknown task")]:
            with self.subTest(message), patch.object(sys, "argv", argv + extra):
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                    main()
                self.assertEqual(raised.exception.code, 2)
                self.assertIn(message, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
