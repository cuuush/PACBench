from copy import deepcopy

from hashlib import sha256

import pytest

from pacbench.providers import honcho_evidence as week

from pacbench.evidence_store import file_binding as binding

from pacbench.workflows.progress import OwnershipConflict

from pacbench.providers.honcho_evidence import _source_completion

from pacbench.providers.honcho_evidence import REJECTION, SOURCE_REJECTION, reserve_retry

from pacbench.workflows.progress import operation_id

def test_week_schedule_preserves_all_turns_and_only_the_eligible_prefix(tmp_path):
    docs,turns,schedule=week.honcho_week_plan()
    assert [len(doc.source_ids) for doc in docs]==[39,23,27,24,9,10,10]
    assert [q['id'] for q in schedule['turns']]==[t.id for t in turns]
    assert (len(turns),schedule['gradable_turns'],schedule['criteria'])==(49,47,192)
    assert len({q['authored_session'] for q in schedule['turns']})==31
    assert schedule['turns'][-1]['ready_files']==[doc.filename for doc in docs]

@pytest.fixture(params=('source-empty', 'source-partial', 'chat'))
def rejected_representation(request):
    source = request.param.startswith('source-')
    payload = b'' if request.param == 'source-empty' else b'truncated content'
    response = sha256(payload).hexdigest()
    slot={'honcho_daily_release':{'sha256':'daily-release'}} if source else {
        'honcho_chat_work':{'queue_ids':[3,5],'purpose':'deriver.representation'}}
    queue={'queue_id':43} if source else {'queue_ids':[3,5]}
    earlier=dict(id='request-1',state='settled',http_status=200,finished='yes',dispatch_execution='response_ended',
        response_body_complete=True,served_model='openai/gpt-6-luna',served_provider='Amazon Bedrock',
        billing_receipt={'proof':True},upstream_request_id='generation-1',request_sha256='request',
        dispatch_owner={'pid':7},response_validation=dict(finish_reasons=['length'],assistant_text_sha256=response,
            assistant_text_characters=len(payload),tool_call_fragments=0),workflow_slot=slot,
        honcho_daily_attempt=dict(claim='owned',trace='native-trace',native_attempt=1,**queue),
        honcho_validation_rejection=dict(reason=REJECTION if payload else SOURCE_REJECTION,
                                        request_sha256='request',response_sha256=response,
                                        upstream_request_id='generation-1',native_attempt=1))
    return earlier

def test_native_retry_needs_unchanged_claim_request_and_verified_failed_output(rejected_representation):
    earlier=rejected_representation
    retry={**deepcopy(earlier),'id':'request-2'}
    retry['honcho_daily_attempt']['native_attempt']=2
    assert reserve_retry({'request-1':earlier},retry)==earlier
    for changed in ({'state':'receipt_missing'},{'honcho_validation_rejection':{}},
                    {'dispatch_execution':'ambiguous'},{'response_body_complete':False},
                    {'request_sha256':'foreign'}, {'billing_receipt':None},
                    {'workflow_capability':{'sha256':'wrong-scope'}},
                    {'response_validation':{**earlier['response_validation'],'finish_reasons':['stop']}},
                    {'response_validation':{**earlier['response_validation'],'tool_call_fragments':1}},
                    {'response_validation':{**earlier['response_validation'],
                                            'assistant_text_sha256':sha256(b'other response').hexdigest()}}):
        with pytest.raises(OwnershipConflict):
            reserve_retry({'request-1':{**earlier,**changed}},retry)
    retry['honcho_daily_attempt']['claim']='new-worker'
    with pytest.raises(OwnershipConflict):
        reserve_retry({'request-1':earlier},retry)
    with pytest.raises(OwnershipConflict):
        reserve_retry({'request-1':earlier},earlier)

