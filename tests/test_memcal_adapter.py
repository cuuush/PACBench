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
from types import SimpleNamespace
from unittest import mock

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


def _chat_message(source_id, thread, sender, text, minute, *, owner=False):
    row = _fake_obs(source_id, channel="imessage", sender=sender,
                    thread=thread, text=text, from_owner=owner,
                    collected_at="2026-09-08T12:00:00")
    row["written_at"] = f"2026-09-08T09:{minute:02d}:00"
    return row


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

    def test_ingest_spools_for_dream_and_restores_clock(self):
        import os
        self.adapter.reset("w3-tmp-spool")
        obs = [_fake_obs("msg_1"), _fake_obs("msg_2")]
        os.environ["MEMCAL_TODAY"] = "sentinel"
        try:
            usage = self.adapter.ingest(obs, "2026-09-08T12:00:00")
            self.assertEqual(os.environ.get("MEMCAL_TODAY"), "sentinel")
        finally:
            os.environ.pop("MEMCAL_TODAY", None)
        self.assertIn("spooled 2/2", usage.notes[0])
        conn = sqlite3.connect(self.home / "memcal.db")
        try:
            spool = conn.execute(
                "SELECT archive_id, entity, processed_at FROM spool").fetchall()
        finally:
            conn.close()
        # Both archived rows queued for dream with live entity convention.
        self.assertEqual(len(spool), 2)
        self.assertTrue(all(r[2] is None for r in spool))
        self.assertTrue(all(r[1] for r in spool))
        self.adapter.cleanup("w3-tmp-spool")

    def test_ingest_refuses_rubric_payload(self):
        self.adapter.reset("w3-tmp-rubric")
        bad = _fake_obs("msg_bad")
        bad["expected"] = ["Eric's game: active"]
        with self.assertRaises(RuntimeError):
            self.adapter.ingest([bad], "2026-09-08T12:00:00")
        self.adapter.cleanup("w3-tmp-rubric")

    def test_native_imessage_ingests_two_threads_once(self):
        self.adapter.reset("w3-native-chat")
        day = [
            _chat_message("pizza-1", "pizza", "Me", "Pizza Friday at Luigi's?", 0,
                          owner=True),
            _chat_message("pizza-2", "pizza", "Devon", "Yes, 7pm works.", 1),
            _chat_message("ducks-1", "ducks", "Maya", "Ducks Saturday at 9?", 2),
            _chat_message("ducks-2", "ducks", "Devon", "I'm in.", 3),
            _chat_message("ducks-3", "ducks", "Me", "Maybe for me.", 4,
                          owner=True),
        ]
        flags = {("imessage", "pizza"): False, ("imessage", "ducks"): True}
        first = self.adapter.ingest(day, "2026-09-08T12:00:00", group_flags=flags)
        self.assertIn("archived 5", first.notes[0])
        conn = sqlite3.connect(self.home / "memcal.db")
        try:
            rows = conn.execute("SELECT external_id FROM archive ORDER BY id").fetchall()
            spool = conn.execute("SELECT entity FROM spool ORDER BY archive_id").fetchall()
        finally:
            conn.close()
        self.assertEqual({r[0] for r in rows}, {m["source_id"] for m in day})
        self.assertEqual(len(spool), 5)
        self.assertEqual({r[0] for r in spool},
                         {"person:Devon", "thread:imessage:ducks"})
        self.assertTrue((self.home / "chat.db").exists())
        self.assertEqual({p["source_id"] for p in self.adapter._last_packets},
                         {m["source_id"] for m in day})
        self.assertEqual({p["entity"] for p in self.adapter._last_packets},
                         {"person:Devon", "thread:imessage:ducks"})

        next_day = [_chat_message("pizza-3", "pizza", "Devon",
                                  "Looking forward to it.", 5)]
        next_day[0]["written_at"] = "2026-09-09T09:05:00"
        next_day[0]["collected_at"] = "2026-09-09T12:00:00"
        self.adapter.ingest(next_day, "2026-09-09T12:00:00", group_flags=flags)
        conn = sqlite3.connect(self.home / "memcal.db")
        try:
            ids = [r[0] for r in conn.execute("SELECT external_id FROM archive")]
            entities = [r[0] for r in conn.execute("SELECT entity FROM spool")]
        finally:
            conn.close()
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)
        self.assertEqual(entities.count("person:Devon"), 3)
        self.adapter.cleanup("w3-native-chat")

    def test_brief_renders_fictional_date_not_host_week(self):
        """H1 provider-path clock: the brief follows MEMCAL_TODAY, not host.

        Regression for the 2026-09-21 smoke leak: evidence archived with
        fictional 2026-09-08 timestamps, but the brief rendered the host
        week (\"today is Monday 21 September 2026\") with nothing known.
        Pinning the product's own MEMCAL_TODAY seam must render the
        fictional date in the week header.
        """
        import os
        self.adapter.reset("w3-tmp-clock")
        try:
            self.adapter.ingest([_fake_obs("msg_c")], "2026-09-08T12:00:00")
            from memcal import brief, config, db
            prev = os.environ.get("MEMCAL_TODAY")
            os.environ["MEMCAL_TODAY"] = "2026-09-08T12:00:00"
            try:
                conn = self.adapter._conn()
                try:
                    ref = db.today()
                    text = brief.render(conn, config.load(self.home))
                finally:
                    conn.close()
            finally:
                if prev is None:
                    os.environ.pop("MEMCAL_TODAY", None)
                else:
                    os.environ["MEMCAL_TODAY"] = prev
        finally:
            self.adapter.cleanup("w3-tmp-clock")
        self.assertEqual(str(ref), "2026-09-08")
        # The header carries the FICTIONAL date from the MEMCAL_TODAY pin
        # (2026-09-08 was a Tuesday) — never the host week. The hard
        # regression is the date, not the weekday word: assert the full
        # header line so a host-week render fails loudly.
        self.assertIn("today is Tuesday 8 September 2026", text)


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

    def test_dream_error_is_not_ready_and_uses_explicit_endpoint(self):
        adapter = MemcalAdapter(
            self.home, dream_model="test-model",
            dream_base_url="https://example.test/v1", dream_api_key="test-key")
        adapter.reset("w3-dream-error")

        def failed_dream(conn, cfg, **_kwargs):
            self.assertEqual(cfg.llm_provider, "openai-compatible")
            self.assertEqual(cfg.openai_base_url, "https://example.test/v1")
            self.assertEqual(cfg.openai_api_key, "test-key")
            self.assertEqual((cfg.propose_model, cfg.sweep_model, cfg.match_model),
                             ("test-model",) * 3)
            self.assertEqual(cfg.propose_output_floor, 16384)
            row = conn.execute(
                "INSERT INTO runs(started_at, finished_at, mode, model, bundles, "
                "items, prompt_tokens, completion_tokens, requests, failed_calls, error) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("2026-09-10T23:59:00", "2026-09-10T23:59:00", "nightly",
                 "test-model", 1, 1, 100, 5, 1, 1, "HTTP 400 unsupported model"))
            conn.commit()
            return SimpleNamespace(run_id=row.lastrowid)

        with mock.patch("memcal.dream.run.dream", side_effect=failed_dream):
            usage = adapter.maintenance("2026-09-10T23:59:00")
        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(adapter._maintenance["state"], "failed")
        self.assertIn("HTTP 400", adapter.readiness().detail)
        adapter.cleanup("w3-dream-error")


if __name__ == "__main__":
    unittest.main()
