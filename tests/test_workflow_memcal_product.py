"""Offline compatibility and audit of the verified Week product and Hermes manifest."""
import json
from pathlib import Path

import pytest

from pacbench.canary_capability import binding, digest
from pacbench.provider_versions import MEMCAL_WEEK_CONDITION, MEMCAL_WEEK_REVISION, MEMCAL_WEEK_TREE, memcal_week_pin
from pacbench.workflow_memcal_tools import manifest, TOOLS, verify_driver_schemas
from pacbench.workflow_tracks import memcal_week_tools


def stock_tools():
    return json.loads((Path(__file__).parent/'fixtures/memcal-hermes-0.8.12.json').read_bytes())


def test_product_selection_and_stock_manifest(monkeypatch):
    from pacbench.workflow_tracks import eligibility
    monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION', MEMCAL_WEEK_CONDITION)
    assert memcal_week_pin() == (MEMCAL_WEEK_CONDITION, MEMCAL_WEEK_REVISION, '0.8.12')
    assert eligibility('memcal', 'first-party-connector', version='0.8.12')['version_source'].endswith(MEMCAL_WEEK_REVISION)
    tools = stock_tools()
    assert tuple(t['function']['name'] for t in tools) == memcal_week_tools(MEMCAL_WEEK_REVISION) == TOOLS
    assert len(tools) == 20 and 'memcal_open_page' not in TOOLS
    verify_driver_schemas(list(reversed(tools)))
    tools[0]['function']['parameters']['properties'] = {}
    with pytest.raises(ValueError, match='driver schemas'):
        verify_driver_schemas(tools)
    monkeypatch.setenv('PACBENCH_MEMCAL_CONDITION', 'wiki-supersession-review-da58057-v1')
    with pytest.raises(ValueError, match='qualified product'):
        memcal_week_pin()


@pytest.mark.parametrize('drift', [None, 'schema', 'receipt', 'tree', 'historical'])
def test_private_export_rechecks_bound_product_and_driver(tmp_path, drift):
    from pacbench.workflow_answer_export import export
    from pacbench.workflow_grading import grade_verified
    from pacbench.workflow_stream import fingerprint
    from tests.test_workflow_grading import checkpoints, Recorder, local_judge

    directory = tmp_path/'workflow'
    scope = tmp_path/'workflow-scope.json'
    access = manifest()
    if drift == 'tree':
        access['memcal_tree'] = '0'*40
    account = dict(account='fixture', store=str(tmp_path/'memcal-home'))
    scope.write_text(json.dumps(dict(provider='memcal', root=str(tmp_path), access=access, **account)))
    with checkpoints(directory, verified=0) as (progress, turns):
        turn = turns[0]
        tools = stock_tools()
        if drift == 'schema':
            tools[0]['function']['description'] += ' changed'
        messages = [dict(role='user', content=turn.prompt)]
        body = dict(model='openai/gpt-6-luna', messages=messages, tools=tools)
        wire = dict(request_id='scripted-driver', request=body, messages=messages, model=body['model'],
                    tools=list(TOOLS), canonical_request_sha256='wrong' if drift == 'receipt' else digest(body))
        surface = dict(session_id=turn.session, gateway_identity='fixture', model_requests=[wire],
                       question_at=turn.at, conversation_times=[turn.at], tool_calls=[], tool_results=[],
                       history=[*messages, dict(role='assistant', content='fixture')], answer='fixture')
        progress.verified_turn(turn, surface, {'driver_receipts':{'coverage':'incomplete'}})
        progress.mark_turn_done(turn.id)
        grade_verified(progress, local_judge(directory, []), directory, Recorder(), limit=1)
        identity = dict(product_manifest=manifest(), condition=dict(provider='memcal', version='0.8.12'), **account)
        if drift == 'historical':
            # A previously recorded 0.8.12 label alone cannot attest the new tree.
            del identity['product_manifest']
        progress.bind('identity', identity)
        progress.bind('workflow_scope', binding(scope))
        key = fingerprint(identity)
        progress.db.execute('UPDATE run SET run_key=?', (key,))
        (directory/'run.json').write_text(json.dumps(dict(identity=identity, run_key=key)))
    dest = tmp_path/'export'
    if drift == 'historical':
        assert export(directory, dest)['audited_judgments'] == 1
        assert 'product_manifest' not in json.loads((dest/'answers.private.jsonl').read_text())
    elif drift:
        with pytest.raises(ValueError, match='manifest|driver'):
            export(directory, dest)
        assert not dest.exists()
    else:
        summary = export(directory, dest)
        row = json.loads((dest/'answers.private.jsonl').read_text())
        assert row['product_manifest'] == manifest() and row['product_manifest']['memcal_tree'] == MEMCAL_WEEK_TREE
        assert row['driver_inputs'][0]['canonical_request_sha256'] == digest(body)
        assert row['judge_attempts'][0]['judge_protocol'] == 'semantic-judge-context-v4'
        assert summary['verified_answers'] == summary['audited_judgments'] == 1
        assert all(type(value) is int for value in summary.values())
