"""plan.md P0-2+P0-4: conversation-packet ingest for the Hindsight adapter.

OFFLINE-ONLY: fakes only (no network, no spend).

Covers: stable logical document ids across recollection / resume /
repetition / checkpoint execution; forbidden encodings absent;
distinct-conversation no-collision; adversarial source_id-vs-time
ordering; append mode on conversation packets (replace on email
singletons); verbatim preservation; op-id on the simulated
collected_at boundary; manifest digest present; mixed-digest refusal.
"""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pacbench import live  # noqa: E402
from pacbench import report  # noqa: E402
from pacbench.source_documents import SOURCE_DOCUMENT_MAPPING_VERSION  # noqa: E402


class FakePG:
    def __init__(self, name):
        self.name = name

    async def start(self):
        pass

    async def get_uri(self):
        return "postgresql://fake/db"

    async def stop(self):
        pass


class SpyEngine:
    """Records full retain_batch_async items (ids, modes, order)."""

    def __init__(self):
        self.calls = []  # one entry per retain_batch_async call

    async def retain_batch_async(self, bank, contents,
                                 request_context=None,
                                 operation_id=None,
                                 return_usage=False, **kw):
        self.calls.append({
            "bank": bank,
            "operation_id": operation_id,
            "items": [dict(c) for c in contents],
        })
        out = [[f"mem-{i}"] for i in range(len(contents))]
        if return_usage:
            from types import SimpleNamespace
            return out, SimpleNamespace(
                input_tokens=100 * len(contents),
                output_tokens=10 * len(contents))
        return out

    async def delete_bank(self, bank_id, request_context=None, **kw):
        return {"units": 0}


def _adapter():
    from provider_adapters.hindsight_adapter import HindsightAdapter
    home = Path(tempfile.mkdtemp(prefix="hs-conv-"))
    engine = SpyEngine()
    adapter = HindsightAdapter(
        home, engine_factory=lambda db_url, llm: engine,
        pg_factory=FakePG)
    return adapter, engine


def _reset(adapter):
    with mock.patch.dict(os.environ,
                         {"HINDSIGHT_API_LLM_API_KEY": "k"}):
        adapter.reset("ns-conv")


def _obs(source_id, channel="text", thread="fam", sender="Gus",
          text="hello", written_at="2026-09-18T09:00:00"):
    return {"source_id": source_id, "channel": channel, "thread": thread,
            "sender": sender, "text": text, "written_at": written_at}


def _items(engine, call=0):
    return engine.calls[call]["items"]


