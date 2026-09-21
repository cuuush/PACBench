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


class DriverResolution(unittest.TestCase):
    def test_exact_match_wins(self):
        from pacbench.creds import resolve_model_id
        self.assertEqual(
            resolve_model_id("muse-spark-1.3-contributor",
                             ["muse-spark-1.3-contributor",
                              "meta/muse-spark-1.3-contributor"]),
            "muse-spark-1.3-contributor")

    def test_no_silent_substitution(self):
        from pacbench.creds import resolve_model_id
        from pacbench.generation import GenerationError
        with self.assertRaises(GenerationError):
            resolve_model_id("muse-spark-1.3-contributor",
                             ["meta/muse-spark-1.3-contributor"])

    def test_unknown_model_fails_clearly(self):
        from pacbench.creds import resolve_model_id
        from pacbench.generation import GenerationError
        with self.assertRaises(GenerationError):
            resolve_model_id("nope", ["meta/muse-spark-1.3-contributor"])


class FailureClassification(unittest.TestCase):
    def test_diagnosis_basis_labels(self):
        """Conservative diagnosis (Q11): trace-confirmed vs heuristic diagnoses
        are labeled; unknown makes no claim."""
        from pacbench import live
        from pacbench.runner import grade_answer
        cp = self._cp()
        ok = grade_answer(cp, "Wednesday at 10am.")
        self.assertEqual(
            live.classify(cp, ok, turn_ok=True,
                          ingested_ids={"dentist-depth/email-eye"},
                          tool_names=["memcal_search_archive"],
                          result_blob="x", policy_blocks=0)["basis"], "none")
        self.assertEqual(
            live.classify(cp, None, turn_ok=False, ingested_ids=set(),
                          tool_names=[], result_blob="", policy_blocks=0)["basis"],
            "trace")
        missing = live.classify(
            cp, grade_answer(cp, "no idea"), turn_ok=True, ingested_ids=set(),
            tool_names=["memcal_search_archive"], result_blob="zzz",
            policy_blocks=0)
        self.assertEqual((missing["category"], missing["basis"]), (1, "trace"))
        heuristic = live.classify(
            cp, grade_answer(cp, "no idea"), turn_ok=True,
            ingested_ids={"dentist-depth/email-eye"},
            tool_names=["memcal_search_archive"], result_blob="zzz",
            policy_blocks=0)
        self.assertEqual(heuristic["basis"], "heuristic")
        self.assertIn(heuristic["category"], (3, 5))

    def _cp(self):
        from pacbench.corpus import get_life
        life = get_life("dentist-depth")
        return next(c for c in life.checkpoints if c.id.endswith("/eye-exam"))

    def test_correct_answer_has_no_category(self):
        from pacbench import live
        from pacbench.runner import grade_answer
        cp = self._cp()
        grade = grade_answer(cp, "Wednesday at 10am.")
        out = live.classify(cp, grade, turn_ok=True,
                            ingested_ids={"dentist-depth/email-eye"},
                            tool_names=["memcal_search_archive"],
                            result_blob="eye exam Wednesday at 10am",
                            policy_blocks=0)
        self.assertIsNone(out["category"])

    def test_missing_evidence_is_ingestion_failure(self):
        from pacbench import live
        from pacbench.runner import grade_answer
        cp = self._cp()
        grade = grade_answer(cp, "I don't know.")
        out = live.classify(cp, grade, turn_ok=True, ingested_ids=set(),
                            tool_names=[], result_blob="", policy_blocks=0)
        self.assertEqual(out["category"], 1)

    def test_no_tool_calls_is_tool_selection(self):
        from pacbench import live
        from pacbench.runner import grade_answer
        cp = self._cp()
        grade = grade_answer(cp, "I don't know.")
        out = live.classify(cp, grade, turn_ok=True,
                            ingested_ids={"dentist-depth/email-eye"},
                            tool_names=[], result_blob="", policy_blocks=0)
        self.assertEqual(out["category"], 4)

    def test_empty_results_is_retrieval_failure(self):
        from pacbench import live
        from pacbench.runner import grade_answer
        cp = self._cp()
        grade = grade_answer(cp, "I don't know.")
        out = live.classify(cp, grade, turn_ok=True,
                            ingested_ids={"dentist-depth/email-eye"},
                            tool_names=["memcal_search_archive"],
                            result_blob="nothing relevant found", policy_blocks=0)
        self.assertEqual(out["category"], 3)

    def test_ignored_evidence_is_reading_failure(self):
        from pacbench import live
        from pacbench.runner import grade_answer
        cp = self._cp()
        grade = grade_answer(cp, "Sometime next month.")
        out = live.classify(cp, grade, turn_ok=True,
                            ingested_ids={"dentist-depth/email-eye"},
                            tool_names=["memcal_open_source"],
                            result_blob="Confirmed: eye exam Wednesday at 10am",
                            policy_blocks=0)
        self.assertEqual(out["category"], 5)

    def test_dead_turn_is_runtime_failure(self):
        from pacbench import live
        cp = self._cp()
        out = live.classify(cp, None, turn_ok=False, ingested_ids=set(),
                            tool_names=[], result_blob="", policy_blocks=0)
        self.assertEqual(out["category"], 7)


