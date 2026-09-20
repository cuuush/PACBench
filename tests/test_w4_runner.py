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