class Stability(unittest.TestCase):
    """Doc ids stable across recollection / resume / repetition / checkpoints."""

    def test_recollection_same_conversation_same_id(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            day1 = [_obs("life1/s-02", written_at="2026-09-18T09:00:00"),
                    _obs("life1/s-01", written_at="2026-09-18T09:05:00")]
            day2 = [_obs("life1/s-04", written_at="2026-09-19T10:00:00")]
            adapter.ingest(day1, "2026-09-18T12:00:00")
            adapter.ingest(day2, "2026-09-19T12:00:00")
            ids1 = {i["document_id"] for i in _items(engine, 0)}
            ids2 = {i["document_id"] for i in _items(engine, 1)}
            self.assertEqual(ids1, {"text:fam"})
            self.assertEqual(ids2, {"text:fam"})
        finally:
            adapter.close()

    def test_resume_retry_same_id(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [_obs("life1/a", written_at="2026-09-18T09:00:00")]
            adapter.ingest(batch, "2026-09-18T12:00:00")
            adapter.ingest(batch, "2026-09-18T12:00:00")  # resume retry
            self.assertEqual(_items(engine, 0)[0]["document_id"],
                             _items(engine, 1)[0]["document_id"])
        finally:
            adapter.close()

    def test_repetition_same_id(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [_obs("life1/a", written_at="2026-09-18T09:00:00")]
            adapter.ingest(batch, "2026-09-18T12:00:00")
            first = _items(engine, 0)[0]["document_id"]
            adapter.reset("ns-conv")  # repetition: fresh bank, same evidence
            adapter.ingest(batch, "2026-09-18T12:00:00")
            self.assertEqual(_items(engine, 1)[0]["document_id"], first)
        finally:
            adapter.close()

    def test_checkpoint_execution_does_not_shape_id(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [_obs("life1/a", written_at="2026-09-18T09:00:00")]
            adapter.ingest(batch, "2026-09-18T12:00:00")  # before cp-1
            adapter.ingest(batch, "2026-09-19T12:00:00")  # before cp-2
            for call in (0, 1):
                doc_id = _items(engine, call)[0]["document_id"]
                self.assertEqual(doc_id, "text:fam")
                self.assertNotIn("cp", doc_id)
        finally:
            adapter.close()


class ForbiddenEncodings(unittest.TestCase):
    def test_no_benchmark_identity_in_ids_or_metadata(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            adapter.ingest(
                [_obs("miller-family-life1/msg-1", sender="Gus",
                      written_at="2026-09-18T09:00:00")],
                "2026-09-18T12:00:00")
            item = _items(engine)[0]
            # Document identity strips the benchmark prefix: the authoring
            # source_id legitimately carries "plan.id/local_id", but the
            # logical id must not.
            doc = item["document_id"].lower()
            for forbidden in ("miller", "family-life1", "life1",
                              "checkpoint", "cp-", "grade", "expected",
                              "story", "2026-09-18t12:00:00"):
                self.assertNotIn(forbidden, doc,
                                 f"benchmark/batching identity leaked: {forbidden}")
            # Metadata key names carry no grading/checkpoint namespace ...
            for key in item["metadata"]:
                self.assertNotIn(key, ("expected", "slots", "supported",
                                       "forbidden", "answer_key",
                                       "support_manifest", "truth",
                                       "checkpoint", "life_id"))
            # ... and no grading/checkpoint values ride along either.
            meta_vals = str(item["metadata"]).lower()
            for forbidden in ("checkpoint", "grade", "expected"):
                self.assertNotIn(forbidden, meta_vals)
        finally:
            adapter.close()


class NoCollision(unittest.TestCase):
    def test_distinct_conversations_distinct_ids(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            adapter.ingest(
                [_obs("life1/a", thread="fam",
                      written_at="2026-09-18T09:00:00"),
                 _obs("life1/b", thread="work",
                      written_at="2026-09-18T09:01:00"),
                 _obs("life1/c", channel="whatsapp", thread="fam",
                      written_at="2026-09-18T09:02:00")],
                "2026-09-18T12:00:00")
            ids = [i["document_id"] for i in _items(engine)]
            self.assertEqual(len(set(ids)), 3)
            self.assertIn("text:fam", ids)
            self.assertIn("text:work", ids)
            self.assertIn("whatsapp:fam", ids)
        finally:
            adapter.close()

    def test_email_singletons_keep_per_message_ids(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            adapter.ingest(
                [_obs("life1/mail-a", channel="email", thread="inbox",
                      written_at="2026-09-18T09:00:00"),
                 _obs("life1/mail-b", channel="email", thread="inbox",
                      sender="Mia", written_at="2026-09-18T09:05:00")],
                "2026-09-18T12:00:00")
            ids = sorted(i["document_id"] for i in _items(engine))
            self.assertEqual(ids, ["email:mail-a", "email:mail-b"])
        finally:
            adapter.close()


class Ordering(unittest.TestCase):
    def test_adversarial_source_id_vs_time(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [_obs("life1/s-02", text="FIRST written",
                          written_at="2026-09-18T09:00:00"),
                     _obs("life1/s-01", text="SECOND written",
                          written_at="2026-09-18T09:05:00")]
            adapter.ingest(batch, "2026-09-18T12:00:00")
            items = _items(engine)
            # Lexicographic source_id order disagrees with time on purpose:
            # "s-01" sorts first as a string but was written second.
            self.assertLess("life1/s-01", "life1/s-02")
            self.assertEqual([i["content"] for i in items],
                             ["FIRST written", "SECOND written"])
            dates = [i["event_date"] for i in items]
            self.assertEqual(dates, sorted(dates))
        finally:
            adapter.close()


class UpdateModes(unittest.TestCase):
    def test_append_on_conversation_replace_on_singleton(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            adapter.ingest(
                [_obs("life1/a", written_at="2026-09-18T09:00:00"),
                 _obs("life1/mail-a", channel="email", thread="inbox",
                      written_at="2026-09-18T09:01:00")],
                "2026-09-18T12:00:00")
            by_doc = {i["document_id"]: i["update_mode"]
                      for i in _items(engine)}
            self.assertEqual(by_doc["text:fam"], "append")
            self.assertEqual(by_doc["email:mail-a"], "replace")
        finally:
            adapter.close()


class Verbatim(unittest.TestCase):
    def test_sender_written_text_thread_order_preserved_no_summary(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [_obs("life1/b", sender="Mia", text="pickup after 5pm",
                          written_at="2026-09-18T09:05:00"),
                     _obs("life1/a", sender="Gus",
                          text="the car is ready Friday",
                          written_at="2026-09-18T09:00:00")]
            adapter.ingest(batch, "2026-09-18T12:00:00")
            items = _items(engine)
            self.assertEqual(
                [(i["content"], i["metadata"]["sender"],
                  i["metadata"]["written_at"], i["metadata"]["thread"])
                 for i in items],
                [("the car is ready Friday", "Gus",
                  "2026-09-18T09:00:00", "fam"),
                 ("pickup after 5pm", "Mia",
                  "2026-09-18T09:05:00", "fam")])
            for item in items:
                self.assertNotIn("summary", item)
                self.assertNotIn("summary", item["metadata"])
        finally:
            adapter.close()


class OpId(unittest.TestCase):
    def test_op_id_on_simulated_boundary_not_wall_clock(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            adapter.ingest([_obs("life1/a",
                                written_at="2026-09-18T09:00:00")],
                           "2026-09-18T12:00:00")
            op = engine.calls[0]["operation_id"]
            self.assertEqual(op, str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                "pacbench-ns-conv:2026-09-18T12:00:00")))
        finally:
            adapter.close()


class ManifestDigest(unittest.TestCase):
    def test_provider_recipe_carries_six_field_digest(self):
        from provider_adapters.hindsight_adapter import HindsightAdapter
        home = Path(tempfile.mkdtemp(prefix="hs-digest-"))
        adapter = HindsightAdapter(
            home, engine_factory=lambda db_url, llm: SpyEngine(),
            pg_factory=FakePG)
        recipe = live._provider_recipe("hindsight", adapter=adapter)
        config = recipe["adapter_config"]
        self.assertEqual(
            sorted(config),
            sorted(live.ADAPTER_CONFIG_FIELDS))
        self.assertEqual(config["source_document_mapping_version"],
                         SOURCE_DOCUMENT_MAPPING_VERSION)
        self.assertIn("logical-document-key",  # real scheme, not n/a
                      config["hindsight_document_id_scheme"])
        digest = recipe["adapter_config_digest"]
        self.assertEqual(len(digest), 64)
        int(digest, 16)  # hex
        # Stable: same config, same digest.
        again = live._provider_recipe("hindsight", adapter=adapter)
        self.assertEqual(again["adapter_config_digest"], digest)
        # Matches the adapter-side hash of the same dict.
        from provider_adapters.hindsight_adapter import (
            adapter_config_digest)
        self.assertEqual(adapter_config_digest(config), digest)

    def test_checkpoint_manifest_includes_digest(self):
        # Checkpoint-manifest wiring without a subprocess: a stub with
        # the real adapter_config() hook and a serverless seam.
        from types import SimpleNamespace
        from provider_adapters.hindsight_adapter import (
            adapter_config as real_config)
        import json
        from pacbench.life import Life
        from pacbench.observation import Observation
        from pacbench.rubric import Checkpoint, Kind, Norm, Slot

        class HookAdapter:
            name = "hindsight"

            def reset(self, namespace):
                pass

            def retrieval(self):
                return {"provider": "hindsight", "tool_allowlist": ()}

            def ingest(self, observations, collected_at,
                       group_flags=None):
                return SimpleNamespace(model_calls=0, latency_s=0.0,
                                       input_tokens=None,
                                       output_tokens=None, notes=("fake",))

            def adapter_config(self):
                return real_config()

            def usage(self):
                return SimpleNamespace(model_calls=0, latency_s=0.0,
                                       input_tokens=None,
                                       output_tokens=None, notes=("fake",))

            def env(self):
                return {}

        obs = (Observation(
            source_id="m1", channel="text",
            written_at="2026-09-08T09:00:00",
            arrived_at="2026-09-08T09:05:00",
            collected_at="2026-09-08T10:00:00",
            sender="Gus", thread="tx", text="day one"),)
        cp = Checkpoint(
            id="t/cp", at="2026-09-08T11:00:00",
            question="What did Gus say?",
            slots=(Slot(id="s", kind=Kind.CURRENT, norm=Norm.TEXT,
                        expected="day one",
                        supported_by=("m1",)),))
        life = Life(id="t", bet="b", role="development",
                    observations=obs, checkpoints=(cp,))
        run_dir = Path(tempfile.mkdtemp(prefix="hs-manifest-"))
        turn = mock.Mock(returncode=0, answer="day one", tool_calls=[],
                         tool_results=[], policy_blocks=0, latency_s=0.1,
                         stderr_tail="", stderr_full="", stdout_tail="",
                         raw_events=0, session_id=None,
                         session_input_tokens=None,
                         session_output_tokens=None)
        with mock.patch("pacbench.live.harness.provision_home",
                        return_value={"ok": True}), \
             mock.patch("pacbench.live.harness.run_turn",
                        return_value=turn):
            live.run_checkpoint_live(
                life=life, checkpoint=cp, adapter=HookAdapter(),
                hermes_home=run_dir / "home", run_dir=run_dir,
                model="m", model_provider="mp", namespace="t-ns")
        manifest = json.loads(
            (run_dir / "manifest.json").read_text())
        recipe = manifest["provider_recipe"]
        self.assertEqual(recipe["provider"], "hindsight")
        self.assertIn("logical-document-key",
                      recipe["adapter_config"]["hindsight_document_id_scheme"])
        self.assertEqual(len(recipe["adapter_config_digest"]), 64)


class DigestRefusal(unittest.TestCase):
    def test_same_digest_aggregates(self):
        summary = report.summarize([], [], adapter_digests=["abc", "abc"])
        self.assertEqual(summary.checkpoints, 0)

    def test_mixed_digests_refuse_naming_both(self):
        with self.assertRaises(RuntimeError) as ctx:
            report.summarize([], [], adapter_digests=["digest-aaa",
                                                      "digest-bbb"])
        msg = str(ctx.exception)
        self.assertIn("digest-aaa", msg)
        self.assertIn("digest-bbb", msg)

    def test_manifest_level_refusal(self):
        good = {"provider_recipe": {"adapter_config_digest": "d1"}}
        bad = {"provider_recipe": {"adapter_config_digest": "d2"}}
        self.assertEqual(report.check_manifest_digests([good, dict(good)]),
                         "d1")
        with self.assertRaises(RuntimeError):
            report.check_manifest_digests([good, bad])


if __name__ == "__main__":
    unittest.main()
