"""plan.md P0-2/P0-4 gap-fill: semantic preservation of one-item-per-packet.

OFFLINE-ONLY: fakes only (no network, no spend).

Gap vs tests/test_conversation_ingest.py (which already covers a 2-message
joined transcript, basic ordering, and basic metadata): this module asserts,
for an N-observation packet, that the SINGLE emitted content item carries
EVERY message's content/sender/written_at exactly once in chronological
order, that the metadata source-id set reconstructs EXACTLY the input set,
that document identity is the logical key, that no benchmark-internal
metadata leaks (forbidden-token sweep), and that a two-packet batch leaks
nothing across packets in either direction.
"""

import os
import sys
import tempfile
import unittest
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
        self.calls = []

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
    home = Path(tempfile.mkdtemp(prefix="hs-pktsem-"))
    engine = SpyEngine()
    adapter = HindsightAdapter(
        home, engine_factory=lambda db_url, llm: engine,
        pg_factory=FakePG)
    return adapter, engine


def _reset(adapter):
    with mock.patch.dict(os.environ,
                         {"HINDSIGHT_API_LLM_API_KEY": "k"}):
        adapter.reset("ns-pktsem")


def _obs(source_id, channel="text", thread="fam", sender="Gus",
          text="hello", written_at="2026-09-18T09:00:00",
          collected_at="2026-09-18T12:00:00"):
    return {"source_id": source_id, "channel": channel, "thread": thread,
            "sender": sender, "text": text, "written_at": written_at,
            "collected_at": collected_at}


def _items(engine, call=0):
    return engine.calls[call]["items"]


# Tokens that must never appear in packet payload surfaces. "life7" is the
# life/story prefix baked into the input source_ids below (allowed ONLY
# inside metadata["source_ids"], which carries raw observation ids by
# contract); everything else must be absent everywhere swept.
FORBIDDEN_IN_PAYLOAD = (
    "checkpoint", "cp-", "cp_", "grade", "grading", "expected",
    "answer_key", "support_manifest", "truth", "lineage",
    "operation_id", "op-id", "op_id", "life_id", "story",
)


