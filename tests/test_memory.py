import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import prooflane
from prooflane.agent import run_agent
from prooflane.evaluation import run_evaluation
from prooflane.harness import run_harness
from prooflane.memory import HISTORY_LIMIT, fingerprint, history_view, workspace_changes
from prooflane.orphans import RECORD_NAME
from prooflane.resume import run_sessions
from prooflane.store import Store
from prooflane.tools import ToolPolicy

CHECK = {
    "tool": "process.run",
    "arguments": {"argv": ["$PYTHON", "-c", "import program"]},
    "assertions": [{"path": ["returncode"], "equals": 0}],
}


def decisions(*actions):
    return [({**action, "reason": "contract stub"}, "contract-stub") for action in actions]


def call(tool, **arguments):
    return {"action": "call", "tool": tool, "arguments": arguments}


def note(kind, statement, **options):
    return call("notes.record", kind=kind, statement=statement, **options)


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        (self.workspace / "program.py").write_text("VALUE = 1\n")
        self.store = Store(str(self.root / "state.db"))

    def agent(self, actions, goal="Investigate", workspace=None, **options):
        policy = ToolPolicy(workspace or self.workspace, allow_process=True)
        with patch("prooflane.agent.request_json", side_effect=decisions(*actions)) as model:
            result = run_agent(self.store, goal, policy, self.root / "runs", **options)
        return result, [entry.args[1] for entry in model.call_args_list]


class NotesTests(MemoryTestCase):
    def test_findings_reach_later_tasks_on_the_same_workspace_only(self):
        first, _ = self.agent(
            [
                call("files.read", path="program.py"),
                note("fact", "program.py defines VALUE = 1", step=0, files=["program.py"]),
                note("hypothesis", "VALUE may need to be 2"),
                {"action": "blocked"},
            ]
        )
        recorded = [step["observation"] for step in first["steps"][1:3]]
        self.assertEqual([item["status"] for item in recorded], ["succeeded", "succeeded"])
        self.assertEqual(recorded[0]["data"]["evidence"]["tool"], "files.read")

        _, payloads = self.agent([{"action": "blocked"}], goal="A different goal")
        memory = payloads[0]["memory"]
        self.assertEqual([item["kind"] for item in memory["notes"]], ["fact", "hypothesis"])
        self.assertFalse(memory["notes"][0]["this_task"])
        self.assertFalse(memory["notes"][0]["stale"])

        (self.workspace / "program.py").write_text("VALUE = 2\n")
        _, payloads = self.agent([{"action": "blocked"}])
        self.assertTrue(payloads[0]["memory"]["notes"][0]["stale"])

        other = self.root / "other"
        other.mkdir()
        _, payloads = self.agent([{"action": "blocked"}], workspace=other)
        self.assertEqual(payloads[0]["memory"], {"notes": [], "omitted": 0})

    def test_observed_kinds_must_cite_a_real_observation(self):
        result, _ = self.agent(
            [
                note("defect", "VALUE is wrong"),
                note("fact", "cites the future", step=5),
                call("files.read", path="program.py"),
                note("hypothesis", "first guess"),
                note("fact", "cites a note", step=3),
                {"action": "blocked"},
            ]
        )
        errors = [step["observation"]["data"].get("error", "") for step in result["steps"][:5]]
        self.assertIn("Cite the number", errors[0])
        self.assertIn("Cite the number", errors[1])
        self.assertIn("tool observation", errors[4])
        self.assertEqual(result["steps"][3]["observation"]["status"], "succeeded")

    def test_replacing_a_note_supersedes_it_and_search_finds_active_notes(self):
        result, payloads = self.agent(
            [
                note("hypothesis", "timeout comes from the retry loop"),
                {"action": "blocked"},
            ]
        )
        first_id = result["steps"][0]["observation"]["data"]["note"]
        result, payloads = self.agent(
            [
                call("files.read", path="program.py"),
                note("ruled_out", "retry loop is absent", step=0, replaces=first_id),
                note("todo", "try again", replaces=first_id),
                call("notes.search", query="RETRY absent"),
                {"action": "blocked"},
            ]
        )
        self.assertEqual(result["steps"][1]["observation"]["status"], "succeeded")
        self.assertIn("active note", result["steps"][2]["observation"]["data"]["error"])
        search = result["steps"][3]["observation"]["data"]
        self.assertEqual(search["matches"], 1)
        self.assertEqual(search["notes"][0]["kind"], "ruled_out")
        self.assertEqual([item["kind"] for item in payloads[-1]["memory"]["notes"]], ["ruled_out"])

    def test_memory_tools_cannot_serve_as_acceptance_checks(self):
        check = {
            "tool": "notes.search",
            "arguments": {"query": "x"},
            "assertions": [{"path": ["matches"], "equals": 0}],
        }
        result, _ = self.agent([{"action": "set_checks", "checks": [check]}, {"action": "blocked"}])
        self.assertIn("observation capability", result["steps"][0]["observation"]["error"])


