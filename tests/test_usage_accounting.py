"""Offline native Hermes token accounting and artifact redaction."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pacbench import harness  # noqa: E402

def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["hermes"], returncode=returncode,
        stdout=stdout, stderr="")


def _event(obj: dict) -> str:
    return json.dumps(obj)


class ParseTurnTokens(unittest.TestCase):
    def test_result_tokens_captured(self):
        stdout = "\n".join([
            _event({"type": "system", "subtype": "init", "session_id": "s1"}),
            _event({"type": "text", "text": "hello"}),
            _event({"type": "result", "session_id": "s1", "text": "hello",
                    "tokens": {"input": 1200, "output": 55, "total": 1255}}),
        ])
        result = harness._parse_turn("p", "t", _completed(stdout), 1.0)
        self.assertEqual(result.answer, "hello")
        self.assertEqual(result.session_input_tokens, 1200)
        self.assertEqual(result.session_output_tokens, 55)
        self.assertEqual(result.session_id, "s1")

    def test_missing_tokens_stay_none(self):
        stdout = "\n".join([
            _event({"type": "text", "text": "hi"}),
            _event({"type": "result", "text": "hi"}),
        ])
        result = harness._parse_turn("p", "t", _completed(stdout), 1.0)
        self.assertIsNone(result.session_input_tokens)
        self.assertIsNone(result.session_output_tokens)

    def test_no_result_event_stays_none(self):
        stdout = _event({"type": "text", "text": "partial"})
        result = harness._parse_turn("p", "t", _completed(stdout, 1), 1.0)
        self.assertIsNone(result.session_input_tokens)
        self.assertIsNone(result.session_output_tokens)
        self.assertEqual(result.returncode, 1)

    def test_malformed_tokens_do_not_crash(self):
        stdout = _event({"type": "result", "text": "x",
                         "tokens": {"input": "nonsense", "output": None}})
        result = harness._parse_turn("p", "t", _completed(stdout), 1.0)
        self.assertIsNone(result.session_input_tokens)
        self.assertIsNone(result.session_output_tokens)

    def test_last_result_event_wins(self):
        stdout = "\n".join([
            _event({"type": "result", "text": "a",
                    "tokens": {"input": 10, "output": 5}}),
            _event({"type": "result", "text": "b",
                    "tokens": {"input": 30, "output": 7}}),
        ])
        result = harness._parse_turn("p", "t", _completed(stdout), 1.0)
        self.assertEqual((result.session_input_tokens,
                          result.session_output_tokens), (30, 7))

    def test_timeout_turn_carries_no_tokens(self):
        with tempfile.TemporaryDirectory(prefix="pacbench-tok-") as tmp:
            home = Path(tmp) / "h-home"
            harness.provision_home(home, provider_plugin_src=None,
                                   provider_name="none")
            with mock.patch.object(
                    harness.subprocess, "run",
                    side_effect=subprocess.TimeoutExpired(
                        cmd=["hermes"], timeout=0.1)):
                result = harness.run_turn("hi", at="2026-09-10T12:00:00",
                                          home=home, allowlist="",
                                          timeout_s=0.1)
        self.assertIsNone(result.session_input_tokens)
        self.assertIsNone(result.session_output_tokens)


class RedactKeepsTokenCounts(unittest.TestCase):
    """Regression: usage-accounting keys survive artifact redaction while
    string secrets are still scrubbed (a pilot once persisted its whole
    usage block as "<redacted>" because "turns_with_tokens" contains
    the substring "token"). Offline: redact + the real rollup, no pilot."""

    def test_efficiency_block_survives_redact(self):
        rolled = {"input": 100, "output": 10, "turns_with_tokens": 1,
                  "turns_total": 2}
        efficiency = {"hermes_turns": 25, "memory_tool_calls": 53,
                      "answer_latency_s_total": 223.0, "tokens": rolled}
        self.assertEqual(harness.redact(efficiency), efficiency)

    def test_turn_record_counts_survive_redact(self):
        record = {"kind": "checkpoint", "session_id": "s1",
                  "session_input_tokens": 500, "session_output_tokens": 40,
                  "turn_input_tokens": 250, "turn_output_tokens": 25,
                  "answer": "Wednesday at 10am."}
        self.assertEqual(harness.redact(record), record)

    def test_string_secrets_still_scrubbed(self):
        dirty = {"META_API_KEY": "SECRET-VALUE-1234567890",
                 "tokens": {"input": 1, "output": 2,
                            "turns_with_tokens": 1, "turns_total": 1},
                 "auth": {"user": "u", "passed": True}}
        clean = harness.redact(dirty)
        self.assertEqual(clean["META_API_KEY"], "<redacted>")
        self.assertEqual(clean["tokens"]["turns_with_tokens"], 1)
        # "auth" is not an accounting key: string values scrubbed, and the
        # nested structure is replaced wholesale, never partially leaked.
        self.assertEqual(clean["auth"], "<redacted>")


if __name__ == "__main__":
    unittest.main()
