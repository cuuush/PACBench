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
                              "repetition": 0, "checkpoints": 1},
                             {"life": "a", "state": "valid",
                              "repetition": 1, "checkpoints": 1}],
                   "pilot_state": "valid", "provider": "lexical",
                   "repetitions": 2, "rep_f1": [0.5, 0.6],
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
        self.assertIn("a rep0: valid", text)
        self.assertIn("a rep1: valid", text)
