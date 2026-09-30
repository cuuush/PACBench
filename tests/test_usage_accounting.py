"""Item 4 regression: token/usage accounting (§35).

- Harness captures the clone's session-cumulative token counters from the
  terminal stream-json `result` event; missing stats stay None (never zero).
- live.turn_token_delta derives per-turn spend as the session delta;
  live.sum_turn_tokens / _pilot_turns roll turns into pilot efficiency.
- MemcalAdapter.maintenance reads the dream pass's own `runs` row for
  provider-side calls/tokens; skips keep tokens None with the reason.

Offline throughout: subprocesses and dream are stubbed; no network, no model.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pacbench import harness  # noqa: E402

try:
    from provider_adapters.memcal_adapter import (  # noqa: E402
        MemcalAdapter,
        read_run_usage,
    )
    _IMPORT_ERROR = None
except Exception as exc:  # memcal checkout missing — skip, never fake a pass
    MemcalAdapter = None  # type: ignore
    _IMPORT_ERROR = exc


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


@unittest.skipIf(MemcalAdapter is None,
                  f"memcal checkout unavailable: {_IMPORT_ERROR}")
class DreamUsageAccounting(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pacbench-tok-")
        self.home = Path(self._tmp.name) / "memcal-home"
        self.adapter = MemcalAdapter(self.home)
        self.adapter.reset("tok-ns")

    def tearDown(self):
        self._tmp.cleanup()

    def _insert_run(self, **columns) -> int:
        import sqlite3
        conn = sqlite3.connect(self.home / "memcal.db")
        try:
            cur = conn.execute(
                "INSERT INTO runs(started_at, mode, model, bundles, items,"
                " finished_at, prompt_tokens, completion_tokens,"
                " requests, failed_calls)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("2026-09-20T00:00:00", "nightly", "test-model", 1, 2,
                 "2026-09-20T00:01:00",
                 columns.get("prompt_tokens", 0),
                 columns.get("completion_tokens", 0),
                 columns.get("requests", 0),
                 columns.get("failed_calls", 0)))
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def test_skipped_dream_keeps_tokens_unknown(self):
        from memcal.dream import run as dream_run
        with mock.patch.object(dream_run, "dream",
                               side_effect=RuntimeError("no credential")):
            usage = self.adapter.maintenance("2026-09-10T23:59:00")
        self.assertEqual(self.adapter._maintenance["state"], "skipped")
        self.assertEqual(usage.model_calls, 0)
        self.assertIsNone(usage.input_tokens)
        self.assertIsNone(usage.output_tokens)
        self.assertIn("skipped", usage.notes[0])
        total = self.adapter.usage()
        self.assertIsNone(total.input_tokens)
        self.assertIsNone(total.output_tokens)

    def test_completed_dream_reports_run_row(self):
        from memcal.dream import run as dream_run
        run_id = self._insert_run(prompt_tokens=900, completion_tokens=120,
                                  requests=3, failed_calls=0)
        with mock.patch.object(dream_run, "dream",
                               return_value=SimpleNamespace(run_id=run_id)):
            usage = self.adapter.maintenance("2026-09-10T23:59:00")
        self.assertEqual(self.adapter._maintenance["state"], "complete")
        self.assertEqual(usage.model_calls, 3)
        self.assertEqual(usage.input_tokens, 900)
        self.assertEqual(usage.output_tokens, 120)
        total = self.adapter.usage()
        self.assertEqual((total.model_calls, total.input_tokens,
                          total.output_tokens), (3, 900, 120))

    def test_nothing_new_pass_reports_observed_zero(self):
        from memcal.dream import run as dream_run
        run_id = self._insert_run(prompt_tokens=0, completion_tokens=0,
                                  requests=0, failed_calls=0)
        with mock.patch.object(dream_run, "dream",
                               return_value=SimpleNamespace(run_id=run_id)):
            usage = self.adapter.maintenance("2026-09-10T23:59:00")
        # Observed zero is data, not unknown: ints, not None.
        self.assertEqual((usage.input_tokens, usage.output_tokens), (0, 0))
        self.assertIsNotNone(self.adapter.usage().input_tokens)

    def test_unreadable_row_keeps_tokens_unknown(self):
        from memcal.dream import run as dream_run
        with mock.patch.object(dream_run, "dream",
                               return_value=SimpleNamespace(run_id=999999)):
            usage = self.adapter.maintenance("2026-09-10T23:59:00")
        self.assertEqual(self.adapter._maintenance["state"], "failed")
        self.assertIsNone(usage.input_tokens)
        self.assertIn("unreadable", usage.notes[0])

    def test_read_run_usage_shapes(self):
        import sqlite3
        run_id = self._insert_run(prompt_tokens=10, completion_tokens=4,
                                  requests=2, failed_calls=1)
        conn = sqlite3.connect(self.home / "memcal.db")
        try:
            spent = read_run_usage(conn, run_id)
            self.assertEqual(spent["prompt_tokens"], 10)
            self.assertEqual(spent["completion_tokens"], 4)
            self.assertEqual(spent["requests"], 2)
            self.assertIsNone(read_run_usage(conn, 999999))
            self.assertIsNone(read_run_usage(conn, None))
        finally:
            conn.close()


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
