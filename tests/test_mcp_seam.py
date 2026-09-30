"""Harness MCP seam + adapter server spec: offline (no servers, no network)."""

import tempfile
import unittest
from pathlib import Path


class ServerLifecycle(unittest.TestCase):
    def test_free_port_is_loopback_ephemeral(self):
        from pacbench.semantic_experiment import _free_loopback_port
        port = _free_loopback_port()
        self.assertGreater(port, 0)
        self.assertLessEqual(port, 65535)

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
        self.assertIn("tool_call", seam["tool_allowlist"])
        self.assertFalse(seam["plugin_available"])
        self.assertTrue(seam["mcp_server"])


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


class FullToolTrace(unittest.TestCase):
    def test_recovers_exact_hermes_tool_message_after_stream_cap(self):
        import sqlite3
        from pacbench.harness import TurnResult, _recover_tool_results

        with tempfile.TemporaryDirectory(prefix="pacbench-trace-") as tmp:
            db_path = Path(tmp) / "state.db"
            with sqlite3.connect(db_path) as db:
                db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, "
                           "session_id TEXT, role TEXT, tool_name TEXT, content TEXT)")
                db.execute("INSERT INTO messages VALUES (1, 'session-1', 'tool', "
                           "'mcp__hindsight__recall', ?)", ("full result " + "x" * 6000,))
            turn = TurnResult("q", "2026-09-08T08:00:00-04:00", "a", [],
                              [{"tool": "mcp__hindsight__recall", "text": "p" * 5000 + "...",
                                "truncated": False}], 0, 1.0, 0, "", 1,
                              session_id="session-1")
            _recover_tool_results(turn, db_path)
            self.assertEqual(turn.tool_results[0]["text"], "full result " + "x" * 6000)
            self.assertEqual(turn.tool_results[0]["stream_preview"], "p" * 5000 + "...")
            self.assertEqual(turn.tool_results[0]["capture"], "hermes_session_db")


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
            self.assertTrue({"retain", "recall", "reflect", "list_memories",
                             "get_memory", "get_document"}.issubset(
                                 retrieval["mcp_tools"]))
            self.assertNotIn("delete_bank", retrieval["mcp_tools"])


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

    def test_memcal_has_no_server_entry(self):
        from provider_adapters.memcal_adapter import MemcalAdapter
        self.assertEqual(MemcalAdapter.server_entry(
            object.__new__(MemcalAdapter)), "")


if __name__ == "__main__":
    unittest.main()