class PilotPreflight(unittest.TestCase):
    def test_scratch_homes_pass_and_live_paths_fail(self):
        import tempfile
        from pacbench import live
        from pacbench.corpus import get_life

        class StubAdapter:
            def retrieval(self):
                return {"provider": "stub", "plugin_available": True,
                        "tool_allowlist": [], "env": {}}

        life = get_life("dentist-depth")
        with tempfile.TemporaryDirectory() as tmp:
            ok = live.preflight(life, Path(tmp) / "h-home",
                                Path(tmp) / "m-home", StubAdapter())
            self.assertEqual(ok, [])
            bad = live.preflight(life, Path.home() / ".hermes" / "x",
                                 Path(tmp) / "m-home", StubAdapter())
            self.assertTrue(bad)


class ReplayAwareClassification(unittest.TestCase):
    """P1: user-turn evidence delivered via turn replay must not read as an
    ingestion failure (§34 — classify only when the trace supports it)."""

    def _cp(self):
        from pacbench.rubric import Checkpoint, Kind, Norm, Slot
        return Checkpoint(
            id="t/cp", at="2026-09-09T20:00:00", question="q",
            slots=(Slot(id="s", kind=Kind.CURRENT, norm=Norm.STATUS,
                        expected="active", supported_by=("t/email", "t/hermes-turn")),))

    def test_replayed_support_is_not_ingestion_failure(self):
        from pacbench import live
        from pacbench.runner import grade_answer
        cp = self._cp()
        grade = grade_answer(cp, "I don't know.")
        out = live.classify(cp, grade, turn_ok=True,
                            ingested_ids={"t/email"},
                            tool_names=[], result_blob="", policy_blocks=0,
                            replayed_ids={"t/hermes-turn"})
        # All required support delivered (one via replay) yet nothing surfaced:
        # no tool was even called, so this is tool-selection, not ingestion.
        self.assertEqual(out["category"], 4)

    def test_truly_missing_support_stays_ingestion_failure(self):
        from pacbench import live
        from pacbench.runner import grade_answer
        cp = self._cp()
        grade = grade_answer(cp, "I don't know.")
        out = live.classify(cp, grade, turn_ok=True,
                            ingested_ids={"t/email"},
                            tool_names=[], result_blob="", policy_blocks=0)
        self.assertEqual(out["category"], 1)


class TurnDiagnostics(unittest.TestCase):
    """P1: no error path is record-less — every failed turn carries the full
    TurnResult (stderr_full + stdout_tail + raw_events + latency)."""

    def test_record_of_carries_full_diagnostics(self):
        from pacbench import live
        from pacbench.harness import TurnResult
        r = TurnResult(prompt="p", at="t", answer="a", tool_calls=[],
                       tool_results=[], policy_blocks=0, latency_s=1.5,
                       returncode=1, stderr_tail="tail", raw_events=3,
                       stderr_full="FULL", stdout_tail="OUT")
        rec = live._record_of(r)
        for key in ("answer", "tool_calls", "tool_results", "session_id",
                    "policy_blocks", "latency_s", "returncode", "stderr_tail",
                    "stderr_full", "stdout_tail", "raw_events"):
            self.assertIn(key, rec)
        self.assertEqual(rec["stderr_full"], "FULL")
        self.assertEqual(rec["latency_s"], 1.5)

    def test_timeout_yields_diagnostic_turn_not_exception(self):
        import subprocess
        from unittest import mock
        from pacbench import harness
        import tempfile
        with tempfile.TemporaryDirectory(prefix="pacbench-p1-") as tmp:
            home = Path(tmp) / "h-home"
            harness.provision_home(home, provider_plugin_src=None,
                                   provider_name="none")
            with mock.patch.object(
                    harness.subprocess, "run",
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["hermes"], timeout=0.1)):
                res = harness.run_turn("hi", at="2026-09-10T12:00:00",
                                       home=home, allowlist="",
                                       timeout_s=0.1)
        self.assertEqual(res.returncode, -1)
        self.assertIn("timed out", res.stderr_full)
        self.assertEqual(res.raw_events, 0)


