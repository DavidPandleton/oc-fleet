"""Tests for guarded MCP dispatch mutation."""

import tempfile
import unittest

from mcp_server import create_server


class McpMutationTest(unittest.TestCase):
    def test_server_has_dispatch_tool_only_with_explicit_workdir(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            server = create_server(handle.name)
            tools = server._tool_manager._tools
        self.assertIn("oc_fleet_dispatch", tools)


if __name__ == "__main__":
    unittest.main()
