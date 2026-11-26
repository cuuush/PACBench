from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace
from uuid import UUID

import pytest

from pacbench.providers.cognee import CogneeWitness


@pytest.mark.parametrize(('indices','accepted'), [([0],True),([1,2],True),([0,1,2],True),([0,2],False)])
def test_verify_exact_contiguous_native_promotion_windows(indices, accepted):
    turns = [
        {'native_session':'hermes_native','prompt':f'Question {i}','answer':f'Answer {i}','qa_id':f'id-{i}'}
        for i in range(3)
    ]
    text = 'Session ID: hermes_native\n\n' + ''.join(
        f"Question: {turns[i]['prompt']}\n\nAnswer: {turns[i]['answer']}\n\n" for i in indices
    )
    digest = sha256(text.encode()).hexdigest()
    witness = SimpleNamespace(turns={'hermes_chat':turns},sources={},explicit_captures={},
        native_documents=lambda: {'promotion':(digest,{'node_set':['user_sessions_from_cache']})})

    if accepted:
        [match] = CogneeWitness.prior_native_captures(witness)
        assert match['data_id']=='promotion'
        assert match['native_session']=='hermes_native' and match['session_id']=='hermes_chat'
        assert match['qa_ids']==[turns[i]['qa_id'] for i in indices]
    else:
        with pytest.raises(ValueError, match='without attributable native capture'):
            CogneeWitness.prior_native_captures(witness)


def test_explicit_cognee_write_requires_matching_native_tool_and_store(tmp_path):
    witness = CogneeWitness.__new__(CogneeWitness)
    witness.root = tmp_path
    witness.dataset = 'pacbench_fixture'
    witness.known_sources = set()
    witness.sources = {}
    witness.turns = {}
    witness.explicit_captures = {}
    text = 'The user corrected a synthetic note.'
    digest = sha256(text.encode()).hexdigest()
    identifier = str(UUID(int=1))
    documents = {identifier:(digest,{})}
    witness.native_documents = lambda: documents
    database = tmp_path/'cognee-system/databases/cognee_db'
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE datasets (id TEXT, name TEXT)')
        db.execute('CREATE TABLE data (id TEXT, dataset_id TEXT)')
        db.execute('INSERT INTO datasets VALUES (?,?)',('native-dataset','wrong'))
        db.execute('INSERT INTO data VALUES (?,?)',(UUID(identifier).hex,'native-dataset'))
    call = {'name':'cognee_remember','input':{'content':' '+text+' '},'tool_call_id':'call-explicit'}
    result = {'success':True,'tool_call_id':'call-explicit',
              'text':json.dumps({'result':'Content stored in Cognee.','status':'running'})}
    surface = {'session_id':'test-chat','tool_calls':[call],'tool_results':[result]}

    result['tool_call_id'] = 'another-call'
    with pytest.raises(ValueError,match='exact native tool receipt'):
        witness.observe_explicit_captures(surface)
    result['tool_call_id'] = 'call-explicit'
    with pytest.raises(ValueError,match='outside the bound native dataset'):
        witness.observe_explicit_captures(surface)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE datasets SET name=?',(witness.dataset,))
    witness.observe_explicit_captures(surface)
    [capture] = witness.prior_native_captures()
    assert capture=={'data_id':identifier,'source_sha256':digest,'native_capture_kind':'explicit_remember',
                    'session_id':'test-chat','tool_call_id':'call-explicit'}
    documents[identifier] = ('another-sha',{})
    with pytest.raises(ValueError,match='without attributable native capture'):
        witness.prior_native_captures()


