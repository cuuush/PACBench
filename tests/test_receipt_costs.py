"""Synthetic accounting evidence plus portable, retained public receipts; no network."""
import json
from decimal import Decimal
from pathlib import Path

from pacbench.usage import phase_cost_report as report
from pacbench.receipt_costs import request_record, summarize


def row(identifier='r', cost=None, **extra):
    return dict(id=identifier, project='pacbench-judge', state='settled', accounted='.5',
                upstream_request_id='gen-' + identifier,
                actual_cost_source='openrouter.usage.cost', actual_cost_usd_decimal=cost, **extra)


def test_estimate_never_actual_unknown_never_zero():
    result = summarize([dict(id='estimate', state='settled', accounted='.5', upstream_origin='direct_meta', token_price_estimate_usd='.5')])
    assert result['actual_reported_usd'] is None
    assert result['token_price_estimate_usd'] == '0.5'
    assert result['actual_total_usd'] is None
    assert summarize([])['actual_reported_usd'] is None
    assert summarize([row(cost='0')])['actual_total_usd'] == '0'


def test_decimal_precision_coverage_and_display():
    result = summarize([row('a', '0.123456789123456789'), row('b', '0.000000000000000001'), row('c')])
    assert result['actual_reported_usd'] == '0.123456789123456790'
    assert result['actual_display_usd'] == '0.12'
    assert result['actual_unknown_requests'] == 1
    assert result['actual_total_usd'] is None


def test_receipt_disagreement_and_identity_conflict_remain_visible():
    a = row(cost='.02', generation_receipt_source='openrouter.generation.total_cost', generation_receipt={'id': 'gen-r', 'total_cost': '.03'})
    result = report([a], [])
    assert len(result['requests'][0]['cost_receipts']) == 2
    assert result['all_in_scope']['token_price_estimate_usd'] is None
    assert result['all_in_scope']['receipt_conflicts'] == 1
    assert result['all_in_scope']['actual_reported_usd'] is None
    a['response_diagnostic_headers'] = {'x-generation-id': 'gen-different'}
    assert request_record(a)['receipt_conflict']


def test_delayed_generation_receipt_uses_identity_not_time_or_state():
    a = row(cost=None, generation_receipt_source='openrouter.generation.total_cost', generation_receipt={'id': 'gen-r', 'total_cost': '.04', 'native_tokens_cached': 0})
    assert request_record(a)['actual_usd'] == '0.04'
    a['generation_receipt']['id'] = 'gen-unrelated'
    assert request_record(a)['actual_usd'] is None


def test_generation_aliases_cannot_double_count_across_projects():
    a, b = row('a', '.02'), row('b', '.02')
    b['upstream_request_id'] = a['upstream_request_id']
    b['project'] = 'other'
    result = report([a, b], [])
    assert result['duplicate_generation_ids'] == {'gen-a': ['a', 'b']}
    assert result['shared_judging']['actual_reported_usd'] is None
    assert result['all_in_scope']['actual_reported_usd'] is None


def test_timeout_without_receipt_and_partial_tokens():
    a = row()
    a.update(state='uncertain', finished=None, http_status=None, input_tokens=12,
             usage_detail={'completion_tokens_details.reasoning_tokens': 3})
    result = summarize([a])
    assert result['unresolved_bound_usd'] == '0.5'
    assert result['actual_unknown_requests'] == 1
    assert result['observed_output_tokens'] is None
    assert result['observed_reasoning_tokens'] == 3
    assert result['requests_missing_cached_tokens'] == 1


def test_retained_fixture_report_is_read_only_and_portable():
    from tests.receipt_fixtures import EVIDENCE, reviewed_receipts
    artifact = EVIDENCE / 'retained-cost-report.json'
    before = artifact.read_bytes()
    calls = [r for r in reviewed_receipts() if '-mem0-' in r['project']]
    result = report(calls, [])
    assert result['all_in_scope']['requests'] == 4
    assert result['all_in_scope']['actual_receipt_requests'] == 4
    assert result['all_in_scope']['actual_reported_usd'] == '0.001558406976'
    # This projection preserves receipts, not the missing controller's arm binding.
    assert result['other_setup_or_unmapped']['requests'] == 4
    assert result['shared_judging']['requests'] == 0
    assert artifact.read_bytes() == before
    assert all(r['billing_provider'] == 'OpenRouter' and r['upstream_provider'] == 'DeepInfra' for r in result['requests'])


def test_causal_phase_requires_exact_run_and_arm_binding(tmp_path):
    import hashlib
    root = tmp_path / 'component'
    arm = root / 'repeat-1/mem0'
    arm.mkdir(parents=True)
    (root / 'summary.json').write_text(json.dumps({'state': 'interrupted', 'run_id': 'run-1'}))
    key = hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]
    a = row(cost='.02', dispatch_context={'run_id': 'run-1', 'arm_id': 'mem0', 'phase': 'embeddings'})
    a['project'] = f'pacbench-{key}-mem0-memory'
    result = report([a], [root])
    assert result['attempts'][0]['phases']['embeddings']['requests'] == 1
    a['dispatch_context']['run_id'] = 'different-run'
    assert report([a], [root])['requests'][0]['phase'] == 'provider_background_or_ambiguous'


def test_mismatched_generation_never_lends_tokens_or_provider():
    a = row(generation_receipt={'id': 'gen-different', 'total_cost': 0, 'native_tokens_prompt': 100,
                                'provider_name': 'Unrelated'})
    r = request_record(a)
    assert r['generation_lookup_identity_mismatch']
    assert r['input_tokens'] is None and r['upstream_provider'] is None


def test_output_context_does_not_export_arbitrary_payloads():
    r = request_record(row(dispatch_context={'prompt': 'PRIVATE', 'worker_id': 'worker', 'phase': 'ingest'}))
    assert 'PRIVATE' not in json.dumps(r)


def test_cli_serializes_half_credit_and_never_overwrites_evidence(tmp_path, monkeypatch):
    from pacbench.usage import phase_cost_main as main
    component = tmp_path / 'component'
    arm = component / 'repeat-1/mem0'
    grades = arm / 'turns/q/followups'
    grades.mkdir(parents=True)
    (grades / 'recovery.json').write_text(json.dumps({'score': {'initial_correct': 0, 'max_points': 1, 'points': .5}}))
    (component / 'summary.json').write_text(json.dumps({'state': 'interrupted'}))
    source = arm / 'model-usage.json'
    source.write_text(json.dumps({'calls': [row(cost='.02')]}))
    original = source.read_bytes()
    output = tmp_path / 'report.json'
    args = ['phase_costs', '--snapshots', str(source), '--components', str(component), '--output', str(output)]
    monkeypatch.setattr('sys.argv', args)
    main()
    saved = json.loads(output.read_text())
    assert saved['attempts'][0]['score']['retention_points'] == '0.5'
    assert saved['coverage']['lifecycle_coverage_complete'] is False
    import pytest
    with pytest.raises(SystemExit):
        main()
    assert source.read_bytes() == original
