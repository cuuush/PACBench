from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace
from uuid import UUID

import pytest

from pacbench.workflow_cognee import CogneeWitness


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
