"""Style report (P0-14): offline provider-blind support-vs-ambient
surface distributions over a real corpus life. No score, no gate."""

import json
import unittest

from pacbench import style_report as sr
from pacbench.corpus import get_life

LIFE_ID = "poker-move"

# No scoring/gating vocabulary may appear anywhere in the report output.
FORBIDDEN = ("score", "threshold", "pass", "fail", "gate", "verdict",
             "naturalness")


def _walk_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk_keys(item)


class StyleReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.life = get_life(LIFE_ID)
        cls.report = sr.style_report_for_life(cls.life)

    def test_runs_over_real_life(self):
        self.assertEqual(self.report["life_id"], LIFE_ID)
        self.assertEqual(self.report["n_observations"],
                         len(self.life.observations))
        self.assertGreater(self.report["n_observations"], 0)

    def test_support_and_ambient_both_nonempty(self):
        self.assertGreater(self.report["n_support"], 0)
        self.assertGreater(self.report["n_ambient"], 0)
        self.assertGreater(self.report["support"]["n_messages"], 0)
        self.assertGreater(self.report["ambient"]["n_messages"], 0)
        self.assertEqual(
            self.report["n_support"] + self.report["n_ambient"],
            self.report["n_observations"])

    def test_covers_all_properties(self):
        for group in ("support", "ambient"):
            section = self.report[group]
            for key in ("chars", "words", "sentences",
                        "has_datetime", "datetime_hits_per_msg",
                        "name_tokens_per_msg", "msgs_with_repeated_name",
                        "event_phrases_per_msg", "msgs_with_repeated_event",
                        "msgs_with_exclamation", "msgs_with_question",
                        "commas_per_msg", "exclamations_per_msg",
                        "questions_per_msg", "upper_ratio",
                        "top_names", "top_event_phrases"):
                self.assertIn(key, section, f"{group}.{key}")

    def test_channel_splits(self):
        by_channel = self.report["by_channel"]
        self.assertGreater(len(by_channel), 0)
        life_channels = {o.channel for o in self.life.observations}
        self.assertEqual(set(by_channel), life_channels)
        for channel, split in by_channel.items():
            self.assertIn("support", split)
            self.assertIn("ambient", split)
            self.assertIn("contrast", split)
            total = (split["support"]["n_messages"]
                     + split["ambient"]["n_messages"])
            self.assertEqual(
                total,
                sum(1 for o in self.life.observations
                    if o.channel == channel),
                channel)

    def test_contrast_is_side_by_side(self):
        contrast = self.report["contrast"]
        self.assertIn("chars", contrast)
        self.assertIn("has_datetime", contrast)
        self.assertIn("support_mean", contrast["chars"])
        self.assertIn("ambient_mean", contrast["chars"])
        self.assertIn("difference", contrast["chars"])

    def test_no_score_or_gate_field(self):
        for text in _walk_keys(self.report):
            for word in FORBIDDEN:
                self.assertNotIn(word, text.lower(),
                                 f"forbidden {word!r} in {text!r}")

    def test_json_serializable_and_deterministic(self):
        blob = sr.report_to_json(self.report)
        self.assertEqual(json.loads(blob), self.report)
        again = sr.style_report_for_life(get_life(LIFE_ID))
        self.assertEqual(again, self.report)

    def test_message_features_surface_only(self):
        feats = sr.message_features("Confirmed: dental cleaning Friday at 2pm!")
        self.assertTrue(feats["has_datetime"])
        self.assertGreater(feats["chars"], 0)
        self.assertGreaterEqual(feats["sentences"], 1)
        self.assertNotIn("score", feats)


if __name__ == "__main__":
    unittest.main()
