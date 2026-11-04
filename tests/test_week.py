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

from tests.test_evaluation import _combined_fixture



def test_bike_pickup_correction_is_followed_by_independent_session_checks(tmp_path):
    scenes,_,resolved=resolve_spec(json.loads(Path('weeks/bike_pickup.json').read_text()),tmp_path,week_start='2026-10-19')
    checks={c['id']:c for c in resolved['checks']}
    assert len(scenes)==3 and len(checks)==5
    assert scenes[-1].channel=='email'
    assert checks['pickup-before']['session']==checks['pickup-change']['session']==checks['coffee-same-chat']['session']
    assert checks['sunday-both']['session']!=checks['pickup-change']['session']
    assert checks['saturday-clear']['session']!=checks['sunday-both']['session']
    assert checks['sunday-both']['requires']==['pickup-change']
    assert checks['saturday-clear']['requires']==['pickup-change']
    evidence=contract_evidence([],resolved['checks'],checks['sunday-both'])
    assert any(m['source_id']=='assistant-user:pickup-change' for m in evidence)


def test_one_week_resolves_facts_sessions_and_prefix_truth(tmp_path):
    spec=json.loads(Path('weeks/pizza_week.json').read_text())
    scenes,_,resolved=resolve_spec(spec,tmp_path,week_start="2026-09-07")
    assert len(scenes)==19 and len(resolved['checks'])==17
    scene_by_id={s.id:s for s in scenes}
    check_by_id={c['id']:c for c in resolved['checks']}
    assert scene_by_id['leo-joins'].participants==('Me','Maya','Leo')
    assert scene_by_id['group-venue-update'].participants==scene_by_id['leo-joins'].participants
    assert scene_by_id['ben-email-reschedule'].email_subject=='Dinner'
    assert check_by_id['pizza-final-details']['at']=='2026-09-13T21:00:00-04:00'
    assert {check_by_id[k]['session'] for k in ('pizza-final-details','maya-change-history','leo-cover')}=={'pizza-recap'}
    assert check_by_id['jen-upcoming']['session']=='jen-next-week'
    assert all(r['at']>scenes[-1].window_end for r in resolved['checks'])
    assert all(r.get('kind','recall')=='recall' and not r.get('requires') and
               not r.get('depends_on') and not r.get('trigger') for r in resolved['checks'])
    rows=[message('old',stamp='2026-09-09T18:00:00-04:00'),message('future',stamp='2026-09-14T18:00:00-04:00')]
    rows[0]['collected_at']='2026-09-09T18:00:00-04:00'
    rows[1]['collected_at']=rows[1]['written_at']
    evidence=contract_evidence(rows,resolved['checks'],check_by_id['maya-change-history'])
    assert {r['source_id'] for r in evidence}=={'old','assistant-user:pizza-final-details','assistant-user:maya-change-history'}
    assert 'assistant-user:leo-cover' not in {r['source_id'] for r in evidence}
    bad=json.loads(json.dumps(spec));bad['days']['wednesday']['scenes'][0]['channel']='missing'
    with pytest.raises(ValueError,match='Unknown channel'):normalize_week(bad,'2026-09-07')


def test_noise_is_a_week_enum_and_high_is_per_member_not_per_group(tmp_path):
    spec=json.loads(Path('weeks/pizza_week.json').read_text());spec['noise']='high'
    scenes,_,resolved=resolve_spec(spec,tmp_path,week_start="2026-09-07")
    assert resolved['noise']=='high'
    scene_by_id={s.id:s for s in scenes}
    assert scene_by_id['devon-invite'].message_range==(1200,1600)
    assert scene_by_id['leo-joins'].message_range==(900,1200)
    assert scene_by_id['leo-joins'].messages_per_person==(300,400)
    assert 'unrelated topics' in scene_prompt(scene_by_id['leo-joins'])
    assert 'members can be silent' not in scene_prompt(scene_by_id['leo-joins']).lower()
    spec['noise']='extreme'
    with pytest.raises(ValueError,match='low, medium or high'):resolve_spec(spec,tmp_path,week_start="2026-09-07")


def test_medium_noise_keeps_email_volume_distinct_from_text_chat(tmp_path):
    spec=json.loads(Path('weeks/pizza_week.json').read_text());spec['noise']='medium'
    scenes,_,_=resolve_spec(spec,tmp_path,week_start='2026-09-07')
    email=next(s for s in scenes if s.id=='ben-email-reschedule')
    dm=next(s for s in scenes if s.id=='devon-invite')
    assert email.message_range==(2,6) and email.messages_per_person is None
    assert dm.message_range==(40,80) and dm.messages_per_person==(20,40)


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