class FictionalClockIntegrity(unittest.TestCase):
    """Contract §30: the isolated Hermes sees fictional time, never host date.

    The vendor tree anchors its clock to PACBENCH_NOW (pinned shims,
    tests/test_vendor_patch.py). This is the harness half: run_turn must
    export exactly the fictional `at` it was given — no host-now
    substitution anywhere on the path.
    """

    def test_run_turn_exports_fictional_now(self):
        import subprocess
        from datetime import datetime, timezone
        from unittest import mock
        from pacbench import harness
        import tempfile
        fictional = "2026-09-10T12:00:00"
        seen: dict = {}

        def fake_run(cmd, **kw):
            seen.update(kw.get("env", {}))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory(prefix="pacbench-clock-") as tmp:
            home = Path(tmp) / "h-home"
            harness.provision_home(home, provider_plugin_src=None,
                                   provider_name="none")
            with mock.patch.object(harness.subprocess, "run",
                                   side_effect=fake_run):
                res = harness.run_turn("hi", at=fictional, home=home,
                                       allowlist="", timeout_s=10)
        self.assertEqual(seen.get("PACBENCH_NOW"), fictional)
        self.assertEqual(res.at, fictional)
        # Fictional, not host: the exported date is not the machine's today.
        host_today = datetime.now(timezone.utc).date().isoformat()
        self.assertNotEqual(seen["PACBENCH_NOW"][:10], host_today)

    def test_run_turn_carries_simulated_now_in_chat_context(self):
        """The model must SEE the fictional now: tool-time shim alone
        leaves model-time host-anchored (clock probe answered the host
        date on a bare turn). The prefix is recorded, never hidden."""
        import subprocess
        from unittest import mock
        from pacbench import harness
        import tempfile
        fictional = "2026-09-10T12:00:00"
        seen_cmd: list = []

        def fake_run(cmd, **kw):
            seen_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory(prefix="pacbench-clock-") as tmp:
            home = Path(tmp) / "h-home"
            harness.provision_home(home, provider_plugin_src=None,
                                   provider_name="none")
            with mock.patch.object(harness.subprocess, "run",
                                   side_effect=fake_run):
                harness.run_turn("hi", at=fictional, home=home,
                                 allowlist="", timeout_s=10)
        q_index = seen_cmd.index("-q") + 1
        sent = seen_cmd[q_index]
        self.assertIn(f"[Simulated now: {fictional}.", sent)
        self.assertTrue(sent.endswith("hi"))


class MaintenanceApplicability(unittest.TestCase):
    """Contract clarification 2026-09-21 (Q3): scheduled maintenance is
    optional. N/A providers stay valid; applicable providers still need a
    complete pass plus ready. Stub adapters — no model, no Hermes."""

    def _stub(self, applicable, state, ready):
        from types import SimpleNamespace
        from pacbench.adapters import Readiness
        return SimpleNamespace(
            maintenance_applicable=applicable,
            _maintenance={"state": state},
            readiness=lambda: Readiness(ready=ready, detail="stub"))

    def test_not_applicable_is_satisfied(self):
        ok, reason = live.maintenance_satisfied(
            self._stub(False, "not-applicable", True))
        self.assertTrue(ok)
        self.assertIn("not applicable", reason)

    def test_complete_and_ready_is_satisfied(self):
        ok, _ = live.maintenance_satisfied(
            self._stub(True, "complete", True))
        self.assertTrue(ok)

    def test_skipped_is_unsatisfied(self):
        ok, reason = live.maintenance_satisfied(
            self._stub(True, "skipped", False))
        self.assertFalse(ok)
        self.assertIn("skipped", reason)

    def test_complete_but_not_ready_is_unsatisfied(self):
        ok, _ = live.maintenance_satisfied(
            self._stub(True, "complete", False))
        self.assertFalse(ok)

    def test_legacy_adapter_defaults_to_applicable(self):
        from types import SimpleNamespace
        from pacbench.adapters import Readiness
        legacy = SimpleNamespace(
            _maintenance={"state": "complete"},
            readiness=lambda: Readiness(ready=True, detail="stub"))
        self.assertTrue(live.maintenance_satisfied(legacy)[0])


