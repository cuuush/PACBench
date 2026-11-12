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
        with mock.patch('pacbench.gateway_client.client_settings',return_value=('http://127.0.0.1:8099/p/unit/v1','offline-test')), \
             tempfile.TemporaryDirectory(prefix="pacbench-tok-") as tmp:
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


def test_late_native_profile_work_keeps_cost_but_does_not_poison_retention():
    from types import SimpleNamespace
    from datetime import datetime,timezone
    from pacbench.usage import attribute_provider_paths,provider_paths_match
    from pacbench.followups import tool_profile
    complete=datetime(2026,10,15,10,0,10,tzinfo=timezone.utc).timestamp()
    def make_turn():
        return SimpleNamespace(tool_calls=[],tool_results=[],provider_traces=[{
            'provider':'honcho','attribution':'background-or-prefetch','record':{
                'timestamp':complete,'toolCalls':[{'toolName':'search_messages','args':{'query':'pizza'}}]}}])
    t=make_turn()
    assert tool_profile(t)['provider_internal_path_needs_review'] is True
    usage={'model_calls':[{'role':'hermes','started':'2026-10-15T10:00:02+00:00'}]}
    attribute_provider_paths(t,usage)
    profile=tool_profile(t)
    assert profile['provider_internal_tool_calls']==1  # Never erase the work.
    assert profile['provider_internal_operations'][0]['category']=='source_messages'
    assert profile['provider_internal_path_needs_review'] is False
    assert profile['source_fallback_attempted'] is False
    assert t.provider_traces[0]['path_attribution']['affects_answer'] is False
    usage['path_attribution_version']='native-completion-v1'
    assert provider_paths_match(t,usage)
    t.provider_traces[0]['path_attribution']['final_hermes_input_at']='changed'
    assert not provider_paths_match(t,usage)
    # If a later Hermes input could consume the response, attribution stays unknown.
    early=make_turn()
    attribute_provider_paths(early,{'model_calls':[{'role':'hermes','started':'2026-10-15T10:00:11+00:00'}]})
    assert tool_profile(early)['provider_internal_path_needs_review'] is True
    absent=make_turn();attribute_provider_paths(absent,{'model_calls':[]})
    assert tool_profile(absent)['provider_internal_path_needs_review'] is True


def test_simulated_native_trace_cannot_be_ordered_against_gateway_time():
    from types import SimpleNamespace
    from datetime import datetime
    from pacbench.usage import attribute_provider_paths
    from pacbench.followups import tool_profile
    for native in ('2026-09-01T12:00:00+00:00','2027-01-01T12:00:00+00:00'):
        for attribution in ('explicit-query-match','background-or-prefetch'):
            turn=SimpleNamespace(tool_calls=[],tool_results=[],provider_clock={'version':'honcho-wall-clock-v3'},
                provider_traces=[{'provider':'honcho','attribution':attribution,'record':{
                    'timestamp':datetime.fromisoformat(native).timestamp(),
                    'toolCalls':[{'toolName':'search_messages','args':{'query':'pizza'}}]}}])
            attribute_provider_paths(turn,{'model_calls':[{'role':'hermes','started':'2026-11-05T12:00:00+00:00'}]})
            assert 'path_attribution' not in turn.provider_traces[0]
            profile=tool_profile(turn)
            if attribution == 'explicit-query-match':
                assert profile['source_fallback_attempted']
            else:
                assert profile['provider_internal_path_needs_review']


def test_gateway_details_and_costs_remain_distinct(tmp_path):
    import sqlite3
    from pacbench.usage import snapshot, window
    db = sqlite3.connect(tmp_path / 'ledger.sqlite3')
    db.execute("CREATE TABLE calls (id TEXT, project TEXT, model TEXT, endpoint TEXT, started TEXT, finished TEXT, state TEXT, input_tokens INTEGER, output_tokens INTEGER, accounted INTEGER, reserved INTEGER, usage_detail TEXT, actual_cost_nano INTEGER, actual_cost_source TEXT, upstream_request_id TEXT, generation_id TEXT)")
    db.executemany("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ('a','provider','spark','chat','1','2','settled',100,20,1000,1000,
         json.dumps({'completion_tokens_details.reasoning_tokens': 5, 'prompt_tokens_details.cached_tokens': 40, 'prompt': 'secret'}), None, None, 'up-a', None),
        ('b','provider','spark','chat','2',None,'unknown',None,None,2000,2000,None,None,None,None,None),
        ('c','provider','spark','chat','3','4','settled',30,10,3000,3000,None,2500,'provider', 'up-c', 'gen-c')])
    db.commit(); db.close()
    capture = snapshot({'provider': 'memory_provider'}, tmp_path)
    assert capture['state'] == 'captured'
    assert capture['calls'][0]['usage_detail'] == {'completion_tokens_details.reasoning_tokens':5, 'prompt_tokens_details.cached_tokens':40}
    assert capture['calls'][2]['actual_cost_usd'] == .0000025
    result = window({'state':'captured','at':'0','calls':[]}, capture)
    group = result['groups'][0]
    assert group['input_tokens'] is None and group['actual_cost_usd'] is None
    assert group['actual_cost_known_usd'] == .0000025 and group['actual_cost_unknown_calls'] == 2
    assert group['token_price_estimate_usd'] == .000001
    assert group['unconfirmed_budget_bound_usd'] == .000002
    assert len(result['model_calls']) == 3


def test_legacy_ledger_remains_readable(tmp_path):
    import sqlite3
    from pacbench.usage import snapshot
    db = sqlite3.connect(tmp_path / 'ledger.sqlite3')
    db.execute("CREATE TABLE calls (id TEXT, project TEXT, model TEXT, endpoint TEXT, started TEXT, finished TEXT, state TEXT, input_tokens INTEGER, output_tokens INTEGER, accounted INTEGER, reserved INTEGER)")
    db.execute("INSERT INTO calls VALUES ('a','p','m','chat','1','2','settled',2,3,10,10)")
    db.commit(); db.close()
    call = snapshot({'p':'hermes'}, tmp_path)['calls'][0]
    assert call['actual_cost_usd'] is None and call['usage_detail'] == {}


def test_reconciliation_refreshes_only_original_request_ids(tmp_path):
    from pacbench.usage import refresh_window, window
    def call(id, state, actual=None):
        return {'id':id, 'role':'memory_provider', 'project':'p', 'model':'spark',
                'state':state, 'input_tokens':10, 'output_tokens':5,
                'accounted_upper_bound_usd':.02, 'actual_cost_usd':actual, 'usage_detail':{}}
    before={'state':'captured','at':'before','calls':[]}
    original=window(before, {'state':'captured','at':'after','calls':[call('a','unknown')]})
    latest={'state':'captured','at':'later','calls':[call('a','settled',.001),call('background','settled',.003)]}
    updated=refresh_window(original, latest)
    assert [c['id'] for c in updated['model_calls']] == ['a']
    assert updated['roles']['memory_provider']['actual_cost_usd'] == .001
    assert updated['roles']['memory_provider']['unconfirmed_budget_bound_usd'] == 0
    assert original['model_calls'][0]['state'] == 'unknown'
