"""P0-6 exposure-equivalence ledger: provider-blind per-boundary digests.

No network, no model, no Hermes run, no spend. Drives the REAL runner
code paths (run_life/_run_life_episode) with two different fake
providers over one frozen bundle, then proves the same evidence was
offered per boundary — and that dropped/reordered/rewritten evidence
fails loudly through compare_exposure.
"""

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pacbench import live  # noqa: E402
from pacbench.exposure import (EXPOSURE_DIGEST_FIELDS,  # noqa: E402
                               check_offered_batch, compare_exposure,
                               exposure_canonical, exposure_digest)


def _obs(source_id, collected_at, text="hello", sender="Gus"):
    return {"source_id": source_id, "channel": "text",
            "written_at": collected_at,
            "arrived_at": collected_at,
            "collected_at": collected_at, "sender": sender, "thread": "tx",
            "text": text, "from_owner": False, "structured": None}


def _fake_turn(prompt, **kw):
    return SimpleNamespace(
        prompt=prompt, at=kw.get("at", ""), answer="day one",
        tool_calls=[], tool_results=[], policy_blocks=0, latency_s=0.1,
        returncode=0, stderr_tail="", stderr_full="", stdout_tail="",
        raw_events=0, session_id=None, session_input_tokens=None,
        session_output_tokens=None)


class FakeProviderB:
    """Second duck-typed provider: distinct packaging, same evidence."""

    name = "fake-b"
    maintenance_applicable = False

    def __init__(self):
        self.calls = []

    def reset(self, namespace):
        self.namespace = namespace

    def retrieval(self):
        return {"provider": "fake-b", "tool_allowlist": [],
                "plugin_available": True, "env": {}}

    def ingest(self, observations, collected_at, group_flags=None):
        # Deliberately different private packaging (native ids, op-ids)
        # that must NEVER leak into the exposure digest.
        packaged = [{"document_id": f"native-{o['source_id']}",
                     "op_id": f"op-{collected_at}",
                     **o} for o in observations]
        self.calls.append((collected_at, packaged))
        return SimpleNamespace(model_calls=0, latency_s=0.0, notes="fake-b")

    def maintenance(self, at):
        return SimpleNamespace(model_calls=0, latency_s=0.0, notes="fake-b")

    def readiness(self):
        return SimpleNamespace(ready=True, notes="fake-b")

    def usage(self):
        return SimpleNamespace(model_calls=0, latency_s=0.0, notes="fake-b")


def _two_day_life(text_day2="day two first"):
    from pacbench.life import Life
    from pacbench.observation import Observation
    from pacbench.rubric import Checkpoint, Kind, Norm, Slot
    obs = (
        Observation(source_id="d1a", channel="text",
                    written_at="2026-09-08T10:00:00",
                    arrived_at="2026-09-08T10:00:00",
                    collected_at="2026-09-08T10:00:00",
                    sender="Gus", thread="tx", text="day one first"),
        Observation(source_id="d1b", channel="text",
                    written_at="2026-09-08T10:05:00",
                    arrived_at="2026-09-08T10:05:00",
                    collected_at="2026-09-08T10:05:00",
                    sender="Gus", thread="tx", text="day one second"),
        Observation(source_id="d2a", channel="text",
                    written_at="2026-09-09T09:00:00",
                    arrived_at="2026-09-09T09:00:00",
                    collected_at="2026-09-09T09:00:00",
                    sender="Gus", thread="tx", text=text_day2),
        Observation(source_id="d2b", channel="text",
                    written_at="2026-09-09T10:00:00",
                    arrived_at="2026-09-09T10:00:00",
                    collected_at="2026-09-09T10:00:00",
                    sender="Gus", thread="tx", text="day two second"),
    )
    cp = Checkpoint(
        id="t/cp", at="2026-09-09T11:00:00",
        question="What did Gus say?",
        slots=(Slot(id="s", kind=Kind.CURRENT, norm=Norm.TEXT,
                    expected="day",
                    supported_by=("d1a", "d2a")),))
    return Life(id="t", bet="b", role="development",
                observations=obs, checkpoints=(cp,))


