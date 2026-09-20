"""PACBench 0.1 contract checks: three development lives, authoring boundary,
validation, freeze/versioning, runner seams, deterministic reporting, and the
absolute naming rule (no LifeSpec concept anywhere).

Free, local, and model-free: nothing here touches the network.
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from pacbench import CORPUS_VERSION, VERSION
from pacbench.authoring import AuthoringError, BeatPlan, LifePlan, build_life
from pacbench.corpus import MANIFESTS, all_lives, development_lives, get_life
from pacbench.freeze import PINNED_HASH, digest, freeze
from pacbench.generation import GenerationError, load_config
from pacbench.grade import grade
from pacbench.hermes import IsolatedHermes, preflight
from pacbench.manifest import public_observations
from pacbench.observation import Observation, delivery_order
from pacbench.report import summarize
from pacbench.runner import exposed_observations, grade_answer, run_checkpoint
from pacbench.rubric import Kind, Norm, Slot, Verdict
from pacbench.validate import check, validate_corpus

REPO = Path(__file__).resolve().parents[1]


class VersionIs01(unittest.TestCase):
    def test_version(self):
        self.assertEqual(VERSION, "0.1.0")
        self.assertEqual(CORPUS_VERSION, "0.2.0")


class DevelopmentLives(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(sorted(l.id for l in development_lives()),
                         ["coat-weekend", "dentist-depth", "poker-collision",
                          "reunion-thread", "revoke-week"])

    def test_seven_day_horizon_with_older_history(self):
        for life in development_lives():
            self.assertEqual(life.horizon_days, 7, life.id)
            days = sorted(o.collected_at[:10] for o in life.observations)
            span = (days[-1], days[0])
            self.assertGreaterEqual(days[-1], "2026-09-13", life.id)   # Sunday
            self.assertLessEqual(days[0], "2026-09-07", life.id)       # Monday
            _ = span
            self.assertTrue(any(o.written_at < "2026-09-07" for o in life.observations),
                            f"{life.id}: expected older history")

    def test_required_channels_covered(self):
        channels = {o.channel for l in development_lives() for o in l.observations}
        for expected in ("email", "whatsapp", "groupme", "hermes", "slack"):
            self.assertIn(expected, channels)
        self.assertTrue({"imessage", "text"} & channels)

    def test_truth_is_distinct_from_evidence_and_rubric(self):
        for life in development_lives():
            self.assertTrue(life.truth, f"{life.id}: world truth must be stated")
            self.assertTrue(life.difficulty_tags, life.id)
            self.assertTrue(life.checkpoints, life.id)
            for truth in life.truth:
                self.assertTrue(truth.statement.strip())

    def test_checkpoints_annotated(self):
        for life in development_lives():
            for cp in life.checkpoints:
                self.assertTrue(cp.family, f"{cp.id}: task family required")
                self.assertTrue(cp.difficulty_tags, f"{cp.id}: difficulty tags required")
                for tag in cp.difficulty_tags:
                    self.assertIn(tag, life.difficulty_tags, cp.id)


class SplitDeclaration(unittest.TestCase):
    """Contract §39: every life declares its split; moving a life between
    splits is deliberate (edit this table). Development lives debug the full
    system; calibration families are small grader/pipeline checks; held-out
    is empty until the comparative corpus exists — and an empty held-out set
    is exactly why no comparative claim is valid yet."""

    def test_declared_splits(self):
        by_id = {life.id: life.role for life in all_lives()}
        self.assertEqual(
            sorted(i for i, r in by_id.items() if r == "development"),
            ["coat-weekend", "dentist-depth", "poker-collision",
             "reunion-thread", "revoke-week"])
        self.assertEqual(
            sorted(i for i, r in by_id.items() if r == "calibration"),
            ["dentist-friday", "poker-move", "poker-unchanged",
             "weekend-plans"])
        self.assertEqual(
            [i for i, r in by_id.items() if r == "held-out"], [])

    def test_roles_are_known(self):
        from pacbench.validate import ROLES
        for life in all_lives():
            self.assertIn(life.role, ROLES, life.id)


class HiddenKeysStayHidden(unittest.TestCase):
    def _slots_of(self, life_id):
        life = get_life(life_id)
        slots = []
        for cp in life.checkpoints:
            slots.extend((*cp.slots, *cp.supported, *cp.forbidden_options))
        return life, slots

    def test_public_payload_has_no_rubric_or_truth(self):
        for life in all_lives():
            for payload in public_observations(life):
                self.assertEqual(set(payload),
                                 {f.name for f in dataclasses.fields(Observation)})
            for cp in life.checkpoints:
                prompt = cp.public_prompt()
                for slot in (*cp.slots, *cp.supported):
                    for forbidden in slot.forbidden:
                        if slot.norm in (Norm.DATE, Norm.TIME, Norm.STATUS) \
                                or slot.kind is Kind.ABSTAIN:
                            self.assertNotIn(forbidden.casefold(), prompt.casefold(),
                                             f"{cp.id}: prompt leaks {forbidden!r}")

    def test_every_supported_id_resolves(self):
        for life in all_lives():
            known = {o.source_id for o in life.observations}
            _, slots = self._slots_of(life.id)
            for slot in slots:
                for oid in slot.supported_by:
                    self.assertIn(oid, known, f"{life.id}: {slot.id}")

    def test_manifests_resolve_and_stay_authoring_side(self):
        for life in development_lives():
            manifest = MANIFESTS[life.id]
            self.assertEqual(manifest.life_id, life.id)
            known = {o.source_id for o in life.observations}
            for entry in manifest.entries:
                for oid in entry.observation_ids:
                    self.assertIn(oid, known)
            # The adapter-visible payload carries no manifest content.
            payload_text = " ".join(p["text"] for p in public_observations(life))
            self.assertNotIn("fact_id", payload_text)


class MatchedControlsExist(unittest.TestCase):
    def test_update_control_is_stable(self):
        life = get_life("dentist-depth")
        eye = next(c for c in life.checkpoints if c.id.endswith("/eye-exam"))
        g = grade(eye, "Your eye exam is Wednesday at 10am.")
        self.assertTrue(all(s.verdict == Verdict.CORRECT for s in g.slots))

    def test_collision_control_is_single(self):
        life = get_life("poker-collision")
        lunch = next(c for c in life.checkpoints if c.id.endswith("/lunch"))
        g = grade(lunch, "Team lunch is Wednesday at noon.")
        self.assertTrue(all(s.verdict == Verdict.CORRECT for s in g.slots))

    def test_proactive_negative_control(self):
        life = get_life("coat-weekend")
        sam = next(c for c in life.checkpoints if c.id.endswith("/sam-visit"))
        quiet = grade(sam, "Have fun!")
        self.assertEqual((quiet.options_found, quiet.options_total), (0, 0))
        self.assertEqual((quiet.recall, quiet.precision), (1.0, 1.0))
        noisy = grade(sam, "Remember to bring the coat!")
        self.assertEqual(noisy.suggested_forbidden, 1)
        self.assertEqual(noisy.precision, 0.0)


class DeterministicGradingOfNewLives(unittest.TestCase):
    def test_dentist_current_and_stale(self):
        life = get_life("dentist-depth")
        now = next(c for c in life.checkpoints if c.id.endswith("/now"))
        good = grade(now, "Your dentist appointment is Thursday at 4:30pm.")
        self.assertTrue(good.complete_plan)
        stale = grade(now, "Dentist is Friday at 2pm at Bright Smile.")
        self.assertEqual(stale.misrepresented, 2)
        self.assertFalse(stale.complete_plan)

    def test_dentist_history(self):
        life = get_life("dentist-depth")
        original = next(c for c in life.checkpoints if c.id.endswith("/original"))
        g = grade(original, "It was originally Friday.")
        self.assertEqual(g.correct, 1)
        canceled = next(c for c in life.checkpoints if c.id.endswith("/canceled"))
        self.assertEqual(grade(canceled, "Yes, it was canceled Wednesday.").correct, 1)

    def test_collision_current_state(self):
        life = get_life("poker-collision")
        eric = next(c for c in life.checkpoints if c.id.endswith("/eric-now"))
        g = grade(eric, "Eric's game is still on Saturday at 8pm, 15 Harbor Rd.")
        self.assertTrue(g.complete_plan)
        merged = grade(eric, "Both games are canceled.")
        self.assertGreater(merged.misrepresented, 0)
        john = next(c for c in life.checkpoints if c.id.endswith("/john-now"))
        self.assertEqual(grade(john, "No, John's game is canceled.").correct, 1)

    def test_coat_reminder_and_opportunities(self):
        life = get_life("coat-weekend")
        coat = next(c for c in life.checkpoints if c.id.endswith("/coat-reminder"))
        self.assertEqual(grade(coat, "Remember to bring his coat!").correct, 1)
        fun = next(c for c in life.checkpoints if c.id.endswith("/anything-fun"))
        full = grade(fun, "Lantern Show concert Saturday, farmers market "
                          "Saturday morning, and family brunch Sunday.")
        self.assertEqual((full.options_found, full.options_total), (3, 3))
        self.assertEqual((full.recall, full.precision), (1.0, 1.0))
        brunch = next(c for c in life.checkpoints if c.id.endswith("/brunch-commitment"))
        self.assertEqual(grade(brunch, "Yes, you committed to brunch.").correct, 1)

    def test_unconfirmed_invitation_abstains(self):
        life = get_life("coat-weekend")
        rsvp = next(c for c in life.checkpoints if c.id.endswith("/concert-rsvp"))
        hedged = grade(rsvp, "Maya invited you but you haven't confirmed — "
                             "want me to ask?")
        self.assertEqual(hedged.abstention, 1.0)
        guess = grade(rsvp, "You're going Saturday.")
        self.assertEqual(guess.slots[0].verdict, Verdict.MISREPRESENTED)


class ValidationAndFreezing(unittest.TestCase):
    def test_whole_corpus_validates(self):
        check(all_lives(), MANIFESTS)  # raises on any error

    def test_freeze_is_deterministic(self):
        self.assertEqual(digest(all_lives()), digest(all_lives()))
        self.assertEqual(digest(all_lives()), PINNED_HASH)
        record = freeze(all_lives())
        self.assertEqual(record["corpus_version"], "0.2.0")
        self.assertEqual(len(record["sha256"]), 64)
        self.assertGreaterEqual(record["observations"], 40)

    def test_new_lives_are_frozen(self):
        for life in development_lives():
            obs = life.observations[0]
            with self.assertRaises(dataclasses.FrozenInstanceError):
                obs.text = "tampered"

    def test_yesno_slots_carry_opposite_polarity_as_forbidden(self):
        """A wrong-polarity YESNO answer is a confabulation (MISREPRESENTED),
        never a silence (MISSED). That requires every required YESNO slot to
        name the opposite polarity in ``forbidden`` — and the grader to match
        polarity on the full answer, since polarity is atemporal (past-tense
        verbs around it must not eat it via history-cue stripping)."""
        for life in all_lives():
            for cp in life.checkpoints:
                for slot in (*cp.slots, *cp.supported):
                    if slot.norm is not Norm.YESNO or not slot.required:
                        continue
                    want = slot.expected.strip().lower()
                    opposite = {"yes": "no", "no": "yes"}.get(want)
                    self.assertIsNotNone(
                        opposite, f"{life.id}/{cp.id}/{slot.id}: "
                                  f"YESNO expected {want!r} is not yes/no")
                    self.assertIn(
                        opposite, [f.strip().lower() for f in slot.forbidden],
                        f"{life.id}/{cp.id}/{slot.id}: opposite polarity "
                        f"{opposite!r} must be forbidden")
        # Behavior pin: polarity confabulation grades MISREPRESENTED.
        canceled = next(c for c in get_life("dentist-depth").checkpoints
                        if c.id.endswith("/canceled"))
        self.assertEqual(
            grade(canceled, "No, it was never canceled.").slots[0].verdict,
            Verdict.MISREPRESENTED)

    def test_historical_confabulation_is_misrepresented(self):
        """A wrong past value is confabulation (MISREPRESENTED), not silence
        — even though history answers are past-tense by necessity and the
        history-cue filter eats past clauses. But naming both values in a
        history-aware answer ("was 4410, changed to 8821") stays CORRECT:
        the expected value's presence wins before the fallback fires."""
        gate_old = next(c for c in get_life("revoke-week").checkpoints
                        if c.id.endswith("/gate-old"))
        self.assertEqual(
            grade(gate_old, "The old code was 8821.").slots[0].verdict,
            Verdict.MISREPRESENTED)
        self.assertEqual(
            grade(gate_old,
                  "The old code was 4410, changed to 8821.").slots[0].verdict,
            Verdict.CORRECT)
        self.assertEqual(
            grade(gate_old, "Not sure.").slots[0].verdict,
            Verdict.MISSED)


