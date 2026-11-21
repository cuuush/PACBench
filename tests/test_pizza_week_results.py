"""Keep the published aggregate tied to the unchanged synthetic v2 contract."""
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from pacbench.workflow_spec import SPEC, compile_week
from pacbench.workflow_memcal_contract import verify_contract

ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_contract_and_cost_partitions():
    result = json.loads((ROOT/'results/pizza-week-v2/summary.json').read_text())
    _, docs, turns, frozen = compile_week('continuing-account')
    verified = verify_contract(docs, turns, frozen)
    assert result['contract']['binding_sha256'] == verified['source_binding']
    assert result['contract']['compiled_spec_sha256'] == sha256(SPEC.read_bytes()).hexdigest()
    assert (verified['turns'], verified['criteria'], verified['chats']) == (49, 192, 31)
    complete = [p for p in result['providers'] if p['state'] == 'complete']
    assert len(complete) == 7
    for p in complete:
        assert (p['verified_turns'], p['criteria_graded'], p['verified_chats'], p['same_day_continuations']) == (49,192,31,18)
    unscored = {p['provider']:p for p in result['providers'] if p not in complete}
    assert set(unscored) == {'Honcho','Supermemory'}
    assert unscored['Supermemory']['verified_turns'] == 6
    assert all(p['correct'] is None and p['criteria_graded'] == 0 for p in unscored.values())
    attempts = result['providers'] + result['separate_qualifications_and_incomplete_attempts']
    for p in attempts:
        assert sum(map(Decimal,p.get('settled_usd_by_phase',{}).values())) == Decimal(p['settled_usd'])
    aggregate = result['aggregate']
    assert sum(Decimal(p['settled_usd']) for p in attempts) == Decimal(aggregate['actual_settled_usd'])
    assert sum(Decimal(p['settled_usd']) for p in complete) == Decimal(aggregate['completed_weeks_settled_usd'])
    assert Decimal(aggregate['completed_weeks_settled_usd']) + Decimal(aggregate['qualifications_and_incomplete_settled_usd']) == Decimal(aggregate['actual_settled_usd'])
    assert sum(p['matched_generations'] for p in attempts) == aggregate['matched_generations']
    assert sum(p.get('unmatched_attempt_count',0) for p in attempts) == aggregate['missing_attempts']
    assert sum(p.get('retried_attempts',0) for p in attempts) == aggregate['retry_attempts']
    assert not result['previous_campaign_reference']['included_in_current_campaign']
