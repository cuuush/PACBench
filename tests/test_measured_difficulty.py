"""P0-13: measured difficulty is attach-at-freeze; author tags never bucket.

A synthetic life requests ``update-depth-8`` but renders only 7 distinct
support batches: the measured block must say 7, the depth-mismatch flag
must fire, the freeze bundle must carry both, and comparative buckets
(report inputs) must come from the measured block — never from the tags.
Offline, deterministic, no network.
"""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


def _life():
    from pacbench.life import Life
    from pacbench.observation import Observation
    from pacbench.rubric import Checkpoint, Kind, Norm, Slot

    def ob(sid, thread, channel, sender, at, text="msg"):
        return Observation(source_id=sid, channel=channel, thread=thread,
                           written_at=at, arrived_at=at, collected_at=at,
                           sender=sender, text=text)

    # cp-deep: 8 support observations but only 7 distinct collection
    # instants (e6/e7 share one) — requested depth 8, measured depth 7.
    deep_times = [f"2026-09-{10 + i:02d}T09:00:00" for i in range(6)] + [
        "2026-09-16T09:00:00", "2026-09-16T09:00:00"]
    deep = [ob(f"e{i}", "tx-big", "text",
               "Maya" if i == 1 else "Gus", deep_times[i])
            for i in range(8)]
    # cp-shallow: 2 support observations, 2 instants, two channels.
    shallow = [ob("a0", "tx-small", "text", "Gus", "2026-09-17T09:00:00"),
               ob("a1", "mail-1", "email", "Gus", "2026-09-18T09:00:00")]
    # Fillers: tx-big reaches 10 messages; 13 observations total.
    fillers = [ob("f0", "tx-big", "text", "Gus", "2026-09-11T10:00:00"),
               ob("f1", "tx-big", "text", "Gus", "2026-09-12T10:00:00"),
               ob("f3", "tx-small", "text", "Gus", "2026-09-13T10:00:00")]
    cp_deep = Checkpoint(
        id="t/deep", at="2026-09-20T12:00:00", question="Deep?",
        difficulty_tags=("update-depth-8",),
        slots=(Slot(id="s-deep", kind=Kind.CURRENT, norm=Norm.TEXT,
                    expected="x",
                    supported_by=tuple(f"e{i}" for i in range(8))),))
    cp_shallow = Checkpoint(
        id="t/shallow", at="2026-09-19T12:00:00", question="Shallow?",
        difficulty_tags=("update-depth-2",),
        slots=(Slot(id="s-shallow", kind=Kind.CURRENT, norm=Norm.TEXT,
                    expected="y", supported_by=("a0", "a1")),))
    return Life(id="t", bet="b", role="development",
                observations=tuple(deep + shallow + fillers),
                checkpoints=(cp_deep, cp_shallow),
                difficulty_tags=("update-depth-8", "update-depth-2",
                                 "cross-channel-2", "entity-collision-2"))