class MaintenanceSpendGate(unittest.TestCase):
    """H4: multi-day lives fail closed without explicit spend approval.

    Provider scheduled work (dream/graph maintenance) is a second spend
    entry point on the provider's own backend. Stub run_life's episode
    core — the gate lives in run_life before any reset/spend.
    """

    def _run(self, tmp, life_id, env_approved):
        import os
        from pathlib import Path
        from unittest import mock
        from pacbench.corpus import get_life

        life = get_life(life_id)
        seen = {}

        def fake_episode(**kwargs):
            seen["ran"] = True
            return {"life": life_id, "state": "valid", "repetition": 0,
                    "checkpoints": 0, "turns": 0, "maintenance_runs": 0,
                    "summary": {}}

        # maintenance_applicable=True stub: memcal-shaped (a real second
        # spend entry point exists) without importing memcal. Lexical
        # (applicable=False) is exempt by design — no scheduled work, no
        # gate — so the gate test needs an applicable adapter.
        from types import SimpleNamespace
        from pacbench.adapters import Readiness
        stub = SimpleNamespace(
            name="stub-applicable",
            maintenance_applicable=True,
            dream_model=None,
            retrieval=lambda: {"provider": "stub-applicable",
                               "tool_allowlist": [],
                               "plugin_available": True,
                               "env": {}},
            reset=lambda ns: None,
            usage=lambda: SimpleNamespace(__dict__={}))

        env = {"PACBENCH_ALLOW_MAINTENANCE_SPEND": "1"} if env_approved else {}
        with mock.patch.dict(os.environ, env, clear=False):
            if not env_approved:
                os.environ.pop("PACBENCH_ALLOW_MAINTENANCE_SPEND", None)
            real = live._run_life_episode
            live._run_life_episode = fake_episode
            try:
                return live.run_life(
                    life=life, provider="stub-applicable",
                    make_adapter=lambda home: stub,
                    life_dir=Path(tmp) / life_id, model="m",
                    model_provider="p", driver_configured_model="m"), seen
            finally:
                live._run_life_episode = real

    def test_multiday_without_approval_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            record, seen = self._run(tmp, "weekend-plans", False)
            self.assertEqual(record["state"], "invalid")
            self.assertTrue(any("maintenance-spend" in r
                                for r in record["reasons"]))
            self.assertNotIn("ran", seen)

    def test_multiday_with_approval_runs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            record, seen = self._run(tmp, "weekend-plans", True)
            self.assertEqual(record["state"], "valid")
            self.assertTrue(seen.get("ran"))

    def test_singleday_without_approval_runs(self):
        import tempfile
        from pacbench.corpus import life_g_toyday
        with tempfile.TemporaryDirectory() as tmp:
            import os
            from pathlib import Path
            from unittest import mock
            from provider_adapters.lexical_adapter import LexicalAdapter
            life = life_g_toyday.lives()[0]
            days = {o.collected_at[:10] for o in life.observations}
            self.assertEqual(len(days), 1)  # guard: stays single-day
            seen = {}

            def fake_episode(**kwargs):
                seen["ran"] = True
                return {"life": life.id, "state": "valid", "repetition": 0,
                        "checkpoints": 0, "turns": 0, "maintenance_runs": 0,
                        "summary": {}}

            os.environ.pop("PACBENCH_ALLOW_MAINTENANCE_SPEND", None)
            real = live._run_life_episode
            live._run_life_episode = fake_episode
            try:
                record = live.run_life(
                    life=life, provider="lexical",
                    make_adapter=lambda home: LexicalAdapter(home),
                    life_dir=Path(tmp) / life.id, model="m",
                    model_provider="p", driver_configured_model="m")
            finally:
                live._run_life_episode = real
            self.assertEqual(record["state"], "valid")
            self.assertTrue(seen.get("ran"))


