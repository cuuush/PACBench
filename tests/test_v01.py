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
from pacbench.grade import Grade, GradedSlot, grade
from pacbench.hermes import IsolatedHermes, preflight
from pacbench.manifest import public_observations
from pacbench.observation import Observation, delivery_order
from pacbench.report import summarize
from pacbench.runner import exposed_observations, grade_answer, run_checkpoint
from pacbench.rubric import Checkpoint, Kind, Norm, Slot, Verdict
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


class LineageDiscipline(unittest.TestCase):
    """Contract §39: close variants share one lineage, whatever their split.

    Lineage — not life names — is what split-independence is checked on.
    Calibration lives are deliberately lineage-shared with development
    (derivative by design, never independent evidence); a future held-out
    life must carry lineage unique across the corpus. Moving a life
    between lineages is deliberate (edit EXPECTED below)."""

    EXPECTED = {
        "dentist-depth": "dentist-cleaning",
        "dentist-friday": "weekend-opportunities",
        "poker-collision": "poker-reschedule",
        "poker-move": "poker-reschedule",
        "poker-unchanged": "poker-reschedule",
        "coat-weekend": "weekend-opportunities",
        "weekend-plans": "weekend-opportunities",
        "reunion-thread": "reunion-picnic",
        "revoke-week": "revoke-dinner",
    }

    def test_declared_lineages(self):
        by_id = {life.id: life.lineage for life in all_lives()}
        self.assertEqual(by_id, self.EXPECTED)

    def test_held_out_lineage_is_unique(self):
        """A renamed variant is not independent held-out evidence: no
        held-out life may share lineage with any other life. Vacuous
        today (held-out is empty) — standing guard for the 30-life
        corpus."""
        by_lineage: dict[str, list[str]] = {}
        for life in all_lives():
            by_lineage.setdefault(life.lineage, []).append(life.id)
        for life in all_lives():
            if life.role == "held-out":
                self.assertEqual(by_lineage[life.lineage], [life.id],
                                 life.id)

    def test_calibration_is_derivative(self):
        """Every calibration lineage contains a development life: small
        families debug the pipeline against their dev parent, and can
        never be promoted into comparative evidence."""
        dev_lineages = {life.lineage for life in all_lives()
                        if life.role == "development"}
        for life in all_lives():
            if life.role == "calibration":
                self.assertIn(life.lineage, dev_lineages, life.id)

    def test_controls_share_lineage(self):
        by_id = {life.id: life for life in all_lives()}
        for life in all_lives():
            if life.control_of:
                self.assertEqual(life.lineage,
                                 by_id[life.control_of].lineage, life.id)


class ExposureLog(unittest.TestCase):
    """Ruling 2026-09-21 (Q7): detailed inspection marks a life
    ``exposed_after_review`` and logs life + date + reason +
    version-after-which-exposed. Aggregate-only review never exposes.
    Vacuous today (held-out is empty, no rows) — standing guard."""

    LOG = REPO / "docs" / "exposure-log.md"

    def _rows(self):
        rows = []
        for line in self.LOG.read_text().splitlines():
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 6 or not cells[0]:
                continue
            if all(set(c) <= {"-"} for c in cells):
                continue  # separator row
            if cells[0] == "life":
                continue  # header row
            if cells[0].startswith("_("):
                continue  # placeholder row: mechanism waiting for lives
            rows.append(cells)
        return rows

    def test_role_vocab_uses_coauthor_term(self):
        from pacbench.validate import ROLES
        self.assertIn("exposed_after_review", ROLES)
        self.assertNotIn("exposed", ROLES)

    def test_log_rows_are_well_formed(self):
        import re
        for life, lineage, date, who, reason, version in self._rows():
            self.assertTrue(life)
            self.assertTrue(lineage, life)
            self.assertRegex(date, r"^\d{4}-\d{2}-\d{2}$", life)
            self.assertTrue(who, life)
            self.assertTrue(reason, life)
            self.assertTrue(version, life)

    def test_every_exposed_life_is_logged(self):
        logged = {life for life, _, _, _, _, _ in self._rows()}
        for life in all_lives():
            if life.role == "exposed_after_review":
                self.assertIn(life.id, logged, life.id)


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

    def test_unconfirmed_invitation_is_uncommitted(self):
        life = get_life("coat-weekend")
        rsvp = next(c for c in life.checkpoints if c.id.endswith("/concert-rsvp"))
        # Commitment status (not going) + known date: both atoms answerable.
        full = grade(rsvp, "No, you haven't RSVP'd — the concert is Saturday.")
        self.assertEqual({s.id: s.verdict for s in full.slots},
                         {"rsvp": Verdict.CORRECT, "date": Verdict.CORRECT})
        # Promoting the opportunity into a commitment confabulates the status
        # atom (the date atom stays correct — slots grade independently).
        guess = grade(rsvp, "You're going Saturday.")
        self.assertEqual({s.id: s.verdict for s in guess.slots},
                         {"rsvp": Verdict.MISREPRESENTED,
                          "date": Verdict.CORRECT})


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


