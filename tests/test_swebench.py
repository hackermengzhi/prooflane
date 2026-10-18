import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prooflane.__main__ import main
from prooflane.swebench import (
    generate_prediction,
    load_instances,
    prepare_instance,
    run_swebench,
    write_predictions,
)
from prooflane.tools import MAX_FILE_BYTES, MAX_OUTPUT_BYTES, Tools

TEST_PATCH = """diff --git a/test_module.py b/test_module.py
--- a/test_module.py
+++ b/test_module.py
@@ -1 +1,2 @@
 # Existing tests
+# Supplied regression test
diff --git a/test_new.py b/test_new.py
new file mode 100644
--- /dev/null
+++ b/test_new.py
@@ -0,0 +1 @@
+# New supplied regression test
"""


class SwebenchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture = self.root / "fixture"
        self.fixture.mkdir()
        self.git(self.fixture, "init", "-q")
        (self.fixture / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.fixture / "obsolete.py").write_text("# Unused code\n", encoding="utf-8")
        (self.fixture / "test_module.py").write_text("# Existing tests\n", encoding="utf-8")
        self.git(self.fixture, "add", ".")
        self.git(
            self.fixture,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "-c",
            f"core.hooksPath={os.devnull}",
            "commit",
            "-qm",
            "Fixture base",
        )
        self.instance = {
            "instance_id": "fixture__project-1",
            "repo": str(self.fixture),
            "base_commit": self.git(self.fixture, "rev-parse", "HEAD").strip(),
            "problem_statement": "Change VALUE to 2. 修复取值。",
            "FAIL_TO_PASS": '["test_value"]',
            "PASS_TO_PASS": "[]",
            "test_patch": TEST_PATCH,
            "version": "1.0",
        }

    def git(self, workspace, *arguments, input=None):
        return subprocess.run(
            ["git", *arguments],
            cwd=workspace,
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=10,
        ).stdout

    def prepare(self, instance, workdir):
        workspace = workdir / instance["instance_id"]
        self.git(
            workdir, "clone", "--quiet", "--no-local", "--depth=1", instance["repo"], str(workspace)
        )
        self.git(workspace, "checkout", "--quiet", "--detach", instance["base_commit"])
        self.git(workspace, "apply", "-", input=instance["test_patch"])
        return workspace

    def fix(self, store, goal, policy, output):
        self.assertIn(self.instance["problem_statement"], goal)
        self.assertIn("Do not execute", goal)
        self.assertFalse(policy.allow_process)
        self.assertEqual(policy.allowed_origins, ())
        self.assertTrue(Path(store.path).is_relative_to(policy.workspace))
        self.assertTrue(output.is_relative_to(policy.workspace))
        (policy.workspace / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
        return {"status": "completed", "error": None}

    def workspace(self):
        workdir = self.root / "workspaces"
        workdir.mkdir()
        return self.prepare(self.instance, workdir)

    def test_prediction_contains_only_agent_changes_and_applies_to_base(self):
        workspace = self.workspace()

        def run(*arguments):
            result = self.fix(*arguments)
            (workspace / "new file.py").write_text("NEW_VALUE = 2\n", encoding="utf-8")
            (workspace / "obsolete.py").unlink()
            (workspace / "test_module.py").write_text("# Agent changed supplied tests\n")
            (workspace / "test_new.py").write_text("# Agent changed new supplied tests\n")
            return result

        prediction = generate_prediction(self.instance, workspace, run=run)
        self.assertEqual(set(prediction), {"instance_id", "model_name_or_path", "model_patch"})
        self.assertEqual(prediction["instance_id"], self.instance["instance_id"])
        self.assertEqual(prediction["model_name_or_path"], "prooflane-agent")
        model_patch = prediction["model_patch"]
        for expected in [
            "diff --git",
            "-VALUE = 1",
            "+VALUE = 2",
            "+NEW_VALUE = 2",
            "deleted file mode",
        ]:
            self.assertIn(expected, model_patch)
        for excluded in ["test_module.py", "test_new.py", ".prooflane", "state.db"]:
            self.assertNotIn(excluded, model_patch)
        # Applying the prediction to the untouched base checks its actual unified diff format.
        self.git(self.fixture, "apply", "--check", "-", input=model_patch)
        self.git(self.fixture, "apply", "-", input=model_patch)
        self.assertEqual((self.fixture / "module.py").read_text(), "VALUE = 2\n")
        self.assertEqual((self.fixture / "new file.py").read_text(), "NEW_VALUE = 2\n")
        self.assertFalse((self.fixture / "obsolete.py").exists())
        self.assertEqual((self.fixture / "test_module.py").read_text(), "# Existing tests\n")
        self.assertFalse((self.fixture / "test_new.py").exists())

    def test_no_agent_edits_produce_an_empty_prediction(self):
        prediction = generate_prediction(
            self.instance, self.workspace(), run=lambda *args: {"status": "completed"}
        )
        self.assertEqual(prediction["model_patch"], "")

    def test_prediction_preserves_crlf_content(self):
        workspace = self.workspace()

        def run(*arguments):
            (workspace / "module.py").write_bytes(b"VALUE = 2\r\n")
            return {"status": "completed"}

        prediction = generate_prediction(self.instance, workspace, run=run)
        self.assertIn("+VALUE = 2\r\n", prediction["model_patch"])
        self.git(self.fixture, "apply", "-", input=prediction["model_patch"])
        self.assertEqual((self.fixture / "module.py").read_bytes(), b"VALUE = 2\r\n")

    def test_existing_agent_loop_can_generate_a_patch_with_static_checks(self):
        workspace = self.workspace()
        decisions = [
            {
                "action": "set_checks",
                "checks": [
                    {
                        "tool": "code.inspect",
                        "arguments": {"path": "module.py"},
                        "assertions": [{"path": ["syntax_valid"], "equals": True}],
                    }
                ],
            },
            {
                "action": "call",
                "tool": "files.edit",
                "arguments": {"path": "module.py", "old": "VALUE = 1", "new": "VALUE = 2"},
            },
            {"action": "finish"},
        ]
        with patch(
            "prooflane.agent.request_json", side_effect=[(item, "stub") for item in decisions]
        ):
            prediction = generate_prediction(self.instance, workspace)
        self.assertIn("+VALUE = 2", prediction["model_patch"])
        summary_path = next((workspace / ".prooflane/swebench/runs").glob("*/summary.json"))
        summary = json.loads(summary_path.read_text())
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["verification"][0]["status"], "passed")
        self.assertFalse(summary["capabilities"]["process.run"]["enabled"])

    def test_agent_policy_keeps_process_and_file_access_restricted(self):
        workspace = self.workspace()
        outside = self.root / "outside.py"
        outside.write_text("SECRET = 1\n")
        (workspace / "escape.py").symlink_to(outside)
        (workspace / "large.py").write_text("x" * (MAX_FILE_BYTES + 1))

        def run(store, goal, policy, output):
            tools = Tools(policy, output / "evidence")
            for tool, arguments in [
                ("process.run", {"argv": [sys.executable, "-c", "raise AssertionError"]}),
                ("files.read", {"path": "../outside.py"}),
                ("files.read", {"path": str(outside)}),
                ("files.read", {"path": "escape.py"}),
                ("files.read", {"path": "large.py"}),
            ]:
                with self.subTest(tool=tool, arguments=arguments):
                    self.assertEqual(tools.call(tool, arguments)["status"], "blocked")
            return self.fix(store, goal, policy, output)

        # These files predate the Agent and must not appear in its prediction.
        prediction = generate_prediction(self.instance, workspace, run=run)
        self.assertIn("+VALUE = 2", prediction["model_patch"])
        self.assertNotIn("SECRET", prediction["model_patch"])

    def test_export_rejects_new_symlinks_that_escape_workspace(self):
        workspace = self.workspace()
        outside = self.root / "secret.txt"
        outside.write_text("secret")

        def run(*arguments):
            (workspace / "escape.txt").symlink_to(outside)
            return {"status": "completed"}

        with self.assertRaisesRegex(ValueError, "escapes"):
            generate_prediction(self.instance, workspace, run=run)

    def test_export_rejects_oversized_files_and_patches(self):
        workspace = self.workspace()

        def large_file(*arguments):
            (workspace / "module.py").write_text("x" * (MAX_FILE_BYTES + 1))
            return {"status": "completed"}

        with self.assertRaisesRegex(ValueError, "text context limit"):
            generate_prediction(self.instance, workspace, run=large_file)
        workdir = self.root / "large-patch"
        workdir.mkdir()
        workspace = self.prepare(self.instance, workdir)

        def large_patch(*arguments):
            for index in range(MAX_OUTPUT_BYTES // MAX_FILE_BYTES + 1):
                (workspace / f"large-{index}.txt").write_text("x" * MAX_FILE_BYTES)
            return {"status": "completed"}

        with self.assertRaisesRegex(ValueError, "output limit"):
            generate_prediction(self.instance, workspace, run=large_patch)

    def test_jsonl_round_trip_preserves_unicode_and_multiline_patch(self):
        instances_path = self.root / "instances.jsonl"
        write_predictions(
            [
                {
                    "instance_id": "fixture-1",
                    "model_name_or_path": "prooflane-agent",
                    "model_patch": "diff --git a/值.py b/值.py\n-VALUE = 1\n+VALUE = 2\n",
                }
            ],
            instances_path,
        )
        rows = load_instances(instances_path)
        output = self.root / "nested/predictions.jsonl"
        write_predictions(rows, output)
        self.assertEqual(load_instances(output), rows)
        self.assertEqual(len(output.read_text().splitlines()), 1)
        instances_path.write_text("\n" + json.dumps(self.instance, ensure_ascii=False) + "\n\n")
        self.assertEqual(load_instances(instances_path), [self.instance])
        write_predictions([], output)
        self.assertEqual(output.read_text(), "")

    def test_load_reports_invalid_json_and_non_object_lines(self):
        path = self.root / "bad.jsonl"
        for line in ["{broken", "[]", "null"]:
            with self.subTest(line=line):
                path.write_text("\n" + line + "\n")
                with self.assertRaisesRegex(ValueError, "line 2"):
                    load_instances(path)

    def test_failed_preparation_and_agent_failure_do_not_stop_later_instances(self):
        instances = [
            {**self.instance, "instance_id": f"fixture__project-{index}"} for index in range(3)
        ]

        def prepare(instance, workdir):
            if instance["instance_id"].endswith("-0"):
                raise RuntimeError("fixture preparation failed")
            return self.prepare(instance, workdir)

        def run(*arguments):
            if arguments[2].workspace.name.endswith("-1"):
                return {"status": "blocked", "error": "fixture model unavailable"}
            return self.fix(*arguments)

        output = self.root / "workspaces"
        predictions = self.root / "predictions.jsonl"
        result = run_swebench(instances, output, predictions, prepare=prepare, run=run)
        self.assertEqual((result["predictions"], result["failed"]), (1, 2))
        self.assertEqual(load_instances(predictions)[0]["instance_id"], "fixture__project-2")
        errors = json.loads((output / "errors.json").read_text())
        self.assertEqual(
            [row["instance_id"] for row in errors], ["fixture__project-0", "fixture__project-1"]
        )
        self.assertIn("preparation failed", errors[0]["error"])
        self.assertIn("model unavailable", errors[1]["error"])

    def test_workspace_names_and_injected_workspace_cannot_escape_output(self):
        output = self.root / "workspaces"
        for identifier in ["../escape", str(self.root / "absolute"), "a/b", ".."]:
            with (
                self.subTest(identifier=identifier),
                patch("prooflane.swebench.subprocess.run") as git,
            ):
                instance = {**self.instance, "instance_id": identifier}
                with self.assertRaisesRegex(ValueError, "directory name"):
                    prepare_instance(instance, output)
                git.assert_not_called()
        result = run_swebench(
            [self.instance],
            output,
            self.root / "predictions.jsonl",
            prepare=lambda *args: self.fixture,
            run=self.fix,
        )
        self.assertEqual(result["failed"], 1)
        self.assertIn("instance directory", (output / "errors.json").read_text())

    def test_production_preparation_is_shallow_and_fetches_historical_base(self):
        instance = {**self.instance, "repo": "owner/project"}
        (self.fixture / "obsolete.py").write_text("# Later revision\n")
        self.git(
            self.fixture,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "-c",
            f"core.hooksPath={os.devnull}",
            "commit",
            "-qam",
            "Later revision",
        )
        run = subprocess.run

        def local_git(argv, **kwargs):
            # Redirect only the transport; exercise the production preparation against real Git.
            argv = [
                self.fixture.as_uri() if arg == "https://github.com/owner/project.git" else arg
                for arg in argv
            ]
            return run(argv, **kwargs)

        with patch("prooflane.swebench.subprocess.run", side_effect=local_git):
            workspace = prepare_instance(instance, self.root / "workspaces")
        self.assertEqual(workspace, self.root / "workspaces" / instance["instance_id"])
        self.assertEqual(self.git(workspace, "rev-parse", "HEAD").strip(), instance["base_commit"])
        self.assertEqual(
            self.git(workspace, "rev-parse", "--is-shallow-repository").strip(), "true"
        )
        self.assertEqual((workspace / "obsolete.py").read_text(), "# Unused code\n")
        self.assertIn("Supplied regression test", (workspace / "test_module.py").read_text())
        self.assertTrue((workspace / "test_new.py").is_file())

    def test_cli_limit_output_and_failure_exit_status(self):
        instances_path = self.root / "instances.jsonl"
        instances = [self.instance, {**self.instance, "instance_id": "fixture__project-2"}]
        instances_path.write_text("".join(json.dumps(row) + "\n" for row in instances))
        output, predictions = self.root / "workspaces", self.root / "predictions.jsonl"

        def run(instances, workdir, out):
            return run_swebench(instances, workdir, out, prepare=self.prepare, run=self.fix)

        argv = [
            "prooflane",
            "swebench",
            "--instances",
            str(instances_path),
            "--out",
            str(predictions),
            "--output",
            str(output),
            "--limit",
            "1",
        ]
        stdout = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch("prooflane.__main__.run_swebench", side_effect=run),
        ):
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(), 0)
        self.assertEqual(json.loads(stdout.getvalue())["predictions"], 1)
        self.assertEqual(len(load_instances(predictions)), 1)
        self.assertFalse((output / "fixture__project-2").exists())
        # Reusing a workspace is a per-instance failure, never a destructive reset.
        with (
            patch.object(sys, "argv", argv),
            patch("prooflane.__main__.run_swebench", side_effect=run),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 2)
        errors = json.loads((output / "errors.json").read_text())
        self.assertEqual(errors[0]["instance_id"], self.instance["instance_id"])
        self.assertTrue(errors[0]["error"])
        self.assertEqual(
            (output / self.instance["instance_id"] / "module.py").read_text(), "VALUE = 2\n"
        )

    def test_cli_rejects_invalid_limit_and_missing_instances(self):
        for extra, message in [
            (["--limit", "0"], "positive"),
            (["--instances", str(self.root / "missing.jsonl")], "No such file"),
        ]:
            stderr = io.StringIO()
            argv = ["prooflane", "swebench", "--out", str(self.root / "out.jsonl"), *extra]
            with self.subTest(extra=extra), patch.object(sys, "argv", argv):
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                self.assertIn(message, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
