"""Pool-shape tests for the judge-calibration candidates.

These tests pin the pool's STRUCTURE (coverage, identity, no-label
discipline) — never gold labels, which come from two independent
humans plus adjudication. No model, no network, no Hermes.
"""

import dataclasses
import re
import unittest

from pacbench.judge_calibration import (
    CATEGORIES, CANDIDATES, POOL_VERSION, CalibrationCandidate)
from pacbench.rubric import Norm


class CalibrationPoolShape(unittest.TestCase):
    def test_eight_categories_covered(self):
        self.assertEqual(set(CATEGORIES),
                         {"paraphrase", "negation", "historical",
                          "declined", "uncertainty", "contradiction",
                          "partial", "mixed"})
        present = {c.category for c in CANDIDATES}
        self.assertEqual(present, set(CATEGORIES))

    def test_each_category_has_depth(self):
        from collections import Counter
        counts = Counter(c.category for c in CANDIDATES)
        for category in CATEGORIES:
            self.assertGreaterEqual(counts[category], 10, category)

    def test_pool_has_room_to_grow(self):
        # Draft pool complete at 200 (104 + 96 race-week/boundary drafts);
        # pilot residuals EXTEND it (versioned), never silently replace.
        self.assertEqual(len(CANDIDATES), 200)

    def test_ids_unique_and_sequential(self):
        ids = [c.id for c in CANDIDATES]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids, [f"CAL-{i:03d}" for i in range(1, len(ids) + 1)])

    def test_no_label_field_anywhere(self):
        """Drafting is not labeling: no gold-label-shaped field may exist."""
        banned = {"label", "gold", "verdict", "truth", "expected_verdict",
                  "is_correct", "correct"}
        fields = {f.name for f in dataclasses.fields(CalibrationCandidate)}
        self.assertEqual(fields,
                         {"id", "category", "norm", "kind",
                          "expected", "answer", "context"})
        self.assertFalse(fields & banned)

    def test_fields_well_formed(self):
        for c in CANDIDATES:
            self.assertIsInstance(c.norm, Norm, c.id)
            self.assertIn(c.kind, ("CURRENT", "HISTORICAL"), c.id)
            self.assertTrue(c.expected.strip(), c.id)
            self.assertTrue(c.answer.strip(), c.id)
            self.assertTrue(c.context.strip(), c.id)

    def test_version_is_draft(self):
        self.assertIn("draft", POOL_VERSION)


if __name__ == "__main__":
    unittest.main()
