from copy import deepcopy
from hashlib import sha256

import pytest

from pacbench import honcho_week_state as week
from pacbench.canary_capability import binding
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.honcho_daily_gate import _source_completion
from pacbench.honcho_first_answer_admission import check_prepared, verify_prior
from pacbench.honcho_week import RUN, cohort_name
from pacbench.honcho_week_retry import REJECTION, SOURCE_REJECTION, reserve_retry
from pacbench.live_dispatch import operation_id


def test_cohort_label_keeps_the_protocol_prefix_and_rejects_paths():
    assert cohort_name(None)==RUN
    assert cohort_name('cohort-02')==f'{RUN}-cohort-02'
    for label in ('','../outside','run/other','-bad','X','a'*33):
        with pytest.raises(ValueError):
            cohort_name(label)


def test_week_schedule_preserves_all_turns_and_only_the_eligible_prefix(tmp_path):
    docs,turns,schedule=week.plan()
    assert [len(doc.source_ids) for doc in docs]==[39,23,27,24,9,10,10]
    assert [q['id'] for q in schedule['turns']]==[t.id for t in turns]
    assert (len(turns),schedule['gradable_turns'],schedule['criteria'])==(49,47,192)
    assert len({q['authored_session'] for q in schedule['turns']})==31
    assert schedule['turns'][-1]['ready_files']==[doc.filename for doc in docs]
    root=tmp_path/'honcho'
    (root/'week-turns').mkdir(parents=True)
    scope=dict(root=str(root),plan=[{'at':doc.cutoff} for doc in docs])
    week.release_scope(scope,docs[0].cutoff)
    with pytest.raises(OwnershipConflict,match='after the next'):
        week.release_scope(scope,docs[1].cutoff)
    week.path_for(root,1,'verified').write_text('{}')
    with pytest.raises(OwnershipConflict,match='turn prefix'):
        week.pending(scope)


def test_honcho_continuation_binds_native_prior_history():
    user=dict(role='user',content='Frozen prompt',api_content='Frozen prompt plus old automatic recall',
              tool_calls=None,tool_name=None,tool_call_id=None)
    answer=dict(role='assistant',content='Native final answer',api_content=None,tool_calls=None,
                tool_name=None,tool_call_id=None)
    history=[user,answer]
    wire=[dict(role=row['role'],content=row['content']) for row in history]
    verify_prior(history,deepcopy(history),wire)
    with pytest.raises(OwnershipConflict):
        verify_prior(history,deepcopy(history),[wire[0],{**wire[1],'content':'Changed answer'}])
    with pytest.raises(OwnershipConflict):
        verify_prior(history,[user,{**answer,'content':'Replaced in the native DB'}],wire)


def test_new_question_may_open_once_after_a_settled_predecessor():
    earlier={'workflow_slot':{'question_id':'earlier','honcho_first_answer':{'tool_rounds':[]}}}
    current={'workflow_slot':{'question_id':'current','honcho_first_answer':{'tool_rounds':[]}}}
    check_prepared({'prior':earlier},current)
    with pytest.raises(OwnershipConflict):
        check_prepared({'prior':earlier,'replay':current},current)


@pytest.fixture(params=('source-empty', 'source-partial', 'chat'))
def rejected_representation(request):
    source = request.param.startswith('source-')
    payload = b'' if request.param == 'source-empty' else b'truncated content'
    response = sha256(payload).hexdigest()
    slot={'honcho_daily_release':{'sha256':'daily-release'}} if source else {
        'honcho_chat_work':{'queue_ids':[3,5],'purpose':'deriver.representation'}}
    queue={'queue_id':43} if source else {'queue_ids':[3,5]}
    earlier=dict(id='request-1',state='settled',http_status=200,dispatch_execution='response_ended',
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
