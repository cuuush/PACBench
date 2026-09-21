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

    async def retain_async(self, bank, text, context="", event_date=None,
                           request_context=None):
        self.retained.append({"bank": bank, "text": text,
                              "context": context, "event_date": event_date})
        return ["mem-1"]


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

        async def always_empty(bank, text, context="", event_date=None,
                               request_context=None):
            calls["n"] += 1
            return []

        fake = FakeEngine()
        fake.retain_async = always_empty
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
        self.assertIn("user_turns_redirected 1", usage.notes[0])
        adapter.close()


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
