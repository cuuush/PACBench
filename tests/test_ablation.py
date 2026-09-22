"""P0-12 support-graph ablation: removing a claimed-essential source must flip
a genuinely multi-source checkpoint to unknowable, leave an unrelated
ablation quiet, and never change what ``supported_by`` means."""

import unittest

from pacbench.life import Life
from pacbench.observation import Observation
from pacbench.rubric import Checkpoint, Kind, Norm, Slot
from pacbench.validate import (
    ablation_check,
    multi_source_dependency,
    validate_corpus,
    validate_life,
)


def _obs(source_id, channel, text, collected="2026-09-02T10:00:00"):
    return Observation(
        source_id=source_id,
        channel=channel,
        written_at="2026-09-02T09:00:00",
        arrived_at="2026-09-02T09:30:00",
        collected_at=collected,
        sender="sam",
        thread=f"{channel}-thread",
        text=text,
    )


def _life(life_id, observations, checkpoints):
    return Life(
        id=life_id,
        bet="support-graph-ablation",
        role="development",
        observations=tuple(observations),
        checkpoints=tuple(checkpoints),
        lineage=f"ablation-probe-{life_id}",
    )


def _multi_source_life():
    """Two required slots, each carried by exactly one channel's evidence."""
    obs = [
        _obs("abl/m1", "email", "The gala moved to the Grand Hall."),
        _obs("abl/m2", "slack", "Doors open at 4:30pm sharp."),
    ]
    cp = Checkpoint(
        id="abl/cp",
        at="2026-09-03T10:00:00",
        question="Where is the gala and when do doors open?",
        slots=(
            Slot(id="venue", kind=Kind.CURRENT, norm=Norm.ENTITY,
                 expected="Grand Hall", supported_by=("abl/m1",)),
            Slot(id="doors", kind=Kind.CURRENT, norm=Norm.TIME,
                 expected="4:30pm", supported_by=("abl/m2",)),
        ),
    )
    return _life("abl", obs, [cp]), cp


