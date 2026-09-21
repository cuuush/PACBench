"""Harness MCP seam + adapter server spec: offline (no servers, no network)."""

import tempfile
import unittest
from pathlib import Path


class ProvisionMcp(unittest.TestCase):
    def test_mcp_servers_written_enabled(self):
        from pacbench import harness
        with tempfile.TemporaryDirectory(prefix="pacbench-mcp-") as tmp:
            home = Path(tmp) / "h-home"
            rec = harness.provision_home(
                home, provider_plugin_src=None, provider_name="hindsight",
                mcp_servers=[{"name": "hindsight",
                              "url": "http://127.0.0.1:8891/mcp/pacbench-t/"}])
            text = (home / "config.yaml").read_text()
            self.assertIn("hindsight:", text)
            self.assertIn("http://127.0.0.1:8891/mcp/pacbench-t/", text)
            self.assertIn("enabled", text)
            self.assertEqual(rec["mcp_servers"], ["hindsight"])

    def test_no_mcp_by_default(self):
        from pacbench import harness
        with tempfile.TemporaryDirectory(prefix="pacbench-mcp-") as tmp:
            home = Path(tmp) / "h-home"
            rec = harness.provision_home(home, provider_plugin_src=None,
                                         provider_name="memcal")
            text = (home / "config.yaml").read_text()
            self.assertNotIn("mcp_servers", text)
            self.assertEqual(rec["mcp_servers"], [])


class ServerSpec(unittest.TestCase):
    def test_url_shape_and_guards(self):
        import os
        from unittest import mock
        from provider_adapters.hindsight_adapter import HindsightAdapter
        with tempfile.TemporaryDirectory(prefix="hs-spec-") as tmp:
            adapter = HindsightAdapter(
                Path(tmp), engine_factory=lambda u, l: None,
                pg_factory=lambda n: None)
            with self.assertRaises(RuntimeError):
                adapter.mcp_url(8891)
            with self.assertRaises(RuntimeError):
                adapter.start_server(8891)
            adapter._bank = "pacbench-ns9"
            spec = adapter.mcp_server_spec(8891)
            self.assertEqual(
                spec, {"name": "hindsight",
                       "url": "http://127.0.0.1:8891/mcp/pacbench-ns9/"})
            with mock.patch.dict(os.environ,
                                 {"HINDSIGHT_API_LLM_API_KEY": "k"}):
                retrieval = adapter.retrieval()
            self.assertEqual(retrieval["mcp_tools"],
                             ["retain", "recall", "reflect"])


if __name__ == "__main__":
    unittest.main()
