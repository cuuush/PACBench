"""W4 verification (offline): collection split, CLI plumbing, provider boundary.

No network, no model, no Hermes run, no live homes. The live Hermes smoke is
proven by actually running the CLI (see the W4 stop report), not by this file.
"""

import sys
import unittest
from io import StringIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pacbench import live  # noqa: E402


def _payload(source_id, channel="email", collected_at="2026-09-08T10:00:00"):
    return {"source_id": source_id, "channel": channel,
            "written_at": "2026-09-08T09:00:00",
            "arrived_at": "2026-09-08T09:05:00",
            "collected_at": collected_at, "sender": "s", "thread": "t",
            "text": "hello", "from_owner": False, "structured": None}


class CollectionSplit(unittest.TestCase):
    def test_user_turns_never_enter_evidence(self):
        payloads = [
            _payload("m1"),
            _payload("t1", channel="hermes"),
            _payload("m2", channel="whatsapp"),
            _payload("t2", channel="agent"),
        ]
        evidence, turns = live.split_collection(payloads)
        self.assertEqual([p["source_id"] for p in evidence], ["m1", "m2"])
        self.assertEqual([p["source_id"] for p in turns], ["t1", "t2"])
        # Order preserved (delivery order in, same order out).
        self.assertEqual([p["source_id"] for p in evidence + turns],
                         ["m1", "m2", "t1", "t2"])

    def test_evidence_batches_group_by_collection_time(self):
        payloads = [
            _payload("b", collected_at="2026-09-09T10:00:00"),
            _payload("a", collected_at="2026-09-08T10:00:00"),
            _payload("a2", collected_at="2026-09-08T10:00:00"),
        ]
        batches = live._batched_by_collection(payloads)
        self.assertEqual([k for k, _ in batches],
                         ["2026-09-08T10:00:00", "2026-09-09T10:00:00"])
        self.assertEqual([p["source_id"] for _, b in batches for p in b],
                         ["a", "a2", "b"])


class CliPlumbing(unittest.TestCase):
    def test_list_lives_offline(self):
        from pacbench.__main__ import main
        out = StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            rc = main(["list-lives"])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertIn("dentist-depth", out.getvalue())

    def test_unknown_provider_is_usage_error(self):
        from pacbench.__main__ import main
        rc = main(["run-checkpoint", "--life", "dentist-depth",
                   "--checkpoint", "dentist-depth/eye-exam",
                   "--provider", "nope"])
        self.assertEqual(rc, 2)

    def test_unknown_checkpoint_is_usage_error(self):
        from pacbench.__main__ import main
        rc = main(["run-checkpoint", "--life", "dentist-depth",
                   "--checkpoint", "dentist-depth/nope",
                   "--provider", "memcal"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
