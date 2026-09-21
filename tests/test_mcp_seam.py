"""Harness MCP seam + adapter server spec: offline (no servers, no network)."""

import tempfile
import unittest
from pathlib import Path


class FakeServerAdapter:
    def __init__(self):
        self.started_on = []
        self.stops = 0

    def start_server(self, port):
        self.started_on.append(port)
        return f"http://127.0.0.1:{port}/mcp/bank/"

    def mcp_server_spec(self, port):
        return {"name": "fake",
                "url": f"http://127.0.0.1:{port}/mcp/bank/"}

    def stop_server(self):
        self.stops += 1


class ServerLifecycle(unittest.TestCase):
    def test_free_port_is_loopback_ephemeral(self):
        from pacbench.live import _free_loopback_port
        port = _free_loopback_port()
        self.assertIsInstance(port, int)
        self.assertGreater(port, 0)
        self.assertLessEqual(port, 65535)

    def test_no_flag_means_no_server(self):
        from pacbench.live import _start_provider_server
        self.assertIsNone(
            _start_provider_server(FakeServerAdapter(), {}))

    def test_flag_starts_and_stop_is_idempotent(self):
        from pacbench.live import (_start_provider_server,
                                   _stop_provider_server)
        adapter = FakeServerAdapter()
        specs = _start_provider_server(adapter, {"mcp_server": True})
        self.assertIsNotNone(specs)
        assert specs is not None
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["name"], "fake")
        self.assertEqual(len(adapter.started_on), 1)
        _stop_provider_server(adapter)
        _stop_provider_server(object())  # no stop_server: no-op
        self.assertEqual(adapter.stops, 1)

    def test_flag_without_adapter_method_fails_closed(self):
        from pacbench.live import _start_provider_server
        with self.assertRaises(RuntimeError):
            _start_provider_server(object(), {"mcp_server": True})

    def test_hindsight_seam_carries_checkpoint_keys(self):
        import os
        from unittest import mock
        from provider_adapters.hindsight_adapter import HindsightAdapter
        home = Path(tempfile.mkdtemp(prefix="hs-seam-"))
        adapter = HindsightAdapter(
            home, engine_factory=lambda db_url, llm: object(),
            pg_factory=type("P", (), {}))
        seam = adapter.retrieval()
        self.assertEqual(seam["provider"], "hindsight")
        self.assertTrue(seam["tool_allowlist"])
        self.assertFalse(seam["plugin_available"])
        self.assertTrue(seam["mcp_server"])


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