class ClockProbeVerdict(unittest.TestCase):
    """Proceed ruling step 2: the adversarial verdict logic, no model."""
    FICTIONAL = "2026-09-09T12:00:00"

    def test_fictional_date_passes(self):
        verdict, _ = live.classify_clock_answer(
            "Wednesday, September 9, 2026.", self.FICTIONAL)
        self.assertEqual(verdict, "pass")

    def test_iso_form_passes(self):
        verdict, _ = live.classify_clock_answer("2026-09-09", self.FICTIONAL)
        self.assertEqual(verdict, "pass")

    def test_host_leak_fails(self):
        from datetime import datetime, timezone
        host = datetime.now(timezone.utc).date()
        verdict, reason = live.classify_clock_answer(
            f"Today is {host.strftime('%B')} {host.day}, 2026.",
            self.FICTIONAL)
        self.assertEqual(verdict, "fail")
        self.assertIn("host-date leak", reason)

    def test_host_leak_dominates_fictional_mention(self):
        from datetime import datetime, timezone
        host = datetime.now(timezone.utc).date()
        verdict, _ = live.classify_clock_answer(
            f"Fictional September 9, but really {host.strftime('%B')} "
            f"{host.day}.", self.FICTIONAL)
        self.assertEqual(verdict, "fail")

    def test_hedge_is_inconclusive_not_forced(self):
        verdict, _ = live.classify_clock_answer(
            "I don't have access to the current date.", self.FICTIONAL)
        self.assertEqual(verdict, "inconclusive")

    def test_bare_weekday_alone_is_inconclusive(self):
        verdict, _ = live.classify_clock_answer("Wednesday.", self.FICTIONAL)
        self.assertEqual(verdict, "inconclusive")