def test_native_third_attempt_requires_distinct_linked_receipts_and_is_final(rejected_representation):
    first=rejected_representation
    second={**deepcopy(first),'id':'request-2','retry_of':'request-1','upstream_request_id':'generation-2'}
    second['honcho_daily_attempt']['native_attempt']=2
    second['honcho_validation_rejection'].update(upstream_request_id='generation-2',native_attempt=2)
    third={**deepcopy(first),'id':'request-3'}
    third['honcho_daily_attempt']['native_attempt']=3
    rows={row['id']:row for row in (first,second)}
    assert reserve_retry(rows,third)==second
    for patch in ({'retry_of':'other'},{'upstream_request_id':'generation-1',
                    'honcho_validation_rejection':{**second['honcho_validation_rejection'],
                                                   'upstream_request_id':'generation-1'}}):
        with pytest.raises(OwnershipConflict):
            reserve_retry({**rows,'request-2':{**second,**patch}},third)
    third['honcho_daily_attempt']['native_attempt']=4
    with pytest.raises(OwnershipConflict):
        reserve_retry(rows,third)

@pytest.mark.parametrize('rejected_representation', ('source-empty', 'source-partial'), indirect=True)
def test_daily_retry_requires_matching_text_count_and_rejection_kind(rejected_representation):
    first=rejected_representation
    retry=deepcopy(first)
    retry['honcho_daily_attempt']['native_attempt']=2
    evidence = first['response_validation']
    contradictory = {**evidence, 'assistant_text_characters': int(not evidence['assistant_text_characters'])}
    wrong_reason = SOURCE_REJECTION if evidence['assistant_text_characters'] else REJECTION
    for changed in ({'response_validation':contradictory},
                    {'honcho_validation_rejection':{**first['honcho_validation_rejection'],'reason':wrong_reason}}):
        with pytest.raises(OwnershipConflict):
            reserve_retry({first['id']:{**first,**changed}},retry)
    retry['workflow_slot']['honcho_daily_release'] = {'sha256':'different-daily-release'}
    with pytest.raises(OwnershipConflict):
        reserve_retry({first['id']:first},retry)

@pytest.mark.parametrize('rejected_representation', ('source-empty', 'source-partial'), indirect=True)
def test_daily_ready_binds_the_successful_receipt_at_the_tail_of_the_stock_chain(tmp_path,rejected_representation):
    path=tmp_path/'workflow-scope.json'
    path.write_text('{}')
    scope=dict(root=str(tmp_path),projects={'honcho':{'memory':'test-memory'}},week_schedule=True)
    first=rejected_representation
    release=first['workflow_slot']['honcho_daily_release']
    first.update(workflow_root=str(tmp_path),workflow_capability=binding(path),project='test-memory',finished='yes')
    first['dispatch_context']={'operation_id':operation_id(first)}
    rows={first['id']:first}
    assert _source_completion(scope,rows,release,43) is None

    # Stock accepted a length-finished, nonempty representation on the first release.
    normal=deepcopy(first)
    del normal['honcho_validation_rejection']
    normal['response_validation'].update(assistant_text_sha256=sha256(b'accepted').hexdigest(),
                                         assistant_text_characters=len('accepted'))
    assert _source_completion(scope,{normal['id']:normal},release,43)==normal

    final={**deepcopy(normal),'id':'request-2','retry_of':first['id'],'upstream_request_id':'generation-2'}
    final['honcho_daily_attempt']['native_attempt']=2
    final['response_validation']['finish_reasons']=['stop']
    assert _source_completion(scope,{**rows,final['id']:final},release,43)==final
    assert _source_completion(scope,{**rows,final['id']:{**final,'retry_of':'different'}},release,43) is None
    assert _source_completion(scope,{**rows,final['id']:{**final,'upstream_request_id':'generation-1'}},release,43) is None

