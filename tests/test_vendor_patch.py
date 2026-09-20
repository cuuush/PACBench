"""Vendor patch integrity: the isolated Hermes tree must equal pin + tracked diff.

The benchmark's Hermes isolation claim rests on three facts: the vendor tree
is the pinned upstream commit, the ONLY modifications are the tracked
fairness-neutral shims, and the run manifest records both. This file enforces
all three offline (no network, no model). If the tree drifts (hand edits,
upstream pull), these fail until the diff is regenerated deliberately.
"""

import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "vendor" / "hermes-isolated"
PATCH = REPO / "patches" / "hermes-isolated.diff"


def _read_pin() -> str:
    for line in (REPO / "vendor" / "HERMES_PIN").read_text().splitlines():
        if line.startswith("HERMES_PIN="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("HERMES_PIN missing from vendor/HERMES_PIN")


class VendorPatchIntegrity(unittest.TestCase):
    def test_tree_is_pinned_commit(self):
        head = subprocess.run(
            ["git", "-C", str(VENDOR), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, _read_pin())

    def test_tree_diff_equals_tracked_patch(self):
        live = subprocess.run(
            ["git", "-C", str(VENDOR), "diff"],
            capture_output=True, text=True).stdout
        self.assertEqual(live, PATCH.read_text(),
                         "vendor tree has hand edits or drift: regenerate "
                         "patches/hermes-isolated.diff deliberately")

    def test_only_expected_files_patched(self):
        names = subprocess.run(
            ["git", "-C", str(VENDOR), "diff", "--name-only"],
            capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(names), [
            "agent/agent_init.py",
            "agent/inline_tool_executors.py",
            "agent/system_prompt.py",
            "agent/tool_executor.py",
            "hermes_time.py",
            "model_tools.py",
        ])

    def test_shims_are_fairness_neutral_markers(self):
        """Every shim line references its contract purpose; no answer keys,
        no scenario names, no provider names in the patch."""
        text = PATCH.read_text()
        for token in ("poker", "dentist", "coat", "Eric", "answer_key",
                      "expected", "memcal", "lexical"):
            self.assertNotIn(token, text.lower().replace("pacbench", ""))
        for marker in ("PACBENCH_NOW", "PACBENCH_TOOL_ALLOWLIST",
                       "PACBENCH_NO_BUILTIN_MEMORY",
                       "PACBENCH_NO_SESSION_SEARCH"):
            self.assertIn(marker, text)

    def test_manifest_records_pin_and_patch(self):
        from pacbench import harness
        record = harness.run_manifest({"probe": True})
        self.assertEqual(record["hermes_pin"], _read_pin())
        self.assertEqual(record["hermes_patches_sha256"],
                         hashlib.sha256(PATCH.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
