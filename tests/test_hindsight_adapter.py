"""Hindsight adapter: offline tests with fake PG/engine (no network)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class FakePG:
    def __init__(self, name):
        self.name = name
        self.started = False

    async def start(self):
        self.started = True

    async def get_uri(self):
        return "postgresql://fake/db"

    async def stop(self):
        self.started = False


class FakeEngine:
    def __init__(self):
        self.retained = []
        self.batches = []
        self.deleted_banks = []

    async def retain_async(self, bank, text, context="",
                           event_date=None,
                           request_context=None):
        self.retained.append({"bank": bank, "text": text,
                              "context": context,
                              "event_date": event_date})
        return ["mem-1"]

    async def retain_batch_async(self, bank, contents,
                                 request_context=None,
                                 operation_id=None,
                                 return_usage=False, **kw):
        self.batches.append({"bank": bank, "n": len(contents),
                             "operation_id": operation_id,
                             "return_usage": return_usage,
                             "event_dates": [c.get("event_date")
                                             for c in contents]})
        out = []
        for c in contents:
            self.retained.append({"bank": bank, "text": c.get("content"),
                                  "context": c.get("context"),
                                  "event_date": c.get("event_date"),
                                  "document_id": c.get("document_id"),
                                  "operation_id": operation_id})
            out.append(["mem-1"])
        if return_usage:
            from types import SimpleNamespace
            return out, SimpleNamespace(input_tokens=100 * len(contents),
                                        output_tokens=10 * len(contents))
        return out

    async def delete_bank(self, bank_id, request_context=None, **kw):
        self.deleted_banks.append(bank_id)
        self.retained = [r for r in self.retained if r["bank"] != bank_id]
        return {"units": 0}


def _adapter(**kw):
    from provider_adapters.hindsight_adapter import HindsightAdapter
    home = Path(tempfile.mkdtemp(prefix="hs-test-"))
    fake = FakeEngine()
    kw.setdefault("engine_factory", lambda db_url, llm: fake)
    kw.setdefault("pg_factory", FakePG)
    adapter = HindsightAdapter(home, **kw)
    return adapter, fake


def _no_key():
    # clear=True: merely omitting the key from the update dict does NOT
    # remove a real exported key (patch.dict defaults to update-only).
    env = {k: v for k, v in os.environ.items()
           if k != "HINDSIGHT_API_LLM_API_KEY"}
    return mock.patch.dict(os.environ, env, clear=True)


class NoSemanticRetry(unittest.TestCase):
    """Empty-id retains are recorded, never semantically retried (§2)."""

    def test_empty_is_single_attempt_recorded(self):
        from provider_adapters.hindsight_adapter import HindsightAdapter
        home = Path(tempfile.mkdtemp(prefix="hs-test-"))
        calls = {"n": 0}

        async def always_empty(bank, contents, request_context=None,
                               operation_id=None, **kw):
            calls["n"] += 1
            return [[] for _ in contents]

        fake = FakeEngine()
        fake.retain_batch_async = always_empty
        adapter = HindsightAdapter(
            home, engine_factory=lambda db_url, llm: fake,
            pg_factory=FakePG)
        env = dict(os.environ,
                   HINDSIGHT_API_LLM_API_KEY="k",
                   META_API_KEY="m", META_BASE_URL="https://x/v1")
        with mock.patch.dict(os.environ, env, clear=False):
            adapter.reset("ns-drop")
            usage = adapter.ingest(
                [{"source_id": "s/2", "text": "gone",
                  "channel": "text", "sender": "a",
                  "written_at": "2026-09-09T10:00:00"}],
                collected_at="2026-09-09T10:00:01")
        adapter.close()
        self.assertIn("retained 0/1", usage.notes[0])
        self.assertIn("recorded not retried", usage.notes[0])
        self.assertEqual(calls["n"], 1)

    def test_ids_count_retained(self):
        adapter, fake = _adapter()
        env = dict(os.environ,
                   HINDSIGHT_API_LLM_API_KEY="k",
                   META_API_KEY="m", META_BASE_URL="https://x/v1")
        with mock.patch.dict(os.environ, env, clear=False):
            adapter.reset("ns-ok")
            usage = adapter.ingest(
                [{"source_id": "s/1", "text": "hello",
                  "channel": "text", "sender": "a",
                  "written_at": "2026-09-09T10:00:00"}],
                collected_at="2026-09-09T10:00:01")
        adapter.close()
        self.assertIn("retained 1/1", usage.notes[0])
        self.assertEqual(len(fake.retained), 1)


class LlmConfigFallback(unittest.TestCase):
    """Internal LLM key falls back to the repo .env (driver precedent)."""

    def test_explicit_key_wins(self):
        from provider_adapters.hindsight_adapter import HindsightAdapter
        home = Path(tempfile.mkdtemp(prefix="hs-test-"))
        adapter = HindsightAdapter(
            home, engine_factory=lambda db_url, llm: FakeEngine(),
            pg_factory=FakePG)
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "explicit"}):
            self.assertEqual(adapter._llm_config().api_key, "explicit")


class ScratchGuard(unittest.TestCase):
    def test_refuses_non_scratch(self):
        from provider_adapters.hindsight_adapter import HindsightAdapter
        with self.assertRaises(RuntimeError):
            HindsightAdapter(Path.home() / "somewhere" / "else")


class ResetFailClosed(unittest.TestCase):
    def test_no_key_refuses(self):
        # No env key AND no .env key: still fail-closed.
        from pacbench.generation import GenerationError
        adapter, _ = _adapter()
        with _no_key(), mock.patch(
                "pacbench.generation.load_config",
                side_effect=GenerationError("no key")):
            with self.assertRaises(RuntimeError):
                adapter.reset("ns1")


class RerunIdempotency(unittest.TestCase):
    """H2: same-namespace reruns never silently double-ingest.

    ``reset`` drops the bound bank's data first (delete_bank), so a
    reset → ingest cycle sees exactly the rerun's observations — clean
    by mechanism, not by manual PG-instance cleanup.
    """

    def _obs(self, sid="t/1"):
        return {"source_id": sid, "channel": "text", "sender": "Gus",
                "thread": "tx", "text": "the car is ready Friday",
                "written_at": "2026-09-18T11:00:00"}

    def test_reset_drops_bank_before_restart(self):
        adapter, fake = _adapter()
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "k"}):
            adapter.reset("ns-rerun")
            adapter.ingest([self._obs()], "2026-09-18T12:00:00")
            first = len(fake.retained)
            self.assertEqual(first, 1)
            # Second cycle on the SAME namespace: bank dropped first,
            # then one fresh ingest — never 2 rows.
            adapter.reset("ns-rerun")
            self.assertEqual(fake.deleted_banks, ["pacbench-ns-rerun"])
            adapter.ingest([self._obs()], "2026-09-18T12:00:00")
        self.assertEqual(len(fake.retained), 1)
        adapter.close()


class Ingest(unittest.TestCase):
    def _obs(self, channel="text", text="the car is ready Friday"):
        return {"source_id": "t/1", "channel": channel, "sender": "Gus",
                "thread": "tx", "text": text,
                "written_at": "2026-09-18T11:00:00"}

    def test_forbidden_keys_raise(self):
        adapter, _ = _adapter()
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "k"}):
            adapter.reset("ns1")
        bad = self._obs()
        bad["expected"] = "Friday"
        with self.assertRaises(RuntimeError):
            adapter.ingest([bad], "2026-09-18T12:00:00")
        adapter.close()

    def test_user_turns_redirected_event_date_parsed(self):
        adapter, fake = _adapter()
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "k"}):
            adapter.reset("ns1")
        mine = self._obs(channel="hermes", text="my private note")
        usage = adapter.ingest([self._obs(), mine], "2026-09-18T12:00:00")
        self.assertEqual(len(fake.retained), 1)
        rec = fake.retained[0]
        self.assertEqual(rec["bank"], "pacbench-ns1")
        self.assertIsNotNone(rec["event_date"])
        self.assertEqual(rec["event_date"].day, 18)
        # P0-2 migration: conversation packets share the stable logical
        # document id (text:tx), not the old per-message source_id scheme.
        self.assertEqual(rec["document_id"], "text:tx")
        self.assertEqual(len(fake.batches), 1)
        import uuid as _uuid
        self.assertEqual(fake.batches[0]["operation_id"], str(_uuid.uuid5(
            _uuid.NAMESPACE_URL,
            "pacbench-ns1:2026-09-18T12:00:00")))
        self.assertIn("user_turns_redirected 1", usage.notes[0])
        adapter.close()

    def test_operation_id_is_deterministic_uuid(self):
        import uuid as _uuid
        adapter, fake = _adapter()
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "k"}):
            adapter.reset("ns1")
        adapter.ingest([self._obs()], "2026-09-18T12:00:00")
        op = fake.batches[0]["operation_id"]
        # Well-formed UUID (product parses outcome-metadata IDs as UUIDs)
        # AND deterministic per packet (idempotent operational retry).
        self.assertEqual(op, str(_uuid.uuid5(
            _uuid.NAMESPACE_URL,
            "pacbench-ns1:2026-09-18T12:00:00")))
        adapter.close()

    def test_single_call_per_ingest_with_usage_telemetry(self):
        # SMART: one retain_batch_async per ingest() call (the caller
        # already groups by collection instant) with return_usage=True,
        # so TokenUsage flows into Usage instead of staying unknown.
        adapter, fake = _adapter()
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "k"}):
            adapter.reset("ns1")
            usage = adapter.ingest(
                [self._obs(), {**self._obs(), "source_id": "t/2"}],
                "2026-09-18T12:00:00")
        self.assertEqual(len(fake.batches), 1)
        self.assertEqual(fake.batches[0]["n"], 2)
        self.assertTrue(fake.batches[0]["return_usage"])
        self.assertIn("retained 2/2", usage.notes[0])
        self.assertIn("retain tokens 200 in / 20 out", usage.notes[0])
        self.assertEqual((usage.input_tokens, usage.output_tokens),
                         (200, 20))
        total = adapter.usage()
        self.assertEqual((total.input_tokens, total.output_tokens),
                         (200, 20))
        adapter.close()


class MentionedAtWallClock(unittest.TestCase):
    """M1: ingest-time wall-clock is the product default, documented not patched.

    The adapter passes authoring ``written_at`` as ``event_date`` (the
    native time anchor) and deliberately does NOT set ``mentioned_at``:
    Hindsight defaults it to processing time (host wall-clock). This
    test pins that contract surface — contents carry event_date, never
    mentioned_at — so a future mentioned_at change is a deliberate
    characterization-backed decision, not drift. Recency-weighted
    Hindsight claims stay invalid until the §4 wall-clock
    characterization lands (ruling BLOCKED item).
    """

    def test_contents_carry_event_date_not_mentioned_at(self):
        adapter, fake = _adapter()
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "k"}):
            adapter.reset("ns1")
            adapter.ingest(
                [{"source_id": "t/9", "channel": "text", "sender": "Gus",
                  "thread": "tx", "text": "old news from March",
                  "written_at": "2026-03-01T10:00:00"}],
                "2026-09-18T12:00:00")
        self.assertEqual(len(fake.batches), 1)
        # FakeEngine records retained payloads; the real contract is on
        # the contents dicts passed to retain_batch_async — capture them.
        adapter2, fake2 = _adapter()
        seen = {}
        orig = fake2.retain_batch_async

        async def spy(bank, contents, **kw):
            seen["contents"] = [dict(c) for c in contents]
            return await orig(bank, contents, **kw)

        fake2.retain_batch_async = spy
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "k"}):
            adapter2.reset("ns1")
            adapter2.ingest(
                [{"source_id": "t/9", "channel": "text", "sender": "Gus",
                  "thread": "tx", "text": "old news from March",
                  "written_at": "2026-03-01T10:00:00"}],
                "2026-09-18T12:00:00")
        adapter.close()
        adapter2.close()
        self.assertIn("contents", seen)
        for item in seen["contents"]:
            self.assertNotIn("mentioned_at", item)
            self.assertIsNotNone(item.get("event_date"))
            self.assertEqual(item["event_date"].month, 3)


class MaintenanceReadiness(unittest.TestCase):
    def _ready_adapter(self):
        adapter, fake = _adapter()
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "k"}):
            adapter.reset("ns1")
        return adapter

    def test_complete_means_ready(self):
        adapter = self._ready_adapter()

        async def fake_job(engine, bank, ctx, operation_id=""):
            return "relinked 3"

        with mock.patch(
                "hindsight_api.engine.graph_maintenance."
                "run_graph_maintenance_job", fake_job):
            usage = adapter.maintenance("2026-09-19T00:00:00")
        self.assertIn("complete", usage.notes[0])
        ready = adapter.readiness()
        self.assertTrue(ready.ready)
        adapter.close()

    def test_skip_means_not_ready(self):
        adapter = self._ready_adapter()

        async def fake_job(engine, bank, ctx, operation_id=""):
            raise RuntimeError("db down")

        with mock.patch(
                "hindsight_api.engine.graph_maintenance."
                "run_graph_maintenance_job", fake_job):
            usage = adapter.maintenance("2026-09-19T00:00:00")
        self.assertIn("skipped", usage.notes[0])
        ready = adapter.readiness()
        self.assertFalse(ready.ready)
        adapter.close()

    def test_not_run_means_not_ready(self):
        adapter = self._ready_adapter()
        self.assertFalse(adapter.readiness().ready)
        adapter.close()

    def test_cleanup_mismatch_raises(self):
        adapter = self._ready_adapter()
        with self.assertRaises(RuntimeError):
            adapter.cleanup("other-ns")
        adapter.cleanup("ns1")


if __name__ == "__main__":
    unittest.main()