class HistoryTests(MemoryTestCase):
    def test_older_steps_keep_an_outline_and_stay_recallable(self):
        reads = [call("files.read", path="program.py") for _ in range(7)]
        result, payloads = self.agent(
            [*reads, call("history.recall", step=0), {"action": "blocked"}]
        )
        history = payloads[7]["history"]
        self.assertEqual(history["omitted"], 0)
        self.assertEqual(
            history["steps"][0],
            {
                "step": 0,
                "action": "call",
                "tool": "files.read",
                "reason": "contract stub",
                "status": "succeeded",
                "target": "program.py",
            },
        )
        self.assertEqual([step["step"] for step in payloads[7]["recent_steps"]], [1, 2, 3, 4, 5, 6])
        recalled = result["steps"][7]["observation"]["data"]
        self.assertEqual(recalled["record"]["observation"]["data"]["content"], "VALUE = 1\n")

    def test_history_is_bounded_and_counts_omitted_steps(self):
        steps = [
            {"action": "call", "tool": "files.list", "reason": str(index)} for index in range(90)
        ]
        view = history_view(steps, 6)
        self.assertEqual(len(view["steps"]), HISTORY_LIMIT)
        self.assertEqual(view["omitted"], 90 - 6 - HISTORY_LIMIT)
        self.assertEqual(view["steps"][-1]["step"], 83)

    def test_recording_a_note_keeps_the_testers_verification_current(self):
        actions = [
            {"action": "delegate", "role": "tester", "task": "verify"},
            {"action": "verify"},
            note("fact", "acceptance passes on program.py", step=1),
            {"action": "report", "message": "verified"},
            {"action": "finish"},
        ]
        with patch("prooflane.harness.request_json", side_effect=decisions(*actions)):
            result = run_harness(
                self.store,
                "Verify",
                ToolPolicy(self.workspace, allow_process=True),
                self.root / "runs",
                checks=[CHECK],
            )
        self.assertEqual(result["status"], "completed", result["error"])
        self.assertEqual(result["steps"][2]["observation"]["data"]["evidence"]["status"], "passed")


# A session that starts a long command through process.run and is then killed with SIGKILL.
CRASHING_SESSION = """
import sys
from pathlib import Path
from prooflane.tools import ToolPolicy, Tools
tools = Tools(ToolPolicy(Path(sys.argv[1]), allow_process=True), Path(sys.argv[2]) / "tools")
tools.call("process.run", {"argv": ["sleep", "60"], "timeout": 60})
"""


