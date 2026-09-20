"""Stage 1 evaluation-contract checks: immutable inputs, collection chronology, hidden
expectations kept out of public data, reproducible grading, and correct marking of good,
missing, wrong, and contradictory answers. All free, local, and model-free.
"""

import dataclasses
import re
import unittest
from pathlib import Path

from pacbench import corpus
from pacbench.corpus import poker_move
from pacbench.grade import grade
from pacbench.observation import Observation, chronology_ok, delivery_order
from pacbench.rubric import Verdict

_PKG = Path(__file__).resolve().parents[1] / "pacbench"


def _where(life_id):
    life = next(l for l in corpus.all_lives() if l.id == life_id)
    return life, next(c for c in life.checkpoints if c.id.endswith("/where"))


class PACBenchIsMemcalFree(unittest.TestCase):
    def test_no_memcal_import_anywhere_in_the_package(self):
        offenders = []
        for path in _PKG.rglob("*.py"):
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if re.match(r"\s*(import|from)\s+memcal(\.|\s|$)", line):
                    offenders.append(f"{path.name}:{i}")
        self.assertEqual(offenders, [], f"pacbench must not import memcal: {offenders}")


class ObservationsAreImmutablePublicData(unittest.TestCase):
    def test_frozen(self):
        obs = poker_move.lives()[0].observations[0]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            obs.text = "tampered"

    def test_public_payload_carries_no_expectation(self):
        obs = poker_move.lives()[0].observations[0]
        keys = set(obs.public())
        self.assertNotIn("expected", keys)
        self.assertNotIn("forbidden", keys)
        self.assertEqual(keys, {f.name for f in dataclasses.fields(Observation)})

    def test_question_hides_the_answer_key(self):
        _, where = _where("poker-move")
        prompt = where.public_prompt().lower()
        for leaked in ("jordan", "saturday", "8pm", "44 birch"):
            self.assertNotIn(leaked, prompt)

    def test_delivery_order_is_stable_and_nonmutating(self):
        obs = list(poker_move.lives()[0].observations)
        before = [o.source_id for o in obs]
        ordered = delivery_order(obs)
        self.assertEqual([o.source_id for o in obs], before)          # input untouched
        self.assertEqual([o.collected_at for o in ordered],
                         sorted(o.collected_at for o in ordered))


class CollectionChronologyHolds(unittest.TestCase):
    def test_every_life_is_chronologically_sound(self):
        lives = corpus.all_lives()
        self.assertTrue(lives)
        for life in lives:
            self.assertEqual(chronology_ok(list(life.observations)), [], life.id)


class GradingIsReproducible(unittest.TestCase):
    def test_same_answer_same_verdicts(self):
        _, where = _where("poker-move")
        answer = "Poker's at Jordan's, 44 Birch Ave, this Saturday at 8pm."
        self.assertEqual(grade(where, answer).to_dict(), grade(where, answer).to_dict())


class GraderMarksTheFourOutcomes(unittest.TestCase):
    def setUp(self):
        _, self.where = _where("poker-move")

    def _verdicts(self, answer):
        return {gs.id: gs.verdict for gs in grade(self.where, answer).slots}

    def test_good_answer_is_all_correct_and_complete(self):
        g = grade(self.where, "Poker's at Jordan's, 44 Birch Ave, this Saturday at 8pm.")
        self.assertEqual((g.correct, g.missed, g.misrepresented), (3, 0, 0))
        self.assertTrue(g.complete_plan)

    def test_missing_location_is_missed_not_wrong(self):
        v = self._verdicts("It's this Saturday at 8pm.")
        self.assertEqual(v["loc"], Verdict.MISSED)
        self.assertEqual(v["day"], Verdict.CORRECT)
        self.assertFalse(grade(self.where, "It's this Saturday at 8pm.").complete_plan)

    def test_stale_answer_is_misrepresented(self):
        g = grade(self.where, "Poker is Friday at 7pm at Sam's, 12 Ash Street.")
        self.assertEqual(g.misrepresented, 3)
        self.assertFalse(g.complete_plan)

    def test_asserting_both_as_current_loses_the_slot(self):
        # A genuine double-booking claim, no history framing.
        v = self._verdicts("Poker is Friday and Saturday, at Sam's and Jordan's.")
        self.assertEqual(v["loc"], Verdict.MISREPRESENTED)
        self.assertEqual(v["day"], Verdict.MISREPRESENTED)

    def test_history_aware_answer_is_still_correct(self):
        # Keeping the old plan as history must not be punished as a stale assertion.
        g = grade(self.where,
                  "Poker moved to Saturday 8pm at Jordan's; it used to be Friday 7pm at Sam's.")
        self.assertEqual((g.correct, g.misrepresented), (3, 0))
        self.assertTrue(g.complete_plan)


