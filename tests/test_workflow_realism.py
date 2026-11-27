from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pacbench import context_isolation as api_input_trace
from pacbench.workflows import native as workflow_native
from pacbench.workflows.conformance import workflow_main as main
from pacbench.workflows.conformance import RecordingAccount, RecordingClient
from pacbench.workflows.delivery import submit_document
from pacbench.workflows.runner import Condition, Journal, deliver, grade_turn, qualify_account, run_workflow
from pacbench.workflows.turn_evidence import captured_turn
from pacbench.corpus.r2 import SPEC, compile_week
from pacbench.workflows.spec import Turn, nightly_documents, instant
from pacbench.workflows.recording import clip_evidence, retry_activity, tool_activity
from pacbench.providers import BOTH, PROVIDERS, eligibility


@pytest.fixture(scope='module')
def week():
    return compile_week('continuing-account')


def condition(p='hindsight',track='nightly-document',state='continuing-account',channels=BOTH):
    return Condition(p,track,state,'recording','sentinel','offline','test','pinned','none','scenario',(),channels)


def message(id, at, channel='imessage', **overrides):
    return dict({'source_id':id,'written_at':at,'arrived_at':at,'collected_at':at,
                 'from_owner':False,'sender':'Sam','thread':'family','text':'Hello from '+id,
                 'structured':{},'channel':channel},**overrides)


def run(tmp,condition,docs,turns,account=None):
    account = account or RecordingAccount(condition, docs)
    try:
        with Journal(tmp/'records') as journal:
            return run_workflow(condition,account,docs,turns,journal,spec_digest='fixture',evidence_kind='recording-fake')
    finally:
        account.close()


@pytest.mark.parametrize('provider',PROVIDERS)
def test_actual_submission_shapes_and_exclusions(tmp_path,week,provider):
    c = condition(provider)
    documents = week[1]
    doc = documents[0]
    supported = provider not in ('memos','memcal','honcho')
    assert eligibility(provider,c.track)['documented_for_scope'] == supported
    account = RecordingAccount(c)
    try:
        if not supported:
            with pytest.raises(ValueError):
                submit_document(provider,account.client,'test',doc)
            assert account.client.request is None
            return
        accepted = account.submit(doc)
        ready = account.readback(doc,accepted)
        assert ready['submitted_sha256'] == hashlib.sha256(doc.text.encode()).hexdigest()
        req = accepted['request']
        if provider == 'mem0':
            assert req['args'] == (doc.text,) and req['kwargs']['infer']
        elif provider == 'supermemory':
            assert 'dreaming' not in req['kwargs'] and req['kwargs']['date']==doc.cutoff
        elif provider == 'hindsight':
            assert req['method']=='retain_files' and req['metadata']==[{'document_id':doc.filename}]
        elif provider == 'openviking':
            assert req['path']=='/api/v1/resources' and req['json']['wait'] is True
            assert req['timeout']==180
        elif provider == 'cognee':
            assert req['kwargs']['session_ids']==[] and req['kwargs']['text']==doc.text
        elif provider == 'reme':
            assert req['method']=='write-native-resource' and req['date']==doc.cutoff[:10]
            with pytest.raises(FileExistsError):
                account.submit(doc)
    finally:
        account.close()


@pytest.mark.parametrize('provider', PROVIDERS)
def test_connector_scope_and_no_document_substitution(tmp_path, week, provider):
    full = eligibility(provider,'first-party-connector')
    assert full['documented_for_scope'] == (provider=='memcal')
    mail = eligibility(provider,'first-party-connector',('email',))
    assert mail['documented_for_scope'] == (provider in ('memcal','cognee','honcho','supermemory'))
    if not mail['documented_for_scope']:
        assert full['boundary'] and full['version_source']
        return
    docs = [d for d in week[1] if d.channel=='email']
    c = condition(provider,'first-party-connector',channels=('email',))
    run(tmp_path,c,docs,[])
    rows = [json.loads(x) for x in (tmp_path/'records/workflow.jsonl').read_text().splitlines()]
    assert not any(r['phase']=='submit' for r in rows)
    for r in rows:
        if r['phase']=='acceptance':
            assert set(r['evidence']['request'])=={'sync_through','channels'}
    with pytest.raises(ValueError,match='partial connector'):
        run(tmp_path/'partial',c,docs,week[2][:1])


def test_projection_cutoff_dst_unknown_join_and_late_delivery():
    fri = '2026-10-31T21:00:00-04:00'
    before_fall = message('before',fri,structured={'participants':['Sam','Me']})
    late = message('late','2026-10-31T20:59:00-04:00',arrived_at='2026-11-01T01:30:00-04:00',
                   collected_at='2026-11-01T01:29:00-05:00')
    joined = message('observed','2026-11-01T20:58:00-05:00',structured={'participants':['Sam','Me','Nora']})
    after = message('after','2026-11-01T21:00:01-05:00')
    files = nightly_documents([after,late,joined,before_fall],'America/New_York','21:00')
    assert [d.cutoff for d in files]==[fri,'2026-11-01T21:00:00-05:00','2026-11-02T21:00:00-05:00']
    assert [d.source_ids for d in files]==[('before',),('late','observed'),('after',)]
    assert 'Nora' not in files[0].text and 'joined' not in files[1].text
    assert 'Hello from late' in files[1].text and 'Delivered: 2026-11-01T01:30:00-04:00' in files[1].text
    with pytest.raises(ValueError,match='unambiguous'):
        nightly_documents([late],'America/New_York','01:30')
    with pytest.raises(ValueError,match='unambiguous'):
        nightly_documents([message('spring','2026-03-08T01:00:00-05:00')],'America/New_York','02:30')


