import shutil
import tempfile
import unittest
from pathlib import Path

from prooflane.tools import ToolPolicy, Tools


class DebugTests(unittest.TestCase):
    def test_adapter_availability_and_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "commands").write_text("version\n")
            tools = Tools(ToolPolicy(root), root / "evidence")
            self.assertEqual(tools.call("debug.capabilities", {})["status"], "succeeded")
            self.assertEqual(
                tools.call("debug.run", {"backend": "lldb", "script": "commands", "pid": 123})[
                    "status"
                ],
                "blocked",
            )

    def test_cheatengine_exports_but_does_not_claim_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "inspect.lua").write_text("print('probe')")
            tools = Tools(ToolPolicy(root, allow_process=True), root / "evidence")
            result = tools.call("debug.run", {"backend": "cheatengine", "script": "inspect.lua"})
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["data"]["execution"], "not_executed")
            self.assertEqual(Path(result["data"]["script"]).read_text(), "print('probe')")

    @unittest.skipUnless(shutil.which("lldb"), "LLDB not installed")
    def test_real_lldb_loads_executable_and_reads_symbols(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "inspect.lldb").write_text("target list\n")
            tools = Tools(ToolPolicy(root, allow_process=True), root / "evidence")
            shutil.copyfile(shutil.which("true"), root / "target")
            result = tools.call(
                "debug.run", {"backend": "lldb", "script": "inspect.lldb", "target": "target"}
            )
            self.assertEqual(result["status"], "succeeded", result)
            self.assertIn("target", result["data"]["output"])