def _run_episode(tmp, life, bundle, make_adapter, provider):
    import json
    life_dir = Path(tmp) / f"life-{provider}"
    with mock.patch("pacbench.live.harness.provision_home",
                    return_value={"ok": True}), \
         mock.patch("pacbench.live.harness.run_turn",
                    side_effect=_fake_turn):
        rec = live.run_life(
            life=life, provider=provider, make_adapter=make_adapter,
            life_dir=life_dir, model="m", model_provider="mp",
            driver_configured_model="m", bundle=bundle)
    manifest = json.loads((life_dir / "manifest.json").read_text())
    return rec, manifest


class ExposureDigestUnit(unittest.TestCase):
    def test_canonical_form_is_exactly_the_8_public_fields(self):
        batch = [_obs("m1", "2026-09-08T10:00:00")]
        canonical = exposure_canonical(batch)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(set(canonical[0]), set(EXPOSURE_DIGEST_FIELDS))
        self.assertEqual(len(EXPOSURE_DIGEST_FIELDS), 8)

    def test_digest_is_deterministic(self):
        batch = [_obs("m1", "2026-09-08T10:00:00"),
                 _obs("m2", "2026-09-08T10:05:00")]
        self.assertEqual(exposure_digest(batch), exposure_digest(batch))
        self.assertEqual(len(exposure_digest(batch)), 64)

    def test_provider_private_fields_never_reach_the_digest(self):
        clean = [_obs("m1", "2026-09-08T10:00:00")]
        spiked = [dict(clean[0], document_id="native-m1",
                       op_id="op-1", operation_ids=["op-1"],
                       MEMCAL_TODAY="2026-09-08",
                       gate_verdict="allow", entity_keys=["e1"])]
        # Projection drops them: identical digest.
        self.assertEqual(exposure_digest(spiked), exposure_digest(clean))
        # And the boundary guard refuses the spiked batch outright.
        with self.assertRaises(RuntimeError):
            check_offered_batch(spiked)
        # Clean public batches pass the guard.
        check_offered_batch(clean)

    def test_reorder_rewrite_drop_change_the_digest(self):
        base = [_obs("m1", "2026-09-08T10:00:00"),
                _obs("m2", "2026-09-08T10:05:00")]
        reordered = [base[1], base[0]]
        self.assertNotEqual(exposure_digest(reordered),
                            exposure_digest(base))
        rewritten = [dict(base[0], text="tampered"), base[1]]
        self.assertNotEqual(exposure_digest(rewritten),
                            exposure_digest(base))
        self.assertNotEqual(exposure_digest(base[:1]),
                            exposure_digest(base))


