"""P0-5 regression: collected_at (discovery time) vs ingested_at (run artifact).

No network, no model, no Hermes run, no spend. Drives the REAL runner
code paths (run_checkpoint_live + run_life/_run_life_episode) with a
FakeEngine-style adapter and mocked turns.

Two observations with distinct collected_at inside one collection day
must share ONE transport batch while keeping their original
per-observation times and order at the adapter. Batching may change
only grouping (+ the ingested_at run artifact), never source
chronology — and the op-id still derives from the collected_at
boundary, never ingested_at.
"""

import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pacbench import live  # noqa: E402

BANK = "pacbench-test-bank"
EARLY = "2026-09-08T10:00:00"
LATE = "2026-09-08T10:05:00"


def _obs(source_id, collected_at, text="hello"):
    return {"source_id": source_id, "channel": "text",
            "written_at": "2026-09-08T09:00:00",
            "arrived_at": "2026-09-08T09:05:00",
            "collected_at": collected_at, "sender": "Gus", "thread": "tx",
            "text": text, "from_owner": False, "structured": None}


class FakeEngine:
    """Hindsight-shaped fake: records what the adapter actually saw and
    derives its op-id from the received collected_at boundary exactly
    the way hindsight_adapter.py does (uuid5 bank:collected_at)."""

    def __init__(self):
        self.calls = []

    def ingest(self, observations, collected_at, group_flags=None):
        op_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{BANK}:{collected_at}"))
        self.calls.append({
            "boundary": collected_at,
            "op_id": op_id,
            # order + per-obs times as received — must be untouched
            "seen": [(o["source_id"], o["collected_at"]) for o in observations],
        })
        return SimpleNamespace(model_calls=0, latency_s=0.0, notes="fake")

    def retrieval(self):
        return {"provider": "fake-engine", "tool_allowlist": ()}

    def reset(self, namespace):
        self.namespace = namespace

    def usage(self):
        return SimpleNamespace(model_calls=0, latency_s=0.0, notes="fake")


def _fake_turn(prompt, **kw):
    return SimpleNamespace(
        prompt=prompt, at=kw.get("at", ""), answer="day one",
        tool_calls=[], tool_results=[], policy_blocks=0, latency_s=0.1,
        returncode=0, stderr_tail="", stderr_full="", stdout_tail="",
        raw_events=0, session_id=None, session_input_tokens=None,
        session_output_tokens=None)


def _life_and_checkpoint():
    from pacbench.life import Life
    from pacbench.observation import Observation
    from pacbench.rubric import Checkpoint, Kind, Norm, Slot
    obs = (
        Observation(source_id="m1", channel="text",
                    written_at="2026-09-08T09:00:00",
                    arrived_at="2026-09-08T09:05:00",
                    collected_at=EARLY,
                    sender="Gus", thread="tx", text="day one first"),
        Observation(source_id="m2", channel="text",
                    written_at="2026-09-08T09:01:00",
                    arrived_at="2026-09-08T09:06:00",
                    collected_at=LATE,
                    sender="Gus", thread="tx", text="day one second"),
    )
    cp = Checkpoint(
        id="t/cp", at="2026-09-08T11:00:00",
        question="What did Gus say?",
        slots=(Slot(id="s", kind=Kind.CURRENT, norm=Norm.TEXT,
                    expected="day one",
                    supported_by=("m1", "m2")),))
    life = Life(id="t", bet="b", role="development",
                observations=obs, checkpoints=(cp,))
    return life, cp