def test_email_headers_quotes_and_oracle():
    at = '2026-10-05T10:00:00-04:00'
    m = message('reply',at,'email',structured={'email_subject':'Plans','participants':['Sam','Me'],
        'email_headers':{'References':'<synthetic-1@example.test>','Cc':'Me <me@example.test>'}},
        text='See you Friday.\n> Monday works for me.')
    doc, = nightly_documents([m],'America/New_York','21:00')
    assert doc.text.count('From: Sam')==1 and m['text'] in doc.text
    assert 'References: <synthetic-1@example.test>' in doc.text and 'In-Reply-To:' not in doc.text
    assert 'To:' not in doc.text and 'Cc: Me <me@example.test>' in doc.text
    for bad in [{**m,'claims':[]},{**m,'structured':{'expected':'answer'}},
                {**m,'structured':{'email_headers':{'Subject':'oracle'}}},
                {**m,'collected_at':'2026-10-04T10:00:00-04:00'}]:
        with pytest.raises(ValueError):
            nightly_documents([bad],'America/New_York','21:00')


def test_week_session_state_and_cutoff(tmp_path,week):
    spec,docs,turns,base = week
    assert base['messages']==142 and len(docs)==48 and len({d.cutoff for d in docs})==7
    rows = dict((t.id,(t,s)) for t,s in run(tmp_path,condition(),docs,turns))
    stages = Counter(t.measure for t in turns)
    assert stages=={'initial':41,'user-correction':2,'correction-uptake':2,
                    'correction-persistence':2,'conversational-followup':2}
    for chain in [('game-reference','friday-lunch-time','saturday-dinner-time','assistant-weekend-after',
                   'weekend-correction','weekend-uptake'),('ben-mail-separate','ben-correction','ben-uptake'),
                  ('lunch-snapshot','lunch-refreshed')]:
        assert len({rows[k][1]['session_id'] for k in chain})==1
        assert len({rows[k][1]['gateway_identity'] for k in chain})==1
        history=rows[chain[-1]][1]['history']
        previous=rows[chain[0]][1]['history']
        assert history[:len(previous)]==previous
    assert len(rows['lunch-snapshot'][1]['source_files']) < len(rows['lunch-refreshed'][1]['source_files'])
    for prefix in ('weekend','ben'):
        first=rows[prefix+'-correction'][1]
        later=rows[prefix+'-persistence'][1]
        assert later['account']==first['account'] and later['session_id']!=first['session_id']
        assert len(later['history'])==2
    for t, surface in rows.values():
        assert surface['source_files']==[d.filename for d in docs if instant(d.cutoff)<=instant(t.at)]
        assert surface['model_requests'][0]['messages'][-1]['content'].startswith(t.prompt)
        assert 'Claims (hidden' not in json.dumps(surface)
    assert len({s['account'] for _,s in rows.values()})==1
    assert len({s['session_id'] for _,s in rows.values()}) < 36
    assert 'lunch-fresh' not in rows


def test_isolated_control_never_accumulates_answers(tmp_path):
    _,docs,turns,_=compile_week('isolated-control')
    rows=run(tmp_path,condition(state='isolated-control'),docs,turns)
    assert len(rows)==42 and sum(len(t.claims) for t in turns)==188
    assert len({s['account'] for _,s in rows})==len({s['session_id'] for _,s in rows})==42
    assert all(len(s['history'])==2 and s['prefetch']=='' for _,s in rows)


@pytest.mark.parametrize('defect',['reset','history','gateway','wire','surface','grant','wrong-account'])
def test_fails_closed_on_native_trace_defect(tmp_path,week,defect):
    c=condition()
    class Fault(RecordingAccount):
        def ask(self,prompt,*,at,session_id):
            s=super().ask(prompt,at=at,session_id=session_id)
            if session_id:
                if defect=='reset':s['session_id']='switched'
                if defect=='gateway':s['gateway_identity']='restarted'
                if defect=='history':s['history'][1]['content']='changed'
                if defect=='wire':s['model_requests'][-1]['messages'][1]['content']='changed'
                if defect=='surface':s['source_files']+=['a future upload']
                if defect=='grant':s['offered_tools']=['file']
                if defect=='wrong-account':s['account']='other'
            return s
    turns=[t for t in week[2] if t.id in ('game-reference','friday-lunch-time')]
    with pytest.raises(ValueError):
        run(tmp_path,c,week[1],turns,Fault(c))
    assert json.loads((tmp_path/'records/workflow.jsonl').read_text().splitlines()[-1])['phase']=='stopped'


@pytest.mark.parametrize('issue',['unknown','late','no-readback','wrong-id'])
def test_accepted_is_not_ready_and_no_resubmit(tmp_path,week,issue):
    c=condition()
    class Fault(RecordingAccount):
        def readback(self,doc,ack):
            value=super().readback(doc,ack)
            if issue=='unknown':value['ready']=False
            if issue=='late':value['at']='2026-11-01T10:00:00-05:00'
            if issue=='no-readback':value['readback']=None
            if issue=='wrong-id':value['native_id']='wrong'
            return value
    with pytest.raises(ValueError):
        run(tmp_path,c,week[1],week[2][:1],Fault(c))
    events=[json.loads(s)['phase'] for s in (tmp_path/'records/workflow.jsonl').read_text().splitlines()]
    assert events.count('submit')==events.count('acceptance')==1 and 'dispatch' not in events


