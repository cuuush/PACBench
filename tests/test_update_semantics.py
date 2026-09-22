"""P0-3 guard: Hindsight update-semantics spec invariants (spec-first, no adapter).

Pins what docs/design/hindsight-transport.md relies on:

- The corpus cannot express edits/deletes/reorders (schema carries no mutation
  markers; authoring is single-write; validation rejects identity reuse), so
  the v1 out-of-scope statements rest on structure, not convention.
- The pinned hindsight_api surface the spec cites (per-item document_id +
  update_mode append/replace; tags-only update_document; no bare
  append/replace/upsert methods) still holds.

Free, local, and model-free: nothing here touches the network or instantiates
an engine.
"""

import dataclasses
import importlib.util
import inspect
import unittest

from pacbench.authoring import AuthoringError, BeatPlan, LifePlan, ThreadPlan, build_life
from pacbench.corpus import MANIFESTS, all_lives
from pacbench.life import Life
from pacbench.observation import Observation
from pacbench.validate import validate_corpus, validate_life

#: The exact Observation fields docs/design/hindsight-transport.md §5 relies on.
EXPECTED_OBSERVATION_FIELDS = frozenset({
    "source_id", "channel", "written_at", "arrived_at", "collected_at",
    "sender", "thread", "text", "from_owner", "structured",
})

#: Names that would indicate a mutation/edit/delete/reorder representation
#: leaking into the provider-neutral schema. None may appear as fields.
BANNED_MUTATION_MARKERS = frozenset({
    "document_id", "update_mode", "update", "edit", "edited",
    "delete", "deleted", "tombstone", "retract", "supersede", "supersedes",
    "op", "operation", "operation_id", "version", "sequence", "seq", "order",
})


def _obs(**kw):
    base: dict = dict(source_id="t/m1", channel="text", written_at="2026-09-08T09:00:00",
                arrived_at="2026-09-08T09:00:00", collected_at="2026-09-08T09:00:00",
                sender="Mom", thread="th", text="hello")
    base.update(kw)
    return Observation(**base)


def _life(*observations):
    return Life(id="t", bet="b", role="development", observations=tuple(observations),
                checkpoints=(), lineage="test-lineage")


class SchemaCarriesNoMutationMarkers(unittest.TestCase):
    def test_exact_field_set(self):
        fields = {f.name for f in dataclasses.fields(Observation)}
        self.assertEqual(fields, EXPECTED_OBSERVATION_FIELDS)

    def test_no_mutation_marker_names(self):
        fields = {f.name for f in dataclasses.fields(Observation)}
        self.assertTrue(fields.isdisjoint(BANNED_MUTATION_MARKERS),
                        f"mutation markers in schema: {fields & BANNED_MUTATION_MARKERS}")

    def test_public_payload_is_exactly_the_fields(self):
        self.assertEqual(set(_obs().public()), EXPECTED_OBSERVATION_FIELDS)

    def test_observation_is_immutable(self):
        o = _obs()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            o.text = "rewritten"  # type: ignore[assignment]  # noqa: B018

    def test_beat_plan_has_no_mutation_fields(self):
        beat_fields = {f.name for f in dataclasses.fields(BeatPlan)}
        self.assertTrue(beat_fields.isdisjoint(BANNED_MUTATION_MARKERS),
                        f"mutation markers in BeatPlan: {beat_fields & BANNED_MUTATION_MARKERS}")


class AuthoringIsSingleWrite(unittest.TestCase):
    def _plan(self, *beats):
        return LifePlan(id="t", bet="b", role="development", week_start="2026-09-07",
                        checkpoints=(),
                        threads=(ThreadPlan(id="th", channel="text"),),
                        beats=beats)

    def test_duplicate_beat_local_id_raises(self):
        beat = BeatPlan(local_id="m1", thread="th", sender="Mom", day=1,
                        clock="09:00", draft="hello")
        with self.assertRaises(AuthoringError):
            build_life(self._plan(beat, beat))

    def test_one_observation_per_beat(self):
        beats = [BeatPlan(local_id=f"m{i}", thread="th", sender="Mom", day=1,
                          clock=f"09:0{i}", draft=f"hello {i}") for i in range(3)]
        life, _ = build_life(self._plan(*beats))
        self.assertEqual([o.source_id for o in life.observations],
                         [f"t/m{i}" for i in range(3)])


class ValidationRejectsIdentityReuse(unittest.TestCase):
    def test_duplicate_source_id_is_an_error(self):
        errors = validate_life(_life(_obs(), _obs(text="edited version")))
        self.assertTrue(any("duplicate observation source_id" in e for e in errors),
                        errors)

    def test_distinct_source_ids_are_not_duplicates(self):
        errors = validate_life(_life(_obs(), _obs(source_id="t/m2")))
        self.assertFalse(any("duplicate observation source_id" in e for e in errors),
                         errors)


class CorpusInvariant(unittest.TestCase):
    def test_pinned_corpus_validates_clean(self):
        self.assertEqual(validate_corpus(all_lives(), MANIFESTS), [])

    def test_no_observation_carries_mutation_markers(self):
        for life in all_lives():
            for o in life.observations:
                self.assertEqual(set(o.public()),
                                 EXPECTED_OBSERVATION_FIELDS, o.source_id)

    def test_source_ids_unique_within_each_life(self):
        for life in all_lives():
            ids = [o.source_id for o in life.observations]
            self.assertEqual(len(set(ids)), len(ids), life.id)


@unittest.skipUnless(importlib.util.find_spec("hindsight_api") is not None,
                     "hindsight_api not installed")
class PinnedHindsightApiSurface(unittest.TestCase):
    """Pin the installed-API facts the update-semantics spec cites.

    If hindsight-api upgrades and any of these fail, the spec
    (docs/design/hindsight-transport.md) must be re-verified first.
    """

    def test_version_is_pinned_and_parseable(self):
        from importlib.metadata import version
        import hindsight_api
        installed = version("hindsight-api")
        self.assertEqual(installed, "0.10.0")
        self.assertEqual(hindsight_api.__version__, installed)

    def test_retain_content_dict_update_mode(self):
        from hindsight_api.engine.retain.types import RetainContentDict
        hints = RetainContentDict.__annotations__
        self.assertIn("document_id", hints)  # per-item document identity
        update_mode = hints.get("update_mode")
        self.assertIsNotNone(update_mode)
        self.assertEqual(set(update_mode.__args__), {"replace", "append"})

    def test_update_document_is_tags_only(self):
        from hindsight_api import MemoryEngine
        params = inspect.signature(MemoryEngine.update_document).parameters
        self.assertIn("tags", params)
        self.assertNotIn("content", params)

    def test_no_bare_content_mutation_methods(self):
        from hindsight_api import MemoryEngine
        public = {m for m in dir(MemoryEngine) if not m.startswith("_")}
        for missing in ("update", "append", "replace", "upsert"):
            self.assertNotIn(missing, public)
        for present in ("retain_batch_async", "update_document",
                        "delete_document", "get_operation_status",
                        "retry_operation"):
            self.assertIn(present, public)


if __name__ == "__main__":
    unittest.main()
