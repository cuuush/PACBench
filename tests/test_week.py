"""Week contracts and fault boundaries, not synthetic provider accuracy."""
import json
from pathlib import Path

import pytest
from pacbench.prepare import resolve_spec, normalize_week, import_week
from pacbench.evaluation import contract_evidence
from pacbench.document_ingestion import prepare_sources, ready_sources
from pacbench.native_checks import run_checks, audit_checks
from pacbench.authoring import scene_prompt
from tests.test_evaluation import checkpoints, correct_judge
from pacbench.harness import TurnResult
from tests.test_document_ingestion import message


def test_one_week_resolves_facts_sessions_and_prefix_truth(tmp_path):
    spec=json.loads(Path('weeks/pizza_week.json').read_text())
    scenes,_,resolved=resolve_spec(spec,tmp_path,week_start="2026-09-07")
    assert len(scenes)==17 and len(resolved['checks'])==18
    scene_by_id={s.id:s for s in scenes}
    check_by_id={c['id']:c for c in resolved['checks']}
    assert scene_by_id['leo-joins'].participants==('Me','Maya','Leo')
    assert scene_by_id['group-venue-update'].participants==scene_by_id['leo-joins'].participants
    assert scene_by_id['ben-email-reschedule'].email_subject=='Dinner'
    assert check_by_id['initial-plan-count']['at']=='2026-09-10T09:00:00-04:00'
    assert {check_by_id[k]['session'] for k in ('initial-plan-count','initial-plan-details')}=={'thursday-morning'}
    assert {check_by_id[k]['session'] for k in ('updated-plan-count','updated-plan-details','leo-availability')}=={'friday-morning'}
    assert check_by_id['devon-venue-fresh']['session']=='friday-devon-fresh'
    assert all(r.get('kind','recall')=='recall' and not r.get('requires') and
               not r.get('depends_on') and not r.get('trigger') for r in resolved['checks'])
    rows=[message('old',stamp='2026-09-09T18:00:00-04:00'),message('future')]
    rows[0]['collected_at']='2026-09-09T18:00:00-04:00'
    evidence=contract_evidence(rows,resolved['checks'],check_by_id['initial-plan-count'])
    assert {r['source_id'] for r in evidence}=={'old','assistant-user:initial-plan-count',
        'assistant-user:pond-initial','assistant-user:pond-cancellation',
        'assistant-user:separate-appointments','assistant-user:ben-one-dinner','assistant-user:ben-moved'}
    assert 'assistant-user:updated-plan-count' not in {r['source_id'] for r in evidence}
    bad=json.loads(json.dumps(spec));bad['days']['wednesday']['scenes'][0]['channel']='missing'
    with pytest.raises(ValueError,match='Unknown channel'):normalize_week(bad,'2026-09-07')


def test_noise_is_a_week_enum_and_high_is_per_member_not_per_group(tmp_path):
    spec=json.loads(Path('weeks/pizza_week.json').read_text());spec['noise']='high'
    scenes,_,resolved=resolve_spec(spec,tmp_path,week_start="2026-09-07")
    assert resolved['noise']=='high'
    scene_by_id={s.id:s for s in scenes}
    assert scene_by_id['devon-invite'].message_range==(1200,1600)
    assert scene_by_id['leo-joins'].message_range==(1800,2400)
    assert scene_by_id['leo-joins'].messages_per_person==(600,800)
    assert 'unrelated topics' in scene_prompt(scene_by_id['leo-joins'])
    assert 'members can be silent' not in scene_prompt(scene_by_id['leo-joins']).lower()
    spec['noise']='extreme'
    with pytest.raises(ValueError,match='low, medium or high'):resolve_spec(spec,tmp_path,week_start="2026-09-07")


def runner_result(prompt, kwargs, *, rc=0, answer='Synthetic answer.'):
    return TurnResult(prompt=f"[Simulated now: {kwargs['at']}. Treat this as the current date and time for everything in this turn.]\n\n{prompt}",
        at=kwargs['at'],answer=answer,tool_calls=[],tool_results=[],policy_blocks=0,latency_s=1,
        returncode=rc,stderr_tail='',raw_events=0,session_id=kwargs['session_id'] or 's-'+prompt,
        session_input_tokens=100,session_output_tokens=20)