def test_recovery_authoring_error_preserves_initial_grade_and_disjoint_counts(tmp_path,monkeypatch):
    from pacbench import native_checks
    from pacbench.semantic_contract import EvaluationContract,ContractClaim
    from pacbench.followups import freeze_policy
    checks=checkpoints(tmp_path)
    row,old,_=checks[0]
    contract=EvaluationContract(old.claims+(ContractClaim('detail-2','Another detail.',('m1',)),),
        old.question,old.evidence_hash,'two-facts')
    checks[0]=(row,contract,freeze_policy(contract,tmp_path/'two-fact-policy.json'))
    def judge(prompt):
        result=json.loads(correct_judge(prompt))
        if len(result['verdicts'])==2:
            result['verdicts'][1]['verdict']='missed'
            result['recovery'][1]['kind']='implicit_omission'
        return json.dumps(result)
    def recover(**kwargs):
        return {'state':'incomplete' if kwargs['grade'].missed else 'complete','score':{},'attempts':[]}
    monkeypatch.setattr(native_checks,'run_followups',recover)
    report=run_checks(checks,'isolated',tmp_path/'arm',judge,'',{},
        question_dir=tmp_path/'questions',run_turn=lambda prompt,**kw:runner_result(prompt,kw),budget_snapshot=lambda:{'projects':[]})
    assert report['state']=='partial'
    assert [x['state'] for x in report['checks']]==['diagnostic','diagnostic']
    assert report['counts']=={'pass':1,'fail':1,'ungraded':0,'recovery_ungraded':1}


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
    (root/'model-calls/call.json').write_text(json.dumps({**record,'response':{'encrypted_content':ENCRYPTED_REDACTION}}))
    (root/'model-calls/private/call.json').write_text(json.dumps(record))
    frozen=root/'week.json';preparation.export_week(root,frozen)
    payload=json.loads(frozen.read_text())
    assert payload['version']==2
    assert len(frozen.read_bytes())<500
    assert 'opaque-replay' not in frozen.read_text()
    assert not any('/private/' in name for name in payload['files'])
    imported=import_week(frozen,tmp_path/'imported')
    call=json.loads((imported/'model-calls/call.json').read_text())
    assert call['text']=='Synthetic draft'
    assert call['response']['encrypted_content']==ENCRYPTED_REDACTION


@pytest.mark.parametrize('change',['content','missing','escape'])
def test_manifest_checks_all_references_before_creating_import(tmp_path,monkeypatch,change):
    from pacbench.prepare import export_week
    import pacbench.prepare as preparation
    monkeypatch.setattr(preparation,'audit_prepared',lambda _: {'state':'pass'})
    root=tmp_path/'original';root.mkdir()
    data=root/'spec.json';data.write_text('{"noise":"medium"}')
    export_week(root,root/'week.json')
    if change=='content':data.write_text('{"noise":"high"}')
    elif change=='missing':data.unlink()
    else:
        outside=tmp_path/'private.json';outside.write_bytes(data.read_bytes())
        data.unlink();data.symlink_to(outside)
    with pytest.raises((ValueError,OSError)):
        import_week(root/'week.json',tmp_path/'imported')
    assert not (tmp_path/'imported').exists()


def test_manifest_refuses_unredacted_replay(tmp_path,monkeypatch):
    import pacbench.prepare as preparation
    monkeypatch.setattr(preparation,'audit_prepared',lambda _: {'state':'pass'})
    (tmp_path/'model-calls').mkdir()
    (tmp_path/'model-calls/call.json').write_text('{"encrypted_content":"private"}')
    with pytest.raises(ValueError,match='Private replay'):
        preparation.export_week(tmp_path,tmp_path/'week.json')
    assert not (tmp_path/'week.json').exists()


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
    assert resolved['checks'][0]['expect']==spec['days']['sunday']['conversations'][0]['turns'][0]['expect']
    assert resolved['checks'][0]['facts']==[]
    with pytest.raises(ValueError,match='Monday'):resolve_spec(spec,tmp_path,week_start='2026-10-27')