def test_clipping_repeats_and_retry_ancestry():
    for size in (1199,1200,1201,2400):
        original='S'+'x'*(size-2)+'Z'
        native=original[:1200]+'...' if size>1200 else original
        got=clip_evidence(original,native,native)
        assert got['provider_to_assistant']=='identical'
        assert got['store_to_provider_surface']==('clipped-prefix' if size>1200 else 'identical')
    assert clip_evidence('hello world','hello world','he...')['provider_to_assistant']=='clipped-prefix'
    assert clip_evidence(None,'found','found')['store_to_provider_surface']=='unknown'
    calls=[{'tool_call_id':str(i),'name':'search','input':{'query':q}} for i,q in enumerate(['a','b','a'])]
    results=[{'tool':'search','text':'same','success':True,'tool_call_id':c['tool_call_id']} for c in reversed(calls)]
    assert tool_activity(calls,results)=={'explicit_attempts':3,'repeat_requests':1,'repeat_results':2,'failed':0}
    rows=[dict(id='a',parent=None,origin='unknown',operation='model'),
          dict(id='b',parent='a',origin='provider',operation='model'),
          dict(id='c',parent='b',origin='harness',operation='model')]
    assert retry_activity(rows+rows)['linked_retries']=={'provider':1,'harness':1}
    with pytest.raises(ValueError): retry_activity(rows[1:])
    with pytest.raises(ValueError): retry_activity(rows+[dict(rows[0],operation='other')])


def test_workflow_journal_uses_elapsed_time_between_phases(tmp_path, monkeypatch):
    from pacbench.workflows import runner as workflow_runner

    ticks = iter([1000.0, 1002.0, 1007.0])
    monkeypatch.setattr(workflow_runner.time, 'monotonic', lambda: next(ticks))
    with Journal(tmp_path/'records') as journal:
        journal.write('submit')
        journal.write('readback')
    rows = [json.loads(line) for line in (tmp_path/'records/workflow.jsonl').read_text().splitlines()]
    assert [(r['phase'],r['elapsed_s']) for r in rows] == [('submit',2.0), ('readback',7.0)]


def test_latest_judge_separate_correction_measure(tmp_path,week):
    rows=run(tmp_path,condition(),week[1],week[2])
    turn,surface=next(x for x in rows if x[0].id=='weekend-uptake')
    recorder=[]
    def offline_verdict(prompt):
        recorder.append(prompt)
        return json.dumps({'verdicts':[{'id':'scope','verdict':'missed','reason':'no information in fixture acknowledgement'}]})
    grade=grade_turn(turn,surface,offline_verdict,tmp_path/'grades')
    assert grade.missed==1 and turn.measure=='correction-uptake'
    evidence=json.loads((tmp_path/'grades/weekend-uptake/judgment.json').read_text())
    assert evidence['judge_protocol']=='semantic-judge-context-v4'
    assert '2026-10-09' in recorder[0] and 'porch list' in recorder[0]
    correction=next(x for x in rows if x[0].id=='weekend-correction')
    assert grade_turn(*correction,offline_verdict,tmp_path/'grades') is None and len(recorder)==1


def test_query_between_cutoff_and_next_evening_sees_only_previous_night(tmp_path):
    def q(id,at):
        return Turn(id,at,id,'continuous','initial',())
    docs=nightly_documents([
        message('old','2026-10-07T19:00:00-04:00'),
        message('new','2026-10-08T18:00:00-04:00','email'),
        message('late','2026-10-07T18:00:00-04:00',arrived_at='2026-10-08T21:01:00-04:00',
                collected_at='2026-10-08T21:01:00-04:00')],
        'America/New_York','21:00')
    turns=[q('before','2026-10-08T20:55:00-04:00'),q('after','2026-10-08T21:05:00-04:00'),
           q('late','2026-10-09T21:05:00-04:00')]
    snapshots=run(tmp_path,condition(),docs,turns)
    assert [len(s['source_files']) for _,s in snapshots]==[1,2,3]
    assert len({s['session_id'] for _,s in snapshots})==1


def test_wire_multi_tool_group_order_and_untruncated_content():
    c=replace(condition(),tool_names=('lookup',))
    turn=Turn('q','2026-10-06T09:00:00-04:00','Question?','chat','initial',())
    calls=[{'id':i,'type':'function','function':{'name':'lookup','arguments':json.dumps({'q':i})}}
           for i in ('a','b')]
    visible='a'*8100+'tail'
    history=[{'role':'user','content':turn.prompt},{'role':'assistant','content':None,'tool_calls':calls},
             *[{'role':'tool','content':visible,'tool_name':'lookup','tool_call_id':i} for i in ('b','a')],
             {'role':'assistant','content':'final'}]
    s={'session_id':'one','gateway_identity':[421,7],'write_barrier':{'session_id':'one','pending_writes':0,'readback':'durable'},'question_at':turn.at,
       'offered_tools':['lookup'],'source_files':[],'answer':'final','prefetch':'','history':history,
       'tool_calls':[{'tool_call_id':i,'name':'lookup','input':{'q':i}} for i in ('a','b')],
       'tool_results':[{'tool_call_id':i,'tool':'lookup','text':visible,'success':True} for i in ('b','a')],
       'model_requests':[{'request_id':str(i),'model':'sentinel','tools':['lookup'],'messages':msgs}
                         for i,msgs in enumerate([history[:1],history[:-1]])],'attempts':[]}
    assert captured_turn(s,turn,None,c,[])['repeat_results']==1
    s['model_requests'][-1]['messages']=[dict(r) for r in history[:-1]]
    s['model_requests'][-1]['messages'][0]['content'] = 'A different question'
    with pytest.raises(ValueError, match='user context'):
        captured_turn(s,turn,None,c,[])
    s['model_requests'][-1]['messages'][0]['content'] = turn.prompt
    s['model_requests'][-1]['messages'][2]['content']=visible[:-1]
    with pytest.raises(ValueError,match='altered'):
        captured_turn(s,turn,None,c,[])


