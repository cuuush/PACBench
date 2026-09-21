"""Review packet builder: offline, deterministic (no model, no network)."""

import unittest

from pacbench.review_packet import build_packet


class Packet(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(build_packet("race-week"), build_packet("race-week"))

    def test_complete_no_missing(self):
        packet = build_packet("race-week")
        for marker in ("DRAFT", "structural validation: CLEAN",
                       "## Truth", "## Checkpoints", "## Support windows",
                       "## Contradiction dispositions", "## Measured difficulty",
                       "## Ambient sample", "SIGN-OFF"):
            self.assertIn(marker, packet)
        self.assertNotIn("?MISSING?", packet)
        self.assertNotIn("UNDISPOSITIONED", packet)


if __name__ == "__main__":
    unittest.main()