@pytest.mark.parametrize('dedicated_draft',[False,True])
def test_english_expectation_becomes_reviewed_criteria_without_a_fact_catalog(tmp_path,dedicated_draft):
    from pacbench.evaluation import freeze_evaluations,load_evaluations,resolve_checks
    row={'id':'one','prompt':'What is the plan?','expect':'Dinner is at the cafe.','at':'2026-09-11T09:00:00-04:00'}
    resolved={**resolve_checks({'checks':[row]},row['at'],'America/New_York'),'question_at':row['at'],'timezone':'America/New_York'}
    messages=[{**message('m1'),'text':'Dinner at the cafe.','collected_at':'2026-09-10T18:00:00-04:00'}]
    prompts=[]
    def model(prompt):
        prompts.append(prompt)
        if 'Identify independently' in prompt:
            assert not dedicated_draft
            return json.dumps({'claims':[{'id':'dinner-place','statement':'Dinner is at the cafe.','evidence':['m1']}]})
        if 'Review each proposed claim' in prompt:return json.dumps({'complete':True,'reviews':[{'id':'dinner-place','supported':True,'relevant':True,'unambiguous':True,'reason':'Source supports the venue.'}]})
        return json.dumps({'safe':True,'faithful':True,'elicits_all':True,'reason':'The natural question covers dinner.'})
    def draft(prompt):
        assert 'Identify independently' in prompt
        prompts.append(prompt)
        return json.dumps({'claims':[{'id':'dinner-place','statement':'Dinner is at the cafe.','evidence':['m1']}]})
    plan=freeze_evaluations(resolved,messages,None,_combined_fixture(model),tmp_path,generate=draft if dedicated_draft else None)
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
        if 'Check this evaluation prompt' in prompt:
            if 'Public question: Bad question' in prompt:
                raise RuntimeError('Simulated judge transport error')
            return json.dumps({'safe':True,'faithful':True,'elicits_all':True,'reason':'Fair question.'})
        if 'Test intent: Dinner venue' in prompt and 'Bad question' not in prompt:
            if 'Identify independently' in prompt:return json.dumps({'claims':[{'id':'place','statement':'Dinner is at the cafe.','evidence':['m1']}]})
            return json.dumps({'complete':True,'reviews':[{'id':'place','supported':True,'relevant':True,'unambiguous':True,'reason':'Source.'}]})
        return json.dumps({'safe':True,'faithful':True,'elicits_all':True,'reason':'Fair question.'})
    messages=[{**message('m1'),'text':'Dinner at the cafe.','collected_at':'2026-09-10T18:00:00-04:00'}]
    plan=freeze_evaluations(resolved,messages,None,_combined_fixture(model),tmp_path)
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
    export_week(root,root/'week.json')
    def forbid(*a,**kw):raise AssertionError('Executing a frozen corpus must not call an author or verifier')
    monkeypatch.setattr(ModelClient,'__call__',forbid)
    copy=import_week(root/'week.json',tmp_path/'copy')
    received,_,_=read_prepared(copy,100)
    assert received==messages


def test_month_has_thirty_batches_stable_threads_and_only_final_questions(tmp_path):
    from pacbench.document_ingestion import daily_batches
    spec=json.loads(Path('weeks/pizza_month.json').read_text())
    scenes,_,resolved=resolve_spec(spec,tmp_path,week_start='2026-09-14')
    assert len(scenes)==48 and resolved['ingestion_days']==30 and resolved['noise']=='medium'
    assert scenes[0].at.startswith('2026-09-15') and scenes[-1].at.startswith('2026-10-14')
    assert len({s.id for s in scenes})==len(scenes)
    assert {s.thread for s in scenes if s.id.endswith('devon-friday')}=={'devon'}
    assert len(resolved['checks'])==15
    assert all(c['at'].startswith('2026-10-19') and c['at']>scenes[-1].window_end for c in resolved['checks'])
    # Partial updates must have their preceding conversation available to the
    # writer, with time to finish before the next scene in that same chat.
    threads={}
    for scene in scenes:
        earlier=threads.get((scene.channel,scene.thread))
        if earlier:assert earlier.window_end<scene.at
        threads[scene.channel,scene.thread]=scene
    assert {c['session'] for c in resolved['checks'] if c['id'] in
            ('devon-friday','devon-current')}=={'devon-friday-fresh','devon-current-fresh'}
    rows=[{**message(str(i),thread=s.thread,stamp=s.at),'collected_at':s.at} for i,s in enumerate(scenes)]
    assert len(daily_batches(rows,'America/New_York'))==30
    spec['ingestion_days']=31
    with pytest.raises(ValueError,match='contiguous'):resolve_spec(spec,tmp_path,week_start='2026-09-14')


def test_multiweek_scope_across_dst_and_repeated_scene_names(tmp_path):
    spec=json.loads(Path('weeks/pizza_week.json').read_text())
    days=spec.pop('days');spec['weeks']=[days,json.loads(json.dumps(days))]
    for day in spec['weeks'][0].values():day.pop('conversations',None)
    scenes,_,resolved=resolve_spec(spec,tmp_path,week_start='2026-10-26')
    assert len(scenes)==38 and len({s.id for s in scenes})==38
    assert next(s for s in scenes if s.id=='w1-devon-invite').at.endswith('-04:00')
    assert next(s for s in scenes if s.id=='w2-devon-invite').at.endswith('-05:00')
    assert all(c['at'].startswith('2026-11-08') for c in resolved['checks'])
    spec['days']=days
    with pytest.raises(ValueError,match='not both'):normalize_week(spec,'2026-10-26')


def test_operator_stop_after_turn_preserves_answer_without_judging(tmp_path):
    stop_file = tmp_path / 'STOP'
    called = []
    def run(prompt, **kwargs):
        called.append(prompt)
        stop_file.touch()
        return runner_result(prompt, kwargs)
    def judge(_prompt):
        pytest.fail('judge must not run after operator stop')
    report = run_checks(checkpoints(tmp_path), 'isolated', tmp_path / 'arm', judge, '',
                        {'PACBENCH_STOP_FILE': str(stop_file)}, question_dir=tmp_path/'q', run_turn=run)
    assert report['state'] == 'interrupted'
    assert len(called) == 1
    assert report['checks'][0]['state'] == 'interrupted'
    assert (tmp_path/'arm/turns'/report['checks'][0]['id']/'turn.json').exists()
    assert not list((tmp_path/'arm').rglob('judgment.json'))