class RepetitionLayout(unittest.TestCase):
    """Contract §40: repetitions are namespaced realizations, never overwrites."""

    def test_single_rep_keeps_flat_layout(self):
        self.assertEqual(live.rep_dir_name("dentist-depth", 0, 1),
                         "dentist-depth")

    def test_multi_rep_namespaces(self):
        self.assertEqual(live.rep_dir_name("dentist-depth", 0, 2),
                         "dentist-depth-rep0")
        self.assertEqual(live.rep_dir_name("dentist-depth", 2, 3),
                         "dentist-depth-rep2")

    def test_bad_reps_raise(self):
        with self.assertRaises(ValueError):
            live.rep_dir_name("x", 1, 1)
        with self.assertRaises(ValueError):
            live.run_pilot(lives=[], provider="memcal",
                           make_adapter=lambda home: None,
                           pilot_dir=Path("/nonexistent"),
                           model="m", model_provider="p",
                           driver_configured_model="m", repetitions=0)

    def test_rep_dirs_covers_both_layouts(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pilot = Path(tmp)
            (pilot / "dentist-depth").mkdir()
            (pilot / "dentist-depth" / "turns.json").write_text(
                json.dumps({"turns": []}))
            (pilot / "dentist-depth-rep0").mkdir()
            (pilot / "dentist-depth-rep0" / "turns.json").write_text(
                json.dumps({"turns": []}))
            (pilot / "dentist-depth-rep1").mkdir()  # no turns.json: ignored
            dirs = live._rep_dirs(pilot, "dentist-depth")
            self.assertEqual(
                [d.name for d in dirs],
                ["dentist-depth", "dentist-depth-rep0"])

    def test_render_report_names_repetitions(self):
        summary = {"lives": [{"life": "a", "state": "valid",
                              "repetition": 0, "checkpoints": 1, "seed": "s0"},
                             {"life": "a", "state": "valid",
                              "repetition": 1, "checkpoints": 1, "seed": "s1"}],
                   "pilot_state": "valid", "provider": "lexical",
                   "repetitions": 2, "rep_f1": [0.5, 0.6],
                   "rep_seeds": ["s0", "s1"],
                   "driver_model": "m", "driver_configured_model": "m",
                   "corpus_version": "0.2.0",
                   "aggregate": {"checkpoints": 2, "slots": 2, "correct": 1,
                                 "missed": 1, "misrepresented": 0,
                                 "precision": 1.0, "recall": 0.5, "f1": 0.55,
                                 "current_accuracy": None,
                                 "historical_accuracy": None,
                                 "complete_plan_accuracy": None,
                                 "abstention_accuracy": None,
                                 "opportunity_recall": None,
                                 "forbidden_suggested": 0,
                                 "by_difficulty_tag": {}},
                   "efficiency": {"hermes_turns": 2, "memory_tool_calls": 0,
                                  "answer_latency_s_total": 1.0,
                                  "tokens": {}},
                   "comparative_claim_valid": False, "comparative_gap": "g"}
        text = live.render_report(summary, Path("/tmp/x"))
        self.assertIn("1 lives × 2 repetitions", text)
        self.assertIn("rep seeds: ['s0', 's1']", text)
        self.assertIn("a rep0 (seed s0): valid", text)
        self.assertIn("a rep1 (seed s1): valid", text)


class RepetitionSeeds(unittest.TestCase):
    """Ruling 2026-09-21 (Q5): predeclared seeds or honest provider-managed.

    run_life is stubbed (no Hermes, no model): it creates the life dir
    with an empty grades.json and returns a valid record. What is under
    test is the provenance recording, not the episode itself."""

    def _stub_run_life(self, **kwargs):
        from pathlib import Path
        import json
        life_dir = kwargs["life_dir"]
        life_dir.mkdir(parents=True, exist_ok=True)
        (life_dir / "grades.json").write_text(json.dumps({"grades": []}))
        return {"life": kwargs["life"].id, "state": "valid",
                "repetition": kwargs.get("repetition", 0),
                "checkpoints": 0}

    def _run(self, tmp, **kwargs):
        from pathlib import Path
        from pacbench.corpus import get_life
        real = live.run_life
        live.run_life = self._stub_run_life
        try:
            return live.run_pilot(
                lives=[get_life("weekend-plans")],
                provider="lexical", make_adapter=lambda home: None,
                pilot_dir=Path(tmp), model="m", model_provider="p",
                driver_configured_model="m", **kwargs)
        finally:
            live.run_life = real

    def test_default_seeds_are_provider_managed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(tmp, repetitions=3)
            summary = out["summary"]
            self.assertEqual(summary["rep_seeds"],
                             ["provider-managed"] * 3)
            self.assertTrue(all(r["seed"] == "provider-managed"
                                for r in summary["lives"]))

    def test_predeclared_seeds_flow_to_reps_and_config(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(tmp, repetitions=3,
                             seeds=["alpha", "beta", "gamma"])
            summary = out["summary"]
            self.assertEqual(summary["rep_seeds"],
                             ["alpha", "beta", "gamma"])
            self.assertEqual([r["seed"] for r in summary["lives"]],
                             ["alpha", "beta", "gamma"])
            cfg = summary["repetition_config"]
            self.assertEqual(cfg["driver_model"], "m")
            self.assertEqual(cfg["timeout_s"], 900.0)
            self.assertTrue(cfg["corpus_hash"])
            self.assertIn("rep seeds: ['alpha', 'beta', 'gamma']",
                          (Path(tmp) / "REPORT.md").read_text())

    def test_seed_count_mismatch_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self._run(tmp, repetitions=3, seeds=["only-one"])

    def test_empty_seed_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self._run(tmp, repetitions=2, seeds=["ok", ""])


class PilotBundles(unittest.TestCase):
    """C1: scored pilots consume frozen bundles only — fail closed otherwise.

    run_pilot threads bundles through to run_life; a partial bundle map
    (missing life) raises before any life runs. Omitting bundles stays
    legal for offline/diagnostic use (manifest records draft-text).
    """

    def _stub_run_life(self, **kwargs):
        from pathlib import Path
        import json
        life_dir = kwargs["life_dir"]
        life_dir.mkdir(parents=True, exist_ok=True)
        (life_dir / "grades.json").write_text(json.dumps({"grades": []}))
        self.seen_bundles.append(kwargs.get("bundle"))
        return {"life": kwargs["life"].id, "state": "valid",
                "repetition": kwargs.get("repetition", 0),
                "checkpoints": 0}

    def _run(self, tmp, **kwargs):
        from pathlib import Path
        from pacbench.corpus import get_life
        self.seen_bundles = []
        real = live.run_life
        live.run_life = self._stub_run_life
        try:
            return live.run_pilot(
                lives=[get_life("weekend-plans")],
                provider="lexical", make_adapter=lambda home: None,
                pilot_dir=Path(tmp), model="m", model_provider="p",
                driver_configured_model="m", **kwargs)
        finally:
            live.run_life = real

    def test_missing_life_bundle_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self._run(tmp, bundles={})
            self.assertEqual(self.seen_bundles, [])

    def test_bundles_thread_through_to_run_life(self):
        import tempfile
        from pacbench import corpus
        from pacbench.frozen import freeze_observations
        life = corpus.get_life("weekend-plans")
        bundle = freeze_observations([life])
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(tmp, bundles={"weekend-plans": bundle})
            self.assertEqual(out["summary"]["pilot_state"], "valid")
            self.assertEqual(self.seen_bundles, [bundle])

    def test_omitted_bundles_stays_diagnostic(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(tmp)
            self.assertEqual(out["summary"]["pilot_state"], "valid")
            self.assertEqual(self.seen_bundles, [None])
