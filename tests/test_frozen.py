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

    def test_stale_bundle_version_fails_closed(self):
        # 0.1.0 bundles lack from_owner/arrived_at/structured: scored runs
        # must fail closed, never silently score with flattened provenance.
        from pacbench import corpus
        from pacbench.frozen import BUNDLE_VERSION, freeze_observations
        from pacbench.live import overlay_bundle_observations
        life = corpus.get_life("poker-collision")
        bundle = freeze_observations([life])
        assert bundle["bundle_version"] == BUNDLE_VERSION
        bundle["bundle_version"] = "0.1.0"
        with self.assertRaises(RuntimeError) as ctx:
            overlay_bundle_observations(life, bundle)
        self.assertIn("BUNDLE_VERSION", str(ctx.exception))

    def test_day_batch_clock_keeps_exact_instant(self):
        # SMART: the day key is transport grouping only. The adapter's
        # collection moment is the batch's latest exact collected_at
        # (MEMCAL_TODAY pin / op-id seed keep exact timing); each payload
        # keeps its own written_at/thread/sender untouched. 2 days × 2
        # msgs → 2 ingest calls (delta-only, never cumulative re-ingest).
        from pacbench.life import Life
        from pacbench.live import run_life
        from pacbench.observation import Observation
        from pacbench.rubric import Checkpoint, Kind, Norm, Slot
        from provider_adapters.lexical_adapter import LexicalAdapter
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
                        sender="Gus", thread="tx", text="day two first"),
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
        life = Life(id="t", bet="b", role="development",
                    observations=obs, checkpoints=(cp,))
        seen = {}

        class SpyAdapter(LexicalAdapter):
            def ingest(self, observations, collected_at):
                seen.setdefault("calls", []).append(
                    (collected_at, [o["source_id"] for o in observations]))
                return super().ingest(observations, collected_at)

        from pacbench.frozen import BUNDLE_VERSION, freeze_observations
        bundle = freeze_observations([life])
        with tempfile.TemporaryDirectory(prefix="episode-") as tmp:
            life_dir = Path(tmp) / "life"
            with mock.patch("pacbench.live.harness.run_turn",
                            side_effect=_fake_turn):
                rec = run_life(
                    life=life, provider="lexical",
                    make_adapter=lambda home: SpyAdapter(home),
                    life_dir=life_dir, model="m", model_provider="mp",
                    driver_configured_model="m", bundle=bundle)
            batches = json.loads(
                (life_dir / "manifest.json").read_text())["import_batches"]
        self.assertEqual([c for c, _ in seen["calls"]],
                         ["2026-09-08T10:05:00", "2026-09-09T10:00:00"])
        self.assertEqual([sorted(ids) for _, ids in seen["calls"]],
                         [["d1a", "d1b"], ["d2a", "d2b"]])
        self.assertEqual([b["batch_day"] for b in batches],
                         ["2026-09-08", "2026-09-09"])
        self.assertEqual(rec["state"], "valid")
