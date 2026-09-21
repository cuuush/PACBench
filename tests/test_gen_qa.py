"""Generation QA screens: synthetic unit tests (no model) plus the
race-week proof run (structural QA on the real draft life).

No network, no model, no Hermes — build_life with generate=None is
deterministic and offline.
"""

import unittest

from pacbench import gen_qa
from pacbench.corpus import life_f_race


class AddedAtoms(unittest.TestCase):
    def test_clean_rewrite_passes(self):
        self.assertEqual(
            gen_qa.added_atoms(
                "Your reservation is at eight in the evening",
                "reservation at 8pm"),
            set())

    def test_added_weekday_is_an_extra_fact(self):
        self.assertIn(
            "saturday",
            gen_qa.added_atoms(
                "Bib pickup Saturday 5pm",
                "Bib pickup Friday"))

    def test_added_time_and_money_flagged(self):
        hits = gen_qa.added_atoms(
            "Dinner 6pm, bring $20", "Dinner at my place")
        self.assertTrue({"6pm", "$20"} & hits)


class FillerScreen(unittest.TestCase):
    def test_mundane_passes(self):
        self.assertEqual(
            gen_qa.screen_filler_text("Leftover soup is in the fridge"), [])

    def test_weekday_time_commitment_negate_flagged(self):
        hits = gen_qa.screen_filler_text(
            "See you Saturday at 8pm for dinner, no excuses")
        self.assertTrue(any(h.startswith("weekday:") for h in hits))
        self.assertIn("digits", hits)
        self.assertIn("word:dinner", hits)
        self.assertTrue(any(h.startswith("negate:") for h in hits))

    def test_extra_forbidden_is_per_life(self):
        self.assertEqual(gen_qa.screen_filler_text("The gym was fun"), [])
        self.assertIn("word:hall",
                      gen_qa.screen_filler_text("The hall was fun", ("hall",)))

    def test_substrings_do_not_convict(self):
        # "sun" in "sunset", "mar" in "farmers", "fri" in "fridge".
        self.assertEqual(
            gen_qa.screen_filler_text("That sunset was unreal"), [])
        self.assertEqual(
            gen_qa.screen_filler_text("The farmers documentary was lovely"),
            [])
        self.assertEqual(
            gen_qa.screen_filler_text("Soup is in the fridge"), [])

    def test_contraction_mon_is_not_monday(self):
        # Live render hit (f0539): c'mon is voice, not a weekday.
        self.assertEqual(
            gen_qa.added_atoms("c'mon y'all gotta get some water in ya",
                               "Hydrate, people"), set())
        self.assertIn("mon", gen_qa.added_atoms("see you mon", "see you"))


class RaceWeekProof(unittest.TestCase):
    """Criterion 1 proof points on the draft life (pre-Spark rendering)."""

    EXTRA = ("hall", "peanut", "housewarming", "coupon")

    def test_scale(self):
        life = life_f_race.lives()[0]
        self.assertGreaterEqual(len(life.observations), 1000)
        self.assertLessEqual(len(life.observations), 2000)

    def test_filler_screens_clean(self):
        life = life_f_race.lives()[0]
        bad = gen_qa.screen_life(life, life_f_race.manifest(), self.EXTRA)
        self.assertEqual(bad, {})

    def test_noise_ratio_in_acceptance_band(self):
        life = life_f_race.lives()[0]
        report = gen_qa.noise_report(life, life_f_race.manifest())
        self.assertTrue(report["in_acceptance_band"], report)

    def test_draft_renders_add_no_facts(self):
        # Draft-verbatim today; the same check runs on Spark renders.
        from pacbench.corpus.life_f_race import _evidence
        for beat in _evidence():
            self.assertEqual(gen_qa.added_atoms(beat.draft, beat.draft),
                             set(), beat.local_id)


if __name__ == "__main__":
    unittest.main()