def test_cost_incomplete_terminal_source_and_answer_keep_retained_bindings(tmp_path, rejected_representation):
    import json

    from pacbench.evidence_store import binding_digest as digest
    from pacbench.providers.honcho_evidence import _honcho_answer_response, _honcho_ingest_response
    from tests.receipt_fixtures import JournalFixture as RunReceiptJournal

    root = tmp_path/'honcho'
    root.mkdir()
    scope = dict(root=str(root), run_id='run', projects={'honcho': {'memory': 'memory', 'driver': 'driver'}},
                 week_schedule=True)
    scope_path = root/'workflow-scope.json'
    scope_path.write_text(json.dumps(scope))
    scope_proof = binding(scope_path)

    def retained(row, role):
        row = {**row, 'workflow_root': str(root), 'workflow_capability': scope_proof,
               'state': 'receipt_missing', 'error_label': 'actual_cost_unknown',
               'billing_receipt': None, 'project': role,
               'honcho_wire_identity': dict(id=row['upstream_request_id'], model='openai/gpt-6-luna',
                                            provider='Amazon Bedrock', stream=False)}
        row['dispatch_context'] = dict(attempt_id=row['id'], run_id='run', operation_id=operation_id(row))
        return row

    # Preserve the native parser's exact failure proof and verify the stock retry tail.
    rejected_representation['workflow_slot'] = {'honcho_daily_release': {'sha256': 'release'}}
    rejected_representation['honcho_daily_attempt']['queue_id'] = 43
    first = retained(rejected_representation, 'memory')
    final = deepcopy(first)
    final['id'] = 'retry'
    final['upstream_request_id'] = 'gen-final'
    final['honcho_daily_attempt']['native_attempt'] = 2
    final['retry_of'] = first['id']
    final.pop('honcho_validation_rejection')
    final['response_validation'].update(finish_reasons=['stop'], assistant_nonwhitespace_characters=8)
    final = retained(final, 'memory')
    assert reserve_retry({first['id']: first}, final) == first
    release = first['workflow_slot']['honcho_daily_release']
    assert _honcho_ingest_response(root, scope, scope_proof['sha256'], {first['id']: first, final['id']: final},
                                   final, {'release': release}, {'queue_id': 43})
    assert not _honcho_ingest_response(root, scope, scope_proof['sha256'], {first['id']: first, final['id']: final},
                                       final, {'release': release}, {'queue_id': 44})

    with RunReceiptJournal(root/'receipts.jsonl').journal() as (rows, append):
        append('prepared', first['id'], first)
        assert reserve_retry(rows, final) == first
        append('prepared', final['id'], final)
        assert _honcho_ingest_response(root, scope, scope_proof['sha256'], rows, final,
                                       {'release': release}, {'queue_id': 43})
        assert not _honcho_ingest_response(root, scope, scope_proof['sha256'], rows, final,
                                           {'release': release}, {'queue_id': 44})
    mismatched_retry = {**final, 'request_sha256': sha256(b'different request').hexdigest()}
    assert not _honcho_ingest_response(root, scope, scope_proof['sha256'],
                                       {first['id']: first, final['id']: mismatched_retry}, mismatched_retry,
                                       {'release': release}, {'queue_id': 43})

    request = {'model': 'openai/gpt-6-luna', 'messages': [{'role': 'user', 'content': 'Question'}]}
    answered = deepcopy(final)
    answered.update(id='answer', request_sha256=digest(request), workflow_slot={
        'honcho_first_answer': {'native_request_id': 'native', 'tool_rounds': []}})
    answered['response_validation']['assistant_trimmed_text_sha256'] = sha256(b'Answer').hexdigest()
    answered = retained(answered, 'driver')
    answered['honcho_wire_identity']['stream'] = True
    answered['response_validation']['sse_done_observed'] = True
    args = root, scope, scope_proof['sha256']
    assert _honcho_answer_response(*args, answered, request, 'Answer')
    assert not _honcho_answer_response(*args, answered, request, 'Altered answer')
    assert not _honcho_answer_response(*args, answered, {**request, 'model': 'wrong'}, 'Answer')
    historical = {key: value for key, value in answered.items() if key != 'honcho_wire_identity'}
    assert not _honcho_answer_response(*args, historical, request, 'Answer')
    assert _honcho_answer_response(*args, {**historical, 'state': 'settled'}, request, 'Answer')
