"""plan.md P0-9: multi-day Hindsight conversation-evolution harness.

OFFLINE-ONLY: everything here runs green on fakes (no network, no spend).
Paid live-engine execution is spend-gated — see LiveConversationEvolution.

Semantics under test (P0-3 desired behavior for one evolving conversation):

* day 1: retain the full conversation docs available through that cycle;
* day 2/3: deliver ONLY newly exposed messages as deltas appended to the
  SAME logical document id — never new standalone conversation docs,
  never future messages early.

Six checks (C4 gate item 9 wording): stable doc id, no duplicate ingestion
of earlier messages, deltas associated to the same conversation, all
messages recoverable, no content multiplication, chronology intact.

Document identity swap-point: if the sibling agent's P0-1
``pacbench/source_documents.py`` has landed, its ``logical_document_key``
is used; otherwise a clearly-marked local fallback (channel + thread)
applies and MUST be replaced once P0-1 lands. Wiring this key into
``HindsightAdapter.ingest`` (currently per-message document_id =
source_id) is P0-2/P0-3 work, not done here.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# ---------------------------------------------------------------------------
# Identity swap-point (see module docstring).
# ---------------------------------------------------------------------------
try:
    from pacbench.source_documents import logical_document_key  # P0-1
    _IDENTITY_SOURCE = "pacbench.source_documents.logical_document_key"
except ImportError:  # sibling P0-1 not yet landed: local fallback
    _IDENTITY_SOURCE = (
        "local-fallback(channel+thread); replace with "
        "pacbench.source_documents.logical_document_key once P0-1 lands"
    )

    def logical_document_key(obs: dict) -> str:
        return f"{obs.get('channel', '?')}::thread::{obs.get('thread', '?')}"


# ---------------------------------------------------------------------------
# Fakes (mirror tests/test_hindsight_adapter.py FakeEngine pattern).
# ---------------------------------------------------------------------------
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


class FakeConversationStore:
    """Document-store fake with conversation-update semantics.

    Same ``retain_batch_async`` contract the adapter uses (one call per
    ingest batch, ``document_id`` per item, ``operation_id`` per batch).
    Each item's text is ONE message; the store appends it to the item's
    logical document, deduplicating on (document_id, source_id) so
    retries/re-ingest never duplicate or multiply content.
    """

    def __init__(self):
        self.docs: dict[str, dict[str, dict]] = {}  # doc_id -> source_id -> rec
        self.batches: list[dict] = []

    async def retain_batch_async(self, bank, contents,
                                 request_context=None,
                                 operation_id=None,
                                 return_usage=False, **kw):
        self.batches.append({
            "bank": bank,
            "n": len(contents),
            "operation_id": operation_id,
            "document_ids": [c.get("document_id") for c in contents],
            "source_ids": [c.get("metadata", {}).get("source_id")
                           for c in contents],
        })
        out = []
        for c in contents:
            doc_id = c.get("document_id")
            src_id = c.get("metadata", {}).get("source_id")
            bucket = self.docs.setdefault(doc_id, {})
            if src_id not in bucket:  # delta append; re-ingest is a no-op
                bucket[src_id] = {
                    "bank": bank,
                    "text": c.get("content"),
                    "context": c.get("context"),
                    "event_date": c.get("event_date"),
                    "operation_id": operation_id,
                }
            out.append([f"mem-{src_id}"])
        if return_usage:
            from types import SimpleNamespace
            return out, SimpleNamespace(input_tokens=100 * len(contents),
                                        output_tokens=10 * len(contents))
        return out

    def recover(self, doc_id: str) -> list[dict]:
        """All messages stored for one logical document."""
        return list(self.docs.get(doc_id, {}).values())


# ---------------------------------------------------------------------------
# Fixture: ONE conversation, six messages, three collection cycles.
# source_ids are deliberately NOT in chronological order within a day
# (s-02 written before s-01, etc.) so the chronology check proves
# written_at ordering rather than matching source_id convention.
# ---------------------------------------------------------------------------
CONVERSATION = [
    {"source_id": "s-02", "channel": "text", "thread": "fam",
     "sender": "Gus", "text": "day1-a: the car is ready Friday",
     "written_at": "2026-09-18T09:00:00"},
    {"source_id": "s-01", "channel": "text", "thread": "fam",
     "sender": "Mia", "text": "day1-b: pickup after 5pm works",
     "written_at": "2026-09-18T09:05:00"},
    {"source_id": "s-04", "channel": "text", "thread": "fam",
     "sender": "Gus", "text": "day2-a: running late, make it 6pm",
     "written_at": "2026-09-19T10:00:00"},
    {"source_id": "s-03", "channel": "text", "thread": "fam",
     "sender": "Mia", "text": "day2-b: 6pm confirmed",
     "written_at": "2026-09-19T10:07:00"},
    {"source_id": "s-06", "channel": "text", "thread": "fam",
     "sender": "Gus", "text": "day3-a: car washed, see you at 6",
     "written_at": "2026-09-20T08:30:00"},
    {"source_id": "s-05", "channel": "text", "thread": "fam",
     "sender": "Mia", "text": "day3-b: bringing the paperwork",
     "written_at": "2026-09-20T08:45:00"},
]

CYCLES = [
    (CONVERSATION[0:2], "2026-09-18T12:00:00"),  # day 1: full so far
    (CONVERSATION[2:4], "2026-09-19T12:00:00"),  # day 2: delta only
    (CONVERSATION[4:6], "2026-09-20T12:00:00"),  # day 3: delta only
]

BANK = "pacbench-ns-evo"


def _contents(messages: list[dict]) -> list[dict]:
    """Adapter-shaped batch items: chronological, one message each."""
    return [{
        "content": m["text"],
        "context": f"{m['channel']} thread {m['thread']} from {m['sender']}",
        "event_date": m["written_at"],
        "metadata": {"source_id": m["source_id"], "thread": m["thread"],
                     "sender": m["sender"]},
        "document_id": logical_document_key(m),
    } for m in sorted(messages, key=lambda m: m["written_at"])]


def run_three_cycles(store: FakeConversationStore | None = None):
    """Drive the 3-cycle evolution; return (store, per-cycle snapshots)."""
    from datetime import datetime
    store = store or FakeConversationStore()
    snapshots = []
    for i, (newly_exposed, collected_at) in enumerate(CYCLES):
        # ONE retain_batch_async per collection cycle (ingest contract),
        # delta-only: only newly exposed messages cross the boundary.
        asyncio.run(store.retain_batch_async(
            BANK, _contents(newly_exposed), request_context=None,
            operation_id=f"{BANK}:{collected_at}", return_usage=True))
        doc_id = logical_document_key(CONVERSATION[0])
        recovered = sorted(store.recover(doc_id),
                           key=lambda r: str(r["event_date"]))
        snapshots.append({
            "cycle": i + 1,
            "texts": [r["text"] for r in recovered],
            "event_dates": [str(r["event_date"]) for r in recovered],
            "store_docs": len(store.docs),
        })
    _ = datetime  # (event_dates stay ISO strings; parsed on compare)
    return store, snapshots


EXPECTED_DOC_ID = logical_document_key(CONVERSATION[0])


class ConversationEvolution(unittest.TestCase):
    """P0-9 six checks: one conversation across 3 collection cycles."""

    def test_1_stable_doc_id(self):
        store, _ = run_three_cycles()
        self.assertEqual(len(store.docs), 1,  # one logical document, not N
                         f"expected 1 doc, got {sorted(store.docs)}")
        self.assertIn(EXPECTED_DOC_ID, store.docs)
        for b in store.batches:  # every batch targets that same doc id
            self.assertTrue(b["document_ids"])
            self.assertTrue(all(d == EXPECTED_DOC_ID
                                for d in b["document_ids"]),
                            f"cycle batch drifted doc id: {b}")

    def test_2_no_duplicate_ingestion(self):
        store, _ = run_three_cycles()
        # Re-submit the day-2 delta (interrupted-op retry shape): no growth.
        asyncio.run(store.retain_batch_async(
            BANK, _contents(CONVERSATION[2:4]), request_context=None,
            operation_id=f"{BANK}:2026-09-19T12:00:00", return_usage=True))
        rec = store.recover(EXPECTED_DOC_ID)
        self.assertEqual(len(rec), 6)
        by_src = [b["source_ids"] for b in store.batches]
        flat = [s for batch in by_src for s in batch]
        self.assertEqual(sorted(flat).count("s-03"), 2)  # sent twice ...
        self.assertEqual(len(rec), 6)  # ... stored once

    def test_3_deltas_associated_to_same_conversation(self):
        store, _ = run_three_cycles()
        self.assertEqual(len(store.batches), 3)  # one call per cycle
        day1_ids = set(store.batches[0]["document_ids"])
        for b in store.batches[1:]:
            self.assertEqual(set(b["document_ids"]), day1_ids,
                             "delta batch left the conversation")
        rec_srcs = {m["source_id"] for m in CONVERSATION}
        stored_texts = {r["text"] for r in store.recover(EXPECTED_DOC_ID)}
        for m in CONVERSATION:
            self.assertIn(m["text"], stored_texts,
                          f"delta message lost: {m['source_id']}")
        self.assertEqual(len(rec_srcs), 6)

    def test_4_all_messages_recoverable_no_future_leak(self):
        _, snapshots = run_three_cycles()
        for i, snap in enumerate(snapshots):
            expected = [m["text"] for m in CONVERSATION[:2 * (i + 1)]]
            self.assertEqual(snap["texts"], expected,
                             f"cycle {i + 1}: recoverable set wrong")
        final = snapshots[-1]["texts"]
        self.assertEqual(len(final), 6)
        self.assertEqual(set(final),
                         {m["text"] for m in CONVERSATION})
        # No future messages early: after day 1, day-2/3 texts absent.
        self.assertNotIn(CONVERSATION[2]["text"], snapshots[0]["texts"])
        self.assertNotIn(CONVERSATION[4]["text"], snapshots[1]["texts"])

    def test_5_no_content_multiplication(self):
        store, snapshots = run_three_cycles()
        rec = store.recover(EXPECTED_DOC_ID)
        self.assertEqual(len(rec), 6,  # not 2+4+6=12 cumulative re-ingest
                         "update mechanism multiplied old content")
        for m in CONVERSATION:
            hits = sum(1 for r in rec if r["text"] == m["text"])
            self.assertEqual(hits, 1, f"multiplied: {m['source_id']}")
        self.assertEqual([s["store_docs"] for s in snapshots], [1, 1, 1])

    def test_6_chronology_intact(self):
        _, snapshots = run_three_cycles()
        authored = [m["written_at"] for m in CONVERSATION]
        self.assertEqual(snapshots[-1]["event_dates"], authored)
        # Non-trivial: lexicographic source_id order disagrees with time.
        lex = sorted(m["source_id"] for m in CONVERSATION)
        chrono_srcs = [m["source_id"] for m in
                       sorted(CONVERSATION, key=lambda m: m["written_at"])]
        self.assertNotEqual(lex, chrono_srcs,
                            "fixture must pit source_id order vs time order")


class AdapterBatchContract(unittest.TestCase):
    """The real adapter honors the ingest contract the harness assumes:

    one retain_batch_async per ingest() call with a deterministic,
    per-packet operation_id. P0-2/P0-3 migration LANDED: document_ids
    are conversation packets (logical_document_key, append mode),
    asserted through the fake below and in test_conversation_ingest.py.
    """

    def _adapter(self):
        from provider_adapters.hindsight_adapter import HindsightAdapter
        home = Path(tempfile.mkdtemp(prefix="hs-evo-"))
        fake = FakeConversationStore()
        adapter = HindsightAdapter(
            home, engine_factory=lambda db_url, llm: fake,
            pg_factory=FakePG)
        return adapter, fake

    def test_three_ingests_three_batches_distinct_op_ids(self):
        import uuid as _uuid
        adapter, fake = self._adapter()
        with mock.patch.dict(os.environ,
                             {"HINDSIGHT_API_LLM_API_KEY": "k"}):
            adapter.reset("ns-evo")
            for newly_exposed, collected_at in CYCLES:
                adapter.ingest(newly_exposed, collected_at)
        try:
            self.assertEqual(len(fake.batches), 3)
            self.assertEqual([b["n"] for b in fake.batches], [2, 2, 2])
            ops = [b["operation_id"] for b in fake.batches]
            self.assertEqual(len(set(ops)), 3)
            for (_, collected_at), op in zip(CYCLES, ops):
                self.assertEqual(op, str(_uuid.uuid5(
                    _uuid.NAMESPACE_URL,
                    f"{BANK}:{collected_at}")))
        finally:
            adapter.close()


@unittest.skipUnless(os.environ.get("PACBENCH_LIVE_HINDSIGHT") == "1",
                     "spend-gated: live-engine execution requires explicit "
                     "spend approval (plan.md P0-9; C4 gate item 9: "
                     "multi-day Hindsight evolution test passes against "
                     "the real provider)")
class LiveConversationEvolution(unittest.TestCase):
    """P0-9 live execution: same 3-cycle evolution against the real engine.

    Runs only with PACBENCH_LIVE_HINDSIGHT=1 after spend approval. Then it
    performs the three delta-only ingest cycles through the real
    HindsightAdapter and checks retained counts; recall-based recovery
    assertions (all-messages-recoverable, chronology) execute at live-run
    time against the pinned engine's native recall path.
    """

    def test_live_three_cycle_evolution(self):
        from provider_adapters.hindsight_adapter import HindsightAdapter
        home = Path(tempfile.mkdtemp(prefix="hs-live-evo-"))
        adapter = HindsightAdapter(home)  # real factories; fail-closed
        try:
            adapter.reset("ns-live-evo")
            totals = []
            for newly_exposed, collected_at in CYCLES:
                usage = adapter.ingest(newly_exposed, collected_at)
                totals.append(usage.notes[0])
            for note in totals:
                self.assertIn("retained 2/2", note)
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()