def wait_until(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("Condition not reached in time")
        time.sleep(0.02)


def group_running(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


class CrashRecoveryTests(MemoryTestCase):
    def crashed_task(self, pid, host=None, evidence=None):
        task_id = self.store.create("Investigate")
        detail = {
            "engine": "llm",
            "goal": "Investigate",
            "workspace": str(self.workspace.resolve()),
            "owner": {"pid": pid, "host": host or socket.gethostname()},
        }
        if evidence:
            detail["evidence"] = str(evidence)
        self.store.transition(task_id, "active", detail)
        self.store.record_event(
            task_id, "agent.step", {"action": "call", "tool": "files.read", "reason": "look"}
        )
        return task_id

    def test_task_whose_owner_exited_is_closed_and_resumed(self):
        exited = subprocess.Popen([sys.executable, "-c", "pass"])
        exited.wait()
        task_id = self.crashed_task(exited.pid)
        result, payloads = self.agent([{"action": "blocked"}], goal=None, resume=task_id)
        self.assertEqual(result["resumed_from"], task_id)
        self.assertEqual(self.store.task(task_id)["status"], "blocked")
        previous = payloads[0]["previous_session"]
        self.assertEqual(previous["error"], "Owner process exited without closing the task")
        self.assertEqual(previous["recent_steps"][0]["tool"], "files.read")
        # The crash left no final snapshot, so changes since then are unknown.
        self.assertIsNone(previous["workspace_changes"])

    def test_resume_stops_the_process_group_a_killed_session_left_running(self):
        evidence = self.root / "crashed-run"
        environment = {**os.environ, "PYTHONPATH": str(Path(prooflane.__file__).parents[1])}
        session = subprocess.Popen(
            [sys.executable, "-c", CRASHING_SESSION, str(self.workspace), str(evidence)],
            env=environment,
        )
        record = evidence / "tools" / "001" / RECORD_NAME
        wait_until(record.exists)
        pgid = json.loads(record.read_text())["pgid"]
        self.addCleanup(lambda: group_running(pgid) and os.killpg(pgid, signal.SIGKILL))
        session.kill()
        session.wait()
        # The session never reached its cleanup, so its command is still running.
        self.assertTrue(group_running(pgid))
        task_id = self.crashed_task(session.pid, evidence=evidence)
        _, payloads = self.agent([{"action": "blocked"}], goal=None, resume=task_id)
        wait_until(lambda: not group_running(pgid))
        self.assertEqual(
            payloads[0]["previous_session"]["process_groups"],
            [{"pgid": pgid, "argv": ["sleep", "60"], "outcome": "stopped"}],
        )

    def test_resume_leaves_a_reused_process_group_id_alone(self):
        unrelated = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(unrelated.wait)
        self.addCleanup(unrelated.kill)
        evidence = self.root / "crashed-run"
        record = evidence / "tools" / "001" / RECORD_NAME
        record.parent.mkdir(parents=True)
        # Same group id as the recorded command, but the process started at another time.
        started = "Thu Jan  1 00:00:00 1970"
        record.write_text(
            json.dumps({"pgid": unrelated.pid, "argv": ["sleep"], "started": started})
        )
        exited = subprocess.Popen([sys.executable, "-c", "pass"])
        exited.wait()
        task_id = self.crashed_task(exited.pid, evidence=evidence)
        _, payloads = self.agent([{"action": "blocked"}], goal=None, resume=task_id)
        self.assertIsNone(unrelated.poll())
        self.assertEqual(
            payloads[0]["previous_session"]["process_groups"],
            [{"pgid": unrelated.pid, "argv": ["sleep"], "outcome": "not_verified"}],
        )

    def test_finished_command_leaves_no_process_group_record(self):
        self.agent([call("process.run", argv=["sleep", "0.2"]), {"action": "blocked"}])
        self.assertEqual(list((self.root / "runs").rglob(RECORD_NAME)), [])

    def test_task_with_a_possibly_live_owner_is_not_resumed(self):
        for task_id in [self.crashed_task(os.getpid()), self.crashed_task(1, "elsewhere")]:
            with self.subTest(task_id), self.assertRaisesRegex(ValueError, "still active"):
                run_agent(
                    self.store, None, ToolPolicy(self.workspace), self.root / "runs", resume=task_id
                )
            self.assertEqual(self.store.task(task_id)["status"], "active")

    def test_resume_reports_workspace_changes_since_the_last_session(self):
        interrupted, _ = self.agent([{"action": "blocked"}])
        (self.workspace / "program.py").write_text("VALUE = 2\n")
        (self.workspace / "notes.txt").write_text("added by a person\n")
        _, payloads = self.agent([{"action": "blocked"}], goal=None, resume=interrupted["task_id"])
        self.assertEqual(
            payloads[0]["previous_session"]["workspace_changes"],
            {"modified": ["program.py"], "added": ["notes.txt"], "removed": []},
        )

    def test_fingerprint_skips_tool_directories(self):
        (self.workspace / ".prooflane").mkdir()
        (self.workspace / ".prooflane" / "state.db").write_text("x")
        self.assertEqual(set(fingerprint(self.workspace)), {"program.py"})
        self.assertIsNone(workspace_changes(None, {}))


class SessionTests(MemoryTestCase):
    def sessions(self, actions, sessions, **options):
        with patch("prooflane.agent.request_json", side_effect=decisions(*actions)):
            return run_sessions(
                run_agent,
                sessions,
                self.store,
                "Implement double",
                ToolPolicy(self.workspace, allow_process=True),
                self.root / "runs",
                **options,
            )

    def test_task_advances_across_sessions_that_run_out_of_steps(self):
        check = {
            **CHECK,
            "arguments": {"argv": ["$PYTHON", "-c", "import program; assert program.VALUE == 3"]},
        }
        result = self.sessions(
            [
                call("files.read", path="program.py"),
                note("fact", "VALUE starts at 1", step=0, files=["program.py"]),
                call("files.edit", path="program.py", old="1", new="2"),
                call("files.edit", path="program.py", old="2", new="3"),
                {"action": "finish"},
            ],
            sessions=3,
            max_steps=2,
            checks=[check],
        )
        self.assertEqual(result["status"], "completed", result["error"])
        self.assertEqual(len(result["sessions"]), 3)
        self.assertEqual(result["total_steps"], 5)
        self.assertEqual(result["resumed_from"], result["sessions"][1])
        self.assertEqual(result["checks_origin"], "provided")
        statuses = [self.store.task(task_id)["status"] for task_id in result["sessions"]]
        self.assertEqual(statuses, ["blocked", "blocked", "completed"])

    def test_only_an_exhausted_step_budget_starts_another_session(self):
        result = self.sessions([{"action": "blocked"}], sessions=3)
        self.assertEqual(len(result["sessions"]), 1)
        with self.assertRaisesRegex(ValueError, "Sessions must be"):
            self.sessions([], sessions=0)

    def test_external_evaluation_refuses_split_sessions(self):
        with self.assertRaisesRegex(ValueError, "External agents"):
            run_evaluation(
                Path("evals/cases"),
                self.store,
                self.root / "eval",
                mode="external",
                agent_command="true",
                sessions=2,
            )


if __name__ == "__main__":
    unittest.main()
