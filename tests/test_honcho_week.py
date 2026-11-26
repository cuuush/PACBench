from copy import deepcopy
import fcntl
from hashlib import sha256
from pathlib import Path

import pytest

from pacbench.providers.honcho import daily_gate as gate
from pacbench.providers.honcho import week_state as week
from pacbench.canary_capability import binding, read
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.providers.honcho.daily_gate import _source_completion
from pacbench.providers.honcho.first_answer_admission import check_prepared, verify_prior
from pacbench.providers.honcho.week import RUN, cohort_name
from pacbench.providers.honcho.week_retry import REJECTION, SOURCE_REJECTION, reserve_retry
from pacbench.live_dispatch import operation_id


def test_cohort_label_keeps_the_protocol_prefix_and_rejects_paths():
    assert cohort_name(None)==RUN
    assert cohort_name('cohort-02')==f'{RUN}-cohort-02'
    for label in ('','../outside','run/other','-bad','X','a'*33):
        with pytest.raises(ValueError):
            cohort_name(label)


def test_launcher_defaults_to_authored_chats_with_explicit_legacy_layout(tmp_path, monkeypatch):
    from pacbench.providers.honcho import week as honcho_week

    def launch(*paths, full_week, cohort_suffix, week_session_layout, week_first_four):
        assert full_week and paths == (tmp_path,)*3
        run_name = cohort_name(cohort_suffix, layout=week_session_layout, first_four=week_first_four)
        _, _, schedule = week.plan(week_session_layout, first_four=week_first_four)
        launches.append((run_name, schedule['protocol']))
        return {}

    launches = []
    monkeypatch.setattr(honcho_week, 'run', launch)
    monkeypatch.setattr(honcho_week.os, 'umask', lambda mode: None)
    argv = [str(tmp_path)]*3
    honcho_week.main(argv)
    honcho_week.main([*argv, '--session-layout', 'single-chat', '--cohort-suffix', 'repeat'])
    honcho_week.main([*argv, '--authored-chat-canary'])
    assert launches == [
        (cohort_name(None, layout=week.AUTHORED_CHATS), week.plan(week.AUTHORED_CHATS)[2]['protocol']),
        (f'{RUN}-repeat', week.PROTOCOL),
        (week.CANARY_PROTOCOL, week.CANARY_PROTOCOL),
    ]


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


def test_authored_layout_keeps_the_same_schedule_and_uses_only_the_chat_predecessor(tmp_path, monkeypatch):
    from pacbench.providers.honcho import week_state as harness

    _, turns, old = harness.plan()
    _, authored_turns, new = harness.plan(harness.AUTHORED_CHATS)
    assert new['turns'] == old['turns']
    assert turns == authored_turns and new['protocol'] != old['protocol']
    assert cohort_name('trial', layout=harness.AUTHORED_CHATS) != cohort_name('trial')
    assert harness.previous_turns({'week_session_layout': harness.AUTHORED_CHATS}, 3) == [(0, turns[0])]
    root = tmp_path / 'honcho'
    (root / 'week-turns').mkdir(parents=True)
    scope = {'root': str(root), 'week_session_layout': harness.AUTHORED_CHATS, 'controller': {'pid': 7}}
    monkeypatch.setattr(harness, 'process_identity', lambda: scope['controller'])
    physical, starts, continuations = {}, 0, 0
    for i, turn in enumerate(turns):
        prior = harness.prior_surface(scope, i)
        if turn.session in physical:
            continuations += 1
            sid = physical[turn.session]
            assert prior['session_id'] == sid
            with pytest.raises(OwnershipConflict, match='substituted'):
                harness.validate_session(scope, i, 'different-native-session')
        else:
            starts += 1
            assert prior is None
            for foreign in physical.values():
                with pytest.raises(OwnershipConflict, match='reused'):
                    harness.validate_session(scope, i, foreign)
                break
            sid = physical[turn.session] = 'chat-' + str(i)
        path = harness.path_for(root, i, 'intent')
        path.write_text('{}')
        harness.publish(scope, i, {'session_id': sid, 'turn': turn.id})
        with pytest.raises(OwnershipConflict, match='active'):
            harness.publish(scope, i, {'session_id': sid, 'turn': turn.id})
    assert (starts, continuations, harness.position(scope)) == (31, 18, 49)


