from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from pacbench.providers.memos import MemosWitness, native_transport_drained
from pacbench.providers import MEMOS_OSS_VERSION, eligibility


@pytest.mark.parametrize('tool', ['memos_get', 'memos_search'])
def test_source_lookup_uses_native_returned_payload(tool):
    value = 'Native file chunk'
    evidence = {'readback': {'native_chunk_sha256': [sha256(value.encode()).hexdigest()]}}
    observer = SimpleNamespace(client=SimpleNamespace(user='owner'), observed={'released.txt':evidence})
    row = {'metadata': {'user_id':'owner', 'sources':[
        {'type':'file','doc_path':'released.txt','content':value}]}}
    payload = {'memory': row} if tool == 'memos_get' else {'memories':[row]}
    invoke = lambda: MemosWitness.opened_file_source(observer, tool, {'text':json.dumps(payload)})
    assert invoke()
    row['metadata']['sources'][0]['content'] = 'Generated or changed text'
    assert not invoke()
    row['metadata']['sources'][0].update(content=value, type='chat')
    assert not invoke()
    row['metadata']['sources'][0]['type'] = 'file'
    row['metadata']['user_id'] = 'another cube'
    assert not invoke()


def test_alternate_memos_deployment_does_not_replace_npm_condition():
    assert not eligibility('memos','nightly-document')['documented_for_scope']
    oss = eligibility('memos','nightly-document',version=MEMOS_OSS_VERSION)
    assert oss['documented_for_scope'] and oss['reviewed_version']==MEMOS_OSS_VERSION
    assert oss['routes'][0]['method']=='POST /product/add with a file item'


@pytest.mark.parametrize('http_status', [None, 404, 503])
def test_unsuccessful_memos_transport_cannot_qualify_raw_fallback(http_status):
    retired={'id':'original','accounting_role':'memory_provider','finished':True,
             'state':'receipt_missing','http_status':http_status}
    successful={**retired,'id':'different-operation','state':'settled','http_status':200,
                'response_validation':{'body_complete':True,'assistant_nonwhitespace_characters':24}}
    with pytest.raises(RuntimeError,match='without usable model output'):
        native_transport_drained([retired,successful])
    assert not native_transport_drained([retired,{**successful,'finished':None,'retry_of':'original'}])
    assert native_transport_drained([retired,{**successful,'retry_of':'original'}])


def test_usable_native_response_can_advance_with_unknown_cost():
    native={'id':'received','accounting_role':'memory_provider','finished':True,
            'state':'receipt_missing','http_status':200,
            'response_validation':{'body_complete':True,'assistant_nonwhitespace_characters':24}}
    assert native_transport_drained([native])
    native['response_validation']['assistant_nonwhitespace_characters']=0
    with pytest.raises(RuntimeError,match='without usable model output'):
        native_transport_drained([native])


@pytest.mark.parametrize('http_status', [None, 503])
def test_terminal_native_failure_blocks_next_nightly_release(tmp_path,http_status):
    endpoint = SimpleNamespace(scheduler=lambda: {'failed':0,'cancelled':0,'waiting':0,'pending':0,'in_progress':0})
    owner = SimpleNamespace(home=tmp_path,witness={'pid':42,'start_time':7},
                            pins={'reuse_observer_sha256':'fixture'})
    (tmp_path/'reuse-observer.json').write_text(json.dumps({
        'pid':42,'start_time':7,'observer_sha256':'fixture','observed_monotonic_ns':2**63,
        'native_failures':[],'sessions':[],
    }))
    ledger = SimpleNamespace(snapshot=lambda: [
        {'id':'native-failed','accounting_role':'memory_provider','finished':True,
         'state':'receipt_missing','http_status':http_status},
    ])
    cutoff='2026-10-05T21:00:00-04:00'
    witness=MemosWitness(tmp_path,owner,endpoint,None,None,None,[SimpleNamespace(cutoff=cutoff)],ledger)
    with pytest.raises(RuntimeError,match='without usable model output'):
        witness.collection_clock(cutoff)
    assert witness.cutoff is None


