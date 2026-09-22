"""Lifecycle cost rollup tests (plan.md P0-15, observability only, no gates).

No network, no spend. Unknown stays unknown (never zero); no per-API-call
column may exist because provider call shapes intentionally differ.
"""

import unittest

from pacbench.adapters import Usage
from pacbench.cost_rollup import (
    CostProhibition,
    DRIVER,
    INGESTION,
    MAINTENANCE,
    OPAQUE,
    QUESTION_TIME,
    RETRIES,
    ROW_ORDER,
    TOTAL,
    rollup_lifecycle,
)

EXPECTED_ROWS = {
    INGESTION,
    MAINTENANCE,
    QUESTION_TIME,
    DRIVER,
    RETRIES,
    OPAQUE,
    TOTAL,
}


def _walk_keys(node):
    """Yield every dict key in the rollup, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk_keys(value)


class CostRollupTest(unittest.TestCase):
    def test_seven_rows_present(self):
        rollup = rollup_lifecycle()
        self.assertEqual(set(rollup.keys()), EXPECTED_ROWS)
        self.assertEqual(list(rollup.keys()), list(ROW_ORDER))
        self.assertEqual(len(rollup), 7)

    def test_opaque_only_input_yields_unknown_total_never_zero(self):
        rollup = rollup_lifecycle(opaque_notes=["overnight dream pass, unpriced"])
        total = rollup[TOTAL]
        self.assertIsNone(total["input_tokens"])
        self.assertIsNone(total["output_tokens"])
        self.assertIsNone(total["cost_usd"])
        self.assertNotEqual(total["input_tokens"], 0)
        self.assertNotEqual(total["output_tokens"], 0)
        self.assertNotEqual(total["cost_usd"], 0)
        self.assertEqual(total["status"], "unknown")
        self.assertEqual(total["label"], "total-known-excluding-unknown")
        opaque = rollup[OPAQUE]
        self.assertIsNone(opaque["input_tokens"])
        self.assertIsNone(opaque["cost_usd"])
        self.assertEqual(opaque["status"], "unknown")

    def test_mixed_known_and_unknown_labels_correctly(self):
        rollup = rollup_lifecycle(
            ingest_usages=[Usage(input_tokens=100, output_tokens=20,
                                 cost_usd=0.01)],
            question_turn_tokens=[(50, 10), (None, None)],
            driver_tokens={"session_input_tokens": 30,
                           "session_output_tokens": 5},
            retries=2,
            opaque_notes=["background consolidation, unpriced"],
        )
        self.assertEqual(rollup[INGESTION]["status"], "known")
        # Maintenance saw no priced work: unknown, never zero.
        self.assertIsNone(rollup[MAINTENANCE]["input_tokens"])
        self.assertEqual(rollup[MAINTENANCE]["status"], "unknown")
        # Partially known turn stream labels partial and sums known turns.
        self.assertEqual(rollup[QUESTION_TIME]["status"], "partial")
        self.assertEqual(rollup[QUESTION_TIME]["input_tokens"], 50)
        self.assertEqual(rollup[DRIVER]["input_tokens"], 30)
        total = rollup[TOTAL]
        self.assertEqual(total["label"], "total-known-excluding-unknown")
        self.assertEqual(total["input_tokens"], 100 + 50 + 30)
        self.assertEqual(total["output_tokens"], 20 + 10 + 5)
        self.assertEqual(total["cost_usd"], 0.01)
        self.assertEqual(total["status"], "partial")
        self.assertIn(MAINTENANCE, total["unknown_components"])
        self.assertIn(RETRIES, total["unknown_components"])
        self.assertNotIn(INGESTION, total["unknown_components"])

    def test_no_per_call_column_exists(self):
        rollup = rollup_lifecycle(
            ingest_usages=[Usage(input_tokens=100, output_tokens=20)],
            maintenance_usages=[Usage(input_tokens=40, output_tokens=8)],
            question_turn_tokens=[(50, 10)],
            driver_tokens=(30, 5),
            retries=[Usage(input_tokens=7, output_tokens=2)],
            opaque_notes=["unpriced"],
        )
        for key in _walk_keys(rollup):
            normalized = str(key).lower().replace("_", "").replace("-", "")
            self.assertNotIn("percall", normalized,
                             f"per-call column forbidden: {key!r}")
            self.assertNotIn("percall", normalized)
        self.assertNotIn("cost_per_call", str(rollup))

    def test_prohibition_documented_in_output(self):
        rollup = rollup_lifecycle(opaque_notes=["unpriced"])
        self.assertIn("call shapes intentionally differ", CostProhibition)
        self.assertIn(CostProhibition, rollup[TOTAL]["note"])
        self.assertIn(CostProhibition, rollup[TOTAL]["detail"])


if __name__ == "__main__":
    unittest.main()
