"""Offline provenance checks; no default origin, model name or ID shape proves a route."""
from pacbench.phase_costs import report
from pacbench.receipt_costs import request_record


def charge(**extra):
    return {'id': 'request', 'project': 'pacbench-judge', 'state': 'settled', 'accounted': '.12',
            'upstream_request_id': 'gen-known', 'actual_cost_source': 'openrouter.usage.cost',
            'actual_cost_usd_decimal': '.12', **extra}


def test_default_legacy_label_never_proves_route_or_price_estimate():
    r = request_record({'id': 'legacy', 'state': 'settled', 'accounted': '.5',
                        'upstream_origin': 'direct_meta', 'model': 'muse-spark-1.3-contributor'})
    assert r['route_status'] == 'recorded-only'
    assert r['billing_provider'] is None and r['upstream_provider'] is None
    assert r['token_price_estimate_usd'] is None and r['actual_usd'] is None


def test_missing_route_does_not_borrow_openrouter_from_matching_generation_id():
    row = {'id': 'missing', 'state': 'uncertain', 'upstream_request_id': 'gen-known',
           'generation_receipt': {'id': 'gen-known', 'total_cost': '.12', 'provider_name': 'DeepInfra'}}
    r = request_record(row)
    assert r['route_status'] == 'recorded-only'
    assert r['actual_usd'] is None and r['billing_provider'] is None
    assert r['upstream_provider'] is None
    assert request_record({'id': 'missing'})['route_status'] == 'missing'


def test_explicit_cost_survives_conflicting_legacy_origin_without_route_assignment():
    result = report([charge(upstream_origin='direct_meta', served_provider='DeepInfra')], [])
    r = result['requests'][0]
    assert r['route_status'] == 'conflicting' and r['billing_provider'] is None
    assert r['receipt_billing_provider'] == 'OpenRouter'
    assert r['actual_usd'] == '0.12' and r['upstream_provider'] is None
    assert result['shared_judging']['actual_reported_usd'] == '0.12'
    assert result['billing_dimensions'][0]['billing_provider'] is None


def test_recorded_only_origin_and_provider_do_not_prove_execution_route():
    r = request_record({'id': 'recorded', 'upstream_origin': 'openrouter', 'served_provider': 'DeepInfra'})
    assert r['route_status'] == 'recorded-only'
    assert r['billing_provider'] is None and r['upstream_provider'] is None


def test_conflicting_response_and_generation_providers_preserve_known_cost():
    r = request_record(charge(served_provider='DeepInfra', generation_receipt_source='openrouter.generation.total_cost',
        generation_receipt={'id': 'gen-known', 'total_cost': '.12', 'provider_name': 'Amazon Bedrock'}))
    assert r['route_status'] == 'conflicting'
    assert r['actual_usd'] == '0.12' and r['upstream_provider'] is None
    assert len(r['cost_receipts']) == 2


def test_same_id_across_known_execution_routes_never_lends_wrong_upstream_or_double_counts():
    a = charge(transport='direct-client-reservations-v1', served_provider='DeepInfra', endpoint='deepinfra/fp8/flex')
    b = charge(id='other', project='other', transport='direct-client-reservations-v1',
               served_provider='Amazon Bedrock', endpoint='amazon-bedrock/global')
    result = report([a, b], [])
    assert result['all_in_scope']['actual_reported_usd'] is None
    assert result['duplicate_generation_ids'] == {'gen-known': ['other', 'request']}
    assert all(r['route_status'] == 'conflicting' and r['upstream_provider'] is None for r in result['requests'])
    assert all(r['cost_receipts'][0]['usd'] == '0.12' for r in result['requests'])
    assert all(b['billing_provider'] is None for b in result['billing_dimensions'])


def test_direct_route_cannot_lend_provenance_to_unscoped_generation_lookup():
    r = request_record({'id': 'request', 'transport': 'direct-client-reservations-v1',
                        'upstream_request_id': 'gen-known',
                        'generation_receipt': {'id': 'gen-known', 'total_cost': '.12', 'provider_name': 'Unrelated'}})
    assert r['billing_provider'] == 'OpenRouter'
    assert r['actual_usd'] is None and r['upstream_provider'] is None


def test_reviewed_receipt_projection_keeps_historical_costs_and_routes():
    import json
    from tests.receipt_fixtures import EVIDENCE, reviewed_receipts
    artifact = EVIDENCE / 'retained-cost-report.json'
    original = artifact.read_bytes()
    result = report(reviewed_receipts(), [])
    assert result['all_in_scope']['actual_reported_usd'] == json.loads(original)['all_in_scope']['actual_reported_usd']
    assert result['all_in_scope']['actual_reported_usd'] == '0.018890392192'
    assert all(r['route_status'] == 'proven' and r['billing_provider'] == 'OpenRouter' for r in result['requests'])
    assert all(r['upstream_provider'] == 'DeepInfra' for r in result['requests'])
    assert result['other_setup_or_unmapped']['requests'] == 72
    assert artifact.read_bytes() == original


def test_generation_identity_contradictions_withhold_named_upstream():
    r = request_record(charge(served_provider='DeepInfra', response_diagnostic_headers={'x-generation-id': 'gen-other'}))
    assert r['route_status'] == 'conflicting'
    assert r['billing_provider'] is None and r['upstream_provider'] is None


def test_cli_generation_index_requires_supported_source_and_observed_receipt(tmp_path, monkeypatch):
    import json
    import pytest
    from pacbench.phase_costs import main
    component = tmp_path / 'component'
    component.mkdir()
    (component / 'summary.json').write_text('{"state":"interrupted"}')
    snapshot = tmp_path / 'snapshot.json'
    row = {'id': 'request', 'project': 'pacbench-judge', 'state': 'uncertain',
           'upstream_request_id': 'gen-known', 'transport': 'direct-client-reservations-v1'}
    snapshot.write_text(json.dumps({'calls': [row]}))
    index = tmp_path / 'generation.json'
    output = tmp_path / 'report.json'
    monkeypatch.setattr('sys.argv', ['phase_costs', '--snapshots', str(snapshot), '--components', str(component),
                                    '--generation-index', str(index), '--output', str(output)])
    item = {'state': 'observed', 'generation': {'id': 'gen-known', 'total_cost': '.12', 'provider_name': 'DeepInfra'}}
    index.write_text(json.dumps({'version': 'unknown', 'items': {'request': item}}))
    with pytest.raises(SystemExit):
        main()
    assert not output.exists()
    item['state'] = 'lookup-failed'
    index.write_text(json.dumps({'version': 'generation-observations-v1', 'items': {'request': item}}))
    main()
    assert json.loads(output.read_text())['all_in_scope']['actual_reported_usd'] is None
    output.unlink()  # Only the synthetic report just produced by this test.
    item['state'] = 'observed'
    index.write_text(json.dumps({'version': 'generation-observations-v1', 'items': {'request': item}}))
    main()
    assert json.loads(output.read_text())['all_in_scope']['actual_reported_usd'] == '0.12'