class MeasuredDifficultyValues(unittest.TestCase):
    """Q25: difficulty values measured from the frozen corpus, not copied
    from the authoring request. First installment: thread-size-xl means the
    rendered thread actually holds 100+ messages."""

    def test_thread_size_xl_is_measured(self):
        from collections import Counter
        life = get_life("reunion-thread")
        thread, size = Counter(
            o.thread for o in life.observations).most_common(1)[0]
        self.assertGreaterEqual(size, 100, thread)
        self.assertIn("thread-size-xl", life.difficulty_tags)

    def test_measured_values_on_reunion_thread(self):
        from pacbench import difficulty as D
        life = get_life("reunion-thread")
        cps = {c.id.split("/")[1]: c for c in life.checkpoints}
        now = cps["now"]
        # The graded move hides in the 146-message group chat; evidence
        # spans email + whatsapp across two collection instants.
        self.assertEqual(D.relevant_thread_size(life, now), 146)
        self.assertEqual(D.required_channels(life, now),
                         {"email", "whatsapp"})
        self.assertEqual(D.support_batches(life, now), 2)
        self.assertEqual(D.evidence_age_hours(life, now), 50.0)
        self.assertEqual(D.total_exposed(life, now.at), 162)
        # History probe: single email batch, ~5 days old.
        original = cps["original"]
        self.assertEqual(D.support_batches(life, original), 1)
        age = D.evidence_age_hours(life, original)
        self.assertIsNotNone(age)
        assert age is not None  # narrow Optional for the type checker
        self.assertAlmostEqual(age, 123.08, places=1)


class ErrorBreakout(unittest.TestCase):
    """§36: MISREPRESENTED breaks out by declared error class; unlabeled
    slots count under "unlabeled" rather than vanishing."""

    def _grade(self, slot_id, verdict):
        g = Grade(checkpoint="x")
        g.slots.append(GradedSlot(slot_id, verdict, "probe"))
        if verdict == Verdict.CORRECT:
            g.correct = 1
        elif verdict == Verdict.MISSED:
            g.missed = 1
        else:
            g.misrepresented = 1
        return g

    def test_misrepresented_breaks_out_by_class(self):
        cp = Checkpoint(
            id="x", at="2026-09-10T12:00:00", question="q",
            slots=(
                Slot(id="a", kind=Kind.CURRENT, norm=Norm.DATE,
                     expected="Saturday", forbidden=("Friday",),
                     error="stale"),
                Slot(id="b", kind=Kind.CURRENT, norm=Norm.YESNO,
                     expected="no", forbidden=("yes",),
                     error="invented"),
                Slot(id="c", kind=Kind.CURRENT, norm=Norm.TEXT,
                     expected="x", forbidden=("y",)),
            ))
        summary = summarize(
            [self._grade("a", Verdict.MISREPRESENTED),
             self._grade("b", Verdict.MISREPRESENTED),
             self._grade("c", Verdict.MISREPRESENTED)], [cp] * 3)
        self.assertEqual(summary.misrepresented, 3)
        self.assertEqual(summary.errors,
                         {"stale": 1, "invented": 1, "unlabeled": 1})
        self.assertEqual(summary.to_dict()["errors"],
                         {"stale": 1, "invented": 1, "unlabeled": 1})

    def test_clean_run_has_empty_breakout(self):
        cp = Checkpoint(
            id="x", at="2026-09-10T12:00:00", question="q",
            slots=(Slot(id="a", kind=Kind.CURRENT, norm=Norm.DATE,
                        expected="Saturday", error="stale"),))
        summary = summarize([self._grade("a", Verdict.CORRECT)], [cp])
        self.assertEqual(summary.errors, {})


