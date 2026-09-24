"""MCP adapter tests without starting a stdio server."""

import tempfile
import unittest

from mcp_server import FastMCP, create_server
from store import RunStore


@unittest.skipUnless(FastMCP is not None, "mcp SDK is not installed")
class McpServerTest(unittest.TestCase):
    def test_read_only_server_exposes_tools(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as handle:
            store = RunStore(handle.name)
            store.upsert_run("r", {"status": "completed"})
            store.upsert_task("r", "a", {"status": "succeeded"})
            server = create_server(handle.name)
            self.assertIsNotNone(server)
            self.assertTrue(hasattr(server, "_tool_manager"))


if __name__ == "__main__":
    unittest.main()