class AblationFlipsMultiSource(unittest.TestCase):
    def test_removing_one_essential_channel_flips_checkpoint(self):
        life, cp = _multi_source_life()
        self.assertEqual(validate_life(life), [])
        self.assertIn(cp.id, multi_source_dependency(life))
        by_channel = ablation_check(life, cp, "slack")
        self.assertTrue(by_channel.answer_changed_or_uncertain)
        self.assertEqual(by_channel.affected_slot_ids, ("doors",))
        by_source = ablation_check(life, cp, "abl/m1")
        self.assertTrue(by_source.answer_changed_or_uncertain)
        self.assertEqual(by_source.affected_slot_ids, ("venue",))

    def test_single_source_checkpoint_unaffected_by_unrelated_removal(self):
        obs = [
            _obs("sgl/m1", "email", "The gala moved to the Grand Hall."),
            _obs("sgl/m2", "slack", "Lunch menu is posted at noon."),
        ]
        cp = Checkpoint(
            id="sgl/cp",
            at="2026-09-03T10:00:00",
            question="Where is the gala?",
            slots=(
                Slot(id="venue", kind=Kind.CURRENT, norm=Norm.ENTITY,
                     expected="Grand Hall", supported_by=("sgl/m1",)),
            ),
        )
        life = _life("sgl", obs, [cp])
        self.assertEqual(validate_life(life), [])
        self.assertNotIn(cp.id, multi_source_dependency(life))
        for removal in ("sgl/m2", "slack"):
            result = ablation_check(life, cp, removal)
            self.assertFalse(result.answer_changed_or_uncertain, removal)
            self.assertEqual(result.affected_slot_ids, ())

    def test_redundant_citation_is_listed_but_not_proven(self):
        # Both channels cited, but only one carries the literal: candidacy
        # without dependence. Citation span != required channels.
        obs = [
            _obs("red/m1", "email", "The gala moved to the Grand Hall."),
            _obs("red/m2", "slack", "Reminder: the gala is this week."),
        ]
        cp = Checkpoint(
            id="red/cp",
            at="2026-09-03T10:00:00",
            question="Where is the gala?",
            slots=(
                Slot(id="venue", kind=Kind.CURRENT, norm=Norm.ENTITY,
                     expected="Grand Hall",
                     supported_by=("red/m1", "red/m2")),
            ),
        )
        life = _life("red", obs, [cp])
        self.assertEqual(validate_life(life), [])
        self.assertIn(cp.id, multi_source_dependency(life))
        result = ablation_check(life, cp, "slack")
        self.assertFalse(result.answer_changed_or_uncertain)
        essential = ablation_check(life, cp, "email")
        self.assertTrue(essential.answer_changed_or_uncertain)

    def test_supported_by_meaning_untouched(self):
        # Multi-channel citation still validates as plain cited evidence:
        # no sufficiency or necessity inferred by the structural layer.
        life, cp = _multi_source_life()
        self.assertEqual(validate_corpus([life]), [])
        with self.assertRaises(ValueError):
            ablation_check(life, cp, "no-such-source")

    def test_future_evidence_does_not_count_as_kept(self):
        obs = [
            _obs("fut/m1", "email", "The gala moved to the Grand Hall.",
                 collected="2026-09-05T10:00:00"),
        ]
        cp = Checkpoint(
            id="fut/cp",
            at="2026-09-03T10:00:00",
            question="Where is the gala?",
            slots=(
                Slot(id="venue", kind=Kind.CURRENT, norm=Norm.ENTITY,
                     expected="Grand Hall", supported_by=("fut/m1",)),
            ),
        )
        life = _life("fut", obs, [cp])
        # Future evidence is a structural error, and ablation cannot treat
        # it as knowable at checkpoint time either.
        self.assertTrue(any("future evidence" in e for e in validate_life(life)))
        result = ablation_check(life, cp, "slack")
        self.assertFalse(result.answer_changed_or_uncertain)


    def test_mixed_offset_timestamps_compare_chronologically(self):
        # Lexically "2026-09-03T10:00:00+05:00" > "2026-09-03T08:00:00+00:00",
        # so a string-<= time filter wrongly excludes this evidence as future.
        # Chronologically it is 05:00 UTC <= 08:00 UTC, so it counts and
        # removing the only carrying channel must flip the checkpoint.
        obs = [
            _obs("tz/m1", "email", "The gala moved to the Grand Hall.",
                 collected="2026-09-03T10:00:00+05:00"),
        ]
        cp = Checkpoint(
            id="tz/cp",
            at="2026-09-03T08:00:00+00:00",
            question="Where is the gala?",
            slots=(
                Slot(id="venue", kind=Kind.CURRENT, norm=Norm.ENTITY,
                     expected="Grand Hall", supported_by=("tz/m1",)),
            ),
        )
        life = _life("tz", obs, [cp])
        result = ablation_check(life, cp, "email")
        self.assertTrue(result.answer_changed_or_uncertain)
        self.assertEqual(result.affected_slot_ids, ("venue",))

    def test_unparseable_timestamps_raise_instead_of_string_compare(self):
        cp = Checkpoint(
            id="bad/cp",
            at="2026-09-03T10:00:00",
            question="Where is the gala?",
            slots=(
                Slot(id="venue", kind=Kind.CURRENT, norm=Norm.ENTITY,
                     expected="Grand Hall", supported_by=("bad/m1",)),
            ),
        )
        bad_obs = _life("bad", [_obs(
            "bad/m1", "email", "The gala moved to the Grand Hall.",
            collected="not-a-time")], [cp])
        with self.assertRaises(ValueError):
            ablation_check(bad_obs, cp, "email")
        good_obs = [_obs("bad/m1", "email",
                         "The gala moved to the Grand Hall.")]
        bad_cp = Checkpoint(
            id="bad/cp",
            at="yesterday",
            question="Where is the gala?",
            slots=(
                Slot(id="venue", kind=Kind.CURRENT, norm=Norm.ENTITY,
                     expected="Grand Hall", supported_by=("bad/m1",)),
            ),
        )
        with self.assertRaises(ValueError):
            ablation_check(_life("bad", good_obs, [bad_cp]), bad_cp, "email")


if __name__ == "__main__":
    unittest.main()
