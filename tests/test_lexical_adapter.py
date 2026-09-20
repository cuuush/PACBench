"""Item 1 verification: lexical baseline adapter + plugin (offline).

Store/ingest isolation, search honesty (planted needle ranks first, negative
control returns no hits), forbidden-payload refusal, scratch-home refusal,
honest no-op maintenance, and None-tokens usage. No network, no model, no
Hermes run, no live homes.
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from provider_adapters.lexical_adapter import (  # noqa: E402
    LEXICAL_TOOL_ALLOWLIST,
    LexicalAdapter,
    check_scratch_home,
)
from pacbench.adapters import Readiness, Usage  # noqa: E402


def _load_plugin():
    """Import the plugin with a stubbed MemoryProvider base (no vendor import)."""
    stub_agent = types.ModuleType("agent")
    stub_mp = types.ModuleType("agent.memory_provider")

    class MemoryProvider:  # minimal stand-in for the vendor ABC
        pass

    stub_mp.MemoryProvider = MemoryProvider
    stub_agent.memory_provider = stub_mp
    sys.modules.setdefault("agent", stub_agent)
    sys.modules.setdefault("agent.memory_provider", stub_mp)
    path = REPO / "provider_adapters" / "lexical_plugin" / "__init__.py"
    spec = importlib.util.spec_from_file_location("lexical_plugin", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_obs(source_id, channel="email", sender="Maya",
              thread="t1", text="Dinner Saturday at 8.",
              collected_at="2026-09-08T10:00:00"):
    return {
        "source_id": source_id,
        "channel": channel,
        "written_at": "2026-09-08T09:00:00",
        "arrived_at": "2026-09-08T09:05:00",
        "collected_at": collected_at,
        "sender": sender,
        "thread": thread,
        "text": text,
        "from_owner": False,
        "structured": None,
    }


class LexicalAdapterHomes(unittest.TestCase):
    def test_scratch_home_refusals(self):
        with self.assertRaises(RuntimeError):
            check_scratch_home(Path.home() / ".memcal")
        with self.assertRaises(RuntimeError):
            check_scratch_home(Path("/etc/pacbench-lexical"))
        with self.assertRaises(RuntimeError):
            LexicalAdapter(Path.home() / ".memcal")


class LexicalAdapterCycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pacbench-lex-")
        self.home = Path(self._tmp.name) / "lexical-home"
        self.adapter = LexicalAdapter(self.home)

    def tearDown(self):
        self._tmp.cleanup()

    def test_reset_ingest_readiness_cleanup(self):
        ns = "lex-life0-rep0"
        self.adapter.reset(ns)
        self.assertTrue((self.home / "lexical.db").exists())

        obs = [
            _fake_obs("msg_1"),
            _fake_obs("msg_2", channel="whatsapp", sender="Eric",
                      thread="poker",
                      text="Poker moved to Jordan's Saturday 8pm."),
            # §31: a direct user turn — redirected, never DB-written.
            _fake_obs("turn_1", channel="hermes", sender="user",
                      thread="session-1", text="Yeah I'm still going."),
        ]
        usage = self.adapter.ingest(obs, "2026-09-08T12:00:00")
        self.assertIsInstance(usage, Usage)
        self.assertEqual(usage.model_calls, 0)
        self.assertIn("user_turns_redirected 1", usage.notes[0])

        conn = sqlite3.connect(self.home / "lexical.db")
        try:
            archived = {row[0] for row in
                        conn.execute("SELECT source_id FROM docs")}
            channels = {row[0] for row in
                        conn.execute("SELECT channel FROM docs")}
        finally:
            conn.close()
        self.assertEqual(archived, {"msg_1", "msg_2"})
        self.assertNotIn("hermes", channels)

        readiness = self.adapter.readiness()
        self.assertIsInstance(readiness, Readiness)
        self.assertTrue(readiness.ready)

        maint = self.adapter.maintenance("2026-09-08T23:59:00")
        self.assertEqual(self.adapter._maintenance["state"], "complete")
        self.assertIn("nothing scheduled", maint.notes[0])

        total = self.adapter.usage()
        self.assertEqual(total.model_calls, 0)
        self.assertIsNone(total.input_tokens)  # observed truth: no model calls
        self.assertIsNone(total.output_tokens)
        self.assertIn("no model calls", total.notes[0])

        seam = self.adapter.retrieval()
        self.assertEqual(seam["provider"], "lexical")
        self.assertTrue(seam["plugin_available"])
        self.assertEqual(seam["tool_allowlist"], list(LEXICAL_TOOL_ALLOWLIST))
        self.assertEqual(seam["env"]["LEXICAL_HOME"], str(self.home))

        with self.assertRaises(RuntimeError):
            self.adapter.cleanup("wrong-namespace")
        self.adapter.cleanup(ns)
        self.assertFalse(self.home.exists())

    def test_ingest_refuses_rubric_payload(self):
        self.adapter.reset("lex-rubric")
        bad = _fake_obs("msg_bad")
        bad["expected"] = ["Eric's game: active"]
        with self.assertRaises(RuntimeError):
            self.adapter.ingest([bad], "2026-09-08T12:00:00")
        self.adapter.cleanup("lex-rubric")

    def test_ingest_is_idempotent(self):
        self.adapter.reset("lex-idem")
        self.adapter.ingest([_fake_obs("m1")], "2026-09-08T12:00:00")
        second = self.adapter.ingest([_fake_obs("m1")], "2026-09-08T12:00:00")
        self.assertIn("archived 0/1", second.notes[0])
        self.adapter.cleanup("lex-idem")


class LexicalPluginSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = _load_plugin()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pacbench-lexplug-")
        self.home = Path(self._tmp.name) / "lexical-home"
        self.home.mkdir()
        conn = sqlite3.connect(self.home / "lexical.db")
        conn.execute(
            "CREATE TABLE docs (source_id TEXT PRIMARY KEY, channel TEXT,"
            " sender TEXT, thread TEXT, written_at TEXT, collected_at TEXT,"
            " text TEXT)")
        conn.executemany(
            "INSERT INTO docs VALUES(?,?,?,?,?,?,?)",
            [("dentist/email-orig", "email", "Bright Smile Dental", "dentist",
              "2026-09-05T09:00:00", "2026-09-05T09:05:00",
              "Reminder: your dental cleaning is Friday at 2:30pm."),
             ("poker/groupme-move", "groupme", "Jordan Vance", "poker",
              "2026-09-09T18:00:00", "2026-09-09T18:05:00",
              "moving poker to Saturday 8pm at my place, 44 Birch Ave"),
             ("noise/whatsapp-food", "whatsapp", "Priya Nair", "social",
              "2026-09-09T12:00:00", "2026-09-09T12:05:00",
              "that ramen place was amazing, we should go again")]),
        conn.commit()
        conn.close()
        self._old = os.environ.get("LEXICAL_HOME")
        os.environ["LEXICAL_HOME"] = str(self.home)
        self.provider = self.plugin.LexicalMemoryProvider()
        self.provider.initialize("sess-1")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("LEXICAL_HOME", None)
        else:
            os.environ["LEXICAL_HOME"] = self._old
        self._tmp.cleanup()

    def _call(self, tool, args):
        return json.loads(self.provider.handle_tool_call(tool, args))

    def test_needle_ranks_first(self):
        out = self._call("lexical_search",
                         {"query": "poker Saturday Birch", "limit": 5})
        self.assertTrue(out["hits"])
        self.assertEqual(out["hits"][0]["source_id"], "poker/groupme-move")
        self.assertGreater(out["hits"][0]["overlap"], 0)

    def test_negative_control_returns_no_hits(self):
        out = self._call("lexical_search",
                         {"query": "quantum zebrasxylophone", "limit": 5})
        self.assertEqual(out["hits"], [])

    def test_open_roundtrip_and_unknown_ref(self):
        out = self._call("lexical_open", {"ref": "dentist/email-orig"})
        self.assertIn("2:30pm", out["text"])
        self.assertEqual(out["sender"], "Bright Smile Dental")
        err = self._call("lexical_open", {"ref": "nope"})
        self.assertIn("error", err)

    def test_limit_is_clamped(self):
        out = self._call("lexical_search", {"query": "a", "limit": 100})
        self.assertLessEqual(len(out["hits"]), 20)

    def test_unknown_tool_is_error(self):
        out = self._call("lexical_guess", {"query": "x"})
        self.assertIn("error", out)

    def test_prefetch_counts_and_missing_db_is_empty(self):
        line = self.provider.prefetch("anything")
        self.assertIn("3 stored", line)
        os.environ["LEXICAL_HOME"] = str(Path(self._tmp.name) / "absent")
        missing = self.plugin.LexicalMemoryProvider()
        missing.initialize("s")
        self.assertEqual(missing.prefetch("q"), "")

    def test_sync_turn_persists_and_non_primary_skips(self):
        self.provider.sync_turn("do I owe Dana?", "You owe nothing.",
                                session_id="sess-1")
        conn = sqlite3.connect(self.home / "lexical.db")
        try:
            rows = conn.execute(
                "SELECT source_id, text FROM docs WHERE source_id LIKE 'turn:%'"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertIn("Dana", rows[0][1])
        # Later questions retrieve the persisted turn.
        out = self._call("lexical_search", {"query": "Dana owe"})
        self.assertTrue(any(h["source_id"].startswith("turn:") for h in out["hits"]))
        other = self.plugin.LexicalMemoryProvider()
        other.initialize("s", agent_context="cron")
        other.sync_turn("x", "y", session_id="s")
        conn = sqlite3.connect(self.home / "lexical.db")
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM docs WHERE source_id LIKE 'turn:%'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
