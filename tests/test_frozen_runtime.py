"""P0-11 frozen-bundle-only runtime: draft-text scored routes are gone.

No network, no model, no Hermes run, no spend. Proves the single
canonical path (author → validate/render → freeze → hash → run):

- run_life / _run_life_episode / run_pilot refuse bundle=None
  (ValueError): bundle=None can never produce a scored-looking valid
  result because it never produces a run at all.
- run_checkpoint_live is hard-labeled diagnostic-only and the
  scoring/reporting gates (report.require_scored_manifest) refuse it.
- A frozen-bundle episode passes the gate and compares equal to
  itself through the exposure-equivalence gate.
"""

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
from pacbench.report import (check_exposure_equivalence,  # noqa: E402
                             require_scored_manifest)


def _fake_turn(prompt, **kw):
    return SimpleNamespace(
        prompt=prompt, at=kw.get("at", ""), answer="Friday",
        tool_calls=[], tool_results=[], policy_blocks=0, latency_s=0.1,
        returncode=0, stderr_tail="", stderr_full="", stdout_tail="",
        raw_events=0, session_id=None, session_input_tokens=None,
        session_output_tokens=None)


class FakeEngine:
    def ingest(self, observations, collected_at, group_flags=None):
        return SimpleNamespace(model_calls=0, latency_s=0.0, notes="fake")

    def retrieval(self):
        return {"provider": "fake-engine", "tool_allowlist": ()}

    def reset(self, namespace):
        self.namespace = namespace

    def usage(self):
        return SimpleNamespace(model_calls=0, latency_s=0.0, notes="fake")


class FrozenBundleOnlyRuntime(unittest.TestCase):
    def test_run_life_without_bundle_refuses(self):
        from pacbench import corpus
        from provider_adapters.lexical_adapter import LexicalAdapter
        life = corpus.get_life("poker-unchanged")
        with tempfile.TemporaryDirectory(prefix="frozen-rt-") as tmp:
            with self.assertRaises(ValueError):
                live.run_life(
                    life=life, provider="lexical",
                    make_adapter=lambda home: LexicalAdapter(home),
                    life_dir=Path(tmp) / "life", model="m",
                    model_provider="mp", driver_configured_model="m",
                    bundle=None)

    def test_life_episode_without_bundle_refuses(self):
        from pacbench import corpus
        life = corpus.get_life("poker-unchanged")
        with tempfile.TemporaryDirectory(prefix="frozen-rt-") as tmp:
            with self.assertRaises(ValueError):
                live._run_life_episode(
                    life=life, provider="lexical", adapter=None,
                    namespace="x", life_dir=Path(tmp) / "life",
                    model="m", model_provider="mp",
                    driver_configured_model="m", bundle=None)  # type: ignore[arg-type]

    def test_run_pilot_without_bundles_refuses(self):
        with self.assertRaises(ValueError):
            live.run_pilot(
                lives=[], provider="lexical",
                make_adapter=lambda home: None,
                pilot_dir=Path("/nonexistent"), model="m",
                model_provider="mp", driver_configured_model="m",
                bundles=None)

    def test_checkpoint_run_is_diagnostic_only_and_refused(self):
        import json
        from pacbench import corpus
        life = corpus.get_life("poker-unchanged")
        cp = life.checkpoints[0]
        engine = FakeEngine()
        with tempfile.TemporaryDirectory(prefix="frozen-rt-") as tmp:
            run_dir = Path(tmp) / "run"
            with mock.patch("pacbench.live.harness.provision_home",
                            return_value={"ok": True}), \
                 mock.patch("pacbench.live.harness.run_turn",
                            side_effect=_fake_turn):
                live.run_checkpoint_live(
                    life=life, checkpoint=cp, adapter=engine,
                    hermes_home=Path(tmp) / "home", run_dir=run_dir,
                    model="m", model_provider="mp", namespace="t-ns")
            manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertTrue(manifest["diagnostic_only"])
        self.assertTrue(
            manifest["evidence_source"].startswith("draft-text"))
        with self.assertRaises(RuntimeError):
            require_scored_manifest(manifest)

    def test_legacy_checkpoint_manifest_still_refused(self):
        with self.assertRaises(RuntimeError):
            require_scored_manifest({"run": "pacbench-w4-live"})
        with self.assertRaises(RuntimeError):
            require_scored_manifest(
                {"run": "pacbench-w5-pilot-life",
                 "evidence_source": "draft-text (diagnostic)"})

    def test_frozen_bundle_episode_passes_the_gates(self):
        import json
        from pacbench import corpus
        from pacbench.frozen import (bundle_hash, freeze_observations,
                                     write_bundle)
        from provider_adapters.lexical_adapter import LexicalAdapter
        life = corpus.get_life("poker-unchanged")
        bundle = freeze_observations([life])
        with tempfile.TemporaryDirectory(prefix="frozen-rt-") as tmp:
            path = Path(tmp) / "tiny.json"
            h = write_bundle(bundle, path)
            life_dir = Path(tmp) / "life"
            with mock.patch("pacbench.live.harness.provision_home",
                            return_value={"ok": True}), \
                 mock.patch("pacbench.live.harness.run_turn",
                            side_effect=_fake_turn):
                rec = live.run_life(
                    life=life, provider="lexical",
                    make_adapter=lambda home: LexicalAdapter(home),
                    life_dir=life_dir, model="m", model_provider="mp",
                    driver_configured_model="m", bundle=bundle)
            manifest = json.loads(
                (life_dir / "manifest.json").read_text())
        self.assertEqual(rec["state"], "valid")
        self.assertEqual(manifest["evidence_source"], "frozen-bundle")
        self.assertEqual(manifest["bundle_hash"], h)
        # The single canonical path end to end: freeze → hash → run →
        # scored gate passes, and the run's exposure matches itself.
        self.assertTrue(require_scored_manifest(manifest))
        self.assertTrue(
            check_exposure_equivalence(manifest, manifest)["match"])


if __name__ == "__main__":
    unittest.main()