class SinglePacketPreservation(unittest.TestCase):
    """N observations in one packet -> one item holding all of them once."""

    def test_every_message_sender_timestamp_id_once_in_order(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            # 4 observations, deliberately shuffled out of order; distinct
            # senders/texts/stamps so exactly-once + order are checkable.
            batch = [
                _obs("life7/m3", sender="Zoe",
                     text="FAM third: bring the blue folder",
                     written_at="2026-09-18T09:20:00"),
                _obs("life7/m1", sender="Gus",
                     text="FAM first: the car is ready Friday",
                     written_at="2026-09-18T09:00:00"),
                _obs("life7/m4", sender="Kai",
                     text="FAM fourth: confirm by noon",
                     written_at="2026-09-18T09:30:00"),
                _obs("life7/m2", sender="Mia",
                     text="FAM second: pickup after 5pm",
                     written_at="2026-09-18T09:10:00"),
            ]
            collected_at = "2026-09-18T12:00:00"
            adapter.ingest(batch, collected_at)

            items = _items(engine)
            self.assertEqual(len(items), 1)
            item = items[0]

            # Same logical document identity on id + metadata.
            self.assertEqual(item["document_id"], "text:fam")
            self.assertEqual(item["metadata"]["logical_document"], "text:fam")
            self.assertEqual(item["document_id"],
                             item["metadata"]["logical_document"])

            # Chronological content lines, verbatim.
            self.assertEqual(
                item["content"],
                "Gus (2026-09-18T09:00:00): FAM first: the car is ready Friday\n"
                "Mia (2026-09-18T09:10:00): FAM second: pickup after 5pm\n"
                "Zoe (2026-09-18T09:20:00): FAM third: bring the blue folder\n"
                "Kai (2026-09-18T09:30:00): FAM fourth: confirm by noon")

            # Each message body exactly once.
            for body in ("the car is ready Friday", "pickup after 5pm",
                         "bring the blue folder", "confirm by noon"):
                self.assertEqual(item["content"].count(body), 1,
                                 f"body not exactly once: {body!r}")
            # Each sender line exactly once (distinct senders in this batch).
            for sender in ("Gus", "Mia", "Zoe", "Kai"):
                self.assertEqual(item["content"].count(f"{sender} ("), 1,
                                 f"sender not exactly once: {sender!r}")
            # Each timestamp exactly once and in chronological order.
            stamps = ("2026-09-18T09:00:00", "2026-09-18T09:10:00",
                      "2026-09-18T09:20:00", "2026-09-18T09:30:00")
            for stamp in stamps:
                self.assertEqual(item["content"].count(stamp), 1,
                                 f"stamp not exactly once: {stamp!r}")
            positions = [item["content"].index(s) for s in stamps]
            self.assertEqual(positions, sorted(positions))

            # Source-id set reconstructed from metadata == input set.
            input_ids = {o["source_id"] for o in batch}
            self.assertEqual(set(item["metadata"]["source_ids"]), input_ids)
            # ... and in chronological order within the list.
            self.assertEqual(item["metadata"]["source_ids"],
                             ["life7/m1", "life7/m2", "life7/m3", "life7/m4"])
            self.assertEqual(item["metadata"]["senders"],
                             ["Gus", "Mia", "Zoe", "Kai"])
            self.assertEqual(item["metadata"]["written_at_start"],
                             "2026-09-18T09:00:00")
            self.assertEqual(item["metadata"]["written_at_end"],
                             "2026-09-18T09:30:00")
        finally:
            adapter.close()


class NoBenchmarkMetadata(unittest.TestCase):
    """Forbidden-token sweep over content + context + metadata."""

    def test_no_internal_tokens_in_packet_surfaces(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [
                _obs("life7/m1", sender="Gus",
                     text="FAM first: the car is ready Friday",
                     written_at="2026-09-18T09:00:00"),
                _obs("life7/m2", sender="Mia",
                     text="FAM second: pickup after 5pm",
                     written_at="2026-09-18T09:10:00"),
            ]
            collected_at = "2026-09-18T12:00:00"
            adapter.ingest(batch, collected_at)

            item = _items(engine)[0]
            op_id = engine.calls[0]["operation_id"]

            # Sweep content + context + document_id + metadata EXCLUDING the
            # raw source_ids list (which carries the "life7/..." observation
            # ids verbatim by contract — the life prefix is allowed there
            # and ONLY there).
            meta_minus_ids = {k: v for k, v in item["metadata"].items()
                              if k != "source_ids"}
            blob = "\n".join((
                str(item["content"]),
                str(item["context"]),
                str(item["document_id"]),
                str(meta_minus_ids),
                str(list(item["metadata"].keys())),
            )).lower()
            for forbidden in FORBIDDEN_IN_PAYLOAD:
                self.assertNotIn(forbidden, blob,
                                 f"benchmark-internal token leaked: {forbidden!r}")
            # Life-prefix and batching identity stay out of the swept
            # surfaces even though the prefix legitimately rides in
            # metadata["source_ids"].
            for forbidden in ("life7", "miller", collected_at.lower()):
                self.assertNotIn(forbidden, blob,
                                 f"benchmark/batching identity leaked: {forbidden!r}")
            # The operational retry id is a call arg, never packet content.
            self.assertNotIn(str(op_id), blob)
            # Metadata key names carry no grading/checkpoint namespace.
            for key in item["metadata"]:
                self.assertNotIn(key, ("expected", "slots", "supported",
                                       "forbidden", "answer_key",
                                       "support_manifest", "truth",
                                       "checkpoint", "life_id",
                                       "operation_id", "collected_at"))
        finally:
            adapter.close()


class NoCrossPacketLeakage(unittest.TestCase):
    """Two-packet batch: each item holds only its own packet, both ways."""

    def test_two_packets_isolated_both_directions(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            fam = [
                _obs("life7/f1", thread="fam", sender="Gus",
                     text="FAM-ALPHA-quokka one",
                     written_at="2026-09-18T09:00:00"),
                _obs("life7/f2", thread="fam", sender="Mia",
                     text="FAM-BETA-quokka two",
                     written_at="2026-09-18T09:10:00"),
            ]
            work = [
                _obs("life7/w1", thread="work", sender="Zoe",
                     text="WORK-GAMMA-zephyr one",
                     written_at="2026-09-18T09:05:00"),
                _obs("life7/w2", thread="work", sender="Kai",
                     text="WORK-DELTA-zephyr two",
                     written_at="2026-09-18T09:15:00"),
            ]
            adapter.ingest(fam + work, "2026-09-18T12:00:00")

            items = _items(engine)
            self.assertEqual(len(items), 2)
            by_doc = {i["document_id"]: i for i in items}
            self.assertEqual(set(by_doc), {"text:fam", "text:work"})
            fam_item, work_item = by_doc["text:fam"], by_doc["text:work"]

            # Own messages present exactly once, in order.
            self.assertEqual(
                fam_item["content"],
                "Gus (2026-09-18T09:00:00): FAM-ALPHA-quokka one\n"
                "Mia (2026-09-18T09:10:00): FAM-BETA-quokka two")
            self.assertEqual(
                work_item["content"],
                "Zoe (2026-09-18T09:05:00): WORK-GAMMA-zephyr one\n"
                "Kai (2026-09-18T09:15:00): WORK-DELTA-zephyr two")
            self.assertEqual(set(fam_item["metadata"]["source_ids"]),
                             {"life7/f1", "life7/f2"})
            self.assertEqual(set(work_item["metadata"]["source_ids"]),
                             {"life7/w1", "life7/w2"})

            # Cross-absence both ways: bodies, senders, stamps, ids.
            for foreign in ("WORK-GAMMA-zephyr one", "WORK-DELTA-zephyr two",
                            "Zoe", "Kai",
                            "2026-09-18T09:05:00", "2026-09-18T09:15:00",
                            "life7/w1", "life7/w2", "text:work"):
                self.assertNotIn(
                    foreign,
                    fam_item["content"] + "\n" + fam_item["context"] + "\n"
                    + str(fam_item["metadata"]) + "\n" + fam_item["document_id"],
                    f"work message leaked into fam packet: {foreign!r}")
            for foreign in ("FAM-ALPHA-quokka one", "FAM-BETA-quokka two",
                            "Gus", "Mia",
                            "2026-09-18T09:00:00", "2026-09-18T09:10:00",
                            "life7/f1", "life7/f2", "text:fam"):
                self.assertNotIn(
                    foreign,
                    work_item["content"] + "\n" + work_item["context"] + "\n"
                    + str(work_item["metadata"]) + "\n" + work_item["document_id"],
                    f"fam message leaked into work packet: {foreign!r}")
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()