class CrossProviderEquivalence(unittest.TestCase):
    def _run_both(self, tmp, life=None, text_day2="day two first"):
        from pacbench.frozen import freeze_observations
        from provider_adapters.lexical_adapter import LexicalAdapter
        life = life or _two_day_life(text_day2=text_day2)
        bundle = freeze_observations([life])
        rec_a, manifest_a = _run_episode(
            tmp, life, bundle,
            lambda home: LexicalAdapter(home), "lexical")
        rec_b, manifest_b = _run_episode(
            tmp, life, bundle,
            lambda home: FakeProviderB(), "fake-b")
        return (rec_a, manifest_a), (rec_b, manifest_b)

    def test_identical_lives_match_across_two_fake_providers(self):
        with tempfile.TemporaryDirectory(prefix="exposure-") as tmp:
            (rec_a, manifest_a), (rec_b, manifest_b) = self._run_both(tmp)
        self.assertEqual(rec_a["state"], "valid")
        self.assertEqual(rec_b["state"], "valid")
        # Both ledgers carry per-boundary digests ...
        for manifest in (manifest_a, manifest_b):
            batches = manifest["import_batches"]
            self.assertEqual(len(batches), 2)
            for entry in batches:
                self.assertIn("exposure_digest", entry)
                self.assertEqual(len(entry["exposure_digest"]), 64)
        # ... proving the same evidence per boundary (wall-clock
        # ingested_at and provider usage legitimately differ, so the
        # comparison must ignore them — and does).
        self.assertNotEqual(
            manifest_a["import_batches"][0].get("ingested_at"),
            manifest_b["import_batches"][0].get("ingested_at"),
            "guard: the compared runs really are distinct runs")
        result = compare_exposure(manifest_a, manifest_b)
        self.assertTrue(result["match"], result["mismatches"])
        self.assertEqual(result["boundaries"], ["2026-09-08", "2026-09-09"])

    def test_rewritten_observation_fails_loudly_end_to_end(self):
        from pacbench.report import check_exposure_equivalence
        with tempfile.TemporaryDirectory(prefix="exposure-") as tmp:
            (_, manifest_a), _ = self._run_both(tmp)
            (_, manifest_rewritten), _ = self._run_both(
                tmp, life=_two_day_life(text_day2="TAMPERED TEXT"))
            # Same life id, different offered text on day 2.
            result = compare_exposure(manifest_a, manifest_rewritten)
            self.assertFalse(result["match"])
            self.assertTrue(
                any(m["boundary"] == "2026-09-09"
                    for m in result["mismatches"]))
            with self.assertRaises(RuntimeError):
                check_exposure_equivalence(manifest_a, manifest_rewritten)

    def test_dropped_observation_fails_loudly(self):
        with tempfile.TemporaryDirectory(prefix="exposure-") as tmp:
            (_, manifest_a), (_, manifest_b) = self._run_both(tmp)
        tampered = copy.deepcopy(manifest_b)
        tampered["import_batches"][0]["observations"] = ["d1a"]
        result = compare_exposure(manifest_a, tampered)
        self.assertFalse(result["match"])
        self.assertTrue(
            any("observations" in m["reason"]
                for m in result["mismatches"]))

    def test_reordered_observation_fails_loudly(self):
        with tempfile.TemporaryDirectory(prefix="exposure-") as tmp:
            (_, manifest_a), (_, manifest_b) = self._run_both(tmp)
        tampered = copy.deepcopy(manifest_b)
        # Reorder BOTH the recorded order and the digest input would be
        # needed to hide this; reordering the record alone must fail.
        tampered["import_batches"][0]["observations"] = ["d1b", "d1a"]
        result = compare_exposure(manifest_a, tampered)
        self.assertFalse(result["match"])

    def test_missing_digest_never_silently_matches(self):
        with tempfile.TemporaryDirectory(prefix="exposure-") as tmp:
            (_, manifest_a), (_, manifest_b) = self._run_both(tmp)
        legacy = copy.deepcopy(manifest_b)
        for entry in legacy["import_batches"]:
            del entry["exposure_digest"]
        result = compare_exposure(manifest_a, legacy)
        self.assertFalse(result["match"])
        self.assertTrue(
            all("missing exposure digest" in m["reason"]
                for m in result["mismatches"]))
        # Two pre-ledger manifests don't match each other either.
        self.assertFalse(compare_exposure(legacy, legacy)["match"])

    def test_boundary_count_mismatch_fails_loudly(self):
        with tempfile.TemporaryDirectory(prefix="exposure-") as tmp:
            (_, manifest_a), (_, manifest_b) = self._run_both(tmp)
        tampered = copy.deepcopy(manifest_b)
        tampered["import_batches"] = tampered["import_batches"][:1]
        result = compare_exposure(manifest_a, tampered)
        self.assertFalse(result["match"])
        self.assertTrue(
            any(m["reason"] == "boundary count differs"
                for m in result["mismatches"]))


if __name__ == "__main__":
    unittest.main()
