"""Batch rendering: offline tests with fake generators (no network)."""

import unittest

from pacbench.batch_render import (
    Beat,
    RenderBundle,
    render_all,
    render_beat,
    substantial_prompt,
)
from pacbench.generation import GenerationError


def _beat(draft="hello world", keep=()):
    return Beat(channel="text", sender="Sam", draft=draft,
                must_include=keep)


class RenderBeat(unittest.TestCase):
    def test_ok_first_try(self):
        rec = render_beat(_beat(), "a",
                          lambda p: "hello world!", substantial_prompt)
        self.assertEqual(rec.status, "ok")
        self.assertEqual(rec.attempts, 1)

    def test_entail_retry_then_ok(self):
        calls = iter(["wrong text", "hello world again"])
        rec = render_beat(_beat("hello world", ("world",)), "a",
                          lambda p: next(calls), substantial_prompt)
        self.assertEqual(rec.status, "ok")
        self.assertEqual(rec.attempts, 2)

    def test_entail_fail_closed(self):
        rec = render_beat(_beat("hello world", ("world",)), "a",
                          lambda p: "wrong text", substantial_prompt,
                          max_entail_retries=1)
        self.assertEqual(rec.status, "failed")
        self.assertTrue(rec.error.startswith("dropped:"))
        self.assertEqual(rec.rendered, "")

    def test_transport_retry_then_ok(self):
        calls = iter([GenerationError("boom"), "hello world"])
        def gen(p):
            val = next(calls)
            if isinstance(val, Exception):
                raise val
            return val
        rec = render_beat(_beat(), "a", gen, substantial_prompt)
        self.assertEqual(rec.status, "ok")

    def test_transport_fail_closed(self):
        def gen(p):
            raise GenerationError("down")
        rec = render_beat(_beat(), "a", gen, substantial_prompt,
                          max_transport_retries=1)
        self.assertEqual(rec.status, "failed")
        self.assertTrue(rec.error.startswith("transport:"))

    def test_added_atoms_recorded(self):
        rec = render_beat(_beat("see you soon"), "a",
                          lambda p: "see you soon Saturday!",
                          substantial_prompt)
        self.assertEqual(rec.status, "ok")
        self.assertIn("saturday", rec.added_atoms)


class RenderAll(unittest.TestCase):
    def _bundle(self):
        return RenderBundle(
            model="m", temperature=0.7, prompter="substantial-v1",
            prompt_sha="x", corpus_version="0.3.0", corpus_hash="y",
            life_id="race-week")

    def test_resume_skips_ok(self):
        bundle = self._bundle()
        bundle.records["a"] = render_beat(
            _beat(), "a", lambda p: "hello world", substantial_prompt)
        seen = []
        def gen(p):
            seen.append(p)
            return "second beat here"
        beats = [("a", _beat()), ("b", _beat("second beat here"))]
        out = render_all(beats, gen, resume=bundle, workers=1)
        self.assertEqual(out.records["a"].attempts, 1)
        self.assertEqual(out.records["b"].status, "ok")
        self.assertEqual(len(seen), 1)

    def test_bundle_round_trip(self):
        bundle = self._bundle()
        bundle.records["a"] = render_beat(
            _beat(), "a", lambda p: "hello world", substantial_prompt)
        back = RenderBundle.from_json(bundle.to_json())
        self.assertEqual(back.records["a"].rendered, "hello world")
        self.assertEqual(back.model, "m")


if __name__ == "__main__":
    unittest.main()