class MeasuredDifficulty(unittest.TestCase):
    def test_measured_values_match_rendered_corpus(self):
        from pacbench.difficulty import measured_difficulty
        life = _life()
        m = measured_difficulty(life)
        self.assertEqual(m["life_id"], "t")
        self.assertEqual(m["max_thread_size"], 10)
        self.assertEqual(m["total_exposed"], 13)
        # 8 support ids, 7 distinct instants; shallow renders 2.
        self.assertEqual(m["measured_update_depth"], 7)
        self.assertEqual(m["per_checkpoint"]["t/deep"]["support_batches"], 7)
        self.assertEqual(m["per_checkpoint"]["t/shallow"]["support_batches"], 2)
        # Relevant threads: deep hides in 10-thread tx-big; shallow spans
        # tx-small (2) + mail-1 (1).
        self.assertEqual(
            m["per_checkpoint"]["t/deep"]["relevant_thread_size"], 10)
        self.assertEqual(
            m["per_checkpoint"]["t/shallow"]["relevant_thread_size"], 2)
        # Channels: text + email overall; shallow needs both.
        self.assertEqual(m["channel_count"], 2)
        self.assertEqual(m["max_required_channels"], 2)
        # Collision proxy: deep support carries 2 distinct voices.
        self.assertEqual(m["collision_count"], 2)
        # Evidence age: deep latest 09-16T09 -> 09-20T12 = 99h;
        # shallow latest 09-18T09 -> 09-19T12 = 27h.
        self.assertEqual(
            m["per_checkpoint"]["t/deep"]["evidence_age_hours"], 99.0)
        self.assertEqual(
            m["per_checkpoint"]["t/shallow"]["evidence_age_hours"], 27.0)
        self.assertEqual(m["evidence_age_hours"]["max"], 99.0)
        self.assertEqual(m["evidence_age_hours"]["min"], 27.0)
        json.dumps(m)  # freeze artifact must stay JSON-serializable

    def test_depth_mismatch_flagged_not_silently_bucketed(self):
        from pacbench.difficulty import (flag_depth_mismatch,
                                         measured_difficulty,
                                         measured_tags_for_checkpoint)
        life = _life()
        measured = measured_difficulty(life)
        flag = flag_depth_mismatch(life, measured)
        self.assertTrue(flag["has_mismatch"])
        self.assertEqual(flag["requested_max"], 8)
        self.assertEqual(flag["measured_update_depth"], 7)
        deep = next(e for e in flag["mismatches"]
                    if e["checkpoint_id"] == "t/deep")
        self.assertEqual((deep["requested"], deep["measured"],
                          deep["mismatch"]), (8, 7, True))
        shallow = next(e for e in flag["mismatches"]
                       if e["checkpoint_id"] == "t/shallow")
        self.assertEqual((shallow["requested"], shallow["measured"],
                          shallow["mismatch"]), (2, 2, False))
        # The depth-8 request buckets as measured-depth-7.
        tags = measured_tags_for_checkpoint(life, life.checkpoints[0],
                                            measured)
        self.assertIn("measured-depth-7", tags)
        self.assertNotIn("update-depth-8", tags)

    def test_freeze_attaches_measured_block(self):
        from pacbench.frozen import (bundle_hash, freeze_observations,
                                     load_bundle, write_bundle)
        life = _life()
        bundle = freeze_observations([life])
        entry = bundle["lives"]["t"]
        self.assertEqual(entry["measured"]["measured_update_depth"], 7)
        self.assertEqual(entry["measured"]["max_thread_size"], 10)
        self.assertEqual(entry["measured"]["total_exposed"], 13)
        diff = entry["difficulty"]
        self.assertIn("update-depth-8", diff["requested_tags"])
        self.assertEqual(diff["measured_update_depth"], 7)
        self.assertTrue(diff["depth_mismatch"])
        with tempfile.TemporaryDirectory(prefix="measured-") as tmp:
            path = Path(tmp) / "bundle.json"
            h = write_bundle(bundle, path)
            self.assertEqual(h, bundle_hash(bundle))
            back = load_bundle(path, h)
            self.assertEqual(back["lives"]["t"]["measured"]
                             ["measured_update_depth"], 7)
            self.assertTrue(back["lives"]["t"]["difficulty"]
                            ["depth_mismatch"])

    def test_comparative_buckets_require_measured(self):
        from pacbench.difficulty import (measured_tags_for_checkpoint,
                                         require_measured)
        from pacbench.grade import Grade, GradedSlot
        from pacbench.report import summarize
        from pacbench.rubric import Verdict
        life = _life()
        deep, shallow = life.checkpoints
        # No measured block, no bucket — never fall back to author tags.
        with self.assertRaises(ValueError):
            measured_tags_for_checkpoint(life, deep, None)
        with self.assertRaises(ValueError):
            require_measured({}, "t")
        with self.assertRaises(ValueError):
            require_measured({"t": {"schema": "nope"}}, "t")
        # Report inputs built from the measured block only.
        from pacbench.difficulty import measured_difficulty
        measured = measured_difficulty(life)
        by_life = {"t": require_measured({"t": measured}, "t")}
        cps = [replace(cp, difficulty_tags=measured_tags_for_checkpoint(
            life, cp, by_life["t"])) for cp in (deep, shallow)]
        grades = [Grade(checkpoint=cps[0].id,
                        slots=[GradedSlot(id="s-deep",
                                          verdict=Verdict.CORRECT,
                                          reason="t")]),
                  Grade(checkpoint=cps[1].id,
                        slots=[GradedSlot(id="s-shallow",
                                          verdict=Verdict.CORRECT,
                                          reason="t")])]
        buckets = summarize(grades, cps).to_dict()["by_difficulty_tag"]
        self.assertIn("measured-depth-7", buckets)
        self.assertIn("measured-depth-2", buckets)
        self.assertTrue(all(k.startswith("measured-") for k in buckets))
        self.assertFalse(any("update-depth" in k for k in buckets))


if __name__ == "__main__":
    unittest.main()