class StatusAndTextNorms(unittest.TestCase):
    """Isolated pins for the two least-covered norms (§33).

    STATUS has no corpus slots today (grep Norm.STATUS over
    pacbench/corpus → only grade.py) — these pins guard its semantics if
    adopted. TEXT is the workhorse (18 slots) and its deterministic layer
    plus judge seam are pinned here, including the known eric-now/status
    residual: a paraphrase with no literal phrase and no judge reads
    MISSED. That is documented judge territory, not a hidden pass.
    """

    def _cp(self, slot):
        return Checkpoint(id="x", at="2026-09-10T12:00:00", question="q",
                          slots=(slot,))

    def _status_slot(self):
        return Slot(id="s", kind=Kind.CURRENT, norm=Norm.STATUS,
                    expected="still on",
                    forbidden=("canceled", "cancelled"))

    def test_status_containment_is_correct(self):
        g = grade(self._cp(self._status_slot()),
                  "The game is still on Saturday at 8pm.")
        self.assertEqual(g.slots[0].verdict, Verdict.CORRECT)

    def test_status_forbidden_is_misrepresented(self):
        g = grade(self._cp(self._status_slot()), "The game is canceled.")
        self.assertEqual(g.slots[0].verdict, Verdict.MISREPRESENTED)

    def test_status_silence_is_missed(self):
        g = grade(self._cp(self._status_slot()),
                  "I have no information about the game.")
        self.assertEqual(g.slots[0].verdict, Verdict.MISSED)

    def _text_slot(self):
        return Slot(id="t", kind=Kind.CURRENT, norm=Norm.TEXT,
                    expected="still on",
                    alternatives=("still happening",),
                    forbidden=("canceled",))

    def test_text_containment_and_alternative_hit(self):
        self.assertEqual(
            grade(self._cp(self._text_slot()),
                  "It is still on.").slots[0].verdict, Verdict.CORRECT)
        self.assertEqual(
            grade(self._cp(self._text_slot()),
                  "It is still happening.").slots[0].verdict,
            Verdict.CORRECT)

    def test_text_paraphrase_without_judge_is_missed(self):
        """Conservative default: no containment, no judge → MISSED, never
        a credited pass the deterministic layer cannot see."""
        g = grade(self._cp(self._text_slot()),
                  "Eric's game is on Saturday.")
        self.assertEqual(g.slots[0].verdict, Verdict.MISSED)

    def test_text_judge_seam_resolves_both_ways(self):
        # The §33 judge answers one blind binary question PER CLAIM, so the
        # model judge affirms the expected claim and denies the forbidden
        # one (a judge returning True for everything would also affirm
        # "canceled" — and forbidden wins by design).
        answer = "Eric's game is on Saturday."
        per_claim = lambda a, c: "cancel" not in c.lower()
        yes = grade(self._cp(self._text_slot()), answer, judge=per_claim)
        self.assertEqual(yes.slots[0].verdict, Verdict.CORRECT)
        no = grade(self._cp(self._text_slot()), answer,
                   judge=lambda a, c: False)
        self.assertEqual(no.slots[0].verdict, Verdict.MISSED)

    def test_time_alpha_clock_words_are_word_boundaried(self):
        """§12.3 scan finding: substring matching read "noon" inside
        "afternoon", turning a correct "Sunday afternoon at 1pm" into a
        false forbidden conviction on reunion now/time. Pure-alpha clock
        words match on boundaries; digit forms keep substring behavior."""
        slot = Slot(id="t", kind=Kind.CURRENT, norm=Norm.TIME,
                    expected="1pm", forbidden=("12pm", "noon"))
        g = grade(self._cp(slot), "Sunday afternoon at 1pm.")
        self.assertEqual(g.slots[0].verdict, Verdict.CORRECT)
        g = grade(self._cp(slot), "See everyone at the picnic Saturday noon.")
        self.assertEqual(g.slots[0].verdict, Verdict.MISREPRESENTED)
        g = grade(self._cp(slot), "Staging is frozen till noon.")
        self.assertEqual(g.slots[0].verdict, Verdict.MISREPRESENTED)

    def test_eric_now_status_residual_is_missed(self):
        """Corpus-anchored judge residual: the live eric-now/status slot
        against a paraphrase asserting the game is on. MISSED today;
        §33 adjudication owns it."""
        life = get_life("poker-collision")
        cp = next(c for c in life.checkpoints if c.id.endswith("/eric-now"))
        g = grade(cp, "Eric's game is on Saturday at 8pm at his place.")
        status = next(s for s in g.slots if s.id == "status")
        self.assertEqual(status.verdict, Verdict.MISSED)