class RunnerSkeleton(unittest.TestCase):
    def test_exposure_follows_collection_clock(self):
        life = get_life("dentist-depth")
        early = {o.source_id for o in exposed_observations(life, "2026-09-08T18:00:00")}
        self.assertIn("dentist-depth/email-orig", early)
        self.assertNotIn("dentist-depth/email-rebook", early)
        late = {o.source_id for o in exposed_observations(life, "2026-09-10T12:00:00")}
        self.assertIn("dentist-depth/email-rebook", late)
        ordered = exposed_observations(life, "2026-09-13T23:59:59")
        self.assertEqual([o.collected_at for o in ordered],
                         sorted(o.collected_at for o in ordered))

    def test_grade_answer_matches_grade(self):
        life = get_life("dentist-depth")
        now = next(c for c in life.checkpoints if c.id.endswith("/now"))
        self.assertTrue(grade_answer(now, "It is Thursday at 4:30pm.").complete_plan)

    def test_live_stages_raise_instead_of_faking(self):
        from pacbench.adapters import ProviderAdapter
        life = get_life("dentist-depth")
        cp = life.checkpoints[0]
        hermes = IsolatedHermes(namespace="test", provider="none")
        with self.assertRaises(NotImplementedError):
            hermes.ask("hi", "2026-09-10T12:00:00")
        with self.assertRaises(TypeError):
            ProviderAdapter()  # abstract: no concrete adapter ships in 0.1
        with self.assertRaises(NotImplementedError):
            run_checkpoint(life, cp, hermes, None, "hash")
        self.assertTrue(preflight())  # unmet requirements stay visible

    def test_report_splits_current_and_history(self):
        life = get_life("dentist-depth")
        now = next(c for c in life.checkpoints if c.id.endswith("/now"))
        original = next(c for c in life.checkpoints if c.id.endswith("/original"))
        grades = [grade(now, "It is Thursday at 4:30pm."),
                  grade(original, "It was originally Friday.")]
        summary = summarize(grades, [now, original])
        d = summary.to_dict()
        self.assertEqual(d["slots"], 3)
        self.assertEqual(d["current_accuracy"], 1.0)
        self.assertEqual(d["historical_accuracy"], 1.0)
        self.assertEqual(d["complete_plan_accuracy"], 1.0)
        self.assertIn("update-depth-4", d["by_difficulty_tag"])