def test_execution_failure_skips_dependent_session_and_continues_independent(tmp_path):
    checks=checkpoints(tmp_path)
    first,second=checks
    third=({'id':'dependent','at':'2026-09-11T09:02:00-04:00','session':'bad'},first[1],first[2])
    first[0]['session']='bad';second[0]['session']='fresh'
    checks=[first,third,second]
    called=[]
    def run(prompt,**kw):
        called.append(kw['session_id'])
        return runner_result(prompt,kw,rc=1 if len(called)==1 else 0)
    def judge(prompt):
        doc=json.loads(correct_judge(prompt));doc.update(interaction='answer',benchmark_error=None);return json.dumps(doc)
    root=tmp_path/'arm'
    report=run_checks(checks,'shared',root,judge,'',{},question_dir=tmp_path/'q',run_turn=run,budget_snapshot=lambda:{'projects':[]})
    assert called==[None,None]
    assert [r['state'] for r in report['checks']]==['ungraded','skipped','diagnostic']
    assert report['state']=='partial' and report['counts']=={'pass':1,'fail':0,'ungraded':2}
    assert audit_checks(checks,'shared',root)==[]


@pytest.mark.parametrize('clarifies',[True,False])
def test_one_scripted_reply_only_when_requested_and_no_recovery_for_actions(tmp_path,clarifies):
    checks=checkpoints(tmp_path)
    a,b=checks;a[0].update(session='story',kind='behavior');b[0].update(session='story',kind='behavior',trigger='clarification',depends_on=a[0]['id'])
    calls=[]
    def run(prompt,**kw):calls.append(kw);return runner_result(prompt,kw)
    def judge(prompt):
        doc=json.loads(correct_judge(prompt));doc.pop('recovery');doc.update(interaction='clarification' if clarifies and len(calls)==1 else 'answer',benchmark_error=None);return json.dumps(doc)
    root=tmp_path/'arm'
    report=run_checks(checks,'shared',root,judge,'',{},question_dir=tmp_path/'q',run_turn=run)
    assert len(calls)==(2 if clarifies else 1)
    assert report['state']=='diagnostic'
    assert not list(root.rglob('followups'))
    if clarifies:assert calls[1]['session_id']=='s-'+a[1].question
    assert audit_checks(checks,'shared',root)==[]


def test_missing_script_truth_is_ungraded_and_does_not_stop_fresh_session(tmp_path):
    checks=checkpoints(tmp_path)
    for r,_,_ in checks:r.update(session=r['id'],kind='behavior')
    calls=[]
    def run(prompt,**kw):calls.append(prompt);return runner_result(prompt,kw)
    def judge(prompt):
        doc=json.loads(correct_judge(prompt));doc.pop('recovery');doc.update(interaction='clarification',benchmark_error='Script omitted scope of a legitimate choice' if len(calls)==1 else None);return json.dumps(doc)
    report=run_checks(checks,'shared',tmp_path/'arm',judge,'',{},question_dir=tmp_path/'q',run_turn=run)
    assert len(calls)==2
    assert report['checks'][0]['verdict'] is None and report['checks'][0]['error_type']=='benchmark_contract'
    assert report['checks'][1]['verdict']=='pass'


def test_conditional_evidence_never_counts_when_reply_was_not_delivered(tmp_path):
    a,b=checkpoints(tmp_path);a[0].update(session='story',kind='behavior');b[0].update(session='story',kind='behavior',trigger='clarification',depends_on=a[0]['id'])
    c=({'id':'later','at':b[0]['at'],'session':'fresh','requires':[b[0]['id']]},a[1],a[2])
    calls=[]
    def run(prompt,**kw):calls.append(prompt);return runner_result(prompt,kw)
    def judge(prompt):
        doc=json.loads(correct_judge(prompt));doc.pop('recovery');doc.update(interaction='answer',benchmark_error=None);return json.dumps(doc)
    report=run_checks([a,b,c],'shared',tmp_path/'arm',judge,'',{},question_dir=tmp_path/'q',run_turn=run)
    assert len(calls)==1 and report['checks'][2]['state']=='skipped' and report['checks'][2]['verdict'] is None


def test_cumulative_fixture_keeps_silent_group_member_and_shared_dm_identity(tmp_path):
    import sqlite3
    from pacbench.imessage_fixture import append_messages
    path=tmp_path/'chat.db'
    roster={'participants':['Me','Maya','Leo'],'chat_name':'Friday crew'}
    rows=[{**message('a','Maya',thread='group'),'structured':roster},
          {**message('b','Me',True,thread='group'),'structured':roster},
          {**message('c','Maya',thread='dm'),'structured':{'participants':['Me','Maya'],'chat_name':''}}]
    append_messages(path,rows)
    append_messages(path,[{**message('d','Maya',thread='group',stamp='2026-09-11T18:00:00-04:00'),'structured':roster}])
    with sqlite3.connect(path) as db:
        group=db.execute("SELECT ROWID FROM chat WHERE display_name='Friday crew'").fetchone()[0]
        assert db.execute('SELECT count(*) FROM chat_handle_join WHERE chat_id=?',(group,)).fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM handle').fetchone()[0]==2
        assert db.execute('SELECT count(DISTINCT handle_id) FROM message WHERE is_from_me=0').fetchone()[0]==1
    manifest=json.loads(path.with_suffix('.fixture.json').read_text())
    assert manifest['threads']['group']['participants']==['Me','Maya','Leo']


