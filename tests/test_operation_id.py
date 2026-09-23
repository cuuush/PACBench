"""Operation-id robustness: canonical boundary, agreement, replay/differ.

OFFLINE-ONLY: fakes only (no network, no spend).

Covers the hardened ingest contract in
``provider_adapters/hindsight_adapter.py``:

* the ingest boundary derives canonically from batch contents (max
  ``collected_at`` over the supplied observations);
* a caller-supplied ``collected_at`` that disagrees with the derived
  boundary fails closed (``ValueError``) before any provider write;
* identical replay mints an identical id; a different boundary mints
  a different id; ONE id per ingest call;
* the adapter id equals ``pacbench.resume.same_operation_id`` for the
  same ``(bank, boundary)`` inputs (single shared helper, no drift).
* mixed-format spellings of one instant (``+00:00`` vs naive, padding,
  offsets) collapse to one canonical boundary and one id — never a
  fork; the corpus stamp format is already canonical, so uniform
  corpus-format batches keep byte-identical ids.
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

from pacbench.resume import (  # noqa: E402
    OPERATION_ID_NAMESPACE,
    _normalize_ts,
    ingest_boundary,
    same_operation_id,
)


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
    """Records retain_batch_async calls (bank + operation_id)."""

    def __init__(self):
        self.calls = []

    async def retain_batch_async(self, bank, contents,
                                 request_context=None,
                                 operation_id=None,
                                 return_usage=False, **kw):
        self.calls.append({"bank": bank, "operation_id": operation_id,
                           "n": len(contents)})
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
    home = Path(tempfile.mkdtemp(prefix="hs-opid-"))
    engine = SpyEngine()
    adapter = HindsightAdapter(
        home, engine_factory=lambda db_url, llm: engine,
        pg_factory=FakePG)
    return adapter, engine


def _reset(adapter, namespace="ns-opid"):
    with mock.patch.dict(os.environ,
                         {"HINDSIGHT_API_LLM_API_KEY": "k"}):
        adapter.reset(namespace)


def _obs(source_id, collected_at, text="hello"):
    return {"source_id": source_id, "channel": "text", "thread": "fam",
            "sender": "Gus", "text": text,
            "written_at": "2026-09-18T09:00:00",
            "collected_at": collected_at}


BANK = "pacbench-ns-opid"
EARLY = "2026-09-18T10:00:00"
LATE = "2026-09-18T12:00:00"


class CanonicalBoundary(unittest.TestCase):
    def test_boundary_is_max_over_batch_contents(self):
        batch = [_obs("m1", EARLY), _obs("m2", LATE)]
        self.assertEqual(ingest_boundary(batch), LATE)
        self.assertEqual(ingest_boundary(list(reversed(batch))), LATE)

    def test_namespace_stays_in_agreement(self):
        self.assertIs(OPERATION_ID_NAMESPACE, uuid.NAMESPACE_URL)


class ReplayAndDiffer(unittest.TestCase):
    def test_identical_replay_identical_id(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [_obs("m1", EARLY), _obs("m2", LATE)]
            adapter.ingest([dict(o) for o in batch], LATE)
            adapter.ingest([dict(o) for o in batch], LATE)  # replay
            self.assertEqual(len(engine.calls), 2)
            self.assertEqual(engine.calls[0]["operation_id"],
                             engine.calls[1]["operation_id"])
        finally:
            adapter.close()

    def test_different_boundary_different_id(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            adapter.ingest([_obs("m1", EARLY)], EARLY)
            adapter.ingest([_obs("m1", LATE)], LATE)
            self.assertEqual(len(engine.calls), 2)
            self.assertNotEqual(engine.calls[0]["operation_id"],
                                engine.calls[1]["operation_id"])
        finally:
            adapter.close()

    def test_one_id_per_ingest_call(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            adapter.ingest([_obs("m1", LATE), _obs("m2", LATE)], LATE)
            self.assertEqual(len(engine.calls), 1)
            self.assertIsNotNone(engine.calls[0]["operation_id"])
        finally:
            adapter.close()


class FailClosed(unittest.TestCase):
    def test_caller_boundary_disagreeing_with_contents_refused(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [_obs("m1", EARLY), _obs("m2", LATE)]
            with self.assertRaises(ValueError):
                adapter.ingest(batch, EARLY)  # stale boundary, not the max
            # Refused BEFORE any provider write.
            self.assertEqual(engine.calls, [])
        finally:
            adapter.close()

    def test_missing_collected_at_refused(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            bad = {"source_id": "m1", "channel": "text", "thread": "fam",
                   "sender": "Gus", "text": "hello",
                   "written_at": "2026-09-18T09:00:00"}
            with self.assertRaises(ValueError):
                adapter.ingest([bad], LATE)
            self.assertEqual(engine.calls, [])
        finally:
            adapter.close()

    def test_empty_batch_refused(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            with self.assertRaises(ValueError):
                adapter.ingest([], LATE)
            self.assertEqual(engine.calls, [])
        finally:
            adapter.close()


class ResumeParity(unittest.TestCase):
    def test_adapter_id_matches_shared_helper(self):
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            batch = [_obs("m1", EARLY), _obs("m2", LATE)]
            adapter.ingest(batch, LATE)
            op = engine.calls[0]["operation_id"]
            boundary = ingest_boundary(batch)
            self.assertEqual(boundary, LATE)
            # Same helper, same inputs, same id — adapter and resume
            # can never drift.
            self.assertEqual(op, same_operation_id((BANK, boundary)))
            self.assertEqual(
                op, str(uuid.uuid5(uuid.NAMESPACE_URL,
                                   f"{BANK}:{LATE}")))
        finally:
            adapter.close()


NAIVE_INSTANT = "2026-03-02T10:00:00"
AWARE_INSTANT = "2026-03-02T10:00:00+00:00"


class MixedFormatInstantIdentity(unittest.TestCase):
    """One instant, however spelled, mints one id (no resume fork)."""

    def test_same_instant_mints_identical_id_across_formats(self):
        self.assertEqual(ingest_boundary([_obs("m1", NAIVE_INSTANT)]),
                         ingest_boundary([_obs("m1", AWARE_INSTANT)]))
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            adapter.ingest([_obs("m1", NAIVE_INSTANT)], NAIVE_INSTANT)
            adapter.ingest([_obs("m1", AWARE_INSTANT)], AWARE_INSTANT)
            # Cross-format caller stamp agrees too (normalized
            # before comparing).
            adapter.ingest([_obs("m1", NAIVE_INSTANT)], AWARE_INSTANT)
            ids = [c["operation_id"] for c in engine.calls]
            self.assertEqual(len(ids), 3)
            self.assertEqual(ids[0], ids[1])
            self.assertEqual(ids[0], ids[2])
            self.assertEqual(ids[0],
                             same_operation_id((BANK, NAIVE_INSTANT)))
        finally:
            adapter.close()

    def test_trailing_space_stamps_do_not_fork(self):
        padded = f"  {LATE}  "
        self.assertEqual(ingest_boundary([_obs("m1", padded)]), LATE)
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            adapter.ingest([_obs("m1", padded)], LATE)
            adapter.ingest([_obs("m1", LATE)], padded)
            self.assertEqual(engine.calls[0]["operation_id"],
                             engine.calls[1]["operation_id"])
        finally:
            adapter.close()

    def test_chronological_max_beats_lexicographic(self):
        # Lexicographic max would pick 10:00+02:00 (= 08:00Z); the true
        # latest instant is 09:00Z.
        batch = [_obs("m1", "2026-03-02T10:00:00+02:00"),
                 _obs("m2", "2026-03-02T09:00:00+00:00")]
        self.assertEqual(ingest_boundary(batch), "2026-03-02T09:00:00")
        self.assertEqual(ingest_boundary(list(reversed(batch))),
                         "2026-03-02T09:00:00")

    def test_garbage_stamp_fails_closed(self):
        with self.assertRaises(ValueError):
            ingest_boundary([_obs("m1", "not-a-time")])
        adapter, engine = _adapter()
        try:
            _reset(adapter)
            with self.assertRaises(ValueError):
                adapter.ingest([_obs("m1", LATE)], "not-a-time")
            self.assertEqual(engine.calls, [])
        finally:
            adapter.close()


class CorpusFormatStability(unittest.TestCase):
    """Uniform corpus-format batches keep byte-identical op-ids."""

    def test_corpus_stamp_format_is_already_canonical(self):
        from pacbench.authoring import ScenePlan, spread_timestamps
        corpus = spread_timestamps(ScenePlan(
            scene="Two friends make a plan", test_intent="Recall the plan",
            window_start="2026-03-02T09:00:00",
            window_end="2026-03-02T10:00:00"), 2)[0]
        self.assertEqual(_normalize_ts(corpus), corpus)
        self.assertEqual(_normalize_ts("2026-03-02T09:00:00"),
                         "2026-03-02T09:00:00")
        batch = [_obs("m1", "2026-03-02T09:00:00"),
                 _obs("m2", "2026-03-02T10:00:00")]
        self.assertEqual(ingest_boundary(batch), "2026-03-02T10:00:00")
        self.assertEqual(
            same_operation_id((BANK, "2026-03-02T10:00:00")),
            str(uuid.uuid5(uuid.NAMESPACE_URL,
                           f"{BANK}:2026-03-02T10:00:00")))


if __name__ == "__main__":
    unittest.main()