def test_actual_request_observer_keeps_tool_payload(tmp_path,monkeypatch):
    home=tmp_path/'hermes-home'
    path=tmp_path/'api-inputs.jsonl'
    monkeypatch.setenv('HERMES_HOME',str(home))
    monkeypatch.setenv('PACBENCH_API_INPUT_TRACE',str(path))
    monkeypatch.setenv('PACBENCH_WORKFLOW_CAPTURE','pizza-week-conversation-v2')
    body={'model':'sentinel','messages':[{'role':'system','content':'synthetic'},
          {'role':'tool','tool_call_id':'b','content':'x'*6000}],'tools':[{'type':'function','function':{'name':'lookup'}}]}
    api_input_trace.record_input('test-driver',{'id':'request1','request':body})
    row=json.loads(path.read_text())
    assert row['request']==body and row['messages']==body['messages'] and row['tools']==['lookup']


def test_warm_hermes_cli_resumes_native_sqlite_session(tmp_path,monkeypatch):
    import sqlite3
    from pacbench.workflows.native import WarmHermes

    home=tmp_path/'hermes-home'
    home.mkdir()
    ctx=tmp_path/'context.jsonl'
    api=tmp_path/'api-inputs.jsonl'
    folder=tmp_path/'clock'
    (folder/'questions').mkdir(parents=True)
    db=sqlite3.connect(home/'state.db')
    db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id, role, content, api_content, tool_name, tool_call_id, tool_calls)')
    env={'PACBENCH_WORKFLOW_CAPTURE':'pizza-week-conversation-v2','PACBENCH_CONTEXT_TRACE':str(ctx),'PACBENCH_API_INPUT_TRACE':str(api)}
    owner=SimpleNamespace(home=home,env=env,witness={'pid':123,'start_time':456},python='python',cwd=tmp_path)
    descriptors=[]
    commands=[]

    def prepare(owner,prompt,at,*,continuation):
        owner.fence()
        value={'nonce':f'{len(descriptors):032x}', 'continuation':continuation, 'at':at, **(continuation or {})}
        descriptors.append(value)
        (folder/'questions'/(value['nonce']+'.json')).write_text(json.dumps(value))
        return value

    def dispatch(cmd,**kwargs):
        commands.append(cmd)
        prompt=cmd[cmd.index('-q')+1]
        sid='ordinary-native-chat'
        db.execute('INSERT INTO messages(session_id,role,content) VALUES (?,?,?)',(sid,'user',prompt))
        messages=[dict(role=r,content=c) for r,c in db.execute('SELECT role,content FROM messages ORDER BY id')]
        db.execute('INSERT INTO messages(session_id,role,content) VALUES (?,?,?)',(sid,'assistant','answer'))
        db.commit()
        with api.open('a') as f:
            print(json.dumps({'protocol':'workflow-driver-input-v1','turn_nonce':descriptors[-1]['nonce'],
                'messages':messages,'request_id':descriptors[-1]['nonce'],'model':'sentinel','tools':[]}),file=f)
        with ctx.open('a') as f:
            print(json.dumps({'session_id':sid,'query':prompt,'prefetch':''}),file=f)
        return SimpleNamespace(returncode=0,stderr='',stdout=json.dumps({'session_id':sid,'type':'result','text':'answer'}))
    monkeypatch.setattr(workflow_native,'_identity',lambda *args: dict(owner.witness))
    monkeypatch.setattr(workflow_native,'audit_question',lambda *args: {'state':'pass'})
    monkeypatch.setattr(workflow_native,'actual_context',lambda *args,**kwargs:{'state':'pass'})
    monkeypatch.setattr(workflow_native.subprocess,'run',dispatch)
    warm=WarmHermes(owner,SimpleNamespace(folder=folder,prepare=prepare),
        lambda: {'gateway_identity':[123,456],'pending_native_writes':0,'inflight_requests':0,
                 'readback':'recording-store','native_errors':[]})
    a=warm.ask('First question?',at='2026-10-06T09:00:00-04:00',session_id=None)
    b=warm.ask('Actually, Saturday only.',at='2026-10-06T09:05:00-04:00',session_id=a['session_id'])
    assert '--resume' not in commands[0] and commands[1][-2:]==['--resume',a['session_id']]
    assert len(commands)==2 and all(cmd[3]=='chat' for cmd in commands)
    for result in (a,b):
        assert result['timing']['cli_result_received_s'] >= 0
        assert result['timing']['post_result_trace_capture_s'] >= 0
        assert result['timing']['native_reported_turn_s'] is None
    assert a['gateway_identity']==b['gateway_identity']==[123,456]
    assert b['history'][:2]==a['history'] and descriptors[1]['continuation']['history_sha256']
    assert [r['content'] for r in b['model_requests'][0]['messages']]==['First question?','answer','Actually, Saturday only.']
    db.close()


