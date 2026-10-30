"""A resumed simulated conversation keeps the date of its first turn."""
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pacbench import harness
from pacbench.native_checks import advance, fresh_state


class SimulatedSessionStart(unittest.TestCase):
    def test_turn_passes_simulated_session_start_to_hermes(self):
        first = '2026-10-19T10:00:00-04:00'
        later = '2026-10-20T10:00:00-04:00'
        captured = []

        def timeout(*args, **kwargs):
            captured.append(kwargs['env'])
            raise subprocess.TimeoutExpired('hermes', 1)

        with TemporaryDirectory() as tmp, patch.object(harness, '_check_paths'), \
             patch('pacbench.context_isolation.workspace_for', return_value=Path(tmp)), \
             patch('pacbench.context_isolation.actual_context', return_value={'state': 'pass'}), \
             patch.object(harness.subprocess, 'run', side_effect=timeout), \
             patch.object(harness, 'provider_trace_snapshot'), \
             patch.object(harness, 'capture_provider_traces'), \
             patch.object(harness, 'enforce_provider_tools'), \
             patch.object(harness, '_recover_tool_results'):
            initial = harness.run_turn('Question', at=first, home=Path(tmp), allowlist='')
            resumed = harness.run_turn('Next', at=later, home=Path(tmp), allowlist='',
                                       session_id='opaque-session', session_started_at=initial.session_started_at)
        self.assertEqual([e['PACBENCH_SESSION_STARTED_AT'] for e in captured], [first, first])
        self.assertEqual(captured[1]['PACBENCH_NOW'], later)
        self.assertEqual(resumed.session_started_at, first)

    def test_legacy_turn_retains_first_public_time(self):
        state = fresh_state()
        turn = harness.TurnResult(prompt='q', at='2026-10-19T10:00:00-04:00', answer='a',
                                  tool_calls=[], tool_results=[], policy_blocks=0, latency_s=0,
                                  returncode=0, stderr_tail='', raw_events=0, session_id='s')
        with TemporaryDirectory() as tmp:
            advance(state, turn, Path(tmp))
        self.assertEqual(state['session_started_at'], turn.at)


if __name__ == '__main__':
    unittest.main()
