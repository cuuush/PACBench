"""Hindsight conversation packet ingestion with a fake engine."""

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
          text="hello", written_at="2026-09-18T09:00:00",
          collected_at="2026-09-18T12:00:00"):
    return {"source_id": source_id, "channel": channel, "thread": thread,
            "sender": sender, "text": text, "written_at": written_at,
            "collected_at": collected_at}


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
            day2 = [_obs("life1/s-04", written_at="2026-09-19T10:00:00",
                         collected_at="2026-09-19T12:00:00")]
            first = adapter.ingest(day1, "2026-09-18T12:00:00")
            second = adapter.ingest(day2, "2026-09-19T12:00:00")
            self.assertEqual((first.model_calls, second.model_calls), (1, 1))
            # One joined item per packet per ingest call.
            self.assertEqual(len(_items(engine, 0)), 1)
            self.assertEqual(len(_items(engine, 1)), 1)
            ids1 = {i["document_id"] for i in _items(engine, 0)}
            ids2 = {i["document_id"] for i in _items(engine, 1)}
            self.assertEqual(ids1, {"text:fam"})
            self.assertEqual(ids2, {"text:fam"})
        finally:
            adapter.close()

    def test_one_daily_batch_contains_two_imessage_threads(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            self.assertIn("mcp__hindsight__list_memories",
                          adapter.retrieval()["tool_allowlist"])
            batch = [
                _obs("pizza-1", channel="imessage", thread="pizza", sender="Me",
                     text="Pizza Friday?", written_at="2026-09-18T09:00:00"),
                _obs("pizza-2", channel="imessage", thread="pizza", sender="Devon",
                     text="Yes, 7pm.", written_at="2026-09-18T09:01:00"),
                _obs("ducks-1", channel="imessage", thread="ducks", sender="Maya",
                     text="Ducks Saturday?", written_at="2026-09-18T09:02:00"),
                _obs("ducks-2", channel="imessage", thread="ducks", sender="Devon",
                     text="I'm in.", written_at="2026-09-18T09:03:00"),
            ]
            adapter.ingest(batch, "2026-09-18T12:00:00", group_flags={
                ("imessage", "pizza"): False,
                ("imessage", "ducks"): True,
            })
            self.assertEqual(len(engine.calls), 1)
            items = {item["document_id"]: item for item in _items(engine)}
            self.assertEqual(set(items), {"imessage:pizza", "imessage:ducks"})
            self.assertEqual(items["imessage:pizza"]["metadata"]["source_ids"],
                             ["pizza-1", "pizza-2"])
            self.assertEqual(items["imessage:ducks"]["metadata"]["source_ids"],
                             ["ducks-1", "ducks-2"])
            self.assertIn("group chat", items["imessage:ducks"]["context"])
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
            batch2 = [_obs("life1/a", written_at="2026-09-18T09:00:00",
                           collected_at="2026-09-19T12:00:00")]
            adapter.ingest(batch2, "2026-09-19T12:00:00")  # before cp-2
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
            # One joined item per packet, chronological lines, verbatim.
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item["document_id"], "text:fam")
            self.assertEqual(
                item["content"],
                "Gus (2026-09-18T09:00:00): FIRST written\n"
                "Gus (2026-09-18T09:05:00): SECOND written")
            self.assertEqual(item["metadata"]["source_ids"],
                             ["life1/s-02", "life1/s-01"])
            self.assertEqual((item["event_date"].hour,
                              item["event_date"].minute), (9, 0))
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
            # One joined-transcript item per packet: chronological
            # "sender (written_at): text" lines, verbatim, no summaries.
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item["document_id"], "text:fam")
            self.assertEqual(
                item["content"],
                "Gus (2026-09-18T09:00:00): the car is ready Friday\n"
                "Mia (2026-09-18T09:05:00): pickup after 5pm")
            self.assertEqual(item["context"],
                             "text thread fam from Gus, Mia")
            meta = item["metadata"]
            self.assertEqual(meta["source_ids"], ["life1/a", "life1/b"])
            self.assertEqual(meta["senders"], ["Gus", "Mia"])
            self.assertEqual(meta["written_at_start"],
                             "2026-09-18T09:00:00")
            self.assertEqual(meta["written_at_end"],
                             "2026-09-18T09:05:00")
            self.assertEqual(meta["thread"], "fam")
            self.assertEqual(meta["logical_document"], "text:fam")
            self.assertEqual(item["update_mode"], "append")
            self.assertNotIn("summary", item)
            self.assertNotIn("summary", meta)
        finally:
            adapter.close()


class OpId(unittest.TestCase):
    def test_op_id_on_simulated_boundary_not_wall_clock(self):
        from pacbench.resume import ingest_boundary, same_operation_id
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [_obs("life1/a",
                          written_at="2026-09-18T09:00:00")]
            adapter.ingest(batch, "2026-09-18T12:00:00")
            op = engine.calls[0]["operation_id"]
            self.assertEqual(op, str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                "pacbench-ns-conv:2026-09-18T12:00:00")))
            # Parity: the adapter shares resume's helper — the boundary
            # derives from batch contents and the id from (bank,
            # boundary) through the same function the runner uses.
            self.assertEqual(ingest_boundary(batch), "2026-09-18T12:00:00")
            self.assertEqual(
                op, same_operation_id(
                    ("pacbench-ns-conv", ingest_boundary(batch))))
        finally:
            adapter.close()
