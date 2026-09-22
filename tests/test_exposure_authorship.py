"""Adversarial authorship tests for the exposure-equivalence gate.

ZERO-SPEND, offline, no network, no live flags: drives only
``pacbench.exposure`` (exposure_digest / compare_exposure) plus the
``pacbench.report.check_exposure_equivalence`` fail-closed wrapper.

Proves the gate FAILS LOUDLY on:
  (a) sender-only mutation (same texts/threads/times, one sender changed),
  (b) thread-only mutation (same texts/senders, one thread changed),
  (c) source-role mutation (from_owner flipped: same texts/threads/
      senders/times, only the source-role bit moved) — pinned in the
      10-field exposure projection, so the digest moves and the gate
      fails closed. Also covers a structured-payload mutation.
"""

import copy
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pacbench.exposure import (EXPOSURE_DIGEST_FIELDS,  # noqa: E402
                               compare_exposure, exposure_canonical,
                               exposure_digest)
from pacbench.report import check_exposure_equivalence  # noqa: E402


def _obs(source_id, collected_at, text="hello", sender="Gus", thread="tx",
         from_owner=False):
    return {"source_id": source_id, "channel": "text",
            "written_at": collected_at,
            "arrived_at": collected_at,
            "collected_at": collected_at, "sender": sender, "thread": thread,
            "text": text, "from_owner": from_owner, "structured": None}


def _manifest_for(batch, day="2026-09-08"):
    """One-boundary manifest honestly digested from the offered batch."""
    return {"import_batches": [{
        "batch_day": day,
        "collected_at": max(o["collected_at"] for o in batch),
        "observations": [o["source_id"] for o in batch],
        "exposure_digest": exposure_digest(batch),
    }]}


def _base_batch():
    return [_obs("m1", "2026-09-08T10:00:00", text="first"),
            _obs("m2", "2026-09-08T10:05:00", text="second")]


class SenderOnlyMutation(unittest.TestCase):
    def test_sender_only_mutation_fails_loudly(self):
        base = _base_batch()
        mutated = copy.deepcopy(base)
        mutated[1]["sender"] = "Mallory"
        # Same texts/threads/times — only the sender moved.
        for orig, mut in zip(base, mutated):
            for k in ("source_id", "channel", "thread", "text",
                      "written_at", "arrived_at", "collected_at"):
                self.assertEqual(mut[k], orig[k])
        self.assertIn("sender", EXPOSURE_DIGEST_FIELDS)
        # Digest level: the projection pins sender, so the digest moves.
        self.assertNotEqual(exposure_canonical(mutated)[1]["sender"],
                            exposure_canonical(base)[1]["sender"])
        self.assertNotEqual(exposure_digest(mutated), exposure_digest(base))
        # Gate level: exact per-boundary match required — digest mismatch.
        result = compare_exposure(_manifest_for(base),
                                 _manifest_for(mutated))
        self.assertFalse(result["match"], result)
        self.assertTrue(any("exposure digest differs" in m["reason"]
                            for m in result["mismatches"]), result)
        with self.assertRaises(RuntimeError):
            check_exposure_equivalence(_manifest_for(base),
                                       _manifest_for(mutated))


class ThreadOnlyMutation(unittest.TestCase):
    def test_thread_only_mutation_fails_loudly(self):
        base = _base_batch()
        mutated = copy.deepcopy(base)
        mutated[0]["thread"] = "ty"
        # Same texts/senders/times — only the thread moved.
        for orig, mut in zip(base, mutated):
            for k in ("source_id", "channel", "sender", "text",
                      "written_at", "arrived_at", "collected_at"):
                self.assertEqual(mut[k], orig[k])
        self.assertIn("thread", EXPOSURE_DIGEST_FIELDS)
        # Digest level: the projection pins thread, so the digest moves.
        self.assertNotEqual(exposure_canonical(mutated)[0]["thread"],
                            exposure_canonical(base)[0]["thread"])
        self.assertNotEqual(exposure_digest(mutated), exposure_digest(base))
        # Gate level: fails closed on the digest mismatch.
        result = compare_exposure(_manifest_for(base),
                                 _manifest_for(mutated))
        self.assertFalse(result["match"], result)
        self.assertTrue(any("exposure digest differs" in m["reason"]
                            for m in result["mismatches"]), result)
        with self.assertRaises(RuntimeError):
            check_exposure_equivalence(_manifest_for(base),
                                       _manifest_for(mutated))


class SourceRoleMutation(unittest.TestCase):
    def test_from_owner_flip_fails_loudly(self):
        self.assertIn("from_owner", EXPOSURE_DIGEST_FIELDS)
        self.assertIn("structured", EXPOSURE_DIGEST_FIELDS)
        base = _base_batch()
        mutated = copy.deepcopy(base)
        mutated[0]["from_owner"] = True
        # Same texts/threads/senders/times — only the source-role bit moved.
        for orig, mut in zip(base, mutated):
            for k in ("source_id", "channel", "thread", "sender",
                      "text", "written_at", "arrived_at",
                      "collected_at", "structured"):
                self.assertEqual(mut[k], orig[k])
        # Digest level: the projection pins from_owner, so the digest moves.
        self.assertNotEqual(exposure_canonical(mutated)[0]["from_owner"],
                            exposure_canonical(base)[0]["from_owner"])
        self.assertNotEqual(exposure_digest(mutated), exposure_digest(base))
        # Gate level: exact per-boundary match required — digest mismatch.
        result = compare_exposure(_manifest_for(base),
                                 _manifest_for(mutated))
        self.assertFalse(result["match"], result)
        self.assertTrue(any("exposure digest differs" in m["reason"]
                            for m in result["mismatches"]), result)
        with self.assertRaises(RuntimeError):
            check_exposure_equivalence(_manifest_for(base),
                                       _manifest_for(mutated))

    def test_structured_flip_fails_loudly(self):
        base = _base_batch()
        mutated = copy.deepcopy(base)
        mutated[1]["structured"] = {"event": "standup"}
        self.assertNotEqual(exposure_canonical(mutated)[1]["structured"],
                            exposure_canonical(base)[1]["structured"])
        self.assertNotEqual(exposure_digest(mutated), exposure_digest(base))
        result = compare_exposure(_manifest_for(base),
                                 _manifest_for(mutated))
        self.assertFalse(result["match"], result)
        self.assertTrue(any("exposure digest differs" in m["reason"]
                            for m in result["mismatches"]), result)
        with self.assertRaises(RuntimeError):
            check_exposure_equivalence(_manifest_for(base),
                                       _manifest_for(mutated))


if __name__ == "__main__":
    unittest.main()