def test_frozen_week_rejects_changed_content_before_execution(tmp_path):
    from pacbench.followups import digest
    body={'version':1,'files':{'spec.json':{'noise':'low'}}}
    file=tmp_path/'week.json';file.write_text(json.dumps({**body,'corpus_hash':digest(body)}))
    changed=json.loads(file.read_text());changed['files']['spec.json']['noise']='high';file.write_text(json.dumps(changed))
    with pytest.raises(ValueError,match='hash/version'):import_week(file,tmp_path/'preparation')
    assert not (tmp_path/'preparation').exists()


def test_frozen_week_preserves_readable_calls_without_private_replay(tmp_path, monkeypatch):
    import pacbench.prepare as preparation
    from pacbench.model_calls import ENCRYPTED_REDACTION
    monkeypatch.setattr(preparation, 'audit_prepared', lambda _: {'state':'pass'})
    root=tmp_path/'authoring';(root/'model-calls/private').mkdir(parents=True)
    record={'text':'Synthetic draft','response':{'encrypted_content':'opaque-replay'}}
    (root/'model-calls/call.json').write_text(json.dumps(record))
    (root/'model-calls/private/call.json').write_text(json.dumps(record))
    frozen=tmp_path/'week.json';preparation.export_week(root,frozen)
    payload=json.loads(frozen.read_text())
    assert 'opaque-replay' not in frozen.read_text()
    assert not any('/private/' in name for name in payload['files'])
    imported=import_week(frozen,tmp_path/'imported')
    call=json.loads((imported/'model-calls/call.json').read_text())
    assert call['text']=='Synthetic draft'
    assert call['response']['encrypted_content']==ENCRYPTED_REDACTION


def test_human_times_weekdays_and_dst_resolve_without_changing_author_file(tmp_path):
    from pacbench.prepare import clock_on_day
    from datetime import date
    assert clock_on_day(date(2026,10,5),'7:30 am')=='2026-10-05T07:30:00'
    assert clock_on_day(date(2026,10,5),'12 am').endswith('T00:00:00')
    assert clock_on_day(date(2026,10,5),'12 PM').endswith('T12:00:00')
    assert clock_on_day(date(2026,10,5),'8 pm').endswith('T20:00:00')
    for value in ('25 pm','0 am','7:90 am','2026-09-09T08:00:00'):
        with pytest.raises(ValueError,match='Invalid human time'):clock_on_day(date(2026,10,5),value)
    spec=json.loads(Path('weeks/pizza_week.json').read_text());original=json.dumps(spec)
    spec['days']['sunday']={'conversations':[{'id':'sunday-fresh','time':'9 am','turns':[
        {'id':'sunday-recap','prompt':'What happened with pizza Friday?','expect':'Recap both Friday outings.'}]}]}
    scenes,_,resolved=resolve_spec(spec,tmp_path,week_start='2026-10-26')
    assert next(s for s in scenes if s.id=='devon-invite').at=='2026-10-28T18:00:00-04:00'
    assert resolved['checks'][-1]['at']=='2026-11-01T09:00:00-05:00'
    assert 'date' not in spec['days']['wednesday']
    assert next(c for c in resolved['checks'] if c['id']=='initial-plan-count')['expect']==spec['days']['thursday']['conversations'][0]['turns'][0]['expect']
    assert resolved['checks'][0]['facts']==[]
    with pytest.raises(ValueError,match='Monday'):resolve_spec(spec,tmp_path,week_start='2026-10-27')