class CollectedAtVsIngestedAt(unittest.TestCase):
    def _run_checkpoint(self, tmp):
        life, cp = _life_and_checkpoint()
        engine = FakeEngine()
        run_dir = Path(tmp) / "run"
        with mock.patch("pacbench.live.harness.provision_home",
                        return_value={"ok": True}), \
             mock.patch("pacbench.live.harness.run_turn",
                        side_effect=_fake_turn):
            live.run_checkpoint_live(
                life=life, checkpoint=cp, adapter=engine,
                hermes_home=Path(tmp) / "home", run_dir=run_dir,
                model="m", model_provider="mp", namespace="t-ns")
        import json
        manifest = json.loads((run_dir / "manifest.json").read_text())
        return engine, manifest

    def test_day_batch_keeps_source_chronology_at_adapter(self):
        with tempfile.TemporaryDirectory(prefix="ingested-at-") as tmp:
            engine, _ = self._run_checkpoint(tmp)
        # One transport batch for the shared collection day ...
        self.assertEqual(len(engine.calls), 1)
        call = engine.calls[0]
        # ... whose boundary is the batch's latest collected_at ...
        self.assertEqual(call["boundary"], LATE)
        # ... while each payload keeps its ORIGINAL time and order.
        self.assertEqual(call["seen"], [("m1", EARLY), ("m2", LATE)])

    def test_import_batches_record_ingested_at_run_artifact(self):
        with tempfile.TemporaryDirectory(prefix="ingested-at-") as tmp:
            _, manifest = self._run_checkpoint(tmp)
        batches = manifest["import_batches"]
        self.assertEqual(len(batches), 1)
        entry = batches[0]
        # Discovery-time record unchanged ...
        self.assertEqual(entry["collected_at"], LATE)
        self.assertEqual(entry["batch_day"], "2026-09-08")
        self.assertEqual(entry["observations"], ["m1", "m2"])
        # ... plus a SEPARATE wall-clock submit moment ...
        ingested = entry["ingested_at"]
        parsed = datetime.fromisoformat(ingested)
        self.assertIsNotNone(parsed.tzinfo,
                             f"ingested_at must be tz-aware ISO: {ingested!r}")
        # ... which is a run artifact, not source chronology: it must
        # not equal (or rewrite) either observation's collected_at.
        self.assertNotIn(ingested, (EARLY, LATE))

    def test_op_id_derives_from_collected_at_boundary(self):
        with tempfile.TemporaryDirectory(prefix="ingested-at-") as tmp:
            engine, manifest = self._run_checkpoint(tmp)
        call = engine.calls[0]
        ingested_at = manifest["import_batches"][0]["ingested_at"]
        # Op-id seeds on the SIMULATED boundary (hindsight formula) ...
        self.assertEqual(
            call["op_id"],
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"{BANK}:{LATE}")))
        # ... and would differ had it (wrongly) keyed on wall-clock.
        self.assertNotEqual(
            call["op_id"],
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"{BANK}:{ingested_at}")))

    def test_life_episode_site_records_ingested_at(self):
        # Second live.py call site: _run_life_episode.ingest_until.
        from provider_adapters.lexical_adapter import LexicalAdapter
        life, _ = _life_and_checkpoint()
        from pacbench.frozen import freeze_observations
        bundle = freeze_observations([life])
        seen = {}

        class SpyAdapter(LexicalAdapter):
            def ingest(self, observations, collected_at, group_flags=None):
                seen.setdefault("calls", []).append(
                    (collected_at,
                     [(o["source_id"], o["collected_at"])
                      for o in observations]))
                return super().ingest(observations, collected_at,
                                      group_flags=group_flags)

        with tempfile.TemporaryDirectory(prefix="ingested-at-") as tmp:
            life_dir = Path(tmp) / "life"
            with mock.patch("pacbench.live.harness.provision_home",
                            return_value={"ok": True}), \
                 mock.patch("pacbench.live.harness.run_turn",
                            side_effect=_fake_turn):
                rec = live.run_life(
                    life=life, provider="lexical",
                    make_adapter=lambda home: SpyAdapter(home),
                    life_dir=life_dir, model="m", model_provider="mp",
                    driver_configured_model="m", bundle=bundle)
            import json
            manifest = json.loads(
                (life_dir / "manifest.json").read_text())
        self.assertEqual(rec["state"], "valid")
        batches = manifest["import_batches"]
        self.assertEqual(len(batches), 1)
        # Adapter saw original times/order; manifest carries both stamps.
        self.assertEqual(seen["calls"][0][1], [("m1", EARLY), ("m2", LATE)])
        self.assertEqual(batches[0]["collected_at"], LATE)
        parsed = datetime.fromisoformat(batches[0]["ingested_at"])
        self.assertIsNotNone(parsed.tzinfo)


if __name__ == "__main__":
    unittest.main()