def test_checkpoint_reads_each_original_once_and_rechecks_seal_next_observation(monkeypatch):
    from unittest.mock import Mock
    from provider_adapters import cognee_checkpoint as checkpoint

    prompt = 'test prompt'
    bodies = {'source':prompt, **{f'old-{i}':'old question' for i in range(99)},
              'stale':f'Session ID: native\n\nQuestion: {prompt}\n\nAnswer: old\n\n',
              'foreign':f'Session ID: other\n\nQuestion: {prompt}\n\nAnswer: saved\n\n',
              'substring':'Source containing '+prompt,
              'promotion':f'Session ID: native\n\nQuestion: {prompt}\n\nAnswer: saved\n\n'}
    backend = Mock()
    backend.list_dataset_data.side_effect = lambda offset,limit,**_: [{'id':key} for key in list(bodies)[offset:offset+limit]]
    backend.read_raw_data.side_effect = lambda data_id, **_: bodies[data_id]
    monkeypatch.setattr(checkpoint, 'native', lambda *_:(backend, 'dataset-id'))
    monkeypatch.setattr(checkpoint, 'pipeline_rows', lambda _:[])
    session = {'name':'cognee','endpoint':'local','dataset':'ours','native_session':'native','close_requested_improve':False}
    drain = {'sessions':[{'session_id':'sid','providers':[session],
                          'sync_dispatches':[{'prompt_sha256':sha256(prompt.encode()).hexdigest()}]}]}
    observation = dict(endpoint='local', dataset='ours', native_root='.', prior_pipeline_ids=[],
                       source_documents={'source':sha256(prompt.encode()).hexdigest()})
    turn = SimpleNamespace(prompt=prompt, answer='saved', session_id='sid')
    result = checkpoint.verify_turn(observation, turn, drain)
    assert result == {'native_promoted_documents':['promotion'], 'source_readback':'unchanged'}
    assert [call.kwargs['data_id'] for call in backend.read_raw_data.call_args_list] == list(bodies)
    del bodies['promotion']
    backend.get_session.return_value = {'qas':[{'qa_id':'earlier','question':prompt,'answer':'old'}]}
    with pytest.raises(checkpoint.GatewayLifecycleError, match='not readable'):
        checkpoint.verify_turn(observation, turn, drain)
    bodies['source'] += 'changed after close'
    with pytest.raises(checkpoint.GatewayLifecycleError, match='sealed source'):
        checkpoint.verify_turn(observation, turn, drain)
    session['dataset'] = 'foreign'
    with pytest.raises(checkpoint.GatewayLifecycleError, match='endpoint or dataset'):
        checkpoint.verify_turn(observation, turn, drain)
    assert backend.close.call_count == 3


@pytest.mark.parametrize(('outcome','ready_at','expected_sleeps'), [
    ('succeeded', .35, [.2, .2]), ('failed', .35, [.2, .2]),
    ('succeeded', 2.35, [.2] * 5 + [1, 1]),
])
def test_final_improve_poll_waits_for_current_terminal_row(monkeypatch, outcome, ready_at, expected_sleeps):
    from unittest.mock import Mock
    from pacbench.providers import cognee as workflow

    doc = SimpleNamespace(cutoff='tonight', filename='file.txt', text='released')
    witness = workflow.CogneeWitness(None, None, None, 'local', 'ours', None, [doc], [], None)
    witness.cutoff = doc.cutoff
    witness.prior_native_captures = lambda: []
    old = dict(id='old', pipeline_run_id='old-run', operation_name='improve', ended_at='then', outcome='succeeded')
    new = dict(id='new', pipeline_run_id='new-run', operation_name='improve', ended_at=None, outcome=None)
    end = {**new, 'id':'done','ended_at':'now','outcome':outcome}
    now = [0.]
    def rows(_):
        if now[0] == 0:
            return [old]
        return [old, new] if now[0] < ready_at else [old, new, end]

    def advance(seconds):
        now[0] += seconds

    qualify = Mock(return_value={'data_id':'doc-id'})
    sleeps = Mock(side_effect=advance)
    monkeypatch.setattr(workflow, 'pipeline_rows', rows)
    monkeypatch.setattr(workflow, 'qualify_incremental_document', qualify)
    monkeypatch.setattr(workflow, 'time', SimpleNamespace(monotonic=lambda:now[0], sleep=sleeps))
    if outcome == 'succeeded':
        assert witness.submit(doc)['request']['data_id'] == 'doc-id'
        assert witness.pending[doc.filename]['native_operation_completion'] == [new, end]
    else:
        with pytest.raises(workflow.QualificationError, match='failed'):
            witness.submit(doc)
        assert not witness.pending
    assert [c.args[0] for c in sleeps.call_args_list] == expected_sleeps
    qualify.assert_called_once()