class HistoricalAndControlProbes(unittest.TestCase):
    def test_old_address_recovers_history(self):
        life = next(l for l in corpus.all_lives() if l.id == "poker-move")
        hist = next(c for c in life.checkpoints if c.id.endswith("/old-address"))
        g = grade(hist, "It was originally at Sam's, 12 Ash Street.")
        self.assertEqual(g.correct, 1)

    def test_control_answers_the_unchanged_plan(self):
        _, where = _where("poker-unchanged")
        g = grade(where, "Poker is Friday at 7pm at Sam's, 12 Ash Street.")
        self.assertEqual((g.correct, g.misrepresented), (3, 0))
        self.assertTrue(g.complete_plan)


class OpenEndedScoresOptionsNotProse(unittest.TestCase):
    def setUp(self):
        life = next(l for l in corpus.all_lives() if l.id == "weekend-plans")
        self.fun = next(c for c in life.checkpoints if c.id.endswith("/anything-fun"))

    def test_full_recall_no_traps(self):
        g = grade(self.fun, "You've got Members' Day at the Foundry Saturday, "
                            "dinner with Dana, and family visiting Sunday.")
        self.assertEqual((g.options_found, g.options_total), (3, 3))
        self.assertEqual(g.suggested_forbidden, 0)
        self.assertEqual((g.recall, g.precision), (1.0, 1.0))

    def test_missing_an_option_lowers_recall(self):
        g = grade(self.fun, "There's Members' Day at the Foundry, and dinner with Dana.")
        self.assertEqual(g.options_found, 2)
        self.assertAlmostEqual(g.recall, 2 / 3)
        self.assertEqual(g.precision, 1.0)

    def test_suggesting_a_forbidden_option_lowers_precision(self):
        g = grade(self.fun, "You could hit the museum gala, the Foundry members' day, "
                            "dinner with Dana, or family Sunday.")
        self.assertEqual(g.suggested_forbidden, 1)
        self.assertEqual(g.recall, 1.0)
        self.assertAlmostEqual(g.precision, 3 / 4)


class AbstentionIsScoredSeparately(unittest.TestCase):
    def setUp(self):
        life = next(l for l in corpus.all_lives() if l.id == "weekend-plans")
        self.dinner = next(c for c in life.checkpoints if c.id.endswith("/dinner-day"))

    def test_hedging_is_correct(self):
        g = grade(self.dinner,
                  "Dana hasn't confirmed which day yet — Saturday or Sunday. Want me to ask?")
        self.assertEqual(g.abstention, 1.0)
        self.assertEqual(g.slots[0].verdict, Verdict.CORRECT)

    def test_guessing_a_day_is_misrepresented(self):
        g = grade(self.dinner, "Dinner with Dana is on Sunday.")
        self.assertEqual(g.abstention, 0.0)
        self.assertEqual(g.slots[0].verdict, Verdict.MISREPRESENTED)


class ImplicitControlIsAnsweredByRetrieval(unittest.TestCase):
    def test_pointed_question_resolves(self):
        life = next(l for l in corpus.all_lives() if l.id == "dentist-friday")
        when = life.checkpoints[0]
        g = grade(when, "Your dental cleaning is Friday at 2:30pm.")
        self.assertEqual((g.correct, g.misrepresented), (1, 0))


class DentistNowDispute(unittest.TestCase):
    """Pilot answer 'Tuesday Sep 22 at 3pm' vs rubric 'Thursday 4:30pm' (§33).

    Ground truth belongs to the corpus: email-rebook establishes Thursday
    4:30pm before the checkpoint, so the rubric is right and the answer is
    wrong — the grader must report MISREPRESENTED, never credit it.
    """

    def setUp(self):
        from pacbench.corpus import get_life
        life = get_life("dentist-depth")
        self.now = next(c for c in life.checkpoints if c.id.endswith("/now"))

    def test_rubric_truth_is_thursday_from_rebook(self):
        by_id = {s.id: s for s in self.now.slots}
        self.assertEqual(by_id["day"].expected, "Thursday")
        self.assertEqual(by_id["time"].expected, "4:30pm")
        self.assertIn("dentist-depth/email-rebook", by_id["day"].supported_by)

    def test_dispute_answer_is_misrepresented_not_credited(self):
        g = grade(self.now, "Tuesday Sep 22 at 3pm.")
        self.assertEqual((g.correct, g.missed, g.misrepresented), (0, 0, 2))
        self.assertFalse(g.complete_plan)

    def test_current_answer_is_complete(self):
        g = grade(self.now, "Your dentist appointment is Thursday at 4:30pm.")
        self.assertTrue(g.complete_plan)


