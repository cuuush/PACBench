"""Frozen bundles + primary-path episode: offline (no model, no network)."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _fake_turn(prompt, **kw):
    return SimpleNamespace(
        prompt=prompt, at=kw.get("at", ""), answer="Friday",
        tool_calls=[], tool_results=[], policy_blocks=0, latency_s=0.1,
        returncode=0, stderr_tail="", stderr_full="", stdout_tail="",
        raw_events=0, session_id=None, session_input_tokens=None,
        session_output_tokens=None)


class FreezeRoundTrip(unittest.TestCase):
    def test_freeze_write_load_verify(self):
        from pacbench import corpus
        from pacbench.frozen import (bundle_hash, freeze_observations,
                                     load_bundle, observations_for,
                                     write_bundle)
        life = corpus.get_life("poker-collision")
        bundle = freeze_observations([life])
        self.assertEqual(len(bundle["lives"]["poker-collision"]
                             ["observations"]), 12)
        with tempfile.TemporaryDirectory(prefix="frozen-") as tmp:
            path = Path(tmp) / "tiny.json"
            h = write_bundle(bundle, path)
            self.assertEqual(h, bundle_hash(bundle))
            back = load_bundle(path, h)
            self.assertEqual(len(observations_for(back, "poker-collision")),
                             12)

    def test_tamper_fails_closed(self):
        from pacbench import corpus
        from pacbench.frozen import (freeze_observations, load_bundle,
                                     write_bundle)
        life = corpus.get_life("poker-collision")
        bundle = freeze_observations([life])
        with tempfile.TemporaryDirectory(prefix="frozen-") as tmp:
            path = Path(tmp) / "tiny.json"
            h = write_bundle(bundle, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["lives"]["poker-collision"]["observations"][0]["text"] += "!"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_bundle(path, h)
            with self.assertRaises(RuntimeError):
                load_bundle(path, "0" * 64)
            with self.assertRaises(RuntimeError):
                load_bundle(Path(tmp) / "missing.json", h)

    def test_overlay_rejects_id_mismatch(self):
        from pacbench import corpus
        from pacbench.frozen import freeze_observations
        from pacbench.live import overlay_bundle_observations
        life = corpus.get_life("poker-collision")
        bundle = freeze_observations([life])
        got = overlay_bundle_observations(life, bundle)
        self.assertEqual(len(got), 12)
        bundle["lives"]["poker-collision"]["observations"] = (
            bundle["lives"]["poker-collision"]["observations"][:-1])
        with self.assertRaises(RuntimeError):
            overlay_bundle_observations(life, bundle)


class RunLifeCli(unittest.TestCase):
    def test_bundle_is_required(self):
        from pacbench.__main__ import build_parser
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["run-life", "--life", "toy-day", "--provider", "memcal",
                 "--bundle-hash", "0" * 64])


class PrimaryEpisodeOffline(unittest.TestCase):
    """run_life primary path with lexical adapter + fake turns (no Hermes)."""

    def _run(self, **kw):
        from pacbench import corpus
        from pacbench.live import run_life
        from provider_adapters.lexical_adapter import LexicalAdapter
        life = corpus.get_life("poker-collision")
        with tempfile.TemporaryDirectory(prefix="episode-") as tmp:
            life_dir = Path(tmp) / "life"
            with mock.patch("pacbench.live.harness.run_turn",
                            side_effect=_fake_turn):
                rec = run_life(
                    life=life, provider="lexical",
                    make_adapter=lambda home: LexicalAdapter(home),
                    life_dir=life_dir, model="m", model_provider="mp",
                    driver_configured_model="m", **kw)
                manifest = json.loads(
                    (life_dir / "manifest.json").read_text())
                return rec, manifest

    def test_draft_episode_marks_diagnostic(self):
        rec, manifest = self._run()
        self.assertEqual(rec["state"], "valid")
        self.assertTrue(
            manifest["evidence_source"].startswith("draft-text"))
        self.assertEqual(manifest["bundle_hash"], "")

    def test_bundled_episode_records_hash(self):
        from pacbench import corpus
        from pacbench.frozen import (bundle_hash, freeze_observations,
                                     write_bundle)
        life = corpus.get_life("poker-collision")
        bundle = freeze_observations([life])
        with tempfile.TemporaryDirectory(prefix="frozen-") as tmp:
            path = Path(tmp) / "tiny.json"
            h = write_bundle(bundle, path)
            rec, manifest = self._run(bundle=bundle)
        self.assertEqual(manifest["evidence_source"], "frozen-bundle")
        self.assertEqual(manifest["bundle_hash"], h)