def test_english_expectation_becomes_reviewed_criteria_without_a_fact_catalog(tmp_path):
    from pacbench.evaluation import freeze_evaluations,load_evaluations,resolve_checks
    row={'id':'one','prompt':'What is the plan?','expect':'Dinner is at the cafe.','at':'2026-09-11T09:00:00-04:00'}
    resolved={**resolve_checks({'checks':[row]},row['at'],'America/New_York'),'question_at':row['at'],'timezone':'America/New_York'}
    messages=[{**message('m1'),'text':'Dinner at the cafe.','collected_at':'2026-09-10T18:00:00-04:00'}]
    prompts=[]
    def model(prompt):
        prompts.append(prompt)
        if 'Identify independently' in prompt:return json.dumps({'claims':[{'id':'dinner-place','statement':'Dinner is at the cafe.','evidence':['m1']}]})
        if 'Review each proposed claim' in prompt:return json.dumps({'complete':True,'reviews':[{'id':'dinner-place','supported':True,'relevant':True,'unambiguous':True,'reason':'Source supports the venue.'}]})
        return json.dumps({'safe':True,'faithful':True,'elicits_all':True,'reason':'The natural question covers dinner.'})
    plan=freeze_evaluations(resolved,messages,None,model,tmp_path)
    loaded,checks=load_evaluations(tmp_path,messages,resolved)
    assert loaded==plan and checks[0][1].claims[0].statement=='Dinner is at the cafe.'
    assert all('Required facts:' not in p for p in prompts)
    changed=json.loads(json.dumps(resolved));changed['checks'][0]['expect']='Dinner is at the park.'
    with pytest.raises(ValueError,match='mismatch'):load_evaluations(tmp_path,messages,changed)


def test_preparation_continues_after_one_contract_error_but_cannot_freeze_success(tmp_path):
    from pacbench.evaluation import freeze_evaluations,resolve_checks
    rows=[{'id':'bad','prompt':'Bad question','expect':'Dinner venue'},{'id':'good','prompt':'Good question','expect':'Dinner venue'}]
    resolved={**resolve_checks({'checks':rows},'2026-09-11T09:00:00-04:00','America/New_York')}
    calls=[]
    def model(prompt):
        calls.append(prompt)
        if 'Test intent: Dinner venue' in prompt and 'Bad question' not in prompt:
            if 'Identify independently' in prompt:return json.dumps({'claims':[{'id':'place','statement':'Dinner is at the cafe.','evidence':['m1']}]})
            return json.dumps({'complete':True,'reviews':[{'id':'place','supported':True,'relevant':True,'unambiguous':True,'reason':'Source.'}]})
        if 'Prompt: Bad question' in prompt:raise RuntimeError('Simulated judge transport error')
        return json.dumps({'safe':True,'faithful':True,'elicits_all':True,'reason':'Fair question.'})
    messages=[{**message('m1'),'text':'Dinner at the cafe.','collected_at':'2026-09-10T18:00:00-04:00'}]
    plan=freeze_evaluations(resolved,messages,None,model,tmp_path)
    assert plan['errors'][0]['id']=='bad'
    assert [r['id'] for r in plan['checks']]==['good']
    assert (tmp_path/'evaluation-errors.json').exists()


def test_portable_corpus_reuse_never_calls_a_model(tmp_path,monkeypatch):
    from pacbench.frozen import write_bundle
    from pacbench.followups import digest,freeze_policy
    from pacbench.semantic_contract import ContractClaim,EvaluationContract
    from pacbench.prepare import export_week
    from pacbench.native_pilot import read_prepared
    from pacbench.model_calls import ModelClient
    root=tmp_path/'original';root.mkdir()
    messages=[message('m1')]
    bundle={'lives':{'one':{'lineage':'one','messages':messages}}}
    hashed=write_bundle(bundle,root/'bundle.json')
    contract={'claims':[{'id':'fact-1','statement':'Friday dinner at seven.','evidence':['m1']}],
        'question':'What is the dinner plan?','evidence_hash':digest(messages)}
    contract['contract_hash']=digest(contract)
    (root/'contract.json').write_text(json.dumps(contract))
    (root/'summary.json').write_text(json.dumps({'state':'prepared','results':[],'bundle_hash':hashed,'contract_hash':contract['contract_hash']}))
    (root/'resolved-spec.json').write_text(json.dumps({'question_at':'2026-09-11T09:00:00-04:00','timezone':'America/New_York','checks':[]}))
    freeze_policy(EvaluationContract((ContractClaim('fact-1','Friday dinner at seven.',('m1',)),),contract['question'],digest(messages),contract['contract_hash']),root/'followup-plan.json')
    export_week(root,tmp_path/'week.json')
    def forbid(*a,**kw):raise AssertionError('Executing a frozen corpus must not call an author or verifier')
    monkeypatch.setattr(ModelClient,'__call__',forbid)
    copy=import_week(tmp_path/'week.json',tmp_path/'copy')
    received,_,_=read_prepared(copy,100)
    assert received==messages