def test_current_entry_reports_separate_denominators_and_no_fake_scores(tmp_path):
    from pacbench.workflows.runner import run_current_week
    c=condition()
    account=RecordingAccount(c)
    try:
        counts=run_current_week(c,account,tmp_path/'run')
        assert {k:v['planned_criteria'] for k,v in counts.items()}=={
            'initial':185,'user-correction':0,'correction-uptake':2,'correction-persistence':2,
            'conversational-followup':3}
        assert all(v['correct'] is None and v['graded_criteria']==0 for v in counts.values())
        with pytest.raises(ValueError,match='fakes'):
            run_current_week(c,account,tmp_path/'rejected',judge=lambda x: 'unreachable')
        with pytest.raises(FileExistsError):
            run_current_week(c,account,tmp_path/'run')
        inputs=[w['user'] for w in account.native_writes]
        assert any('porch list' in s for s in inputs)
        assert len(inputs)==49
    finally:
        account.close()


def test_connector_future_item_witness_rejected(tmp_path,week):
    c=condition('memcal','first-party-connector')
    class Fault(RecordingAccount):
        def sync_readback(self,ack):
            ready=super().sync_readback(ack)
            ready['new_source_ids'].append('tomorrows-message')
            return ready
    with pytest.raises(ValueError,match='unexpected source set'):
        run(tmp_path,c,week[1],week[2],Fault(c,week[1]))


def test_matrix_cli_does_not_claim_hosted_compatibility(capsys):
    main(['plan','--track','first-party-connector'])
    result=json.loads(capsys.readouterr().out)
    assert len(result['providers'])==9 and result['prompt_caching']=='unmeasured'
    assert all(not x['live_verified'] for x in result['providers'].values())


def test_conformance_cli_reports_unsupported_track_and_accepts_channel_only_import(tmp_path, capsys):
    unsupported = tmp_path/'unsupported'
    assert main(['conform', '--provider', 'honcho', '--track', 'nightly-document',
                 '--seven-daily-documents', '--output', str(unsupported)]) == 2
    row = json.loads(capsys.readouterr().out)['providers']['honcho']
    assert row['result'] == 'conformance-unavailable'
    assert 'generic offline conformance' in row['reason']
    assert (row['recorded_files'], row['offline_recorded_turns']) == (0, 0)
    assert not unsupported.exists()

    assert main(['conform', '--track', 'first-party-connector', '--output', str(tmp_path/'matrix')]) == 0
    providers = json.loads(capsys.readouterr().out)['providers']
    assert set(providers) == set(PROVIDERS)
    assert providers['mem0']['result'] == 'conformance-unavailable'
    row = providers['honcho']
    assert row['result'] == 'channel-only import conformance'
    assert row['recorded_files'] > 0 and row['offline_recorded_turns'] == 0


