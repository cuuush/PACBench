"""W3 verification: Memcal adapter against a scratch MEMCAL_HOME.

Reset, ingest fake public observations, readiness, retrieval seam, cleanup —
all local (sqlite + tmp dir), no model, no network, no Hermes run, no live
homes. Also pins the §31 rule: hermes-channel user turns are redirected, never
written to the store by the adapter.
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

try:
    from provider_adapters.memcal_adapter import (
        MemcalAdapter,
        USER_TURN_CHANNELS,
        check_scratch_home,
    )
    from pacbench.adapters import Readiness, Usage
    _IMPORT_ERROR = None
except Exception as exc:  # memcal checkout missing — skip, never fake a pass
    MemcalAdapter = None  # type: ignore
    _IMPORT_ERROR = exc


def _fake_obs(source_id, channel="email", sender="Maya",
              thread="t1", text="Dinner Saturday at 8.",
              from_owner=False, collected_at="2026-09-08T10:00:00"):
    return {
        "source_id": source_id,
        "channel": channel,
        "written_at": "2026-09-08T09:00:00",
        "arrived_at": "2026-09-08T09:05:00",
        "collected_at": collected_at,
        "sender": sender,
        "thread": thread,
        "text": text,
        "from_owner": from_owner,
        "structured": None,
    }


@unittest.skipIf(MemcalAdapter is None,
                 f"memcal checkout unavailable: {_IMPORT_ERROR}")
class MemcalAdapterTmpHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pacbench-w3-")
        self.home = Path(self._tmp.name) / "memcal-home"
        self.adapter = MemcalAdapter(self.home)

    def tearDown(self):
        self._tmp.cleanup()

    def test_scratch_home_refuses_live_home(self):
        live = Path.home() / ".memcal"
        with self.assertRaises(RuntimeError):
            check_scratch_home(live)
        with self.assertRaises(RuntimeError):
            MemcalAdapter(live)

    def test_scratch_home_refuses_non_scratch(self):
        with self.assertRaises(RuntimeError):
            check_scratch_home(Path("/etc/pacbench-memcal"))

    def test_reset_ingest_readiness_cleanup(self):
        ns = "w3-tmp-life0-rep0"
        self.adapter.reset(ns)
        self.assertTrue((self.home / "memcal.db").exists())

        obs = [
            _fake_obs("msg_1"),
            _fake_obs("msg_2", channel="whatsapp", sender="Eric",
                      thread="poker", text="Poker moved to Jordan's Saturday 8pm."),
            _fake_obs("msg_3", channel="groupme", sender="John",
                      thread="poker", text="My game is canceled."),
            # §31: a direct user turn — must be redirected, not DB-written.
            _fake_obs("turn_1", channel="hermes", sender="user",
                      thread="session-1", text="Yeah I'm still going.",
                      from_owner=True),
        ]
        usage = self.adapter.ingest(obs, "2026-09-08T12:00:00")
        self.assertIsInstance(usage, Usage)
        self.assertIn("user_turns_redirected 1", usage.notes[0])

        conn = sqlite3.connect(self.home / "memcal.db")
        try:
            archived = {row[0] for row in
                        conn.execute("SELECT external_id FROM archive")}
        finally:
            conn.close()
        self.assertEqual(archived, {"msg_1", "msg_2", "msg_3"})
        for ch in USER_TURN_CHANNELS:
            self.assertNotIn(ch, [
                r[0] for r in
                sqlite3.connect(self.home / "memcal.db").execute(
                    "SELECT channel FROM archive").fetchall()])

        readiness = self.adapter.readiness()
        self.assertIsInstance(readiness, Readiness)
        # Nothing has run yet: not-run reads not-ready (§42 honesty).
        self.assertFalse(readiness.ready)

        seam = self.adapter.retrieval()
        self.assertEqual(seam["provider"], "memcal")
        self.assertTrue(seam["plugin_available"])
        self.assertIn("memcal_search_archive", seam["tool_allowlist"])
        self.assertEqual(seam["env"]["MEMCAL_HOME"], str(self.home))

        total = self.adapter.usage()
        self.assertIn("archived 3", total.notes[0])
        self.assertIsNone(total.cost_usd)  # unknown stays unknown, never zero

        self.adapter.cleanup(ns)
        self.assertFalse(self.home.exists())

    def test_ingest_refuses_rubric_payload(self):
        self.adapter.reset("w3-tmp-rubric")
        bad = _fake_obs("msg_bad")
        bad["expected"] = ["Eric's game: active"]
        with self.assertRaises(RuntimeError):
            self.adapter.ingest([bad], "2026-09-08T12:00:00")
        self.adapter.cleanup("w3-tmp-rubric")


    def test_skipped_maintenance_reads_not_ready(self):
        """A skipped dream pass means the provider was NOT fully exercised:
        readiness must not pass it off as healthy (§42). No dream runs here —
        the skip state is injected directly."""
        self.adapter.reset("w3-tmp-skipped")
        self.adapter._maintenance = {"state": "skipped", "at": "t",
                                     "reason": "ValueError: boom"}
        readiness = self.adapter.readiness()
        self.assertFalse(readiness.ready)
        self.assertIn("skipped", readiness.detail)
        self.adapter.cleanup("w3-tmp-skipped")


if __name__ == "__main__":
    unittest.main()
