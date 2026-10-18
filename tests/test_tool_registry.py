"""Guard against the tool registry, its catalog and role whitelists drifting apart."""

import tempfile
import unittest
from pathlib import Path

from prooflane.harness import ROLE_TOOLS, RoleTools
from prooflane.tools import ToolPolicy, Tools


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def base_tools(self):
        return Tools(ToolPolicy(self.workspace, allow_process=True), self.root / "evidence")

    def test_every_handler_has_a_catalog_entry_and_vice_versa(self):
        tools = self.base_tools()
        self.assertEqual(set(tools.handlers), set(tools.catalog()))

    def test_role_whitelists_only_name_real_tools(self):
        names = set(self.base_tools().handlers)
        for role, allowed in ROLE_TOOLS.items():
            missing = allowed - names
            self.assertEqual(missing, set(), f"{role} lists unknown tools: {sorted(missing)}")

    def test_new_quality_and_analysis_tools_reach_the_relevant_roles(self):
        # Regression guard: tools were once added without wiring them to any role.
        for role in ("developer", "tester"):
            catalog = RoleTools(
                ToolPolicy(self.workspace, allow_process=True), self.root / role, role
            ).catalog()
            for name in ("quality.mutate", "quality.flaky", "env.snapshot", "artifact.identify"):
                self.assertIn(name, catalog, f"{role} cannot use {name}")


if __name__ == "__main__":
    unittest.main()