def test_conformance_cli_reports_a_supported_route_that_records_no_work(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr('pacbench.workflows.conformance.run_compiled_workflow', lambda *args, **kwargs: {})
    assert main(['conform', '--provider', 'mem0', '--track', 'nightly-document',
                 '--output', str(tmp_path/'empty')]) == 2
    row = json.loads(capsys.readouterr().out)['providers']['mem0']
    assert row['result'] == 'conformance-unavailable'
    assert row['reason'] == 'Conformance recorded no source files or turns.'


def test_open_chat_clock_never_forces_shutdown(monkeypatch):
    from pacbench.workflows.native import OpenChatClockOwner
    monkeypatch.setattr(workflow_native,'_identity',lambda *a:None)
    owner=SimpleNamespace(home=Path('/synthetic'),env={},witness={'pid':123,'start_time':99},
                          fence=lambda:pytest.fail('called shutdown-only fence'))
    proof={'gateway_identity':[123,99],'pending_native_writes':0,'inflight_requests':0,
           'readback':'synthetic-store-readback','native_errors':[]}
    adapter=OpenChatClockOwner(owner,lambda:dict(proof))
    assert adapter.fence()==proof
    proof['pending_native_writes']=1
    with pytest.raises(RuntimeError):adapter.fence()


def test_native_exclusion_before_unverified_mac_or_grant():
    from pacbench.workflows.native import NativeAccount
    c=condition('memcal','first-party-connector')
    class Witness:
        def verify_deployment(self,condition,identity):
            return {'account':identity,'condition':vars(condition),'observed_version':condition.version,
                'frontend_identity':condition.frontend,'observed_patch':condition.gateway_patch,'granted_tools':[],
                'effective_model_route':condition.model_route,
                'store_identity':'example','synthetic_source_provenance':'isolated'}
    account=NativeAccount('example','memcal',None,None,None,Witness())
    with pytest.raises(ValueError,match='unverified source grant'):
        qualify_account(account, c, 'native')


def test_local_warm_gateway_for_current_conversation(tmp_path, monkeypatch):
    """Actual native gateway/CLI and DB, only synthetic loopback storage/model response."""
    import time
    from tests.test_gateway_reuse import PYTHON, LocalNativeFixture, native_home
    from pacbench.provider_clock import CheckpointClock
    from pacbench.gateway_reuse import PersistentGateway
    if not PYTHON.is_file():
        pytest.skip('pinned Hermes runtime is not mounted')
    def observe(body):
        import uuid
        nonce = json.loads((clock.folder/'active.json').read_text())['nonce']
        monkeypatch.setenv('PACBENCH_PYTHON_CLOCK_NONCE', nonce)
        api_input_trace.record_input('fixture-driver', {'id':uuid.uuid4().hex, 'request':body})
    fixture=LocalNativeFixture(tmp_path/'store', on_driver_request=observe)
    fixture.release.set()
    try:
        home,env=native_home(tmp_path,fixture.endpoint)
        env.update(PACBENCH_WORKFLOW_CAPTURE='pizza-week-conversation-v2',
                   PACBENCH_API_INPUT_TRACE=str(tmp_path/'api-inputs.jsonl'))
        cwd=tmp_path/'empty'
        cwd.mkdir()
        clock=CheckpointClock(home,env['PACBENCH_NOW'],'offline-src')
        with PersistentGateway(home,PYTHON,env,cwd=cwd,checkpoint='offline-src',observation_clock=clock) as owner:
            def idle():
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    state=json.loads((home/'reuse-observer.json').read_text())
                    if (not state['native_failures'] and
                            all(not r['pending_futures'] and all(not p.get('unfinished_writes') for p in r['providers'])
                                for r in state['sessions'])):
                        return {'gateway_identity':[state['pid'],state['start_time']],'inflight_requests':0,
                                'pending_native_writes':0,'readback':state['observed_monotonic_ns'],
                                'native_errors':state['native_failures']}
                    time.sleep(.05)
                raise RuntimeError('fixture write did not finish')
            monkeypatch.setenv('HERMES_HOME', str(home))
            for key in ('PACBENCH_WORKFLOW_CAPTURE','PACBENCH_API_INPUT_TRACE'):
                monkeypatch.setenv(key, env[key])
            monkeypatch.delenv('PACBENCH_REUSE_CLOCK_STATE', raising=False)
            warm=workflow_native.WarmHermes(owner,clock,idle)
            prompts=['What did I call the list?','Actually, call it my porch list.','What name should you use?']
            prior=None
            for index,prompt in enumerate(prompts):
                at=f'2026-10-05T09:{10*index:02}:00-04:00'
                result=warm.ask(prompt,at=at,session_id=prior['session_id'] if prior else None)
                assert result['clock_evidence']['state']=='pass'
                result.update(source_files=[],account='fixture',write_barrier={'session_id':result['session_id'], 'pending_writes':0, 'readback':idle()},
                              attempts=[])
                c=replace(condition(), model='offline-model', tool_names=())
                captured_turn(result, Turn('probe',at,prompt,'chat','initial',()), prior, c, [])
                assert [r['content'] for r in result['history'] if r['role']=='user']==prompts[:index+1]
                if prior:
                    assert prior['history']==result['history'][:len(prior['history'])]
                    assert prior['gateway_identity']==result['gateway_identity'] and prior['session_id']==result['session_id']
                prior=result
            assert any('call it my porch list' in str(m['content']) for m in result['model_requests'][0]['messages'] if m['role']=='user')
            warm.end_chat(result['session_id'])
            owner.fence(result['session_id'])
            assert (fixture.workspace/'native-writes.jsonl').is_file()
    finally:
        fixture.close()


def test_memos_shipped_clip_uses_same_field_as_model_saw():
    from pacbench.workflows.recording import tool_text_boundary
    raw='start'+'x'*1500+'end'
    excerpt=raw[:1200]+'...'
    out=tool_text_boundary({'stored':raw,'native':excerpt,'field_path':['meta','userText']},
                           json.dumps({'meta':{'userText':excerpt}}))
    assert out['store_to_provider_surface']=='clipped-prefix'
    assert out['provider_to_assistant']=='identical'


def test_unavailable_verbatim_body_in_prefetch_blocks_answer(tmp_path, week):
    condition_ = condition()
    future = week[1][-1].bodies[-1]
    assert len(future) >= 40

    class Leaked(RecordingAccount):
        def ask(self, prompt, **kwargs):
            surface = super().ask(prompt, **kwargs)
            surface['prefetch'] = future
            return surface

    with pytest.raises(ValueError, match='unavailable source body'):
        run(tmp_path, condition_, week[1], week[2][:1], Leaked(condition_))


def test_fresh_control_id_with_prior_writes_is_rejected(tmp_path):
    _, docs, turns, _ = compile_week('isolated-control')
    c = condition(state='isolated-control')

    class Contaminated(RecordingAccount):
        def isolated_copy(self):
            other = super().isolated_copy()
            other.native_writes.append({'user':'previous answer'})
            return other

    with pytest.raises(ValueError, match='source-only'):
        run(tmp_path, c, docs, turns[:1], Contaminated(c))


def test_fresh_chat_cannot_restart_the_continuing_gateway(tmp_path, week):
    c = condition()

    class RestartedGateway(RecordingAccount):
        def ask(self, prompt, **kwargs):
            if self.native_writes and kwargs['session_id'] is None:
                self.gateway = 'replacement-gateway'
            return super().ask(prompt, **kwargs)

    assert week[2][0].session != week[2][1].session
    with pytest.raises(ValueError, match='between chats'):
        run(tmp_path, c, week[1], week[2][:2], RestartedGateway(c))



def test_document_track_advances_service_clock_once_per_evening(tmp_path, week):
    class MonotonicAccount(RecordingAccount):
        advances = []

        def advance(self, at):
            assert not self.advances or instant(at) > instant(self.advances[-1])
            self.advances.append(at)
            return super().advance(at)

    c = condition()
    account = MonotonicAccount(c)
    run(tmp_path, c, week[1], [], account)
    assert len(account.advances) == 7
    assert len(account.files) == 48


def test_unexpected_source_is_detected_before_any_dispatch(tmp_path, week):
    c = condition()

    class Contaminated(RecordingAccount):
        def source_inventory(self):
            return super().source_inventory() + ['future file']

        def ask(self, *args, **kwargs):
            pytest.fail('Dispatched a question against out-of-scope sources')

    with pytest.raises(ValueError, match='before answer dispatch'):
        run(tmp_path, c, week[1], week[2][:1], Contaminated(c))


@pytest.mark.parametrize('field,value', [('session_id', 'other-chat'), ('pending_writes', 1), ('readback', None)])
def test_unverified_native_writes_stop_turn(tmp_path, week, field, value):
    c = condition()

    class Pending(RecordingAccount):
        def ask(self, *args, **kwargs):
            surface = super().ask(*args, **kwargs)
            surface['write_barrier'][field] = value
            return surface

    with pytest.raises(ValueError, match='turn writes are unverified'):
        run(tmp_path, c, week[1], week[2][:1], Pending(c))


def test_nightly_export_uses_arrival_and_explicit_access_before_collector_time():
    row = message('late-collector', '2026-10-05T18:00:00-04:00',
                  arrived_at='2026-10-05T19:00:00-04:00', collected_at='2026-10-06T22:00:00-04:00')
    backfill = {**row, 'source_id':'backfill', 'available_at':'2026-10-06T21:01:00-04:00'}
    exports = nightly_documents([row, backfill], 'America/New_York', '21:00')
    assert [(d.cutoff[:10], d.source_ids) for d in exports] == [
        ('2026-10-05', ('late-collector',)), ('2026-10-07', ('backfill',))]
    assert row['collected_at'] not in exports[0].text


def test_preloaded_continuing_account_cannot_supply_first_answer(tmp_path, week):
    c = condition()
    account = RecordingAccount(c)
    account.native_writes.append({'user':'preloaded answer'})
    with pytest.raises(ValueError, match='source-only'):
        run(tmp_path, c, week[1], week[2][:1], account)


@pytest.mark.parametrize('fault', ['store-alias','evidence-kind'])
def test_isolated_clone_qualifies_its_own_store_and_kind(tmp_path, fault):
    _, docs, turns, _ = compile_week('isolated-control')
    c = condition(state='isolated-control')
    class FaultyClone(RecordingAccount):
        def isolated_copy(self):
            other = super().isolated_copy()
            if fault == 'store-alias':
                original = other.verify_condition
                other.verify_condition = lambda c: {**original(c), 'store_identity':self.identity}
            else:
                other.evidence_kind = 'other'
            return other
    with pytest.raises(ValueError, match='store|kind'):
        run(tmp_path, c, docs, turns[:1], FaultyClone(c))


def test_caller_and_deployment_agreeing_on_generic_tool_is_insufficient():
    from pacbench.workflows.native import NativeAccount
    from pacbench.providers import HERMES_TOOLS
    c = replace(condition('mem0'), version=PROVIDERS['mem0'].reviewed_version,
                tool_names=(*HERMES_TOOLS['mem0'], 'terminal'))
    class Witness:
        def verify_deployment(self, condition, account):
            return dict(account=account, condition=vars(condition), synthetic_source_provenance='fixture',
                        observed_version=c.version, store_identity='store', frontend_identity=c.frontend,
                        observed_patch=c.gateway_patch, granted_tools=c.tool_names, effective_model_route=c.model_route)
    with pytest.raises(ValueError, match='reviewed integration'):
        qualify_account(NativeAccount('account','mem0','scope',None,None,Witness()), c, 'native')


@pytest.mark.parametrize('fault', ['missing-request','changed-arguments','replaced-ids'])
def test_stream_cannot_change_native_tool_group(fault):
    c = replace(condition(), tool_names=('lookup',))
    turn = Turn('probe','2026-10-05T11:00:00-04:00','lookup?', 'chat','initial',())
    fn = {'id':'a','type':'function','function':{'name':'lookup','arguments':'{"q":"A"}'}}
    history = [{'role':'user','content':turn.prompt},{'role':'assistant','content':'','tool_calls':[fn]},
               {'role':'tool','tool_name':'lookup','tool_call_id':'a','content':'found'},
               {'role':'assistant','content':'done'}]
    call_id = 'fake' if fault == 'replaced-ids' else 'a'
    surface = dict(history=history, session_id='one', gateway_identity='gateway', question_at=turn.at,
       write_barrier={'session_id':'one','pending_writes':0,'readback':'ok'}, source_files=[], answer='done',
       offered_tools=['lookup'], attempts=[], prefetch='', tool_calls=[{'name':'lookup','tool_call_id':call_id,
       'input':{'q':'B' if fault=='changed-arguments' else 'A'}}],
       tool_results=[{'tool':'lookup','tool_call_id':call_id,'text':'found','success':True}],
       model_requests=[dict(request_id=str(i),model='sentinel',tools=['lookup'],messages=history[:i])
                       for i in ([3] if fault=='missing-request' else [1,3])])
    with pytest.raises(ValueError, match='tool group|stream tool'):
        captured_turn(surface,turn,None,c,[])


def test_missing_receipts_keeps_answer_and_reports_incomplete_coverage(tmp_path, week):
    run(tmp_path,condition(),week[1],week[2][:1])
    rows = [json.loads(line) for line in (tmp_path/'records/workflow.jsonl').read_text().splitlines()]
    verified = next(r for r in rows if r['phase']=='verified-turn')
    coverage = verified['activity']['driver_receipts']
    assert coverage['coverage']=='incomplete' and len(coverage['missing'])==1


@pytest.mark.parametrize('receipt', ['missing', 'wrong-route', 'valid'])
def test_native_grade_requires_matching_transport_receipt(tmp_path, week, receipt):
    from pacbench.model_calls import ModelOutput
    from pacbench.workflows.runner import UnverifiedJudgment
    turn, surface = next(x for x in run(tmp_path, condition(), week[1], week[2]) if x[0].id=='weekend-uptake')
    def judge(prompt):
        verdict = json.dumps({'verdicts':[{'id':'scope','verdict':'correct','reason':'test response'}]})
        if receipt == 'missing':
            return verdict
        model, endpoint = 'openai/gpt-6-luna', 'amazon-bedrock/global'
        request = {'model':model, 'messages':[{'role':'user','content':prompt}]}
        sent = {**request, 'provider':{'allow_fallbacks':False,'require_parameters':True,'order':[endpoint],
                                      'only':[endpoint if receipt=='valid' else 'different']}}
        record = {'status':'ok','url':'https://openrouter.ai/api/v1/chat/completions',
                  'model':model,'request':request,'text':verdict,'judge_transport':{
                      'id':'opaque-attempt','request':sent,'endpoint':endpoint,
                      'request_sha256':hashlib.sha256(json.dumps(sent,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}}
        path = tmp_path/'model-calls'/'attempt.json'
        path.parent.mkdir()
        path.write_text(json.dumps(record))
        return ModelOutput(verdict, model, {}, str(path))
    if receipt == 'valid':
        assert grade_turn(turn,surface,judge,tmp_path/'grades',require_receipt=True).correct == 1
    else:
        with pytest.raises(UnverifiedJudgment):
            grade_turn(turn,surface,judge,tmp_path/'grades',require_receipt=True)


def test_compiled_r2_refuses_native_execution_before_dispatch(tmp_path, monkeypatch):
    from pacbench.workflows import runner
    route = condition()
    account = RecordingAccount(route)
    account.evidence_kind = 'native'
    destination = tmp_path / 'week'
    def dispatch(*args, **kwargs):
        pytest.fail('a recording workflow attempted to dispatch')
    monkeypatch.setattr(runner, 'run_workflow', dispatch)
    with pytest.raises(ValueError, match='offline conformance only'):
        runner.run_current_week(route, account, destination, judge=dispatch)
    assert not destination.exists()


def test_cross_day_judge_context_has_each_turns_own_date(tmp_path, week):
    turn, surface = next(x for x in run(tmp_path,condition(),week[1],week[2]) if x[0].id=='lunch-refreshed')
    def judge(prompt):
        prior = json.loads(prompt.split('Prior public conversation: ')[1].splitlines()[0])
        assert {r['at'][:10] for r in prior} == {'2026-10-06','2026-10-07'}
        return json.dumps({'verdicts':[dict(id=c.id, verdict='missed',reason='fixture') for c in turn.claims]})
    assert grade_turn(turn,surface,judge,tmp_path/'grades').missed == len(turn.claims)


@pytest.mark.parametrize('bad', ['scope-clock','channel-clock','channel-ids'])
def test_connector_cannot_substitute_later_watermark_or_other_channels(tmp_path, week, bad):
    c = condition('memcal','first-party-connector')
    class Fault(RecordingAccount):
        def advance(self, at):
            row = super().advance(at)
            if bad == 'scope-clock':
                row['as_of'] = '2030-01-01T00:00:00Z'
            return row

        def sync_readback(self, ack):
            row = super().sync_readback(ack)
            witness = row['channel_readback']['email']
            if bad == 'channel-clock':
                witness['as_of'] = '2030-01-01T00:00:00Z'
            if bad == 'channel-ids':
                witness['new_source_ids'] = list(row['new_source_ids'])
            return row
    with pytest.raises(ValueError, match='clock|channel readback'):
        run(tmp_path,c,week[1],[],Fault(c,week[1]))


def test_supermemory_submits_one_evening_before_waiting_for_consolidation(tmp_path, week):
    from pacbench.workflows.runner import deliver
    c = condition('supermemory')
    docs = [d for d in week[1] if d.cutoff == week[1][0].cutoff]
    account = RecordingAccount(c)
    try:
        with Journal(tmp_path/'evidence') as journal:
            observed = deliver(account,c,docs,journal,week[2][0].at)
        events = [json.loads(s) for s in (tmp_path/'evidence/workflow.jsonl').read_text().splitlines()]
        phases = [r['phase'] for r in events]
        assert phases == ['source-clock']+['submit','acceptance']*len(docs)+['readback']*len(docs)
        assert observed == account.source_inventory() == [d.filename for d in docs]
        assert [r['evidence']['submitted_sha256'] for r in events if r['phase']=='readback']==[d.sha256 for d in docs]
    finally:
        account.close()