def test_authored_chat_canary_owns_only_day_one_and_exactly_four_turns(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='authored chat layout'):
        week.plan(first_four=True)
    with pytest.raises(ValueError, match='fixed run directory'):
        cohort_name('another', layout=week.AUTHORED_CHATS, first_four=True)
    assert cohort_name(None, layout=week.AUTHORED_CHATS, first_four=True) == week.CANARY_PROTOCOL

    root = tmp_path / week.CANARY_PROTOCOL / 'honcho'
    root.mkdir(parents=True)
    manifest = gate.materialize_canonical_files(root/'frozen-documents', first_release=True)
    full_docs, full_turns, full_schedule = week.plan(week.AUTHORED_CHATS)
    with (tmp_path/'execution.lock').open('w+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        scope = gate.create(root, execution_lock=lock, session_id='source', canonical_manifest=manifest['path'],
                            manifest_sha256=manifest['sha256'], prepaid={}, auto_recharge_evidence={},
                            layout=gate.CANONICAL_LAYOUT, week=True, week_session_layout=week.AUTHORED_CHATS,
                            week_first_four=True)
        bound = read(scope)
        gate.validate(bound, scope)
        docs, turns, schedule = week.plan_for(bound)
        assert read(bound['week_schedule']) == schedule
        assert schedule['turns'] == full_schedule['turns'][:4]
        assert turns == full_turns[:4] and docs == full_docs[:1]
        assert len(bound['plan']) == 1 and len(bound['plan'][0]['source_ids']) == 39
        assert list(Path(manifest['path']).parent.glob('*.txt')) == [Path(bound['plan'][0]['path'])]
        with pytest.raises(gate.GateError, match='frozen daily renderer'):
            gate._plan(manifest, gate.CANONICAL_LAYOUT)
        with pytest.raises(gate.GateError, match='frozen daily renderer'):
            gate.validate({**bound, 'week_first_four': False}, scope)
        monkeypatch.setattr(gate, 'controller', lambda _: bound)
        with pytest.raises(gate.GateError, match='Next exact daily release'):
            gate.begin(root, full_docs[1].cutoff)
        with pytest.raises(gate.GateError, match='Frozen follow-up'):
            gate.followup_ready(root, full_turns[4].id)
        for i, sid in enumerate(('first', 'second', 'third', 'first')):
            assert week.pending(bound)[1] == turns[i]
            week.path_for(root, i, 'intent').write_text('{}')
            week.publish(bound, i, {'session_id': sid})
        assert week.position(bound) == 4
        with pytest.raises(OwnershipConflict, match='already complete'):
            week.pending(bound)


def test_honcho_stock_capture_inventory_is_bound_per_durable_session(tmp_path, monkeypatch):
    from pacbench.providers.honcho import week_capture as capture

    intents = [{'session': 'sid-a', 'question': 'turn-one', 'chunks': [{'peer': 'p', 'sha256': 'a'}]},
               {'session': 'sid-b', 'question': 'turn-two', 'chunks': [{'peer': 'p', 'sha256': 'b'}]},
               {'session': 'sid-a', 'question': 'turn-three', 'chunks': [{'peer': 'p', 'sha256': 'c'}]}]
    monkeypatch.setattr(capture, 'intents', lambda _: intents)
    rows = [
        dict(local_id=1, public_id='a1', session='sid-a', session_id=9, seq=1, peer='p', sha256='a', file={}, meta={}),
        dict(local_id=3, public_id='a2', session='sid-a', session_id=9, seq=2, peer='p', sha256='c', file={}, meta={}),
        dict(local_id=2, public_id='b1', session='sid-b', session_id=10, seq=1, peer='p', sha256='b', file={}, meta={}),
    ]
    bound, expected = capture.inventory({}, {'messages': rows})
    assert expected == 3
    assert [bound[i]['origin'] for i in (1, 2, 3)] == ['turn-one', 'turn-two', 'turn-three']
    rows[-1]['session_id'] = 9
    with pytest.raises(OwnershipConflict, match='foreign'):
        capture.inventory({}, {'messages': rows})


def test_authored_chat_close_observes_only_its_manager():
    from pacbench.providers.honcho.week_capture import _closed_manager

    provider = dict(name='honcho', session_key='selected', pending=0, writer_queue=0, active_flushes=0,
                    workers=['honcho-async-writer'], clean=False)
    selected = dict(shutdown_returned=True, pending_futures=0, native_drain={'status': 'drained'},
                    providers=[provider], clean=False)
    assert _closed_manager(selected, 'selected')
    assert not _closed_manager(selected, 'other')
    assert not _closed_manager({**selected, 'native_drain': {'status': 'running'}}, 'selected')
    assert not _closed_manager({**selected, 'providers': [{**provider, 'writer_queue': 1}]}, 'selected')


def test_deferred_chat_requires_a_matching_native_ineligible_batch():
    from pacbench.providers.honcho.week_capture import deferred_batch_evidence

    item = dict(unit='owned-chat', type='representation', claim=None)
    batch = dict(unit='owned-chat', type='representation', pending_count=2,
                 total_tokens=511, oldest_created_at='2026-11-23T19:39:01+00:00', eligible=False)
    assert deferred_batch_evidence([item, item], [batch]) == [batch]
    assert deferred_batch_evidence([], [batch]) == []
    assert deferred_batch_evidence([item, item], []) is None
    assert deferred_batch_evidence([item, item], [batch, batch]) is None
    assert deferred_batch_evidence([item, {**item, 'claim': 'active'}], [batch]) is None
    for changed in ({'eligible': True}, {'pending_count': 1}, {'type': 'summary'}, {'unit': 'other'}):
        assert deferred_batch_evidence([item, item], [{**batch, **changed}]) is None


def test_cost_incomplete_terminal_source_and_answer_keep_retained_bindings(tmp_path, rejected_representation):
    import json

    from pacbench.canary_capability import digest
    from pacbench.cohort_regrade_retained import _honcho_answer_response, _honcho_ingest_response
    from pacbench.observational_receipts import RunReceiptJournal

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