def test_stock_raw_capture_requires_exact_accepted_pair_and_bound_request(tmp_path):
    gateway=tmp_path/'workflow'
    gateway.mkdir()
    (gateway/'workflow.jsonl').write_text(json.dumps({
       'phase':'dispatch','question':'rsvp-before','prompt':'Exact user input'}))
    trace=tmp_path/'memory-api-inputs'
    trace.mkdir()
    native='native-attempt'
    (trace/(native+'.json')).write_text(json.dumps({
       'request_id':native,'project':'test-memory',
       'request':{'messages':[{'content':'user: Exact user input\nassistant: Exact assistant output'}]},
    }))
    receipt={'id':native,'project':'test-memory','accounting_role':'memory_provider',
             'http_status':200,'workflow_slot':{'kind':'answer','question_id':'rsvp-before'}}
    witness=MemosWitness(tmp_path,None,None,None,None,None,[],SimpleNamespace(snapshot=lambda:[receipt]))
    accepted={'memory_ids':['working-id'],'memory_types':['LongTermMemory'],
              'exact_user_in_ack':True,'exact_assistant_in_ack':True}
    assert witness.working_memory_processed(accepted,'Exact user input','Exact assistant output')==[native]
    assert not witness.working_memory_processed(accepted,'Exact user input','altered assistant output')
    assert not witness.working_memory_processed({**accepted,'exact_assistant_in_ack':False},'Exact user input','Exact assistant output')
    receipt['workflow_slot']['question_id']='wrong-turn'
    assert not witness.working_memory_processed(accepted,'Exact user input','Exact assistant output')


@pytest.mark.parametrize('materialized', [False, True])
def test_answer_uses_only_successful_post_drain_graph_and_next_dispatch_reads_again(tmp_path, monkeypatch, materialized):
    from unittest.mock import Mock
    from pacbench.providers.memos import MemosAccount

    prompt, answer = 'question', 'response'
    receipt = dict(session_id='chat', at='now', cube_id='user', memory_ids=['native'],
                   question_sha256=sha256(prompt.encode()).hexdigest(),
                   answer_sha256=sha256(answer.encode()).hexdigest())
    retained = tmp_path/'memos-native-captures'
    retained.mkdir()
    (retained/'nonce.json').write_text(json.dumps(receipt))
    source = {'id':'file-id','metadata':{'sources':[{'type':'file','doc_path':'released'}]}}
    chat = {'id':'chat-id','metadata':{'session_id':'chat','user_id':'user','sources':[
        {'role':'user','content':prompt},{'role':'assistant','content':answer}]}}
    successful_graph = [source,chat] if materialized else [source]
    # The first post-drain observation cannot prove capture. Only the next can.
    client = SimpleNamespace(user='user', inventory=Mock(side_effect=[[source], [], successful_graph, []]))
    ledger = SimpleNamespace(snapshot=lambda: [])
    witness = MemosWitness(tmp_path, None, client, None, None, None, [], ledger)
    witness.observed = {'released':{}}
    witness.idle = Mock(return_value={'readback':{'native_scheduler':{}}})
    witness.working_memory_processed = Mock(side_effect=[[], ['attempt']])
    monkeypatch.setattr('pacbench.providers.memos.time.sleep', lambda _:None)
    account = MemosAccount('fixture', 'memos', 'scope', None, None, witness)
    assert account.source_inventory() == ['released']
    trace = dict(session_id='chat', answer=answer, question_at='now', history=[{'role':'user','content':prompt}],
                 model_requests=[{'request_id':'r','turn_nonce':'nonce'}], tool_calls=[], tool_results=[], tool_provenance=[])
    verified = account.observe_answer(trace)
    assert verified['source_files'] == ['released'] and client.inventory.call_count == 3
    proof = verified['write_barrier']['readback']
    assert proof['active_exact_qa_provenance'] is materialized
    assert proof['native_extraction_attempts'] == ([] if materialized else ['attempt'])
    assert witness.idle.call_count == 2
    with pytest.raises(ValueError, match='missing or unreleased'):
        account.source_inventory()
    assert client.inventory.call_count == 4


@pytest.mark.parametrize('files', [[], ['released', 'future']])
def test_successful_capture_cannot_hide_wrong_post_answer_files(files):
    witness = MemosWitness(None, None, None, None, None, None, [], None)
    witness.observed = {'released':{}}
    rows = [{'id':f,'metadata':{'sources':[{'type':'file','doc_path':f}]}} for f in files]
    with pytest.raises(ValueError, match='missing or unreleased'):
        witness._verified_source_files(rows)