class YesNoPolarityFollowsFirstCue(unittest.TestCase):
    """Denial-blind presence matching once credited 'never canceled' as 'yes'
    via an incidental 'confirmed'. Polarity now follows the first cue."""

    def setUp(self):
        from pacbench.corpus import get_life
        life = get_life("dentist-depth")
        self.canceled = next(c for c in life.checkpoints if c.id.endswith("/canceled"))
        plife = get_life("poker-collision")
        self.john = next(c for c in plife.checkpoints if c.id.endswith("/john-now"))
        self.eric_cancel = next(
            c for c in plife.checkpoints if c.id.endswith("/eric-canceled"))

    def test_affirmation_is_correct(self):
        g = grade(self.canceled, "Yes, it was canceled Wednesday.")
        self.assertEqual(g.slots[0].verdict, Verdict.CORRECT)

    def test_denial_with_incidental_affirm_word_is_not_correct(self):
        g = grade(self.canceled,
                  "No - it has been confirmed since you set it, never canceled.")
        # A false denial of a real cancellation is a confabulation, not a
        # silence: opposite-polarity YESNO slots carry the polarity as
        # forbidden, so this promotes from MISSED to MISREPRESENTED.
        self.assertEqual(g.slots[0].verdict, Verdict.MISREPRESENTED)

    def test_expected_no_answers_still_correct(self):
        self.assertEqual(
            grade(self.john, "No, John's game is canceled.").slots[0].verdict,
            Verdict.CORRECT)
        self.assertEqual(
            grade(self.eric_cancel, "No - still on, moved to 8pm Saturday.")
            .slots[0].verdict, Verdict.CORRECT)

    def test_false_affirmation_of_expected_no_is_not_correct(self):
        g = grade(self.eric_cancel, "Yes, Eric's game was canceled.")
        # Inventing a cancellation that never happened is misrepresentation.
        self.assertEqual(g.slots[0].verdict, Verdict.MISREPRESENTED)

    def test_overlapping_cue_prefers_longer_match(self):
        self.assertEqual(
            grade(self.john, "You are not going; game is off.").slots[0].verdict,
            Verdict.CORRECT)


class MovedFromFramingIsHistory(unittest.TestCase):
    """'Moved it from 7pm to 8pm' marks 7pm superseded even with words between
    the verb and 'from' (fresh-pilot eric-now graded it as current). A bare
    'moved to <stale>' with no from-framing still counts as current."""

    def setUp(self):
        from pacbench.corpus import get_life
        life = get_life("poker-collision")
        self.eric = next(c for c in life.checkpoints if c.id.endswith("/eric-now"))

    def test_moved_it_from_does_not_assert_old_time(self):
        g = grade(self.eric,
                  "You're on for Eric's game Sat Sep 26, 8pm at 15 Harbor Rd "
                  "with Eric Vance — bringing chips.\n\n"
                  "Eric moved it from 7pm to 8pm on Sep 9.")
        by_id = {s.id: s.verdict for s in g.slots}
        self.assertEqual(by_id["time"], Verdict.CORRECT)
        self.assertEqual(by_id["day"], Verdict.CORRECT)

    def test_bare_moved_to_stale_still_counts(self):
        g = grade(self.eric, "Eric's game moved to 7pm Saturday.")
        by_id = {s.id: s.verdict for s in g.slots}
        self.assertEqual(by_id["time"], Verdict.MISREPRESENTED)


class DeclinedMentionIsNotASuggestion(unittest.TestCase):
    """Naming a declined option ('you passed on the waterfront festival') is
    not suggesting it (fresh-pilot anything-fun flagged it as forbidden). A
    genuine recommendation still flags."""

    def setUp(self):
        from pacbench.corpus import get_life
        life = get_life("coat-weekend")
        self.fun = next(c for c in life.checkpoints if c.id.endswith("/anything-fun"))

    def test_passed_on_mention_is_not_forbidden(self):
        g = grade(self.fun,
                  "From what I have: family brunch today, and you passed on "
                  "the waterfront festival next Saturday — otherwise free.")
        self.assertEqual(g.suggested_forbidden, 0)
        self.assertEqual(g.options_found, 1)

    def test_genuine_recommendation_still_flags(self):
        g = grade(self.fun, "You could hit the waterfront festival Saturday.")
        self.assertEqual(g.suggested_forbidden, 1)


class RendererPreservesTheKey(unittest.TestCase):
    def setUp(self):
        from pacbench.render import Beat
        self.beat = Beat(channel="groupme", sender="Jordan Vance",
                         draft="moving poker to Saturday 8pm at 44 Birch Ave",
                         must_include=("Saturday", "8pm", "44 Birch"))

    def test_no_generator_is_the_draft_verbatim(self):
        from pacbench.render import render
        self.assertEqual(render(self.beat), self.beat.draft)

    def test_entails_reports_dropped_tokens(self):
        from pacbench.render import entails
        self.assertEqual(entails("poker is Saturday at 8pm", self.beat), ["44 Birch"])
        self.assertEqual(entails("Saturday 8pm at 44 Birch Ave, be there", self.beat), [])

    def test_render_refuses_a_rewrite_that_drops_a_fact(self):
        from pacbench.render import RenderError, render
        with self.assertRaises(RenderError):
            render(self.beat, generate=lambda _p: "poker moved to Saturday night, come by")

    def test_render_accepts_a_faithful_rewrite(self):
        from pacbench.render import render
        out = render(self.beat,
                     generate=lambda _p: "heads up — poker's Saturday 8pm now, 44 Birch Ave")
        self.assertIn("44 Birch", out)


if __name__ == "__main__":
    unittest.main()