class AuthoringBoundary(unittest.TestCase):
    def test_build_rejects_unknown_thread(self):
        plan = LifePlan(id="x", bet="b", role="development", week_start="2026-09-07",
                        checkpoints=(),
                        beats=(BeatPlan(local_id="m", thread="nope", sender="s",
                                        day=0, clock="10:00", draft="hello"),))
        with self.assertRaises(AuthoringError):
            build_life(plan)

    def test_draft_ships_verbatim_without_generator(self):
        from pacbench.render import Beat, render
        beat = Beat(channel="groupme", sender="s", draft="plain draft",
                    must_include=("draft",))
        self.assertEqual(render(beat), "plain draft")


class GenerationConfig(unittest.TestCase):
    def test_env_parsing_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("MODEL=muse-spark-1.3-contributor\nMETA=topsecret\n")
            config, credential = load_config(env)
            self.assertEqual(config.model, "muse-spark-1.3-contributor")
            self.assertEqual(credential, "topsecret")
            self.assertNotIn("topsecret", repr(config))
        with self.assertRaises(GenerationError):
            load_config(Path(tmp) / "missing")

    def test_repo_env_selects_spark(self):
        config, credential = load_config(REPO / ".env")
        self.assertIn("muse-spark", config.model)
        self.assertTrue(len(credential) > 0)  # present; value never asserted


class AbsoluteNaming(unittest.TestCase):
    def test_no_forbidden_concept_name(self):
        forbidden = ["Life" + "Spec", "life" + "_" + "spec", "LIFE" + "SPEC"]
        offenders = []
        for path in (*REPO.rglob("*.py"), *REPO.glob("*.toml"), *REPO.glob("*.md")):
            if path.name == "test_v01.py":
                continue  # this file names the rule only to enforce it
            text = path.read_text()
            for name in forbidden:
                if name in text:
                    offenders.append(f"{path.name}: {name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
