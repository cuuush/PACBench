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
        self.assertIn("mcp__hindsight__recall", seam["tool_allowlist"])
        self.assertIn("tool_describe", seam["tool_allowlist"])
        self.assertIn("tool_search", seam["tool_allowlist"])
        self.assertFalse(seam["plugin_available"])
        self.assertTrue(seam["mcp_server"])


class PreflightMcp(unittest.TestCase):
    def test_mcp_seam_needs_no_plugin(self):
        import tempfile
        from pathlib import Path
        from pacbench.live import preflight
        from provider_adapters.hindsight_adapter import HindsightAdapter
        from pacbench.corpus.life_g_toyday import lives
        life = lives()[0]
        adapter = HindsightAdapter(
            Path(tempfile.mkdtemp(prefix="hs-")),
            engine_factory=lambda db_url, llm: None,
            pg_factory=lambda name: None)
        with tempfile.TemporaryDirectory() as tmp:
            problems = preflight(
                life, Path(tmp) / "h", Path(tmp) / "m", adapter)
        self.assertEqual(problems, [])


class TurnEnvClock(unittest.TestCase):
    def test_fictional_now_pins_memcal_clock(self):
        import tempfile
        from pathlib import Path
        from pacbench.harness import turn_env
        with tempfile.TemporaryDirectory() as tmp:
            env = turn_env("2026-09-10T19:00:00", Path(tmp), "allow")
        self.assertEqual(env["PACBENCH_NOW"], "2026-09-10T19:00:00")
        # memcal's documented benchmark seam: brief windows + write
        # precedence follow MEMCAL_TODAY, never the machine clock.
        self.assertEqual(env["MEMCAL_TODAY"], "2026-09-10T19:00:00")


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


class PinnedInterpreter(unittest.TestCase):
    """Hindsight server_entry: pinned interpreter, never sys.executable.

    Bare sys.executable inherits whatever launched the run (Homebrew
    3.14 lacks hindsight_api → ModuleNotFoundError mid-run); a fresh
    hardcoded literal rots on the next machine. server_entry() is the
    single audit surface: start_server launches exactly it, and the
    retrieval seam records it.
    """

    def test_entry_is_harness_pin_not_running_interpreter(self):
        from unittest import mock
        from pacbench import harness as _harness
        from provider_adapters.hindsight_adapter import HindsightAdapter
        with tempfile.TemporaryDirectory(prefix="hs-pin-") as tmp:
            adapter = HindsightAdapter(
                Path(tmp), engine_factory=lambda u, l: None,
                pg_factory=lambda n: None)
            entry = adapter.server_entry()
        self.assertEqual(entry,
                         f"{_harness.python_bin()} -m hindsight_api.mcp_local")
        self.assertIn("hindsight_api.mcp_local", entry)
        # start_server launches EXACTLY the pinned entry, whatever
        # interpreter happens to be running this test.
        adapter._engine = object()
        pg = mock.Mock()
        pg.get_uri = mock.Mock(return_value="not-a-coro")
        adapter._pg = pg
        adapter._loop = mock.Mock()
        adapter._loop.run = lambda coro: "db-url"
        adapter._llm_config = lambda: mock.Mock(
            api_key="k", provider="p", model="m", base_url="u")
        with mock.patch("subprocess.Popen") as popen, mock.patch(
                "socket.create_connection",
                side_effect=OSError("nope")), mock.patch(
                "provider_adapters.hindsight_adapter.HindsightAdapter."
                "stop_server", lambda self: None):
            popen.return_value.poll.return_value = 1
            with self.assertRaises(RuntimeError):
                adapter.start_server(8891, timeout_s=0.05)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[:2],
                         [_harness.python_bin(), "-m"])
        self.assertEqual(argv[2], "hindsight_api.mcp_local")

    def test_pin_default_and_override(self):
        import os
        from unittest import mock
        from pacbench import harness as _harness
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_harness.PYTHON_ENV_OVERRIDE, None)
            self.assertEqual(_harness.python_bin(), _harness.DEFAULT_PYTHON)
            with mock.patch.dict(os.environ,
                                 {_harness.PYTHON_ENV_OVERRIDE: "/opt/py3"}):
                self.assertEqual(_harness.python_bin(), "/opt/py3")

    def test_plugin_adapters_need_no_server(self):
        from provider_adapters.lexical_adapter import LexicalAdapter
        from provider_adapters.memcal_adapter import MemcalAdapter
        with tempfile.TemporaryDirectory(prefix="pin-") as tmp:
            self.assertEqual(LexicalAdapter(Path(tmp) / "l").server_entry(),
                             "")
        # memcal server_entry is method-level (no instance state needed):
        # construct without __init__ (which requires MEMCAL_SRC).
        self.assertEqual(MemcalAdapter.server_entry(
            object.__new__(MemcalAdapter)), "")


if __name__ == "__main__":
    unittest.main()