class ContradictionScan(unittest.TestCase):
    """Contract §12.3: no accidental contradictions to protected facts.

    The scanner flags every non-support observation asserting a forbidden
    value in the current tense under grader semantics. Each of today's 96
    hits carries a reviewed disposition in
    pacbench/contradictions.py::KNOWN (zero accidental). Any new corpus
    text that contradicts a protected fact fails here until a human
    dispositions it or fixes the corpus."""

    def test_no_undispositioned_contradictions(self):
        from pacbench.contradictions import undispositioned
        from pacbench.corpus import all_lives
        self.assertEqual(undispositioned(all_lives()), [])

    def test_dispositions_use_closed_vocabulary(self):
        from pacbench.contradictions import KNOWN, REASONS
        self.assertTrue(KNOWN, "allowlist must not be empty")
        for key, reason in KNOWN.items():
            self.assertIn(reason, REASONS, key)

    def test_scan_catches_known_hearsay(self):
        from pacbench.contradictions import scan_contradictions
        life = get_life("poker-collision")
        keys = {c.key() for c in scan_contradictions(life)}
        self.assertIn(
            ("poker-collision", "poker-collision/wa-priya-claim",
             "eric-now", "status", "canceled"), keys)


class HalfLifeCoverage(unittest.TestCase):
    """Contract §18: information has different useful lifetimes.

    Each lifetime class maps to the slots exercising it — or to a
    declared gap. The small-corpus phase is over (standing integrity
    rule), so gaps are owned by the future generated-life spec, never
    silently absent and never hand-patched here: GAPS must equal exactly
    the documented hole set, so adding coverage (or losing it) forces a
    doc update."""

    HISTORY = [  # event state that later becomes history
        ("dentist-depth", "original", "day"),
        ("dentist-depth", "canceled", "canceled"),
        ("poker-move", "old-address", "old-loc"),
        ("poker-collision", "eric-original", "time"),
        ("reunion-thread", "original", "day"),
        ("reunion-thread", "canceled", "canceled"),
        ("revoke-week", "dinner-canceled", "canceled"),
        ("revoke-week", "gate-old", "code"),
    ]
    EXPIRED_TRAPS = [  # lapsed matter surfaced as live
        ("coat-weekend", "anything-fun", "trap-glassworks"),
        ("weekend-plans", "anything-fun", "trap-gala"),
        ("revoke-week", "plants-silent", "trap-water"),
        ("revoke-week", "plants-silent", "trap-plants"),
    ]
    #: The "expired" class lumped-declined by taxonomy design (rubric:
    #: "lapsed, declined, or completed matter surfaced as live"): declined
    #: invitations suggested as live options.
    DECLINED_TRAPS = [
        ("coat-weekend", "anything-fun", "trap-festival"),
        ("weekend-plans", "anything-fun", "trap-5k"),
    ]
    #: Lifetime classes with zero corpus coverage, owned by the
    #: generated-life spec: "right now" states, durable preferences
    #: ("hate 6 AM flights"), and time-bounded validity ("good until
    #: Thursday" — current expiry is event-driven, never date-bounded).
    GAPS = frozenset({"very-short-lived", "durable-preference",
                      "time-bounded-validity"})

    def _index(self):
        from pacbench.corpus import all_lives
        index = {}
        for life in all_lives():
            for cp in life.checkpoints:
                for slot in (*cp.slots, *cp.supported,
                             *cp.forbidden_options):
                    index[(life.id, cp.id.split("/")[-1],
                           slot.id)] = slot
        return index

    def test_history_slots_are_historical(self):
        from pacbench.rubric import Kind
        index = self._index()
        for key in self.HISTORY:
            self.assertIs(index[key].kind, Kind.HISTORICAL, key)

    def test_expired_trap_map_is_complete(self):
        """Every error="expired" slot in the corpus is in the map: no
        orphan expiry coverage drifting outside the §18 account."""
        from pacbench.corpus import all_lives
        found = set()
        for life in all_lives():
            for cp in life.checkpoints:
                for slot in (*cp.slots, *cp.supported,
                             *cp.forbidden_options):
                    if slot.error == "expired":
                        found.add((life.id, cp.id.split("/")[-1],
                                   slot.id))
        self.assertEqual(found,
                         set(self.EXPIRED_TRAPS) | set(self.DECLINED_TRAPS))

    def test_durable_and_temporary_current(self):
        from pacbench.rubric import Kind, Norm
        index = self._index()
        durable = index[("dentist-depth", "dentist-name", "who")]
        self.assertIs(durable.kind, Kind.CURRENT)
        self.assertIs(durable.norm, Norm.ENTITY)
        current = index[("revoke-week", "gate-now", "code")]
        self.assertIs(current.kind, Kind.CURRENT)
        self.assertIs(current.norm, Norm.ENTITY)
        self.assertEqual(current.expected, "8821")

    def test_gaps_are_exactly_declared(self):
        self.assertEqual(
            self.GAPS,
            frozenset({"very-short-lived", "durable-preference",
                       "time-bounded-validity"}))


if __name__ == "__main__":
    unittest.main()
